# SPDX-License-Identifier: Apache-2.0
"""Bounded preflight and byte freezing for a trusted native FCStd worker profile.

This module does not import FreeCAD, restore a document, unpack a model, or save
CAD. A successful preflight is a finite format/profile check, not a sandbox for
untrusted files or a proof that native restore has no callbacks. The caller must
still own an isolated worker and verify document ownership through its lifecycle.

FreeCAD 1.1 serialization references:
https://github.com/FreeCAD/FreeCAD/blob/1.1.0/src/App/Document.cpp
https://github.com/FreeCAD/FreeCAD/blob/1.1.0/src/App/ExtensionContainer.cpp
https://github.com/FreeCAD/FreeCAD/blob/1.1.0/src/Mod/Part/App/PropertyTopoShape.cpp
"""
import errno
import hashlib
import io
import math
import os
from pathlib import Path
import re
import stat
import struct
import unicodedata
import uuid
import xml.etree.ElementTree as ET
import zipfile
import zlib

MAX_COMPRESSED_BYTES = 64 * 1024 * 1024
MAX_EXPANDED_BYTES = 256 * 1024 * 1024
MAX_XML_BYTES = 16 * 1024 * 1024
MAX_ZIP_ENTRIES = 10000
MAX_XML_NODES = 100000
MAX_XML_DEPTH = 64
MAX_OBJECTS = 4096
MAX_LIST_ITEMS = 100000
_CHUNK = 65536
_SEAL = object()
_STAGED_NAME = "input.FCStd"

SUPPORTED_OBJECT_TYPES = frozenset((
    "Part::Feature", "PartDesign::Body", "Sketcher::SketchObject",
    "PartDesign::Pad", "PartDesign::Pocket",
    "App::Origin", "App::Line", "App::Plane", "App::Point",
))
# Native, non-Python property classes. Unknown classes are not dynamically loaded.
_SCALARS = (
    "String", "StringList", "StringSet", "Map", "Bool", "BoolList", "Integer",
    "IntegerConstraint", "IntegerList", "Enumeration", "Float", "FloatConstraint",
    "FloatList", "Quantity", "QuantityConstraint", "Length", "Distance", "Angle",
    "Area", "Volume", "Speed", "Acceleration", "Precision", "Percent", "Temperature",
    "Pressure", "Force", "Vector", "VectorList", "VectorDistance", "Position",
    "Placement", "PlacementList", "Matrix", "Rotation", "UUID", "Color", "ColorList",
    "Material", "MaterialList", "Font",
)
_LINKS = frozenset("App::Property" + name for name in (
    "Link", "LinkHidden", "LinkList", "LinkListHidden", "LinkSub", "LinkSubHidden",
    "LinkSubList", "LinkSubListHidden",
))
SUPPORTED_PROPERTY_TYPES = frozenset("App::Property" + name for name in _SCALARS) | _LINKS | frozenset((
    "App::PropertyExpressionEngine", "Part::PropertyPartShape",
    "Part::PropertyGeometryList", "Sketcher::PropertyConstraintList",
    "Materials::PropertyMaterial",
))
_NATIVE_EXTENSIONS = {
    "PartDesign::Body": frozenset(("App::OriginGroupExtension",)),
    "Sketcher::SketchObject": frozenset(("Part::AttachExtension",)),
    "PartDesign::Pad": frozenset(("App::SuppressibleExtension", "Part::PreviewExtension")),
    "PartDesign::Pocket": frozenset(("App::SuppressibleExtension", "Part::PreviewExtension")),
    "App::Origin": frozenset(("App::GroupExtension",)),
}
_GUI_EXTENSIONS = frozenset((
    "Gui::ViewProviderGroupExtension", "Gui::ViewProviderGeoFeatureGroupExtension",
    "Gui::ViewProviderOriginGroupExtension", "Gui::ViewProviderSuppressibleExtension",
    "PartGui::ViewProviderAttachExtension", "PartGui::ViewProviderGridExtension",
    "PartGui::ViewProviderPreviewExtension", "Gui::ViewProviderFaceTexture",
))
_GEOMETRIES = frozenset("Part::" + name for name in (
    "GeomPoint", "GeomLine", "GeomLineSegment", "GeomCircle", "GeomArcOfCircle",
    "GeomEllipse", "GeomArcOfEllipse", "GeomHyperbola", "GeomArcOfHyperbola",
    "GeomParabola", "GeomArcOfParabola", "GeomBSplineCurve", "GeomBezierCurve",
))
_MESSAGES = {
    "WORKER_INPUT_UNSUPPORTED": "The FCStd uses an unsupported native input profile.",
    "WORKER_INPUT_PATH_INVALID": "An absolute canonical FCStd path is required.",
    "WORKER_INPUT_UNSAFE": "The input file or directory identity is unsafe.",
    "WORKER_INPUT_LIMIT": "The FCStd exceeds the input resource budget.",
    "WORKER_INPUT_INVALID": "The FCStd archive or XML is invalid.",
    "WORKER_INPUT_CHANGED": "The source or frozen input changed.",
    "WORKER_INPUT_STAGING_INVALID": "An existing private owned staging directory is required.",
    "WORKER_INPUT_IO_FAILED": "The input could not be frozen or verified.",
    "WORKER_INPUT_CLEANUP_UNVERIFIED": "Owned input cleanup could not be verified.",
}


