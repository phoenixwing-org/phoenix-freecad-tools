# SPDX-License-Identifier: Apache-2.0
"""Finite native TechDraw PDF capture; no public path or confirmation input.

Snapshots are private, process-local capabilities. Preparing them never opens a
page or renders. Opening and PDF capture require a controller-owned guard, and
the caller schedules bind_open_scene on a later GUI turn without pumping events.
Native export is not read-only: known drawing cache flags may change; geometry,
references, resources, styles and scene semantics must remain frozen. This is
not a projection-ready or manufacturing-approval check.
"""
import hashlib
import importlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
import xml.etree.ElementTree as ET

MAX_PDF_BYTES = 8 * 1024 * 1024
MAX_RESOURCE_BYTES = 512 * 1024
MAX_SHAPE_BYTES = 4 * 1024 * 1024
MAX_SCENE_ITEMS = 4096
MAX_PATH_ELEMENTS = 65536
MAX_SCENE_BYTES = 4 * 1024 * 1024
MAX_FONT_BYTES = 64 * 1024 * 1024
MAX_FONT_TABLE_BYTES = 32 * 1024 * 1024
MAX_PROJECTED_EDGES = 2048
MAX_PROJECTION_BYTES = 8 * 1024 * 1024
PAPER_PAINT_PIXELS = 1000
MAX_PAPER_PAINT_BYTES = 4 * PAPER_PAINT_PIXELS * PAPER_PAINT_PIXELS
_MARKER = "pft-native-drawing-export-v1"
_MESSAGES = {
    "DRAWING_EXPORT_UNSUPPORTED": "This page or native rendering input is outside the supported PDF profile.",
    "DRAWING_EXPORT_STALE": "The document, page, rendering inputs or native scene changed; prepare again.",
    "DRAWING_EXPORT_PAGE_OPEN_REQUIRED": "Open the bound page through its native preview action first.",
    "DRAWING_EXPORT_NATIVE_CONFIRMATION_REQUIRED": "PDF capture requires its current native controller guard.",
    "DRAWING_EXPORT_CAPTURE_FAILED": "Native PDF capture did not produce verified frozen bytes.",
    "DRAWING_EXPORT_CAPTURE_UNVERIFIED": "Native capture entered, but document or rendering preservation is unverified.",
    "DRAWING_EXPORT_STAGE_UNVERIFIED": "The private PDF stage could not be verified.",
}
ERROR_CODES = frozenset(_MESSAGES)


class PftDrawingExportProfileError(RuntimeError):
    def __init__(self, code, *, capture_state="not_captured", cleanup_state="complete"):
        self.code = code if code in _MESSAGES else "DRAWING_EXPORT_UNSUPPORTED"
        self.message = _MESSAGES[self.code]
        self.capture_state, self.cleanup_state = capture_state, cleanup_state
        super().__init__(self.message)


def _fail(code="DRAWING_EXPORT_UNSUPPORTED"):
    raise PftDrawingExportProfileError(code)