class PftWorkerInputError(RuntimeError):
    def __init__(self, code):
        self.code = code
        self.message = _MESSAGES[code]
        super().__init__(self.message)


def _fail(code):
    raise PftWorkerInputError(code)


class FrozenInput:
    """Read-only process-local receipt; never deserialize one from an RPC request."""
    __slots__ = ("source_path", "staged_path", "revision", "compressed_bytes",
                 "object_types", "restore_visibility", "_source_stat", "_root", "_root_identity",
                 "_directory", "_directory_identity", "_file_identity", "_seal",
                 "_cleaned")

    def __init__(self, *, _seal, **values):
        if _seal is not _SEAL:
            _fail("WORKER_INPUT_UNSAFE")
        for name, value in values.items():
            object.__setattr__(self, name, value)
        object.__setattr__(self, "_seal", _seal)
        object.__setattr__(self, "_cleaned", False)

    def __setattr__(self, name, value):
        raise AttributeError("Frozen input receipts are immutable")

    def __repr__(self):
        return "FrozenInput(revision={!r}, compressed_bytes={!r})".format(
            self.revision, self.compressed_bytes)


def _platform():
    if (os.name != "posix" or not hasattr(os, "O_NOFOLLOW")
            or not hasattr(os, "O_DIRECTORY")
            or os.open not in os.supports_dir_fd or os.stat not in os.supports_dir_fd
            or os.unlink not in os.supports_dir_fd or os.mkdir not in os.supports_dir_fd
            or os.rmdir not in os.supports_dir_fd):
        _fail("WORKER_INPUT_UNSUPPORTED")


def _absolute(value):
    try:
        value = os.fspath(value)
    except TypeError:
        _fail("WORKER_INPUT_PATH_INVALID")
    if (not isinstance(value, str) or not value or len(value) > 4096
            or value != value.strip() or not os.path.isabs(value)
            or os.path.normpath(value) != value or value.startswith("//")
            or any(unicodedata.category(c) in ("Cc", "Cf", "Cs") for c in value)):
        _fail("WORKER_INPUT_PATH_INVALID")
    return value


def _stamp(info):
    # Reading changes atime; it must not invalidate a frozen revision.
    return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink, info.st_uid,
            info.st_gid, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _identity(info):
    return (info.st_dev, info.st_ino, info.st_uid)


def _private(info):
    return (stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid()
            and stat.S_IMODE(info.st_mode) == 0o700)


def _close(*descriptors):
    """Attempt each close once; a failed close must never be retried by fd number."""
    failed = False
    for descriptor in descriptors:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                failed = True
    return failed


def _open_directory(path, private=False):
    descriptor = None
    try:
        descriptor = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        for name in Path(path).parts[1:]:
            following = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                dir_fd=descriptor)
            previous, descriptor = descriptor, following
            if _close(previous):
                _fail("WORKER_INPUT_IO_FAILED")
        info = os.fstat(descriptor)
        if not stat.S_ISDIR(info.st_mode) or (private and not _private(info)):
            _fail("WORKER_INPUT_STAGING_INVALID" if private else "WORKER_INPUT_UNSAFE")
        result, descriptor = descriptor, None
        return result
    except PftWorkerInputError:
        raise
    except OSError:
        _fail("WORKER_INPUT_STAGING_INVALID" if private else "WORKER_INPUT_UNSAFE")
    finally:
        if _close(descriptor):
            _fail("WORKER_INPUT_IO_FAILED")


def _read_file(path, expected=None, private=False):
    parent = descriptor = None
    try:
        parent = _open_directory(os.path.dirname(path))
        descriptor = os.open(os.path.basename(path),
                             os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        before = os.fstat(descriptor)
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                or (private and (before.st_uid != os.getuid()
                                 or stat.S_IMODE(before.st_mode) != 0o600))):
            _fail("WORKER_INPUT_UNSAFE")
        stamp = _stamp(before)
        if expected is not None and stamp != expected:
            _fail("WORKER_INPUT_CHANGED")
        if before.st_size <= 0 or before.st_size > MAX_COMPRESSED_BYTES:
            _fail("WORKER_INPUT_LIMIT")
        chunks, length = [], 0
        while length <= before.st_size:
            chunk = os.read(descriptor, min(_CHUNK, before.st_size + 1 - length))
            if not chunk:
                break
            chunks.append(chunk)
            length += len(chunk)
        named = os.stat(os.path.basename(path), dir_fd=parent, follow_symlinks=False)
        if length != before.st_size or _stamp(os.fstat(descriptor)) != stamp or _stamp(named) != stamp:
            _fail("WORKER_INPUT_CHANGED")
        # Rewalk the absolute parent so a rename/replacement is not silently followed.
        check = _open_directory(os.path.dirname(path))
        try:
            if _identity(os.fstat(check)) != _identity(os.fstat(parent)):
                _fail("WORKER_INPUT_CHANGED")
        finally:
            if _close(check):
                _fail("WORKER_INPUT_IO_FAILED")
        return b"".join(chunks), stamp
    except PftWorkerInputError:
        raise
    except OSError as error:
        _fail("WORKER_INPUT_UNSAFE" if error.errno == errno.ELOOP else "WORKER_INPUT_IO_FAILED")
    finally:
        if _close(descriptor, parent):
            _fail("WORKER_INPUT_IO_FAILED")


def _archive_name(name):
    if (not name or len(name) > 4096 or name.startswith("/") or "\\" in name
            or ":" in name or any(unicodedata.category(c) in ("Cc", "Cf", "Cs") for c in name)):
        _fail("WORKER_INPUT_INVALID")
    pieces = name[:-1].split("/") if name.endswith("/") else name.split("/")
    if any(piece in ("", ".", "..") for piece in pieces):
        _fail("WORKER_INPUT_INVALID")
    return name


def _member(raw, info, directory_start):
    """Validate local header and actual raw deflate output, not just ZIP sizes."""
    offset = info.header_offset
    if offset < 0 or offset + 30 > directory_start:
        _fail("WORKER_INPUT_INVALID")
    header = struct.unpack_from("<4s5H3I2H", raw, offset)
    signature, _version, flags, method, _time, _date, crc, packed, unpacked, names, extra = header
    start = offset + 30 + names + extra
    end = start + info.compress_size
    if (signature != b"PK\x03\x04" or flags != info.flag_bits or method != info.compress_type
            or start > directory_start or end > directory_start):
        _fail("WORKER_INPUT_INVALID")
    try:
        local_name = raw[offset+30:offset+30+names].decode("utf-8" if flags & 0x800 else "cp437")
    except UnicodeError:
        _fail("WORKER_INPUT_INVALID")
    if local_name != info.filename:
        _fail("WORKER_INPUT_INVALID")
    if not flags & 8 and (crc != info.CRC or packed != info.compress_size or unpacked != info.file_size):
        _fail("WORKER_INPUT_INVALID")
    if flags & 8 and ((crc and crc != info.CRC) or (packed and packed != info.compress_size)
                      or (unpacked and unpacked != info.file_size)):
        _fail("WORKER_INPUT_INVALID")
    output, size, checksum = [], 0, 0
    keep = info.filename in ("Document.xml", "GuiDocument.xml")
    decoder = zlib.decompressobj(-15) if method == zipfile.ZIP_DEFLATED else None
    try:
        for pos in range(start, end, _CHUNK):
            chunk = raw[pos:min(pos+_CHUNK, end)]
            while chunk:
                if decoder:
                    value = decoder.decompress(chunk, min(_CHUNK, info.file_size-size+1))
                    chunk = decoder.unconsumed_tail
                    if decoder.unused_data:
                        _fail("WORKER_INPUT_INVALID")
                else:
                    value, chunk = chunk, b""
                size += len(value)
                if size > info.file_size:
                    _fail("WORKER_INPUT_INVALID")
                checksum = zlib.crc32(value, checksum)
                if keep:
                    output.append(value)
        if decoder and not decoder.eof:
            _fail("WORKER_INPUT_INVALID")
    except zlib.error:
        _fail("WORKER_INPUT_INVALID")
    if size != info.file_size or checksum & 0xFFFFFFFF != info.CRC:
        _fail("WORKER_INPUT_INVALID")
    return (offset, end), b"".join(output) if keep else None


class _Tree(ET.TreeBuilder):
    def __init__(self):
        super().__init__()
        self.depth, self.nodes = 0, 0

    def start(self, tag, attrs):
        self.depth += 1
        self.nodes += 1
        if self.depth > MAX_XML_DEPTH or self.nodes > MAX_XML_NODES:
            _fail("WORKER_INPUT_LIMIT")
        if len(attrs) > 64 or len(tag) > 128 or any(len(v) > 1024 * 1024 for v in attrs.values()):
            _fail("WORKER_INPUT_LIMIT")
        return super().start(tag, attrs)

    def end(self, tag):
        element = super().end(tag)
        self.depth -= 1
        return element

    def pi(self, target, text=None):
        _fail("WORKER_INPUT_INVALID")