def _sibling(name):
    key, path = "_pft_export_" + name, Path(__file__).resolve().with_name(name + ".py")
    if key in sys.modules:
        module = sys.modules[key]
        if Path(module.__file__).resolve() != path:
            _fail()
        return module
    spec = importlib.util.spec_from_file_location(key, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[key] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(key, None)
        raise
    return module


def _seq(value, maximum=256):
    if type(value) not in (list, tuple) or len(value) > maximum:
        _fail()
    return tuple(value)


def _text(value, maximum=8192):
    if type(value) is not str or len(value.encode("utf8")) > maximum:
        _fail()
    return value


def _num(value):
    try:
        valid = type(value) in (float, int) and math.isfinite(value)
    except OverflowError:
        valid = False
    if not valid:
        _fail()
    return float(value)


def _integer(value):
    if type(value) is not int or not -(2 ** 63) <= value < 2 ** 64:
        _fail()
    return value


def _bool(value):
    if type(value) is not bool:
        _fail()
    return value


def _vector(value):
    return tuple(_num(getattr(value, name)) for name in ("x", "y", "z"))


def _quantity(value):
    if type(value).__module__ != "Base" or type(value).__name__ != "Quantity":
        _fail()
    signature = _seq(value.Unit.Signature, 8)
    if len(signature) != 8 or any(type(n) is not int for n in signature):
        _fail()
    return _num(value.Value), signature


def _shape(value, allow_null=False):
    if type(value).__module__ != "Part":
        _fail()
    null = value.isNull()
    if type(null) is not bool:
        _fail()
    if null and allow_null:
        return "null", None
    if null or value.isValid() is not True:
        _fail()
    # A full native BRep, not bbox/hashCode alone. Keep only its bounded digest.
    encoded = _text(value.exportBrepToString(), MAX_SHAPE_BYTES).encode("utf8")
    if not encoded:
        _fail()
    return len(encoded), hashlib.sha256(encoded).hexdigest()


def _identity(document, obj):
    drawing = _sibling("pft_drawing")
    if (obj is None or obj.Document is not document or not drawing._name(obj.Name)
            or document.getObject(obj.Name) is not obj):
        _fail()
    return id(obj), obj.Name, _text(obj.TypeId, 128)


def _properties(obj, document):
    names = _seq(obj.PropertiesList, 256)
    if len(set(names)) != len(names) or any(type(name) is not str for name in names) or "Proxy" in names:
        _fail()
    try:
        getattr(obj, "Proxy")
    except AttributeError:
        pass
    else:
        _fail()
    result = []
    for name in names:
        kind = _text(obj.getTypeIdOfProperty(name), 128)
        value = getattr(obj, name)
        if kind == "App::PropertyBool":
            frozen = _bool(value)
        elif kind in ("App::PropertyString", "App::PropertyEnumeration", "App::PropertyFont",
                      "App::PropertyFile", "App::PropertyFileIncluded"):
            frozen = _text(value, 32768 if name.startswith("FormatSpec") else 8192)
            if (name.startswith("FormatSpec") or name == "Caption") and re.search(
                    r"<(?:img|object)|(?:src\s*=|url\s*\()", frozen, re.I):
                _fail()
        elif kind in ("App::PropertyInteger", "App::PropertyIntegerConstraint", "App::PropertyPercent"):
            frozen = _integer(value)
        elif kind in ("App::PropertyFloat", "App::PropertyFloatConstraint"):
            frozen = _num(value)
        elif kind in ("App::PropertyLength", "App::PropertyDistance", "App::PropertyAngle",
                      "App::PropertyQuantity", "App::PropertyQuantityConstraint"):
            frozen = _quantity(value)
        elif kind == "App::PropertyVector":
            frozen = _vector(value)
        elif kind == "App::PropertyVectorList":
            frozen = tuple(_vector(item) for item in _seq(value, 256))
        elif kind == "App::PropertyColor":
            frozen = tuple(_num(item) for item in _seq(value, 4))
            if len(frozen) != 4 or any(not 0 <= item <= 1 for item in frozen):
                _fail()
        elif kind == "App::PropertyLink":
            frozen = None if value is None else _identity(document, value)
        elif kind == "App::PropertyLinkList":
            frozen = tuple(_identity(document, item) for item in _seq(value, 16))
        elif kind == "App::PropertyLinkSubList":
            frozen = []
            for entry in _seq(value, 8):
                if type(entry) is not tuple or len(entry) != 2:
                    _fail()
                owner, refs = entry
                frozen.append((_identity(document, owner), tuple(_text(ref, 64) for ref in _seq(refs, 8))))
            frozen = tuple(frozen)
        elif kind in ("App::PropertyExpressionEngine", "App::PropertyXLinkList",
                      "TechDraw::PropertyCosmeticVertexList", "TechDraw::PropertyCosmeticEdgeList",
                      "TechDraw::PropertyCenterLineList", "TechDraw::PropertyGeomFormatList"):
            if type(value) is not list or value:
                _fail()
            frozen = ()
        elif kind == "App::PropertyMap":
            if type(value) is not dict or len(value) > 100:
                _fail()
            frozen = tuple(sorted((_text(k, 256), _text(v, 4096)) for k, v in value.items()))
            if len(json.dumps(frozen, ensure_ascii=False).encode("utf8")) > 128 * 1024:
                _fail()
        elif kind == "Part::PropertyTopoShapeList":
            frozen = tuple(_shape(item, allow_null=True) for item in _seq(value, 8))
        else:
            _fail()
        result.append((name, kind, frozen))
    return tuple(result)


def _resource(path):
    path = _text(path, 4096)
    if not path or not os.path.isabs(path):
        _fail()
    before = os.lstat(path)
    if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= MAX_RESOURCE_BYTES:
        _fail()
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    try:
        opened = os.fstat(fd)
        if _stamp(before) != _stamp(opened):
            _fail()
        with os.fdopen(fd, "rb") as stream:
            fd = None
            data = stream.read(MAX_RESOURCE_BYTES + 1)
            after = os.fstat(stream.fileno())
        if len(data) != before.st_size or _stamp(after) != _stamp(before) or _stamp(os.lstat(path)) != _stamp(before):
            _fail()
    finally:
        if fd is not None:
            os.close(fd)
    return data


def _svg(data):
    if (type(data) is not bytes or not 0 < len(data) <= MAX_RESOURCE_BYTES
            or re.search(br"<!\s*(?:DOCTYPE|ENTITY)|<\?xml-stylesheet", data, re.I)):
        _fail()
    root = ET.fromstring(data)
    if root.tag != "{http://www.w3.org/2000/svg}svg":
        _fail()
    count = 0
    def visit(element, depth):
        nonlocal count
        count += 1
        if count > 8192 or depth > 32:
            _fail()
        local = element.tag.rsplit("}", 1)[-1]
        if local.lower() in ("script", "foreignobject", "image", "animate", "animatetransform", "set"):
            _fail()
        for key, value in element.attrib.items():
            local_key = key.rsplit("}", 1)[-1].lower()
            if local_key.startswith("on") or local_key == "href" and not value.startswith("#"):
                _fail()
            if re.search(r"(?:@import|@font-face|expression\s*\(|\\)", value, re.I):
                _fail()
            if any(not target.strip(" \t\r\n\"'").startswith("#")
                   for target in re.findall(r"url\s*\(([^)]*)\)", value, re.I)):
                _fail()
        if local == "style" and element.text:
            text = element.text
            if re.search(r"(?:@import|@font-face|expression\s*\(|\\)", text, re.I):
                _fail()
            if any(not target.strip(" \t\r\n\"'").startswith("#")
                   for target in re.findall(r"url\s*\(([^)]*)\)", text, re.I)):
                _fail()
        for child in element:
            visit(child, depth + 1)
    visit(root, 0)
    return hashlib.sha256(data).hexdigest()


def _stamp(info):
    return info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns, info.st_ctime_ns


_PREFERENCES = {
    "Colors": {"PageColor": "Unsigned", "Background": "Unsigned", "NormalColor": "Unsigned", "HiddenColor": "Unsigned",
               "LightTextColor": "Unsigned", "LightOnDark": "Bool", "Monochrome": "Bool"},
    "Labels": {"LabelFont": "String", "LabelSize": "Float"},
    "Dimensions": {"UseGlobalDecimals": "Bool", "AltDecimals": "Int", "ShowUnits": "Bool",
                   "DiameterSymbol": "String", "TolSizeAdjust": "Float", "FontSize": "Float",
                   "ArrowSize": "Float", "Color": "Unsigned"},
    "Decorations": {"PrintCenterMarks": "Bool", "ShowCenterMarks": "Bool", "VertexColor": "Unsigned",
                    "LineStyleHidden": "Int", "LineStyleCenter": "Int"},
    "General": {"VertexScale": "Float", "EdgeCapStyle": "Int", "EdgeFuzz": "Float", "MarkFuzz": "Float", "AlwaysShowLabel": "Bool",
                "GlobalUpdateDrawings": "Bool", "AllowPageOverride": "Bool"},
    "View": {"ViewFrameMode": "Int"}, "Rez": {"Resolution": "Float"},
    "Standards": {"LineStandard": "Int"},
    "Files": {"LineDefLocation": "String", "LineElementLocation": "String"},
}


def _parameter(group, key, kind):
    defaults = {"Bool": (False, True), "String": ("\x01absent-a", "\x01absent-b"),
                "Unsigned": (0, 4294967295), "Int": (-2147483647, 2147483647), "Float": (-1.125, 1.125)}
    a, b = (getattr(group, "Get" + kind)(key, value) for value in defaults[kind])
    if kind == "Bool":
        _bool(a); _bool(b)
    elif kind == "String":
        _text(a, 4096); _text(b, 4096)
    else:
        _num(a); _num(b)
    return (True, a) if a == b else (False, None)


def _preferences(app):
    rows = []
    for suffix, keys in _PREFERENCES.items():
        group = app.ParamGet("User parameter:BaseApp/Preferences/Mod/TechDraw/" + suffix)
        for key, kind in keys.items():
            value = _parameter(group, key, kind)
            rows.append((suffix, key, kind, value))
    rows.append(("PDF", "PDFVersion", "Int", _parameter(app.ParamGet("User parameter:BaseApp/Preferences/Mod"), "PDFVersion", "Int")))
    unit_group = app.ParamGet("User parameter:BaseApp/Preferences/Units")
    for key in ("UserSchema", "Decimals"):
        rows.append(("Units", key, "Int", _parameter(unit_group, key, "Int")))
    values = {(suffix, key): value for suffix, key, kind, value in rows}
    default = Path(app.getResourceDir()) / "Mod" / "TechDraw" / "LineGroup"
    definitions = values[("Files", "LineDefLocation")]
    elements = values[("Files", "LineElementLocation")]
    definition_dir = Path(definitions[1] if definitions[0] else default)
    element_dir = Path(elements[1] if elements[0] else default)
    info = definition_dir.lstat()
    if not stat.S_ISDIR(info.st_mode):
        _fail()
    entries = list(definition_dir.iterdir())
    if len(entries) > 64:
        _fail()
    choices = sorted(path.name[:-len(".LineDef.csv")] for path in entries
                     if path.name.endswith(".LineDef.csv") and path.is_file())
    index_value = values[("Standards", "LineStandard")]
    index = max(0, index_value[1]) if index_value[0] else 1
    if type(index) is not int or not 0 <= index < len(choices):
        _fail()
    choice = choices[index]
    files = (("line", hashlib.sha256(_resource(str(definition_dir / (choice + ".LineDef.csv")))).hexdigest()),
             ("element", hashlib.sha256(_resource(str(element_dir / (choice + ".ElementDef.csv")))).hexdigest()))
    return tuple(rows), files


def _qt():
    from PySide6 import QtCore, QtGui, QtWidgets
    return QtCore, QtGui, QtWidgets


def _enum(value):
    return _integer(value.value if hasattr(value, "value") else value)


def _rect(rect):
    return tuple(_num(getattr(rect, key)()) for key in ("x", "y", "width", "height"))


def _transform(value):
    return tuple(_num(getattr(value, "m" + str(i) + str(j))()) for i in range(1, 4) for j in range(1, 4))


def _color(value):
    return tuple(_num(v) for v in value.getRgbF())


def _brush(value):
    if value.gradient() is not None or not value.textureImage().isNull():
        _fail()
    return _enum(value.style()), _color(value.color()), _transform(value.transform())


def _pen(value):
    return (_brush(value.brush()), _num(value.widthF()), _enum(value.style()), _enum(value.capStyle()),
            _enum(value.joinStyle()), _num(value.miterLimit()), _bool(value.isCosmetic()),
            tuple(_num(n) for n in _seq(value.dashPattern(), 32)), _num(value.dashOffset()))


_FONT_TABLES = ("head", "hhea", "maxp", "OS/2", "hmtx", "cmap", "loca", "glyf", "kern", "name", "post",
                "cvt ", "fpgm", "prep", "gasp", "GDEF", "GPOS", "GSUB", "CFF ", "CFF2", "fvar", "gvar",
                "HVAR", "VVAR", "MVAR", "avar", "STAT", "vhea", "vmtx", "VORG", "BASE", "JSTF", "COLR",
                "CPAL", "CBDT", "CBLC", "sbix", "SVG ")


def _font_tables(raw, cache):
    if raw.isValid() is not True:
        _fail()
    key = (_text(raw.familyName(), 4096), _text(raw.styleName(), 4096), _num(raw.pixelSize()),
           _num(raw.unitsPerEm()), _enum(raw.style()), _integer(raw.weight()))
    # Native QRawFont equality binds the actual font engine. Family/style names
    # alone do not prove equal font data. Keep these borrowed values only for
    # this one bounded snapshot; do not retain GUI items or persistent pins.
    for previous, result in cache[0]:
        if raw == previous:
            return key, result
    if len(cache[0]) >= 128:
        _fail()
    tables = []
    for tag in _FONT_TABLES:
        data = bytes(raw.fontTable(tag))
        if len(data) > MAX_FONT_TABLE_BYTES:
            _fail()
        if data:
            fingerprint = (tag, len(data), hashlib.sha256(data).hexdigest())
            if fingerprint not in cache[1]:
                cache[2] += len(data)
                if cache[2] > MAX_FONT_BYTES:
                    _fail()
                cache[1].add(fingerprint)
            tables.append(fingerprint)
    if not tables:
        _fail()
    result = tuple(tables)
    cache[0].append((raw, result))
    return key, result


def _text_fonts(document, QtGui, cache):
    """Layout a detached copy; schedule disposal of only our own native clone.

    Qt 6.8.3 returns a borrowed wrapper for clone(), so Python GC does not
    dispose it. deleteLater is deferred to the normal GUI loop; this function
    never pumps events or retains a borrowed QTextLayout after its document.
    """
    if type(document) is not QtGui.QTextDocument:
        _fail()
    detached = document.clone()
    if type(detached) is not QtGui.QTextDocument:
        # A stale borrowed wrapper at a reused C++ address is not our document
        # capability. Never cast or delete an unknown wrapper to recover it.
        raise PftDrawingExportProfileError("DRAWING_EXPORT_UNSUPPORTED", cleanup_state="unknown")
    try:
        detached.documentLayout().documentSize()
        block, count, fonts = detached.begin(), 0, set()
        while block.isValid():
            count += 1
            if count > 256:
                _fail()
            # QTextBlock.length includes one paragraph separator and counts
            # native QString UTF-16 units. Python len would miscount non-BMP.
            length = _integer(block.length()) - 1
            if not 0 <= length <= 131072:
                _fail()
            if length:
                for run in _seq(block.layout().glyphRuns(0, length), 256):
                    fonts.add(_font_tables(run.rawFont(), cache))
            block = block.next()
        return tuple(sorted(fonts))
    finally:
        detached.deleteLater()


def _svg_fonts(data, texts, QtGui, cache):
    root = ET.fromstring(data)
    font_specs = set()
    def visit(element, inherited):
        local = element.tag.rsplit("}", 1)[-1]
        # The initial closed resource profile supports native inline SVG fonts,
        # not stylesheets with selector-dependent or external font resolution.
        if local == "style" and (element.text or "").strip() or element.attrib.get("class"):
            _fail()
        current = dict(inherited)
        for declaration in element.attrib.get("style", "").split(";"):
            if ":" in declaration:
                key, value = declaration.split(":", 1)
                current[key.strip()] = value.strip()
        for key in ("font-family", "font-weight", "font-style"):
            if key in element.attrib:
                current[key] = element.attrib[key]
        if local in ("text", "tspan"):
            family = current.get("font-family", "sans-serif")
            weight = current.get("font-weight", "normal")
            style = current.get("font-style", "normal")
            font_specs.add((family, weight, style))
        for child in element:
            visit(child, current)
    visit(root, {})
    if len(font_specs) > 16:
        _fail()
    content = " ".join(root.itertext()) + " " + " ".join(texts.values())
    _text(content, 131072)
    result = []
    for family, weight, style in sorted(font_specs):
        families = tuple(part.strip().strip("\"'") for part in family.split(","))
        if not families or len(families) > 8 or any(not part for part in families):
            _fail()
        font = QtGui.QFont()
        font.setFamilies(list(families))
        font.setPointSizeF(12.)
        if weight in ("normal", "400"):
            native_weight = 400
        elif weight in ("bold", "700"):
            native_weight = 700
        elif re.fullmatch(r"[1-9]00", weight):
            native_weight = int(weight)
        else:
            _fail()
        font.setWeight(QtGui.QFont.Weight(native_weight))
        if style not in ("normal", "italic", "oblique"):
            _fail()
        font.setItalic(style != "normal")
        document = QtGui.QTextDocument()
        document.setDefaultFont(font)
        document.setPlainText(content)
        result.append((family, weight, style, _font_tables(QtGui.QRawFont.fromFont(font), cache),
                       _text_fonts(document, QtGui, cache)))
    return tuple(result)


def _font(value, QtGui, cache):
    info = QtGui.QFontInfo(value)
    result = (_text(value.toString(), 4096), _text(info.family(), 4096), _text(info.styleName(), 4096),
              _num(info.pointSizeF()), _integer(info.pixelSize()), _bool(info.exactMatch()),
              _enum(value.styleStrategy()), _enum(value.styleHint()), _enum(value.hintingPreference()),
              _enum(value.capitalization()), _integer(value.stretch()), _bool(value.kerning()),
              _enum(value.letterSpacingType()), _num(value.letterSpacing()), _num(value.wordSpacing()))
    # Non-default variable-font/feature controls need a separate native profile.
    for field in ("featureTags", "variableAxisTags"):
        method = getattr(value, field, None)
        if callable(method) and _seq(method(), 64):
            _fail()
    return result, _font_tables(QtGui.QRawFont.fromFont(value), cache)


def _path(value, budget):
    count = _integer(value.elementCount())
    budget[0] += count
    if count < 0 or budget[0] > MAX_PATH_ELEMENTS:
        _fail()
    return (_enum(value.fillRule()), tuple((_enum(value.elementAt(i).type),
                    _num(value.elementAt(i).x), _num(value.elementAt(i).y)) for i in range(count)))


# FreeCAD 1.1.4 QGIUserTypes.h plus each concrete C++ inheritance header.
# Non-virtual Qt base getters must never be inferred from a Python wrapper's
# optional methods: PySide can expose a different base wrapper after redraw.
_RECT_ITEMS = frozenset((3, 65536, 65540))
_TEXT_ITEMS = frozenset((65539, 65542, 65548))
_PRIM_PATH_ITEMS = frozenset((65545, 65550, 65553, 65555, 65556, 65569))
_GROUP_ITEMS = frozenset((10, 13, 65551, 65566, 65575, 65577, 65584))
_SUPPORTED_ITEMS = _RECT_ITEMS | _TEXT_ITEMS | _PRIM_PATH_ITEMS | _GROUP_ITEMS | {2, 65538}


def _required_method(item, name):
    method = None
    try:
        method = getattr(item, name, None)
        if not callable(method):
            _fail()
        return method
    finally:
        item = method = None


def _scene(page, gui):
    QtCore, QtGui, QtWidgets = _qt()
    native = importlib.import_module("TechDrawGui")
    scene = native.getSceneForPage(page)
    if scene is None or type(scene) is not QtWidgets.QGraphicsScene:
        _fail()
    views = _seq(scene.views(), 8)
    if any(type(view) is not QtWidgets.QGraphicsView or view.scene() is not scene for view in views):
        _fail()
    if not views:
        return scene, None
    if _seq(scene.selectedItems(), MAX_SCENE_ITEMS):
        _fail()
    all_items = roots = templates = tagged = ()
    children_by_id, font_cache, item_snapshot = {}, [[], set(), 0], None
    try:
        all_items = _seq(scene.items(), MAX_SCENE_ITEMS)
        # PySide 6.8.3 parentItem() can transfer ownership of native top-level
        # TechDraw groups to Python. Dropping that wrapper then deletes the user's
        # scene. Build the forest from borrowed childItems lists instead.
        children_by_id = {id(item): _seq(item.childItems(), MAX_SCENE_ITEMS) for item in all_items}
        child_ids = {id(child) for children in children_by_id.values() for child in children}
        roots = [item for item in all_items if id(item) not in child_ids]
        expected = {view.Name for view in page.Views if view.TypeId == "TechDraw::DrawViewPart"}
        found = {item.data(1) for item in roots if item.data(0) == "QGIV"}
        if found != expected or len(roots) != len(expected) + 1:
            _fail()
        templates = [item for item in roots if item.type() == 65566]  # 1.1.4 QGISVGTemplate
        if len(templates) != 1:
            _fail()
        tagged = [item for item in all_items if item.data(0) == "QGIV"]
        expected_nodes = {item.Name: 65577 if item.TypeId == "TechDraw::DrawViewPart" else 65575
                          for item in page.Views}
        if (len(tagged) != len(expected_nodes) or {item.data(1) for item in tagged} != set(expected_nodes)
                or any(item.type() != expected_nodes[item.data(1)] for item in tagged)):
            _fail()
        paths, seen, font_cache = [0], set(), [[], set(), 0]
        def item_snapshot(item, depth=0):
            child = effect = document = image = None
            try:
                if depth > 32 or id(item) in seen or len(seen) >= MAX_SCENE_ITEMS:
                    _fail()
                seen.add(id(item))
                if type(item).__module__ != "PySide6.QtWidgets":
                    _fail()
                kind = _integer(item.type())
                if kind not in _SUPPORTED_ITEMS:
                    _fail()
                effect = item.graphicsEffect()
                if effect is not None:
                    # LightOnDark's known colorize effect is frozen below.
                    if type(effect) is not QtWidgets.QGraphicsColorizeEffect:
                        _fail()
                    effect_state = (_bool(effect.isEnabled()), _color(effect.color()), _num(effect.strength()))
                else:
                    effect_state = None
                fields = [("type", kind), ("binding", (item.data(0), item.data(1))), ("visible", _bool(item.isVisible())),
                          ("position", (_num(item.pos().x()), _num(item.pos().y()))),
                          ("transform", _transform(item.transform())), ("rotation", _num(item.rotation())),
                          ("scene_transform", _transform(item.sceneTransform())),
                          ("transform_origin", (_num(item.transformOriginPoint().x()), _num(item.transformOriginPoint().y()))),
                          ("scale", _num(item.scale())), ("opacity", _num(item.opacity())),
                          ("z", _num(item.zValue())), ("bounds", _rect(item.boundingRect())),
                          ("flags", _enum(item.flags())), ("effect", effect_state)]
                if kind in _PRIM_PATH_ITEMS:
                    # QGIEdge/DimLines/CMark expose native stroked selection outlines;
                    # Face/Arrow/Vertex expose their native virtual shape. Never call
                    # these the original centreline path. Projected OCC edge BReps and
                    # EdgeFuzz/MarkFuzz independently bind the underlying geometry.
                    fields.append(("native_shape", _path(item.shape(), paths)))
                if kind == 2:
                    fields.append(("path", _path(_required_method(item, "path")(), paths)))
                if kind in _RECT_ITEMS:
                    fields.append(("rect", _rect(_required_method(item, "rect")())))
                has_base_style = kind in _RECT_ITEMS or kind == 2 or kind in _PRIM_PATH_ITEMS
                if has_base_style and kind not in (65555, 65556) and not (kind == 65569 and item.isVisible() is False):
                    # For Edge/Face only, QGIPrimPath::paint populates stale base pen
                    # caches. Exact source/VP preferences bind their real style inputs.
                    # Other supported PrimPath base styles remain required and exact.
                    fields.append(("pen", _pen(_required_method(item, "pen")())))
                    fields.append(("brush", _brush(_required_method(item, "brush")())))
                if kind in _TEXT_ITEMS:
                    text = _text(_required_method(item, "toPlainText")(), 65536)
                    fields += [("font", _font(_required_method(item, "font")(), QtGui, font_cache)),
                               ("toPlainText", text)]
                    if text:
                        # document() constructs unused empty hover controls lazily.
                        # Only nonempty native text needs HTML and fallback glyphs.
                        document = _required_method(item, "document")()
                        if type(document) is not QtGui.QTextDocument:
                            _fail()
                        html = _text(document.toHtml(), 131072)
                        if re.search(r"<(?:img|object)|(?:src\s*=|url\s*\()", html, re.I):
                            _fail()
                        fields += [("html", html),
                                   ("text_color", _color(_required_method(item, "defaultTextColor")())),
                                   ("glyph_fonts", _text_fonts(document, QtGui, font_cache))]
                if kind == 65538:
                    image = _required_method(item, "pixmap")().toImage()
                    if image.width() * image.height() > 1_000_000:
                        _fail()
                    raw = bytes(image.constBits()[:image.sizeInBytes()]) if not image.isNull() else b""
                    fields.append(("pixmap", (image.width(), image.height(), _enum(image.format()), hashlib.sha256(raw).hexdigest())))
                children = []
                for child in children_by_id[id(item)]:
                    children.append(item_snapshot(child, depth + 1))
                return tuple(fields), tuple(children)
            finally:
                # Tracebacks must not retain borrowed items/documents after a
                # failed snapshot and a later native redraw.
                item = child = effect = document = image = None
        forest_rows = []
        root_item = None
        for root_item in roots:
            try:
                forest_rows.append(item_snapshot(root_item))
            finally:
                root_item = None
        forest = tuple(sorted(forest_rows, key=lambda row: json.dumps(row)))
        if len(seen) != len(all_items) or len(json.dumps(forest, ensure_ascii=False).encode("utf8")) > MAX_SCENE_BYTES:
            _fail()
        locale = QtCore.QLocale()
        locale_state = (locale.name(), locale.decimalPoint(), locale.groupSeparator(), _enum(locale.numberOptions()))
        return scene, (_rect(scene.sceneRect()), forest, locale_state)
    finally:
        item_snapshot = None
        children_by_id.clear()
        font_cache[0].clear()
        all_items = roots = templates = tagged = views = ()


def _projected_cache(view):
    """Freeze cached OCC edges in native list and EdgeN order; no recompute.

    FreeCAD 1.1.4 DrawViewPartPyImp.cpp reads getEdgeGeometry directly.
    False leaves the native display (+Y down) geometry unchanged. The indexed
    getter additionally freezes the public EdgeN order in its own coordinates.
    Each full BRep and orientation is bound, not only a bounding rectangle.
    """
    part = importlib.import_module("Part")
    total_bytes = 0
    def edge_state(edge):
        nonlocal total_bytes
        if type(edge) is not part.Edge:
            _fail()
        brep = _shape(edge)
        total_bytes += brep[0]
        if total_bytes > MAX_PROJECTION_BYTES:
            _fail()
        orientation = _text(edge.Orientation, 32)
        if orientation not in ("Forward", "Reversed", "Internal", "External"):
            _fail()
        box = edge.BoundBox
        bounds = tuple(_num(getattr(box, name)) for name in
                       ("XMin", "YMin", "ZMin", "XMax", "YMax", "ZMax"))
        return orientation, bounds, brep
    visible, hidden = view.getVisibleEdges(False), view.getHiddenEdges(False)
    if type(visible) is not list or type(hidden) is not list:
        _fail()
    count = len(visible) + len(hidden)
    if not 1 <= count <= MAX_PROJECTED_EDGES:
        _fail()
    visible_state, hidden_state = tuple(edge_state(e) for e in visible), tuple(edge_state(e) for e in hidden)
    indexed_state = tuple((index, edge_state(view.getEdgeByIndex(index))) for index in range(count))
    try:
        view.getEdgeByIndex(count)
    except ValueError:
        pass
    else:
        _fail()
    return visible_state, hidden_state, indexed_state


def _selection(gui):
    rows = []
    for item in _seq(gui.Selection.getSelectionEx(), 10000):
        rows.append((_text(item.DocumentName, 256), _text(item.ObjectName, 256),
                     tuple(_text(name, 256) for name in _seq(item.SubElementNames, 256))))
    pre = gui.Selection.getPreselection()
    # An empty native SelectionObject can contain placeholder subelement names.
    # A preselection is bound by its document/object, not those empty payloads.
    preselection = None
    if pre is not None and (getattr(pre, "ObjectName", "") or getattr(pre, "DocumentName", "")):
        preselection = (_text(pre.DocumentName, 256), _text(pre.ObjectName, 256),
                        tuple(_text(name, 256) for name in _seq(pre.SubElementNames, 256)))
    return tuple(rows), preselection


def _state(obj, allow_cache=False):
    states = _seq(obj.State, 16)
    if (not states or any(type(v) is not str for v in states)
            or set(states) - ({"Up-to-date", "Expanded", "Touched"} if allow_cache else {"Up-to-date", "Expanded"})
            or not allow_cache and "Up-to-date" not in states or obj.isValid() is not True):
        _fail()
    return states


def _bound_dependencies(rows):
    """Reject extra native recompute edges without inspecting their targets."""
    result = []
    for obj, expected in rows:
        actual = _seq(obj.OutList, 16)
        identities = tuple(id(child) for child in actual)
        if (len(identities) != len(expected) or len(set(identities)) != len(identities)
                or set(identities) != {id(child) for child in expected}):
            _fail()
        result.append((id(obj), identities))
    return tuple(result)


def _app_snapshot(app, gui, document, page, controller, after_capture=False):
    drawing, dims = _sibling("pft_drawing"), _sibling("pft_drawing_dimensions")
    drawing._context(app, gui, document, controller)
    if tuple(app.Version()[:3]) != ("1", "1", "4"):
        _fail()
    selection = _selection(gui)
    _identity(document, page)
    if page.TypeId != "TechDraw::DrawPage":
        _fail()
    members = _seq(page.Views, 11)
    if len({id(o) for o in members}) != len(members):
        _fail()
    parts = tuple(o for o in members if o.TypeId == "TechDraw::DrawViewPart")
    dimensions = tuple(o for o in members if o.TypeId == "TechDraw::DrawViewDimension")
    if not 1 <= len(parts) <= 3 or len(dimensions) > 8 or len(parts) + len(dimensions) != len(members):
        _fail()
    template = page.Template
    _identity(document, template)
    if (template.TypeId != "TechDraw::DrawSVGTemplate" or tuple(template.InList) != (page,)
            or _seq(template.ExpressionEngine, 100)):
        _fail()
    source_list = _seq(parts[0].Source, 2)
    if len(source_list) != 1:
        _fail()
    source = source_list[0]
    source_state = drawing._native(source, document)
    if source.TypeId not in ("Part::Feature", "PartDesign::Body"):
        _fail()
    shapes = [(source, _shape(source.Shape), drawing._shape(source.Shape))]
    dependency_roots = (source,)
    if source.TypeId == "PartDesign::Body":
        tip = source.Tip
        tip_state = drawing._native(tip, document)
        if (tip is source or not tip.TypeId.startswith(("PartDesign::", "Part::"))
                or sum(item is tip for item in _seq(source.Group, 10000)) != 1):
            _fail()
        shapes.append((tip, _shape(tip.Shape), drawing._shape(tip.Shape), tip_state))
        dependency_roots = (source, tip)
    dependencies = drawing._dependency_snapshot(document, dependency_roots)
    parents, parent_states, global_placement, global_state = drawing._parent_context(document, source)
    nodes = (page, template, *members)
    # Undo/Redo can leave native page/dimension caches Touched although their
    # bound values and projected edges are readable. Freeze those exact initial
    # flags; only the guarded native capture may refresh them. Projection views,
    # source dependencies and unrelated document nodes must already be clean.
    initial_cache_ids = {id(obj) for obj in (page, template, *dimensions)}
    identities, rows, cache_states = [], [], []
    for obj in nodes:
        identities.append(_identity(document, obj))
        cache_states.append((_identity(document, obj), _state(obj, id(obj) in initial_cache_ids or after_capture)))
        props = _properties(obj, document)
        view = obj.ViewObject
        if view.Object is not obj:
            _fail()
        rows.append((_identity(document, obj), props, id(view), _text(view.TypeId, 128), _properties(view, document)))
    refs = _sibling("pft_drawing_references")
    edge_states = []
    dependency_rows = [(page, (template, *members)), (template, ())]
    for view in parts:
        if (tuple(view.Source) != (source,) or view.CoarseView is not False or view.Perspective is not False
                or _seq(view.XSource, 100)):
            _fail()
        for field in ("CosmeticVertexes", "CosmeticEdges", "CenterLines", "GeomFormats"):
            if type(getattr(view, field)) is not list or getattr(view, field):
                _fail()
        consumers = _seq(view.InList, 16)
        if any(item is not page and not any(item is dim for dim in dimensions) for item in consumers):
            _fail()
        edge_states.append(("projected_cache", _identity(document, view), _projected_cache(view)))
        dependency_rows.append((view, (source,)))
    for dim in dimensions:
        if (dim.Type not in ("Distance", "DistanceX", "DistanceY", "Diameter", "Radius")
                or dim.MeasureType != "Projected" or dim.Arbitrary is not False
                or not refs._dimension_reference_scope(dim) or dim.References3D):
            _fail()
        references = dims._refs(dim.References2D)
        if len(references) != 1 or len(references[0][1]) != 1 or not any(references[0][0] is v for v in parts):
            _fail()
        owner, names = references[0]
        refs.edge_index(names[0])
        record = refs.read_edge(owner, names[0])
        expected, actual = dims._value(record, dim.Type), _num(dim.getRawValue())
        if not 0 < actual <= 1e9 or not math.isclose(actual, expected, rel_tol=1e-7, abs_tol=1e-6):
            _fail()
        edge_states.append((_identity(document, dim), refs.edge_signature(record), actual))
        dependency_rows.append((dim, (owner,)))
    bound_dependencies = _bound_dependencies(dependency_rows)
    objects = _seq(document.Objects, 10000)
    registry = []
    for obj in objects:
        identity = _identity(document, obj)
        permitted = id(obj) in initial_cache_ids or after_capture and any(obj is item for item in nodes)
        registry.append((identity, _state(obj, permitted)))
    if len({row[0][1] for row in registry}) != len(registry):
        _fail()
    # Source appearance is protected even though it does not supply PDF pens.
    source_view = source.ViewObject
    appearance = tuple((name, tuple(getattr(source_view, name)) if name.endswith("Color") else getattr(source_view, name))
                       for name in ("Visibility", "ShapeColor", "LineColor", "PointColor", "Transparency", "LineWidth", "PointSize", "DisplayMode"))
    visual = _sibling("pft_visual_tools")
    appearance = (id(source_view), appearance, visual._ordinary_snapshot(source))
    QtCore, QtGui, QtWidgets = _qt()
    font_cache = [[], set(), 0]
    resources = []
    for field in ("Template", "PageResult"):
        data = _resource(getattr(template, field))
        resources.append((field, _svg(data), _svg_fonts(data, template.EditableTexts, QtGui, font_cache)))
    resources = tuple(resources)
    preferences = _preferences(app)
    gui_doc = gui.getDocument(document.Name)
    protected = (source_state, tuple((id(row[0]), *row[1:]) for row in shapes),
                 tuple((id(p), id(child), tuple(id(o) for o in members)) for p, child, members in parents),
                 parent_states, global_state, appearance, tuple(rows), tuple(edge_states),
                 resources, preferences, _text(document.FileName, 4096), _bool(gui_doc.Modified), selection,
                 dependencies, bound_dependencies)
    drawing._context(app, gui, document, controller)
    if _selection(gui) != selection:
        _fail()
    return {"protected": protected, "states": tuple(registry), "source": source,
            "template": template, "nodes": nodes, "paper_mm": (dims._number(template.Width), dims._number(template.Height)),
            "views": parts, "dimensions": dimensions}


def _snapshot(app, gui, document, page, controller, after_capture=False):
    data = _app_snapshot(app, gui, document, page, controller, after_capture)
    scene, semantic = _scene(page, gui)
    _selection(gui)
    return {"_profile": _MARKER, "document": document, "page": page, "data": data,
            "scene_object": scene, "scene": semantic, "pending_open": False, "_capture_attempted": False,
            "_capture_phase": "prepared",
            "public_summary": {"document": document.Name, "page": page.Name, "source": data["source"].Name,
                               "view_count": len(data["views"]), "dimension_count": len(data["dimensions"]),
                               "page_open": semantic is not None, "paper_mm": list(data["paper_mm"]),
                               "projection_verification": "not_checked", "manual_review_required": True}}


def snapshot(app, gui, document, page, controller):
    try:
        first = _snapshot(app, gui, document, page, controller)
        verify(app, gui, first, controller)
        return first
    except PftDrawingExportProfileError:
        raise
    except Exception:
        _fail()


def _context(value):
    if type(value) is not dict or value.get("_profile") != _MARKER:
        _fail("DRAWING_EXPORT_STALE")
    return value


def _states_match(old, new, allowed):
    if len(old) != len(new):
        return False
    for (identity, before), (other, after) in zip(old, new):
        if identity != other:
            return False
        if before != after and (identity[0] not in allowed
                or tuple(s for s in before if s not in ("Touched", "Up-to-date"))
                   != tuple(s for s in after if s not in ("Touched", "Up-to-date"))):
            return False
    return True


def verify(app, gui, context, controller, *, after_capture=False):
    try:
        context = _context(context)
        if after_capture and context.get("_capture_phase") not in ("native", "captured"):
            _fail("DRAWING_EXPORT_STALE")
        current = _snapshot(app, gui, context["document"], context["page"], controller, after_capture)
        allowed = ({id(item) for item in context["data"]["nodes"]}
                   if after_capture and context.get("_capture_phase") == "native" else set())
        if (current["data"]["protected"] != context["data"]["protected"]
                or not _states_match(context["data"]["states"], current["data"]["states"], allowed)
                or current["scene_object"] is not context["scene_object"] or current["scene"] != context["scene"]):
            _fail("DRAWING_EXPORT_STALE")
        current["_capture_phase"] = context.get("_capture_phase", "prepared")
        current["_capture_attempted"] = context.get("_capture_attempted", False)
        return current
    except PftDrawingExportProfileError as error:
        raise PftDrawingExportProfileError("DRAWING_EXPORT_STALE",
            cleanup_state="unknown" if error.cleanup_state == "unknown" else "complete") from None
    except Exception:
        _fail("DRAWING_EXPORT_STALE")


def _guard(guard):
    if not callable(guard):
        _fail("DRAWING_EXPORT_NATIVE_CONFIRMATION_REQUIRED")
    try:
        permitted = guard()
    except Exception:
        _fail("DRAWING_EXPORT_STALE")
    if permitted is False:
        _fail("DRAWING_EXPORT_STALE")


def _restore_renderer_parameter(app, context):
    """Remove only the false key that native PagePrinter itself materializes.

    PagePrinter unconditionally SetBool(LightOnDark, previousValue). If the key
    was absent (effective native default false), that creates a new false key.
    Do not overwrite a pre-existing value or any unexpected concurrent change.
    """
    before_rows, before_files = context["data"]["protected"][9]
    old = next(row for row in before_rows if row[:3] == ("Colors", "LightOnDark", "Bool"))
    current_rows, current_files = _preferences(app)
    new = next(row for row in current_rows if row[:3] == old[:3])
    if new == old:
        return
    if (new[3] != (True, False) or old[3] not in ((False, None), (True, True))
            or current_files != before_files
            or tuple(row for row in current_rows if row[:3] != old[:3])
               != tuple(row for row in before_rows if row[:3] != old[:3])):
        _fail("DRAWING_EXPORT_CAPTURE_UNVERIFIED")
    document, page = context["document"], context["page"]
    if (app.getDocument(document.Name) is not document or page.Document is not document
            or document.getObject(page.Name) is not page):
        _fail("DRAWING_EXPORT_CAPTURE_UNVERIFIED")
    group = app.ParamGet("User parameter:BaseApp/Preferences/Mod/TechDraw/Colors")
    if _parameter(group, "LightOnDark", "Bool") != (True, False):
        _fail("DRAWING_EXPORT_CAPTURE_UNVERIFIED")
    if old[3][0]:
        group.SetBool("LightOnDark", True)
    else:
        group.RemBool("LightOnDark")
    if _parameter(group, "LightOnDark", "Bool") != old[3]:
        _fail("DRAWING_EXPORT_CAPTURE_UNVERIFIED")


def _page_visibility(data, page_id):
    """Locate the exact frozen page/VP native bool pair, without new getters."""
    protected = _seq(data["protected"], 15)
    if len(protected) != 15:
        _fail("DRAWING_EXPORT_STALE")
    rows = _seq(protected[6], 16)
    candidates = []
    for index, row in enumerate(rows):
        if (type(row) is not tuple or len(row) != 5 or type(row[0]) is not tuple
                or len(row[0]) != 3):
            _fail("DRAWING_EXPORT_STALE")
        if row[0][0] == page_id:
            candidates.append((index, row))
    if len(candidates) != 1:
        _fail("DRAWING_EXPORT_STALE")
    index, row = candidates[0]
    if row[0][2] != "TechDraw::DrawPage" or type(row[2]) is not int or type(row[3]) is not str:
        _fail("DRAWING_EXPORT_STALE")
    positions, values = [], []
    for properties in (row[1], row[4]):
        properties = _seq(properties, 256)
        if (any(type(value) is not tuple or len(value) != 3 or type(value[0]) is not str for value in properties)
                or len({value[0] for value in properties}) != len(properties)):
            _fail("DRAWING_EXPORT_STALE")
        found = [(i, value) for i, value in enumerate(properties) if value[0] == "Visibility"]
        if (len(found) != 1 or found[0][1][1] != "App::PropertyBool"
                or type(found[0][1][2]) is not bool):
            _fail("DRAWING_EXPORT_STALE")
        positions.append(found[0][0]); values.append(found[0][1][2])
    if values[0] is not values[1]:
        _fail("DRAWING_EXPORT_STALE")
    return index, row, tuple(positions), values[0]


def _opened_app_matches(before, after, page_id, was_open):
    """The one synchronous show may reveal only its exact closed page pair.

    Native ViewProviderDocumentObject.show synchronizes Page and VP Visibility.
    This comparison-only expected tuple never changes either actual snapshot.
    All other frozen fields, owners, identities, types and properties are exact.
    """
    was_open = _bool(was_open)
    index, row, positions, old_visible = _page_visibility(before, page_id)
    _, _, _, new_visible = _page_visibility(after, page_id)
    if new_visible is not True or was_open and old_visible is not True:
        return False
    if old_visible is True:
        return before["protected"] == after["protected"]
    expected = list(before["protected"])
    rows = list(expected[6])
    page_properties, vp_properties = list(row[1]), list(row[4])
    for props, position in zip((page_properties, vp_properties), positions):
        name, kind, _ = props[position]
        props[position] = name, kind, True
    rows[index] = row[0], tuple(page_properties), row[2], row[3], tuple(vp_properties)
    expected[6] = tuple(rows)
    return tuple(expected) == after["protected"]


def open_page(app, gui, context, controller, *, guard):
    _guard(guard)
    context = _context(context)
    verify(app, gui, context, controller)
    was_open = context["scene"] is not None
    _, _, _, visible = _page_visibility(context["data"], id(context["page"]))
    if was_open and visible is not True:
        _fail("DRAWING_EXPORT_STALE")
    entered = False
    try:
        _guard(guard)
        entered = True
        context["page"].ViewObject.show()
        _guard(guard)
        current = _app_snapshot(app, gui, context["document"], context["page"], controller)
        # First opening may touch only this frozen native template.
        if (not _opened_app_matches(context["data"], current, id(context["page"]), was_open)
                or not _states_match(context["data"]["states"], current["states"], {id(current["template"])})):
            _fail("DRAWING_EXPORT_STALE")
        result = dict(context)
        result.update(data=current, pending_open=True, scene=None, _opened_was_open=was_open)
        return result
    except PftDrawingExportProfileError as error:
        if entered:
            error.cad_protection_state, error.page_open_state = "unverified", "unknown"
        raise
    except Exception:
        error = PftDrawingExportProfileError("DRAWING_EXPORT_CAPTURE_UNVERIFIED")
        error.cad_protection_state, error.page_open_state = "unverified", "unknown"
        raise error from None


def _paper_rect(scene_rect, paper_mm):
    """Derive the actual native paper from QGSPage's three-paper scene extent.

    FreeCAD's active Rez factor is static; a current preference/default cannot
    establish it. QGSPage::matchSceneRectToTemplate supplies this geometry and
    PagePrinter uses (0, -guiHeight, guiWidth, guiHeight) for the paper itself.
    This proves the current scene geometry, not a later C++ Rez-factor change;
    the original post-export scene verification remains mandatory.
    """
    rect, paper = _seq(scene_rect, 4), _seq(paper_mm, 2)
    if len(rect) != 4 or len(paper) != 2:
        _fail()
    x, y, width, height = (_num(value) for value in rect)
    paper_width, paper_height = (_num(value) for value in paper)
    if min(width, height, paper_width, paper_height) <= 0:
        _fail()
    gui_width, gui_height = _num(width / 3.), _num(height / 3.)
    if min(gui_width, gui_height) <= 0:
        _fail()
    factor_x, factor_y = _num(gui_width / paper_width), _num(gui_height / paper_height)
    scale_x, scale_y = _num(PAPER_PAINT_PIXELS / gui_width), _num(PAPER_PAINT_PIXELS / gui_height)
    if min(factor_x, factor_y, scale_x, scale_y) <= 0 or not all((
            math.isclose(x, -gui_width, rel_tol=1e-9, abs_tol=0.),
            math.isclose(y, _num(-2. * gui_height), rel_tol=1e-9, abs_tol=0.),
            math.isclose(factor_x, factor_y, rel_tol=1e-9, abs_tol=0.))):
        _fail()
    return 0., -gui_height, gui_width, gui_height


def _render_paper(scene, paper_mm, *, guard):
    """Paint only this bound paper to bounded private memory before freezing.

    No viewport/global repaint, event pumping, PDF generation or image output.
    QGraphicsScene.render is void. Hidden/off-paper content need not be painted;
    this call never exempts later scene changes from the strict PDF guard.
    """
    image = painter = source = target = None
    begun, cleanup_unknown, error = False, False, None
    try:
        _guard(guard)
        QtCore, QtGui, QtWidgets = _qt()
        if type(scene) is not QtWidgets.QGraphicsScene:
            _fail()
        paper = _paper_rect(_rect(scene.sceneRect()), paper_mm)
        image = QtGui.QImage(PAPER_PAINT_PIXELS, PAPER_PAINT_PIXELS,
                             QtGui.QImage.Format_RGBA8888_Premultiplied)
        if type(image) is not QtGui.QImage or image.isNull():
            _fail("DRAWING_EXPORT_CAPTURE_UNVERIFIED")
        image.setDevicePixelRatio(1.)
        image.fill(QtCore.Qt.white)
        if (image.width() != PAPER_PAINT_PIXELS or image.height() != PAPER_PAINT_PIXELS
                or image.bytesPerLine() != 4 * PAPER_PAINT_PIXELS
                or image.sizeInBytes() != MAX_PAPER_PAINT_BYTES
                or image.devicePixelRatio() != 1.):
            _fail()
        source = QtCore.QRectF(*paper)
        target = QtCore.QRectF(0., 0., float(PAPER_PAINT_PIXELS), float(PAPER_PAINT_PIXELS))
        painter = QtGui.QPainter()
        if type(painter) is not QtGui.QPainter:
            _fail()
        # begin may enter native code before raising; an unknown outcome must
        # not be reported as verified cleanup or retried.
        cleanup_unknown = True
        if painter.begin(image) is not True:
            _fail("DRAWING_EXPORT_CAPTURE_UNVERIFIED")
        begun = True
        cleanup_unknown = False
        if painter.isActive() is not True:
            _fail("DRAWING_EXPORT_CAPTURE_UNVERIFIED")
        _guard(guard)
        scene.render(painter, target, source, QtCore.Qt.KeepAspectRatio)
        _guard(guard)
    except Exception as caught:
        error = caught
    finally:
        if begun:
            try:
                # Never retry end, including exceptions after native completion.
                if painter.end() is not True:
                    cleanup_unknown = True
            except Exception:
                cleanup_unknown = True
        image = painter = source = target = scene = paper_mm = None
    if error is not None or cleanup_unknown:
        failure = error if isinstance(error, PftDrawingExportProfileError) else PftDrawingExportProfileError(
            "DRAWING_EXPORT_CAPTURE_UNVERIFIED")
        if cleanup_unknown or getattr(error, "cleanup_state", None) == "unknown":
            failure.cleanup_state = "unknown"
        failure.cad_protection_state, failure.page_open_state = "unverified", "unknown"
        error = None
        raise failure from None
    _guard(guard)


def bind_open_scene(app, gui, opened_context, controller, *, guard):
    _guard(guard)
    context = _context(opened_context)
    if context.get("pending_open") is not True:
        _fail("DRAWING_EXPORT_STALE")
    scene = semantic = data = current = None
    try:
        # Validate CAD/resources before entering any target-page painting.
        data = _app_snapshot(app, gui, context["document"], context["page"], controller)
        if (data["protected"] != context["data"]["protected"]
                or not _states_match(context["data"]["states"], data["states"], {id(data["template"])})):
            _fail("DRAWING_EXPORT_STALE")
        scene, semantic = _scene(context["page"], gui)
        if (semantic is None or context.get("_opened_was_open") is True
                and scene is not context["scene_object"]):
            _fail("DRAWING_EXPORT_STALE")
        _guard(guard)
        _render_paper(scene, data["paper_mm"], guard=guard)
        _guard(guard)
        current = _snapshot(app, gui, context["document"], context["page"], controller)
        if (current["scene"] is None or current["data"]["protected"] != context["data"]["protected"]
                or current["scene_object"] is not scene
                or not _states_match(context["data"]["states"], current["data"]["states"], {id(current["data"]["template"])})):
            _fail("DRAWING_EXPORT_STALE")
        _guard(guard)
        verify(app, gui, current, controller)
        _guard(guard)
        return current
    except PftDrawingExportProfileError as error:
        error.cad_protection_state, error.page_open_state = "unverified", "unknown"
        raise
    except Exception as error:
        failure = PftDrawingExportProfileError("DRAWING_EXPORT_CAPTURE_UNVERIFIED",
            cleanup_state="unknown" if getattr(error, "cleanup_state", None) == "unknown" else "complete")
        failure.cad_protection_state, failure.page_open_state = "unverified", "unknown"
        raise failure from None
    finally:
        scene = semantic = data = current = context = opened_context = None


def capture_pdf(app, gui, context, controller, *, guard):
    """One explicit native capture; never retries, saves CAD or performs Undo."""
    _guard(guard)
    context = _context(context)
    if context.get("pending_open") or context.get("scene") is None:
        _fail("DRAWING_EXPORT_PAGE_OPEN_REQUIRED")
    if context.get("_capture_attempted") is not False or context.get("_capture_phase") != "prepared":
        _fail("DRAWING_EXPORT_STALE")
    verify(app, gui, context, controller)
    directory = target = parent_fd = file_identity = None
    capture_state, cleanup_state, error, result = "not_captured", "complete", None, None
    try:
        _guard(guard)
        directory = Path(tempfile.mkdtemp(prefix="pft-drawing-export-")).resolve()
        info = directory.lstat()
        directory_identity = info.st_dev, info.st_ino
        if not stat.S_ISDIR(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o700:
            _fail("DRAWING_EXPORT_STAGE_UNVERIFIED")
        parent_fd = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0))
        if (os.fstat(parent_fd).st_dev, os.fstat(parent_fd).st_ino) != directory_identity:
            _fail("DRAWING_EXPORT_STAGE_UNVERIFIED")
        target = directory / "capture.pdf"
        if os.listdir(parent_fd):
            _fail("DRAWING_EXPORT_STAGE_UNVERIFIED")
        # Reserve the exact file inode before native entry. QPdfWriter must
        # truncate this owned file in place; a replaced pathname is unverified
        # and is never adopted or deleted during cleanup.
        stage_fd = os.open(target.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                           | getattr(os, "O_NOFOLLOW", 0), 0o600, dir_fd=parent_fd)
        try:
            reserved = os.fstat(stage_fd)
            file_identity = reserved.st_dev, reserved.st_ino
            if not stat.S_ISREG(reserved.st_mode) or reserved.st_nlink != 1 or stat.S_IMODE(reserved.st_mode) != 0o600:
                _fail("DRAWING_EXPORT_STAGE_UNVERIFIED")
        finally:
            try:
                os.close(stage_fd)
            except Exception:
                cleanup_state = "unknown"
                raise
        _guard(guard)
        verify(app, gui, context, controller)
        _guard(guard)
        context["_capture_attempted"] = True
        context["_capture_phase"] = "native"
        capture_state = "unknown"
        native = importlib.import_module("TechDrawGui")
        native.exportPageAsPdf(context["page"], str(target))
        _guard(guard)
        _restore_renderer_parameter(app, context)
        before = os.stat(target.name, dir_fd=parent_fd, follow_symlinks=False)
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                or (before.st_dev, before.st_ino) != file_identity
                or stat.S_IMODE(before.st_mode) != 0o600):
            _fail("DRAWING_EXPORT_STAGE_UNVERIFIED")
        if not 0 < before.st_size <= MAX_PDF_BYTES:
            _fail("DRAWING_EXPORT_STAGE_UNVERIFIED")
        fd = os.open(target.name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0), dir_fd=parent_fd)
        with os.fdopen(fd, "rb") as stream:
            if _stamp(os.fstat(stream.fileno())) != _stamp(before):
                _fail("DRAWING_EXPORT_STAGE_UNVERIFIED")
            data = stream.read(MAX_PDF_BYTES + 1)
            after = os.fstat(stream.fileno())
        if (not data.startswith(b"%PDF-") or len(data) != before.st_size
                or _stamp(before) != _stamp(after)
                or _stamp(os.stat(target.name, dir_fd=parent_fd, follow_symlinks=False)) != _stamp(before)):
            _fail("DRAWING_EXPORT_STAGE_UNVERIFIED")
        _guard(guard)
        post = verify(app, gui, context, controller, after_capture=True)
        _guard(guard)
        capture_state = "captured"
        post["_capture_attempted"] = True
        post["_capture_phase"] = "captured"
        result = {"bytes": data, "post_context": post, "capture_state": capture_state}
    except PftDrawingExportProfileError as failure:
        error = failure
    except Exception:
        error = PftDrawingExportProfileError("DRAWING_EXPORT_CAPTURE_FAILED" if capture_state == "not_captured"
                                            else "DRAWING_EXPORT_CAPTURE_UNVERIFIED")
    finally:
        if capture_state != "not_captured":
            try:
                # Cleanup of a precise native-created false key is independent
                # of permission to start a new action after stop/cancel.
                _restore_renderer_parameter(app, context)
            except Exception:
                cleanup_state = "unknown"
                if error is None:
                    error = PftDrawingExportProfileError("DRAWING_EXPORT_CAPTURE_UNVERIFIED")
        try:
            if parent_fd is not None:
                if target is not None:
                    try:
                        current = os.stat(target.name, dir_fd=parent_fd, follow_symlinks=False)
                    except FileNotFoundError:
                        current = None
                    if current is None:
                        # An owned inode may have been moved outside this
                        # namespace; absence is not proof that it was cleaned.
                        if file_identity is not None:
                            cleanup_state = "unknown"
                    elif (file_identity is None or (current.st_dev, current.st_ino) != file_identity
                          or not stat.S_ISREG(current.st_mode) or current.st_nlink != 1):
                        # Do not follow or search for an external hardlink or
                        # renamed path, and do not erase foreign replacements.
                        cleanup_state = "unknown"
                    else:
                        os.unlink(target.name, dir_fd=parent_fd)
                if os.listdir(parent_fd):
                    cleanup_state = "unknown"
                owned_fd, parent_fd = parent_fd, None
                os.close(owned_fd)
            if directory is not None and cleanup_state == "complete":
                named = directory.lstat()
                if (named.st_dev, named.st_ino) != directory_identity:
                    cleanup_state = "unknown"
                else:
                    os.rmdir(directory)
        except Exception:
            cleanup_state = "unknown"
        finally:
            if parent_fd is not None:
                owned_fd, parent_fd = parent_fd, None
                try:
                    os.close(owned_fd)
                except Exception:
                    cleanup_state = "unknown"
    if error is not None:
        error.capture_state = capture_state
        error.cleanup_state = ("unknown" if cleanup_state == "unknown" or error.cleanup_state == "unknown"
                               else "complete")
        raise error
    result["cleanup_state"] = cleanup_state
    return result