def _xml(data):
    if not data or len(data) > MAX_XML_BYTES:
        _fail("WORKER_INPUT_LIMIT")
    try:
        text = data.decode("utf-8")
        if "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
            _fail("WORKER_INPUT_INVALID")
        declaration = re.match(r"\ufeff?<\?xml\b([^?]*)\?>", text)
        if declaration:
            encoding = re.search(r"\bencoding\s*=\s*(['\"])(.*?)\1", declaration[1])
            if encoding and encoding[2].lower() not in ("utf-8", "utf8"):
                _fail("WORKER_INPUT_INVALID")
        root = ET.fromstring(data, parser=ET.XMLParser(target=_Tree()))
    except (UnicodeError, ET.ParseError, ValueError, RecursionError):
        _fail("WORKER_INPUT_INVALID")
    if root.tag != "Document":
        _fail("WORKER_INPUT_INVALID")
    return root


def _count(element, actual):
    value = element.get("Count")
    if value is None or not re.fullmatch(r"0|[1-9][0-9]{0,6}", value) or int(value) != actual:
        _fail("WORKER_INPUT_INVALID")


def _one(element, tag):
    matches = element.findall(tag)
    if len(matches) != 1:
        _fail("WORKER_INPUT_INVALID")
    return matches[0]


def _name(value):
    if (not value or len(value) > 256 or value != value.strip() or "/" in value or "\\" in value
            or value in (".", "..") or "#" in value
            or any(unicodedata.category(c) in ("Cc", "Cf", "Cs") for c in value)):
        _fail("WORKER_INPUT_INVALID")
    return value


def _extensions(container, allowed):
    found = container.findall("Extensions")
    if len(found) > 1:
        _fail("WORKER_INPUT_INVALID")
    if not found:
        if container.get("Extensions") is not None:
            _fail("WORKER_INPUT_INVALID")
        return
    if container.get("Extensions") != "True":
        _fail("WORKER_INPUT_INVALID")
    extensions = list(found[0])
    _count(found[0], len(extensions))
    seen = set()
    for extension in extensions:
        kind = extension.get("type", "")
        if (extension.tag != "Extension" or kind not in allowed
                or extension.get("name") != kind.rsplit("::", 1)[-1]
                or kind in seen or len(extension) or (extension.text or "").strip()):
            _fail("WORKER_INPUT_UNSUPPORTED")
        seen.add(kind)


def _properties(root, members, object_names):
    for node in root.iter():
        if not isinstance(node.tag, str) or "}" in node.tag or ":" in node.tag:
            _fail("WORKER_INPUT_INVALID")
        if node.tag.lower() in ("xlink", "python", "proxy", "geoextension"):
            _fail("WORKER_INPUT_UNSUPPORTED")
        for key, value in node.attrib.items():
            if "}" in key or ":" in key:
                _fail("WORKER_INPUT_INVALID")
            if key.lower() in ("count", "transientcount"):
                if not re.fullmatch(r"0|[1-9][0-9]{0,19}", value):
                    _fail("WORKER_INPUT_INVALID")
                if int(value) > MAX_LIST_ITEMS:
                    _fail("WORKER_INPUT_LIMIT")
            if key.lower() in ("type", "viewtype") and ("python" in value.lower() or "proxy" in value.lower()):
                _fail("WORKER_INPUT_UNSUPPORTED")
            if key.lower() == "file" and value and (_archive_name(value) not in members or value.endswith("/")):
                _fail("WORKER_INPUT_UNSUPPORTED")
        if node.tag == "GeoExtensions" and (len(node) or node.get("count", "0") != "0"):
            _fail("WORKER_INPUT_UNSUPPORTED")
        if (node.tag in ("GeometryList", "ConstraintList", "VectorList", "FloatList",
                         "IntegerList", "BoolList", "StringList", "LinkList", "LinkSubList", "LinkSub")
                and "count" in node.attrib and not node.get("file")
                and int(node.get("count")) != len(node)):
            _fail("WORKER_INPUT_INVALID")
        if node.tag == "Properties":
            children = list(node)
            properties = [p for p in children if p.tag == "Property"]
            transients = [p for p in children if p.tag == "_Property"]
            if children != transients + properties:
                _fail("WORKER_INPUT_INVALID")
            _count(node, len(properties))
            transient_count = node.get("TransientCount", "0")
            if (not re.fullmatch(r"0|[1-9][0-9]{0,6}", transient_count)
                    or int(transient_count) != len(transients)):
                _fail("WORKER_INPUT_INVALID")
            names = [p.get("name") for p in children]
            if len(set(names)) != len(names) or any(not name for name in names):
                _fail("WORKER_INPUT_INVALID")
        if node.tag in ("Property", "_Property"):
            kind = node.get("type")
            if kind not in SUPPORTED_PROPERTY_TYPES or node.get("name", "").lower() == "proxy":
                _fail("WORKER_INPUT_UNSUPPORTED")
            if node.tag == "_Property":
                # Native transient entries persist only the status of existing
                # fields. They are not value carriers or dynamic properties.
                if (set(node.attrib) != {"name", "type", "status"} or len(node)
                        or (node.text or "").strip()
                        or not re.fullmatch(r"0|[1-9][0-9]{0,19}", node.get("status", ""))
                        or int(node.get("status")) > 0xFFFFFFFFFFFFFFFF):
                    _fail("WORKER_INPUT_INVALID")
                continue
            if kind == "App::PropertyExpressionEngine":
                if len(node) != 1 or node[0].tag != "ExpressionEngine":
                    _fail("WORKER_INPUT_INVALID")
                engine = node[0]
                if (len(engine) or (engine.text or "").strip()
                        or any(key.lower() != "count" or value != "0" for key, value in engine.attrib.items())):
                    _fail("WORKER_INPUT_UNSUPPORTED")
            if kind in _LINKS:
                for link in node.iter():
                    if link.tag in ("Link", "LinkSub"):
                        for key in ("value", "name", "obj"):
                            value = link.get(key)
                            if value and value not in object_names:
                                _fail("WORKER_INPUT_UNSUPPORTED")
                        if link.get("document") or link.get("doc"):
                            _fail("WORKER_INPUT_UNSUPPORTED")
        if node.tag == "Geometry" and node.get("type") and node.get("type") not in _GEOMETRIES:
            _fail("WORKER_INPUT_UNSUPPORTED")


def _camera(settings):
    if not settings:
        return
    # A saved Camera is Inventor text. Never pass File/USE/other nodes to Coin.
    text = re.sub(r"^\s*#Inventor V2\.1 ascii\s*", "", settings)
    match = re.fullmatch(r"\s*(OrthographicCamera|PerspectiveCamera)\s*\{([^{}]*)\}\s*", text)
    if not match:
        _fail("WORKER_INPUT_UNSUPPORTED")
    tokens, index, seen = match[2].split(), 0, set()
    fields = {"viewportMapping": 1, "position": 3, "orientation": 4, "nearDistance": 1,
              "farDistance": 1, "aspectRatio": 1, "focalDistance": 1,
              "height" if match[1] == "OrthographicCamera" else "heightAngle": 1}
    while index < len(tokens):
        field = tokens[index]
        count = fields.get(field)
        if count is None or field in seen or index+count >= len(tokens):
            _fail("WORKER_INPUT_UNSUPPORTED")
        seen.add(field)
        values = tokens[index+1:index+count+1]
        if field == "viewportMapping":
            if values[0] not in ("CROP_VIEWPORT_FILL_FRAME", "CROP_VIEWPORT_LINE_FRAME",
                                 "CROP_VIEWPORT_NO_FRAME", "ADJUST_CAMERA", "LEAVE_ALONE"):
                _fail("WORKER_INPUT_UNSUPPORTED")
        else:
            try:
                if any(not re.fullmatch(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?", value)
                       or not math.isfinite(float(value)) for value in values):
                    _fail("WORKER_INPUT_UNSUPPORTED")
            except ValueError:
                _fail("WORKER_INPUT_UNSUPPORTED")
        index += count+1


def _documents(xml, members):
    root = _xml(xml["Document.xml"])
    if root.get("SchemaVersion") not in ("4",):
        _fail("WORKER_INPUT_UNSUPPORTED")
    if any(child.tag not in ("StringHasher", "StringHasher2", "Properties", "Objects", "ObjectData") for child in root):
        _fail("WORKER_INPUT_UNSUPPORTED")
    if len(root.findall("Properties")) > 1 or len(root.findall("StringHasher")) > 1 or len(root.findall("StringHasher2")) > 1:
        _fail("WORKER_INPUT_INVALID")
    objects, data = _one(root, "Objects"), _one(root, "ObjectData")
    declarations = objects.findall("Object")
    if not declarations or len(declarations) > MAX_OBJECTS:
        _fail("WORKER_INPUT_LIMIT")
    _count(objects, len(declarations))
    kinds = {}
    for obj in declarations:
        name = _name(obj.get("name"))
        if name in kinds:
            _fail("WORKER_INPUT_INVALID")
        if obj.get("type") not in SUPPORTED_OBJECT_TYPES or "ViewType" in obj.attrib:
            _fail("WORKER_INPUT_UNSUPPORTED")
        if len(obj):
            _fail("WORKER_INPUT_INVALID")
        kinds[name] = obj.get("type")
    dependency_names = set()
    for child in objects:
        if child.tag == "Object":
            continue
        if child.tag != "ObjectDeps" or child.get("Name") not in kinds:
            _fail("WORKER_INPUT_INVALID")
        if child.get("Name") in dependency_names:
            _fail("WORKER_INPUT_INVALID")
        dependency_names.add(child.get("Name"))
        _count(child, len(child))
        if any(dep.tag != "Dep" or dep.get("Name") not in kinds for dep in child):
            _fail("WORKER_INPUT_UNSUPPORTED")
    if objects.get("Dependencies") not in (None, "1") or (objects.get("Dependencies") == "1" and dependency_names != set(kinds)):
        _fail("WORKER_INPUT_INVALID")
    if dependency_names and objects.get("Dependencies") != "1":
        _fail("WORKER_INPUT_INVALID")
    records = list(data)
    _count(data, len(records))
    names = [record.get("name") for record in records]
    if len(set(names)) != len(names) or set(names) != set(kinds):
        _fail("WORKER_INPUT_INVALID")
    for record in records:
        if record.tag != "Object" or any(child.tag not in ("Properties", "Extensions") for child in record):
            _fail("WORKER_INPUT_INVALID")
        _one(record, "Properties")
        _extensions(record, _NATIVE_EXTENSIONS.get(kinds[record.get("name")], frozenset()))
    _properties(root, members, set(kinds))
    # GUI restore can initially hide providers when no GuiDocument is present.
    # Preserve only explicit native bools from the already validated XML; do
    # not infer visibility from names, geometry, defaults or the restored view.
    visibility = None
    if "GuiDocument.xml" not in xml:
        values = []
        for record in records:
            properties = _one(record, "Properties")
            selected = [item for item in properties if item.tag == "Property" and item.get("name") == "Visibility"]
            if len(selected) != 1:
                _fail("WORKER_INPUT_UNSUPPORTED")
            property_ = selected[0]
            if (property_.get("type") != "App::PropertyBool" or len(property_) != 1
                    or (property_.text or "").strip()):
                _fail("WORKER_INPUT_UNSUPPORTED")
            boolean = property_[0]
            if (boolean.tag != "Bool" or set(boolean.attrib) != {"value"}
                    or boolean.get("value") not in ("true", "false") or len(boolean)
                    or (boolean.text or "").strip() or (boolean.tail or "").strip()):
                _fail("WORKER_INPUT_UNSUPPORTED")
            name = record.get("name")
            values.append((name, kinds[name], boolean.get("value") == "true"))
        visibility = tuple(values)
    if "GuiDocument.xml" in xml:
        gui = _xml(xml["GuiDocument.xml"])
        if gui.get("SchemaVersion") != "1":
            _fail("WORKER_INPUT_UNSUPPORTED")
        if any(child.tag not in ("Expand", "ViewProviderData", "Camera") for child in gui):
            _fail("WORKER_INPUT_UNSUPPORTED")
        camera = _one(gui, "Camera")
        if len(camera) or set(camera.attrib) != {"settings"}:
            _fail("WORKER_INPUT_UNSUPPORTED")
        _camera(camera.get("settings", ""))
        providers = _one(gui, "ViewProviderData")
        _count(providers, len(providers))
        seen = set()
        for provider in providers:
            name = provider.get("name")
            if (provider.tag != "ViewProvider" or name not in kinds or name in seen
                    or any(child.tag not in ("Properties", "Extensions") for child in provider)):
                _fail("WORKER_INPUT_INVALID")
            seen.add(name)
            _one(provider, "Properties")
            _extensions(provider, _GUI_EXTENSIONS)
        _properties(gui, members, set(kinds))
    return tuple(sorted(set(kinds.values()))), visibility


def _preflight(raw):
    if len(raw) > MAX_COMPRESSED_BYTES:
        _fail("WORKER_INPUT_LIMIT")
    try:
        end = raw.rfind(b"PK\x05\x06", max(0, len(raw)-65557))
        if end < 0 or end+22 > len(raw):
            _fail("WORKER_INPUT_INVALID")
        signature, disk, central_disk, disk_entries, entries, central_size, central_offset, comment = struct.unpack_from("<4s4H2IH", raw, end)
        if (disk or central_disk or disk_entries != entries or entries == 65535
                or end+22+comment != len(raw) or central_offset+central_size != end
                or not raw.startswith(b"PK\x03\x04")):
            _fail("WORKER_INPUT_INVALID")
        if not 0 < entries <= MAX_ZIP_ENTRIES:
            _fail("WORKER_INPUT_LIMIT")
        # Bound central-directory enumeration before ZipFile allocates ZipInfo objects.
        position, actual_entries = central_offset, 0
        while position < end:
            actual_entries += 1
            if actual_entries > MAX_ZIP_ENTRIES:
                _fail("WORKER_INPUT_LIMIT")
            if position+46 > end or raw[position:position+4] != b"PK\x01\x02":
                _fail("WORKER_INPUT_INVALID")
            names, extra, entry_comment = struct.unpack_from("<3H", raw, position+28)
            position += 46+names+extra+entry_comment
        if position != end or actual_entries != entries:
            _fail("WORKER_INPUT_INVALID")
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            infos = archive.infolist()
            if not 0 < len(infos) <= MAX_ZIP_ENTRIES:
                _fail("WORKER_INPUT_LIMIT")
            if len(infos) != entries or archive.start_dir != central_offset:
                _fail("WORKER_INPUT_INVALID")
            names, members, total, spans, xml = set(), set(), 0, [], {}
            for info in infos:
                name = _archive_name(info.filename)
                key = unicodedata.normalize("NFC", name).casefold()
                if key in names or info.orig_filename != name:
                    _fail("WORKER_INPUT_INVALID")
                names.add(key)
                members.add(name)
                mode = info.external_attr >> 16
                if (info.flag_bits & ~0x80E or info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)
                        or stat.S_IFMT(mode) not in (0, stat.S_IFDIR if info.is_dir() else stat.S_IFREG)):
                    _fail("WORKER_INPUT_UNSUPPORTED")
                if info.is_dir() and (info.file_size or info.compress_size):
                    _fail("WORKER_INPUT_INVALID")
                total += info.file_size
                if total > MAX_EXPANDED_BYTES or (name in ("Document.xml", "GuiDocument.xml") and info.file_size > MAX_XML_BYTES):
                    _fail("WORKER_INPUT_LIMIT")
                span, value = _member(raw, info, central_offset)
                spans.append(span)
                if value is not None:
                    xml[name] = value
            spans.sort()
            if spans[0][0] != 0 or any(after[0] < before[1] for before, after in zip(spans, spans[1:])):
                _fail("WORKER_INPUT_INVALID")
            if "Document.xml" not in xml:
                _fail("WORKER_INPUT_INVALID")
            return _documents(xml, members)
    except PftWorkerInputError:
        raise
    except (zipfile.BadZipFile, UnicodeError, ValueError, struct.error, OverflowError, RuntimeError):
        _fail("WORKER_INPUT_INVALID")


def _receipt(record):
    if type(record) is not FrozenInput or record._seal is not _SEAL or record._cleaned:
        _fail("WORKER_INPUT_UNSAFE")


def _discard_empty(root_path, root_identity, name, directory_identity):
    root = directory = None
    try:
        root = _open_directory(root_path, private=True)
        if _identity(os.fstat(root)) != root_identity:
            _fail("WORKER_INPUT_CLEANUP_UNVERIFIED")
        directory = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root)
        if (_identity(os.fstat(directory)) != directory_identity or os.listdir(directory)
                or _identity(os.stat(name, dir_fd=root, follow_symlinks=False)) != directory_identity):
            _fail("WORKER_INPUT_CLEANUP_UNVERIFIED")
        os.rmdir(name, dir_fd=root)
        os.fsync(root)
    except (OSError, PftWorkerInputError):
        _fail("WORKER_INPUT_CLEANUP_UNVERIFIED")
    finally:
        if _close(directory, root):
            _fail("WORKER_INPUT_CLEANUP_UNVERIFIED")


def freeze_fcstd(source, staging_root):
    """Preflight and freeze exact bytes. Only a new owned child is ever written."""
    _platform()
    source, staging_root = _absolute(source), _absolute(staging_root)
    if Path(source).suffix.lower() != ".fcstd":
        _fail("WORKER_INPUT_PATH_INVALID")
    root = _open_directory(staging_root, private=True)
    record = directory = directory_identity = created = descriptor = None
    failure = None
    root_identity = _identity(os.fstat(root))
    try:
        raw, source_stat = _read_file(source)
        (kinds, visibility), revision = _preflight(raw), hashlib.sha256(raw).hexdigest()
        directory = "pft-input-" + uuid.uuid4().hex
        os.mkdir(directory, mode=0o700, dir_fd=root)
        directory_identity = _identity(os.stat(directory, dir_fd=root, follow_symlinks=False))
        created = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root)
        directory_stat = os.fstat(created)
        if not _private(directory_stat) or _identity(directory_stat) != directory_identity:
            _fail("WORKER_INPUT_STAGING_INVALID")
        descriptor = os.open(_STAGED_NAME, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             0o600, dir_fd=created)
        file_stat = os.fstat(descriptor)
        record = FrozenInput(_seal=_SEAL, source_path=source,
                             staged_path=os.path.join(staging_root, directory, _STAGED_NAME),
                             revision=revision, compressed_bytes=len(raw), object_types=kinds,
                             restore_visibility=visibility,
                             _source_stat=source_stat, _root=staging_root,
                             _root_identity=root_identity, _directory=directory,
                             _directory_identity=directory_identity,
                             _file_identity=_identity(file_stat))
        offset = 0
        while offset < len(raw):
            count = os.write(descriptor, raw[offset:offset+_CHUNK])
            if count <= 0:
                _fail("WORKER_INPUT_IO_FAILED")
            offset += count
        os.fsync(descriptor)
        closing, descriptor = descriptor, None
        if _close(closing):
            _fail("WORKER_INPUT_IO_FAILED")
        os.fsync(created)
        os.fsync(root)
        verify_frozen_input(record)
    except BaseException as error:
        failure = error
    close_failed = _close(descriptor, created, root)
    if failure is not None or close_failed:
        if record is not None:
            cleanup_frozen_input(record)
        elif directory_identity is not None:
            _discard_empty(staging_root, root_identity, directory, directory_identity)
        if isinstance(failure, PftWorkerInputError):
            raise failure
        if failure is not None and not isinstance(failure, Exception):
            raise failure
        _fail("WORKER_INPUT_IO_FAILED")
    return record


def verify_frozen_input(record):
    """Recheck both immutable revision and source; call before open and completion."""
    _platform()
    _receipt(record)
    root = directory = None
    try:
        root = _open_directory(record._root, private=True)
        if _identity(os.fstat(root)) != record._root_identity:
            _fail("WORKER_INPUT_CHANGED")
        directory = os.open(record._directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root)
        if not _private(os.fstat(directory)) or _identity(os.fstat(directory)) != record._directory_identity:
            _fail("WORKER_INPUT_CHANGED")
        if os.listdir(directory) != [_STAGED_NAME]:
            _fail("WORKER_INPUT_CHANGED")
        frozen, _ = _read_file(record.staged_path, private=True)
        info = os.stat(_STAGED_NAME, dir_fd=directory, follow_symlinks=False)
        if (_identity(info) != record._file_identity
                or len(frozen) != record.compressed_bytes
                or hashlib.sha256(frozen).hexdigest() != record.revision):
            _fail("WORKER_INPUT_CHANGED")
        source, _ = _read_file(record.source_path, expected=record._source_stat)
        if hashlib.sha256(source).hexdigest() != record.revision:
            _fail("WORKER_INPUT_CHANGED")
    except PftWorkerInputError as error:
        if error.code in ("WORKER_INPUT_UNSAFE", "WORKER_INPUT_STAGING_INVALID", "WORKER_INPUT_IO_FAILED"):
            _fail("WORKER_INPUT_CHANGED")
        raise
    except OSError:
        _fail("WORKER_INPUT_CHANGED")
    finally:
        if _close(directory, root):
            _fail("WORKER_INPUT_CHANGED")


def cleanup_frozen_input(record):
    """Delete only this receipt's inodes; unknown entries are retained for review."""
    _platform()
    if type(record) is not FrozenInput or record._seal is not _SEAL:
        _fail("WORKER_INPUT_CLEANUP_UNVERIFIED")
    if record._cleaned:
        return
    root = directory = None
    try:
        root = _open_directory(record._root, private=True)
        if _identity(os.fstat(root)) != record._root_identity:
            _fail("WORKER_INPUT_CLEANUP_UNVERIFIED")
        directory = os.open(record._directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root)
        if not _private(os.fstat(directory)) or _identity(os.fstat(directory)) != record._directory_identity:
            _fail("WORKER_INPUT_CLEANUP_UNVERIFIED")
        names = os.listdir(directory)
        if names not in ([], [_STAGED_NAME]):
            _fail("WORKER_INPUT_CLEANUP_UNVERIFIED")
        if names:
            info = os.stat(_STAGED_NAME, dir_fd=directory, follow_symlinks=False)
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                    or _identity(info) != record._file_identity):
                _fail("WORKER_INPUT_CLEANUP_UNVERIFIED")
            os.unlink(_STAGED_NAME, dir_fd=directory)
        if _identity(os.stat(record._directory, dir_fd=root, follow_symlinks=False)) != record._directory_identity:
            _fail("WORKER_INPUT_CLEANUP_UNVERIFIED")
        os.rmdir(record._directory, dir_fd=root)
        os.fsync(root)
        object.__setattr__(record, "_cleaned", True)
    except PftWorkerInputError as error:
        if error.code == "WORKER_INPUT_CLEANUP_UNVERIFIED":
            raise
        _fail("WORKER_INPUT_CLEANUP_UNVERIFIED")
    except OSError:
        _fail("WORKER_INPUT_CLEANUP_UNVERIFIED")
    finally:
        if _close(directory, root):
            _fail("WORKER_INPUT_CLEANUP_UNVERIFIED")
