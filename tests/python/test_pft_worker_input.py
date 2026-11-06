# SPDX-License-Identifier: Apache-2.0
"""Synthetic ZIP/XML only: no FreeCAD imports, real CAD, preferences or services."""
import hashlib
import importlib.util
import io
import os
from pathlib import Path
import stat
import struct
import tempfile
import unittest
from unittest.mock import patch
import warnings
import xml.etree.ElementTree as ET
import zipfile
import zlib

MODULE = Path(__file__).parents[2] / "addon/PhoenixFreeCADTools/pft_worker_input.py"
spec = importlib.util.spec_from_file_location("pft_worker_input_test", MODULE)
worker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(worker)


def prop(name, kind, value):
    return '<Property name="{}" type="{}">{}</Property>'.format(name, kind, value)


def document(objects=None, visibility=True):
    # These are serialization-shaped artificial fixtures, not native BRep models.
    if objects is None:
        objects = [("Feature", "Part::Feature",
                    prop("Label", "App::PropertyString", '<String value="Neutral feature"/>')
                    + prop("Shape", "Part::PropertyPartShape", '<Part file="Feature.Shape.brp"/>'),
                    ())]
    declarations, records = [], []
    for name, kind, properties, extensions in objects:
        declarations.append('<Object type="{}" name="{}" id="{}"/>'.format(kind, name, len(declarations)+1))
        root = ET.fromstring("<Properties>{}</Properties>".format(
            prop("ExpressionEngine", "App::PropertyExpressionEngine", '<ExpressionEngine count="0"/>')
            + (prop("Visibility", "App::PropertyBool", '<Bool value="{}"/>'.format("true" if visibility else "false"))
               if visibility is not None else "") + properties))
        root.set("Count", str(len(root)))
        exts = "".join('<Extension type="{}" name="{}"/>'.format(t, t.rsplit("::", 1)[-1])
                       for t in extensions)
        ext_xml = '<Extensions Count="{}">{}</Extensions>'.format(len(extensions), exts) if extensions else ""
        records.append('<Object name="{}"{}>{}{}</Object>'.format(
            name, ' Extensions="True"' if extensions else "", ext_xml,
            ET.tostring(root, encoding="unicode")))
    return ('<?xml version="1.0" encoding="utf-8"?>'
            '<Document SchemaVersion="4" StringHasher="1"><Properties Count="0"/>'
            '<Objects Count="{}">{}</Objects><ObjectData Count="{}">{}</ObjectData>'
            '</Document>').format(len(objects), "".join(declarations), len(objects), "".join(records)).encode()


def gui(properties="", extensions=(), camera=""):
    values = ET.fromstring("<Properties>{}</Properties>".format(properties))
    values.set("Count", str(len(values)))
    exts = "".join('<Extension type="{}" name="{}"/>'.format(t, t.rsplit("::", 1)[-1])
                   for t in extensions)
    root = ET.fromstring('<Document SchemaVersion="1"><ViewProviderData Count="1">'
                        '<ViewProvider name="Feature"{}>{}{}</ViewProvider>'
                        '</ViewProviderData></Document>'.format(
                            ' Extensions="True"' if extensions else "",
                            '<Extensions Count="{}">{}</Extensions>'.format(len(extensions), exts) if extensions else "",
                            ET.tostring(values, encoding="unicode")))
    ET.SubElement(root, "Camera", settings=camera)
    return ET.tostring(root, encoding="utf-8")


def archive(xml=None, extra=(), gui_xml=None, compression=zipfile.ZIP_DEFLATED):
    stream = io.BytesIO()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        with zipfile.ZipFile(stream, "w", compression=compression) as output:
            output.writestr("Document.xml", document() if xml is None else xml)
            output.writestr("Feature.Shape.brp", b"synthetic shape carrier; never restored")
            if gui_xml is not None:
                output.writestr("GuiDocument.xml", gui_xml)
            for name, value in extra:
                output.writestr(name, value)
    return stream.getvalue()


def edit_member(raw, name, change):
    """Change corresponding local and central sizes/CRC/flags in a synthetic ZIP."""
    result = bytearray(raw)
    with zipfile.ZipFile(io.BytesIO(raw)) as source:
        info = source.getinfo(name)
        local = info.header_offset
        central = source.start_dir
        while True:
            if result[central:central+4] != b"PK\x01\x02":
                raise AssertionError("central directory missing")
            n, extra, comment = struct.unpack_from("<3H", result, central+28)
            filename = result[central+46:central+46+n].decode()
            if filename == name:
                break
            central += 46+n+extra+comment
        change(result, local, central, info)
    return bytes(result)


class WorkerInputTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="pft-worker-input-", dir="/private/tmp")
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name)
        self.staging = self.folder / "staging"
        self.staging.mkdir(mode=0o700)
        self.source = self.folder / "Neutral.FCStd"
        self.seed()

    def seed(self, raw=None):
        raw = archive() if raw is None else raw
        self.source.write_bytes(raw)
        return raw

    def freeze(self):
        return worker.freeze_fcstd(self.source, self.staging)

    def error(self, code, call):
        with self.assertRaises(worker.PftWorkerInputError) as raised:
            call()
        self.assertEqual(raised.exception.code, code)
        self.assertNotIn(str(self.folder), str(raised.exception))
        return raised.exception

    def test_import_is_pure_and_does_not_open_paths_or_import_freecad(self):
        fresh_spec = importlib.util.spec_from_file_location("pft_worker_input_pure_test", MODULE)
        fresh = importlib.util.module_from_spec(fresh_spec)
        with patch.object(os, "open", side_effect=AssertionError("unexpected disk access")), \
                patch.object(Path, "home", side_effect=AssertionError("unexpected home access")):
            fresh_spec.loader.exec_module(fresh)
        self.assertFalse(hasattr(fresh, "App"))
        self.assertFalse(hasattr(fresh, "Gui"))

    def test_exact_byte_revision_private_staging_source_unchanged_and_idempotent_cleanup(self):
        self.source.chmod(0o644)
        before = self.source.stat()
        raw = self.source.read_bytes()
        sibling = self.staging / "unrelated.txt"
        sibling.write_bytes(b"keep")
        receipt = self.freeze()
        self.assertEqual(receipt.revision, hashlib.sha256(raw).hexdigest())
        self.assertEqual(receipt.compressed_bytes, len(raw))
        self.assertEqual(receipt.object_types, ("Part::Feature",))
        self.assertEqual(Path(receipt.staged_path).read_bytes(), raw)
        self.assertEqual(stat.S_IMODE(Path(receipt.staged_path).stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(Path(receipt.staged_path).parent.stat().st_mode), 0o700)
        self.assertEqual(self.source.read_bytes(), raw)
        self.assertEqual(self.source.stat().st_mtime_ns, before.st_mtime_ns)
        self.assertEqual(self.source.stat().st_mode, before.st_mode)
        self.assertNotIn(str(self.folder), repr(receipt))
        with self.assertRaises(AttributeError):
            receipt.revision = "replacement"
        worker.verify_frozen_input(receipt)
        worker.cleanup_frozen_input(receipt)
        worker.cleanup_frozen_input(receipt)
        self.assertEqual(list(self.staging.iterdir()), [sibling])
        self.assertEqual(sibling.read_bytes(), b"keep")

    def test_no_gui_explicit_visibility_is_immutable_and_exact_bytes_are_preserved(self):
        visible = prop("Visibility", "App::PropertyBool", '<Bool value="true"/>')
        hidden = prop("Visibility", "App::PropertyBool", '<Bool value="false"/>')
        raw = self.seed(archive(document([("Visible", "Part::Feature", visible, ()),
                                          ("Hidden", "Part::Feature", hidden, ())], visibility=None)))
        original = self.source.stat()
        receipt = self.freeze()
        self.assertEqual(receipt.restore_visibility,
                         (("Visible", "Part::Feature", True), ("Hidden", "Part::Feature", False)))
        self.assertIs(type(receipt.restore_visibility), tuple)
        with self.assertRaises(AttributeError):
            receipt.restore_visibility = ()
        self.assertEqual(self.source.read_bytes(), raw)
        self.assertEqual(Path(receipt.staged_path).read_bytes(), raw)
        self.assertEqual(receipt.revision, hashlib.sha256(raw).hexdigest())
        self.assertEqual(self.source.stat().st_mtime_ns, original.st_mtime_ns)
        worker.verify_frozen_input(receipt)
        worker.cleanup_frozen_input(receipt)

    def test_saved_gui_state_never_requests_document_visibility_initialization(self):
        for visibility in (None, True, False):
            with self.subTest(visibility=visibility):
                xml = document(visibility=visibility)
                raw = self.seed(archive(xml, gui_xml=gui(prop("Visibility", "App::PropertyBool", '<Bool value="false"/>'))))
                receipt = self.freeze()
                self.assertIsNone(receipt.restore_visibility)
                self.assertEqual(Path(receipt.staged_path).read_bytes(), raw)
                worker.cleanup_frozen_input(receipt)
        # The original document bool is irrelevant when a supported GUI state
        # was saved; it is neither copied nor used to override that GUI state.
        xml = document(visibility=None)
        self.seed(archive(xml, gui_xml=gui()))
        receipt = self.freeze()
        self.assertIsNone(receipt.restore_visibility)
        worker.cleanup_frozen_input(receipt)

    def test_no_gui_missing_or_ambiguous_visibility_is_explicitly_unsupported(self):
        properties = ["", prop("Visibility", "App::PropertyString", '<String value="true"/>')]
        for value in ("True", "False", "1", "0", "", " true", "false "):
            properties.append(prop("Visibility", "App::PropertyBool", '<Bool value="{}"/>'.format(value)))
        properties.extend((prop("Visibility", "App::PropertyBool", '<Bool/>'),
                           prop("Visibility", "App::PropertyBool", '<Bool value="true" extra="x"/>'),
                           prop("Visibility", "App::PropertyBool", '<Bool value="true"><Bool value="false"/></Bool>'),
                           prop("Visibility", "App::PropertyBool", '<Bool value="true"/><Bool value="false"/>')))
        for value in properties:
            with self.subTest(value=value):
                self.seed(archive(document([("Feature", "Part::Feature", value, ())], visibility=None)))
                self.error("WORKER_INPUT_UNSUPPORTED", self.freeze)
                self.assertEqual(list(self.staging.iterdir()), [])
        duplicate = prop("Visibility", "App::PropertyBool", '<Bool value="true"/>') * 2
        self.seed(archive(document([("Feature", "Part::Feature", duplicate, ())], visibility=None)))
        self.error("WORKER_INPUT_INVALID", self.freeze)
        self.assertEqual(list(self.staging.iterdir()), [])

    def test_unicode_paths_and_native_object_names(self):
        renamed = self.folder / "中性板.FCStd"
        self.source.rename(renamed)
        self.source = renamed
        self.seed(archive(document([("零件", "Part::Feature", "", ())])))
        receipt = self.freeze()
        worker.verify_frozen_input(receipt)
        worker.cleanup_frozen_input(receipt)

    def test_path_canonicality_and_extension(self):
        for path in ("Neutral.FCStd", str(self.source)+" ", str(self.folder)+"/./Neutral.FCStd",
                     str(self.folder)+"/x/../Neutral.FCStd", str(self.folder)+"//Neutral.FCStd",
                     str(self.folder)+"/bad\x00.FCStd", str(self.folder)+"/Neutral.step"):
            with self.subTest(path=path):
                self.error("WORKER_INPUT_PATH_INVALID", lambda: worker.freeze_fcstd(path, self.staging))
        self.assertEqual(list(self.staging.iterdir()), [])

    def test_source_symlink_parent_symlink_hardlink_and_fifo_are_refused(self):
        link = self.folder / "link.FCStd"
        link.symlink_to(self.source)
        self.error("WORKER_INPUT_UNSAFE", lambda: worker.freeze_fcstd(link, self.staging))
        alias = self.folder / "alias"
        alias.symlink_to(self.folder, target_is_directory=True)
        self.error("WORKER_INPUT_UNSAFE", lambda: worker.freeze_fcstd(alias / self.source.name, self.staging))
        hard = self.folder / "other.FCStd"
        os.link(self.source, hard)
        self.error("WORKER_INPUT_UNSAFE", self.freeze)
        hard.unlink()
        fifo = self.folder / "fifo.FCStd"
        os.mkfifo(fifo)
        self.error("WORKER_INPUT_UNSAFE", lambda: worker.freeze_fcstd(fifo, self.staging))
        self.assertEqual(list(self.staging.iterdir()), [])

    def test_staging_must_exist_private_and_real_without_chmod(self):
        missing = self.folder / "missing"
        self.error("WORKER_INPUT_STAGING_INVALID", lambda: worker.freeze_fcstd(self.source, missing))
        self.assertFalse(missing.exists())
        self.staging.chmod(0o755)
        self.error("WORKER_INPUT_STAGING_INVALID", self.freeze)
        self.assertEqual(stat.S_IMODE(self.staging.stat().st_mode), 0o755)
        self.staging.chmod(0o700)
        alias = self.folder / "staging-alias"
        alias.symlink_to(self.staging, target_is_directory=True)
        self.error("WORKER_INPUT_STAGING_INVALID", lambda: worker.freeze_fcstd(self.source, alias))
        with patch.object(worker.os, "getuid", return_value=os.getuid()+1):
            self.error("WORKER_INPUT_STAGING_INVALID", self.freeze)

    def test_platform_without_nofollow_guarantees_is_explicitly_unsupported(self):
        with patch.object(worker.os, "name", "nt"):
            self.error("WORKER_INPUT_UNSUPPORTED", self.freeze)
        self.assertEqual(list(self.staging.iterdir()), [])

    def test_zip_budgets_actual_members_and_xml(self):
        raw = self.source.read_bytes()
        with patch.object(worker, "MAX_COMPRESSED_BYTES", len(raw)):
            receipt = self.freeze()
            worker.cleanup_frozen_input(receipt)
        with patch.object(worker, "MAX_COMPRESSED_BYTES", len(raw)-1):
            self.error("WORKER_INPUT_LIMIT", self.freeze)
        with patch.object(worker, "MAX_ZIP_ENTRIES", 1):
            self.error("WORKER_INPUT_LIMIT", self.freeze)
        with patch.object(worker, "MAX_EXPANDED_BYTES", len(document())):
            self.error("WORKER_INPUT_LIMIT", self.freeze)
        with patch.object(worker, "MAX_XML_BYTES", len(document())-1):
            self.error("WORKER_INPUT_LIMIT", self.freeze)
        self.assertEqual(list(self.staging.iterdir()), [])

    def test_zip_traversal_duplicates_aliases_symlink_and_encryption(self):
        for name in ("../outside", "/outside", "a/../outside", "a//b", "C:/outside", "a\\b", "./outside"):
            with self.subTest(name=name):
                self.seed(archive(extra=[(name, b"not extracted")]))
                self.error("WORKER_INPUT_INVALID", self.freeze)
        for name in ("Document.xml", "document.xml"):
            self.seed(archive(extra=[(name, document())]))
            self.error("WORKER_INPUT_INVALID", self.freeze)
        self.seed(archive(extra=[("é", b"a"), ("e\u0301", b"b")]))
        self.error("WORKER_INPUT_INVALID", self.freeze)
        item = zipfile.ZipInfo("link")
        item.create_system = 3
        item.external_attr = (stat.S_IFLNK | 0o777) << 16
        self.seed(archive(extra=[(item, b"outside")]))
        self.error("WORKER_INPUT_UNSUPPORTED", self.freeze)
        raw = archive()
        encrypted = edit_member(raw, "Document.xml", lambda data, local, central, info: (
            struct.pack_into("<H", data, local+6, info.flag_bits | 1),
            struct.pack_into("<H", data, central+8, info.flag_bits | 1)))
        self.seed(encrypted)
        self.error("WORKER_INPUT_UNSUPPORTED", self.freeze)
        self.assertEqual(list(self.staging.iterdir()), [])

    def test_corruption_local_header_mismatch_trailing_and_declared_size_truncation(self):
        self.seed(b"not a zip")
        self.error("WORKER_INPUT_INVALID", self.freeze)
        self.seed(archive()+b"unexpected trailing data")
        self.error("WORKER_INPUT_INVALID", self.freeze)
        raw = archive(extra=[("extra.bin", b"A"*128)])
        broken = edit_member(raw, "extra.bin", lambda data, local, central, info:
                             data.__setitem__(local+30, ord("x")))
        self.seed(broken)
        self.error("WORKER_INPUT_INVALID", self.freeze)

        def dishonest_size(data, local, central, info):
            # ZipExtFile can truncate to a declared size. Raw stream validation must reject it.
            crc = zlib.crc32(b"A")
            struct.pack_into("<I", data, local+14, crc)
            struct.pack_into("<I", data, local+22, 1)
            struct.pack_into("<I", data, central+16, crc)
            struct.pack_into("<I", data, central+24, 1)
        self.seed(edit_member(raw, "extra.bin", dishonest_size))
        self.error("WORKER_INPUT_INVALID", self.freeze)
        corrupt = bytearray(archive())
        corrupt[45] ^= 0xFF
        self.seed(bytes(corrupt))
        self.error("WORKER_INPUT_INVALID", self.freeze)
        self.assertEqual(list(self.staging.iterdir()), [])

    def test_invalid_xml_entities_encoding_duplicates_and_count_mismatch(self):
        cases = [
            b"\xff", b"<Document>", b"<Wrong/>",
            b'<!DOCTYPE Document [<!ENTITY x SYSTEM "file:///no-file">]><Document>&x;</Document>',
            document().replace(b'Count="1"', b'Count="2"', 1),
            document().replace(b'SchemaVersion="4"', b'SchemaVersion="4" SchemaVersion="4"'),
            document().replace(b'encoding="utf-8"', b'encoding="iso-8859-1"'),
        ]
        for xml in cases:
            with self.subTest(xml=xml[:80]):
                self.seed(archive(xml))
                self.error("WORKER_INPUT_INVALID", self.freeze)
        with patch.object(worker, "MAX_XML_DEPTH", 2):
            self.seed()
            self.error("WORKER_INPUT_LIMIT", self.freeze)
        with patch.object(worker, "MAX_XML_NODES", 2):
            self.error("WORKER_INPUT_LIMIT", self.freeze)
        self.assertEqual(list(self.staging.iterdir()), [])

    def test_transient_status_records_have_independent_counts_and_no_value_payload(self):
        # Hand-built minimal structure, independent of native fixture bytes/XML.
        root = ET.fromstring(document())
        properties = root.find("Properties")
        properties.set("Count", "1")
        properties.set("TransientCount", "2")
        ET.SubElement(properties, "_Property", name="RuntimeField", type="App::PropertyString", status="1")
        ET.SubElement(properties, "_Property", name="RuntimeTarget", type="App::PropertyLink", status="33554433")
        regular = ET.SubElement(properties, "Property", name="Title", type="App::PropertyString")
        ET.SubElement(regular, "String", value="Independent synthetic fixture")
        self.seed(archive(ET.tostring(root)))
        receipt = self.freeze()
        worker.verify_frozen_input(receipt)
        worker.cleanup_frozen_input(receipt)

        def changed(change):
            copied = ET.fromstring(ET.tostring(root))
            change(copied.find("Properties"))
            return archive(ET.tostring(copied))
        invalid = [
            lambda p: p.set("Count", "3"),
            lambda p: p.set("TransientCount", "1"),
            lambda p: p.attrib.pop("TransientCount"),
            lambda p: p[0].set("name", "Title"),
            lambda p: p[0].set("status", "-1"),
            lambda p: p[0].set("status", "1.5"),
            lambda p: p[0].set("status", str(2**64)),
            lambda p: p[0].attrib.pop("status"),
            lambda p: p[0].set("unknown", "ignored"),
            lambda p: ET.SubElement(p[0], "String", value="not transient metadata"),
            lambda p: (p.remove(p[0]), p.append(ET.Element("_Property", name="RuntimeField", type="App::PropertyString", status="1"))),
            lambda p: ET.SubElement(p, "Unknown"),
        ]
        for change in invalid:
            self.seed(changed(change))
            self.error("WORKER_INPUT_INVALID", self.freeze)
        for change in (lambda p: p[0].set("type", "App::PropertyPythonObject"),
                       lambda p: p[0].set("name", "Proxy"),
                       lambda p: p[0].set("type", "Unknown::Property")):
            self.seed(changed(change))
            self.error("WORKER_INPUT_UNSUPPORTED", self.freeze)
        self.assertEqual(list(self.staging.iterdir()), [])

    def test_unknown_schema_object_python_proxy_property_and_expression_are_refused(self):
        cases = [
            document().replace(b'SchemaVersion="4"', b'SchemaVersion="5"'),
            document().replace(b'Part::Feature', b'Part::FeaturePython'),
            document().replace(b'Part::Feature', b'App::Link'),
            document().replace(b'Part::Feature', b'Unknown::Feature'),
            document().replace(b'App::PropertyString', b'App::PropertyPythonObject'),
            document().replace(b'name="Label"', b'name="Proxy"'),
            document().replace(b'App::PropertyString', b'App::PropertyFile'),
            document().replace(b'<ExpressionEngine count="0" />',
                               b'<ExpressionEngine count="1"><Expression path="Length" expression="2+2"/></ExpressionEngine>'),
            document().replace(b'id="1"', b'id="1" ViewType="PartGui::ViewProviderPart"'),
        ]
        for xml in cases:
            with self.subTest(xml=xml[:90]):
                self.seed(archive(xml))
                self.error("WORKER_INPUT_UNSUPPORTED", self.freeze)
        self.assertEqual(list(self.staging.iterdir()), [])

    def test_closed_local_links_are_allowed_external_or_dangling_links_are_refused(self):
        objects = [("Feature", "Part::Feature",
                    prop("Target", "App::PropertyLink", '<Link value="Peer"/>'), ()),
                   ("Peer", "Part::Feature", "", ())]
        self.seed(archive(document(objects)))
        receipt = self.freeze()
        worker.cleanup_frozen_input(receipt)
        for link in ('<Link value="Missing"/>', '<Link value="Other#Peer"/>',
                     '<XLink file="Other.FCStd" name="Peer"/>'):
            self.seed(archive(document([("Feature", "Part::Feature",
                           prop("Target", "App::PropertyLink", link), ())])))
            self.error("WORKER_INPUT_UNSUPPORTED", self.freeze)

    def test_shape_internal_resources_are_bound_to_archive_members(self):
        xml = document().replace(b'Feature.Shape.brp', b'Shapes/Feature.bin')
        self.seed(archive(xml, extra=[("Shapes/Feature.bin", b"native carrier placeholder"),
                                     ("Shapes/Feature.bin.Table", b"table placeholder")]))
        receipt = self.freeze()
        worker.cleanup_frozen_input(receipt)
        for resource in (b"Missing.brp", b"../outside.brp", b"/outside.brp"):
            self.seed(archive(document().replace(b'Feature.Shape.brp', resource)))
            expected = "WORKER_INPUT_INVALID" if b"/" in resource else "WORKER_INPUT_UNSUPPORTED"
            self.error(expected, self.freeze)

    def test_body_sketch_pad_pocket_and_origin_nodes_native_extensions_profile(self):
        objects = [("Body", "PartDesign::Body",
                    prop("Group", "App::PropertyLinkList", '<LinkList count="3"><Link value="Sketch"/><Link value="Pad"/><Link value="Pocket"/></LinkList>'),
                    ("App::OriginGroupExtension",)),
                   ("Origin", "App::Origin", "", ("App::GroupExtension",)),
                   ("Sketch", "Sketcher::SketchObject",
                    prop("Geometry", "Part::PropertyGeometryList",
                         '<GeometryList count="1"><Geometry type="Part::GeomLineSegment"/></GeometryList>')
                    + prop("Constraints", "Sketcher::PropertyConstraintList", '<ConstraintList count="0"/>'),
                    ("Part::AttachExtension",)),
                   ("Pad", "PartDesign::Pad",
                    prop("Profile", "App::PropertyLinkSub", '<LinkSub value="Sketch" count="0"/>')
                    + prop("Length", "App::PropertyLength", '<Float value="12"/>'),
                    ("App::SuppressibleExtension", "Part::PreviewExtension")),
                   ("Pocket", "PartDesign::Pocket",
                    prop("BaseFeature", "App::PropertyLink", '<Link value="Pad"/>'),
                    ("App::SuppressibleExtension", "Part::PreviewExtension"))]
        objects += [(name, kind, "", ()) for name, kind in (
            ("X", "App::Line"), ("Y", "App::Line"), ("Z", "App::Line"),
            ("XY", "App::Plane"), ("XZ", "App::Plane"), ("YZ", "App::Plane"),
            ("Center", "App::Point"))]
        self.seed(archive(document(objects)))
        receipt = self.freeze()
        self.assertEqual(set(receipt.object_types), worker.SUPPORTED_OBJECT_TYPES - {"Part::Feature"})
        worker.cleanup_frozen_input(receipt)
        for kind, extensions in (("Part::Feature", ("Part::AttachExtension",)),
                                 ("PartDesign::Body", ("Unknown::PythonExtension",))):
            self.seed(archive(document([("Feature", kind, "", extensions)])))
            self.error("WORKER_INPUT_UNSUPPORTED", self.freeze)

    def test_gui_proxy_extension_and_inventor_file_nodes_are_refused(self):
        camera = ("#Inventor V2.1 ascii\nOrthographicCamera { viewportMapping ADJUST_CAMERA "
                  "position 1 2 3 orientation 0 0 1 0 nearDistance 1 farDistance 100 "
                  "aspectRatio 1 focalDistance 10 height 20 }")
        self.seed(archive(gui_xml=gui(prop("Visibility", "App::PropertyBool", '<Bool value="true"/>'),
                                     camera=camera)))
        receipt = self.freeze()
        worker.cleanup_frozen_input(receipt)
        unsupported = [
            gui(prop("Proxy", "App::PropertyPythonObject", "<Python/>")),
            gui(extensions=("Unknown::ViewProviderExtension",)),
            gui(camera='File { name "/never-read" }'),
            gui(camera="OrthographicCamera { height nan }"),
            gui(camera="OrthographicCamera { height 20 height 40 }"),
        ]
        for xml in unsupported:
            self.seed(archive(gui_xml=xml))
            self.error("WORKER_INPUT_UNSUPPORTED", self.freeze)

    def test_nested_geo_extensions_unbounded_counts_and_dangling_linksublist_are_refused(self):
        cases = [
            ("WORKER_INPUT_UNSUPPORTED", prop("Geometry", "Part::PropertyGeometryList",
                '<GeometryList count="1"><Geometry type="Part::GeomLineSegment"><GeoExtensions count="1">'
                '<GeoExtension type="Unknown::NativeClass"/></GeoExtensions></Geometry></GeometryList>')),
            ("WORKER_INPUT_LIMIT", prop("Geometry", "Part::PropertyGeometryList", '<GeometryList count="2147483647"/>')),
            ("WORKER_INPUT_INVALID", prop("Geometry", "Part::PropertyGeometryList", '<GeometryList count="2"><Geometry type="Part::GeomLineSegment"/></GeometryList>')),
            ("WORKER_INPUT_UNSUPPORTED", prop("Target", "App::PropertyLinkSubList", '<LinkSubList count="1"><Link obj="Missing" sub="Face1"/></LinkSubList>')),
        ]
        for code, value in cases:
            self.seed(archive(document([("Feature", "Part::Feature", value, ())])))
            self.error(code, self.freeze)
        self.assertEqual(list(self.staging.iterdir()), [])

    def test_unknown_or_duplicate_gui_structure_is_refused(self):
        raw = gui()
        cases = [
            ("WORKER_INPUT_UNSUPPORTED", raw.replace(b"</Document>", b'<Unknown><Camera settings="File {}"/></Unknown></Document>')),
            ("WORKER_INPUT_INVALID", raw.replace(b"</Document>", b'<Camera settings=""/></Document>')),
            ("WORKER_INPUT_INVALID", raw.replace(b"</ViewProvider>", b'<Unknown/></ViewProvider>')),
        ]
        for code, value in cases:
            self.seed(archive(gui_xml=value))
            self.error(code, self.freeze)

    def test_native_gui_extensions_are_exact_empty_pairs_only(self):
        self.seed(archive(gui_xml=gui(extensions=("Gui::ViewProviderFaceTexture",))))
        receipt = self.freeze()
        worker.cleanup_frozen_input(receipt)
        self.seed(archive(gui_xml=gui(extensions=("Gui::ViewProviderFaceTexture",)).replace(
            b'name="ViewProviderFaceTexture"', b'name="Other"')))
        self.error("WORKER_INPUT_UNSUPPORTED", self.freeze)
        self.seed(archive(gui_xml=gui(extensions=("Gui::ViewProviderFaceTexture",)).replace(
            b'<Extension type="Gui::ViewProviderFaceTexture" name="ViewProviderFaceTexture" />',
            b'<Extension type="Gui::ViewProviderFaceTexture" name="ViewProviderFaceTexture"><Unknown/></Extension>')))
        self.error("WORKER_INPUT_UNSUPPORTED", self.freeze)

    def test_failure_closing_post_verify_stage_does_not_lose_cleanup_receipt(self):
        original_verify, original_close = worker.verify_frozen_input, os.close
        state, receipts = {"verified": False, "failed": False}, []
        def verified(receipt):
            result = original_verify(receipt)
            receipts.append(receipt)
            state["verified"] = True
            return result
        def failed(descriptor):
            original_close(descriptor)
            if state["verified"] and not state["failed"]:
                state["failed"] = True
                raise OSError("private post-verify close details")
        with patch.object(worker, "verify_frozen_input", side_effect=verified), \
                patch.object(worker.os, "close", side_effect=failed):
            self.error("WORKER_INPUT_IO_FAILED", self.freeze)
        self.assertTrue(state["failed"])
        self.assertEqual(list(self.staging.iterdir()), [])
        self.assertTrue(receipts[0]._cleaned)

    def test_cleanup_post_delete_close_failure_is_fixed_and_never_success(self):
        receipt = self.freeze()
        original_close, failed_once = os.close, []
        def failed(descriptor):
            original_close(descriptor)
            if not Path(receipt.staged_path).parent.exists() and not failed_once:
                failed_once.append(1)
                raise OSError("private cleanup close details")
        with patch.object(worker.os, "close", side_effect=failed):
            self.error("WORKER_INPUT_CLEANUP_UNVERIFIED", lambda: worker.cleanup_frozen_input(receipt))
        self.assertEqual(list(self.staging.iterdir()), [])

    def test_source_mutation_during_preflight_cleans_only_own_stage(self):
        original = worker._preflight
        def changed(raw):
            result = original(raw)
            self.seed(archive(extra=[("different", b"revision")]))
            return result
        with patch.object(worker, "_preflight", side_effect=changed):
            self.error("WORKER_INPUT_CHANGED", self.freeze)
        self.assertEqual(list(self.staging.iterdir()), [])

    def test_partial_write_failure_cleans_owned_file_and_directory(self):
        original, calls = os.write, []
        def failed(descriptor, value):
            calls.append(1)
            if len(calls) > 1:
                raise OSError("private failure details")
            return original(descriptor, value[:1])
        with patch.object(worker.os, "write", side_effect=failed):
            self.error("WORKER_INPUT_IO_FAILED", self.freeze)
        self.assertEqual(list(self.staging.iterdir()), [])

    def test_source_hash_detects_changes_even_if_metadata_comparison_is_forced_equal(self):
        receipt = self.freeze()
        raw = self.source.read_bytes()
        self.source.write_bytes(raw.replace(b"PK", b"PL", 1))
        with patch.object(worker, "_stamp", return_value=receipt._source_stat):
            self.error("WORKER_INPUT_CHANGED", lambda: worker.verify_frozen_input(receipt))
        worker.cleanup_frozen_input(receipt)

    def test_verify_refuses_source_change_stage_change_and_hardlink(self):
        receipt = self.freeze()
        self.source.chmod(0o600 if stat.S_IMODE(self.source.stat().st_mode) != 0o600 else 0o644)
        self.error("WORKER_INPUT_CHANGED", lambda: worker.verify_frozen_input(receipt))
        worker.cleanup_frozen_input(receipt)
        receipt = self.freeze()
        staged = Path(receipt.staged_path)
        staged.write_bytes(b"changed frozen bytes")
        self.error("WORKER_INPUT_CHANGED", lambda: worker.verify_frozen_input(receipt))
        worker.cleanup_frozen_input(receipt)
        receipt = self.freeze()
        os.link(self.source, self.folder / "source-hardlink.FCStd")
        self.error("WORKER_INPUT_CHANGED", lambda: worker.verify_frozen_input(receipt))
        worker.cleanup_frozen_input(receipt)

    def test_cleanup_refuses_replaced_file_and_retains_unknown_entries(self):
        receipt = self.freeze()
        staged = Path(receipt.staged_path)
        unknown = staged.parent / "unknown"
        unknown.write_bytes(b"must retain")
        self.error("WORKER_INPUT_CLEANUP_UNVERIFIED", lambda: worker.cleanup_frozen_input(receipt))
        self.assertTrue(staged.exists())
        self.assertEqual(unknown.read_bytes(), b"must retain")
        unknown.unlink()
        original = staged.with_name("owned-original")
        staged.rename(original)
        staged.write_bytes(b"unowned replacement")
        original.unlink()
        self.error("WORKER_INPUT_CHANGED", lambda: worker.verify_frozen_input(receipt))
        self.error("WORKER_INPUT_CLEANUP_UNVERIFIED", lambda: worker.cleanup_frozen_input(receipt))
        self.assertEqual(staged.read_bytes(), b"unowned replacement")

    def test_cleanup_refuses_root_replacement_and_forged_receipt(self):
        receipt = self.freeze()
        previous = self.folder / "previous-staging"
        self.staging.rename(previous)
        self.staging.mkdir(mode=0o700)
        marker = self.staging / "keep"
        marker.write_bytes(b"unowned root")
        self.error("WORKER_INPUT_CLEANUP_UNVERIFIED", lambda: worker.cleanup_frozen_input(receipt))
        self.assertEqual(marker.read_bytes(), b"unowned root")
        self.assertTrue((previous / Path(receipt.staged_path).parent.name / "input.FCStd").exists())
        self.error("WORKER_INPUT_CLEANUP_UNVERIFIED", lambda: worker.cleanup_frozen_input({}))
        self.error("WORKER_INPUT_UNSAFE", lambda: worker.FrozenInput(_seal=object()))

    def test_fsync_failure_does_not_return_success_and_cleans_new_owned_stage(self):
        original, calls = os.fsync, []
        def once(descriptor):
            calls.append(1)
            if len(calls) == 1:
                raise OSError("private fsync details")
            return original(descriptor)
        with patch.object(worker.os, "fsync", side_effect=once):
            self.error("WORKER_INPUT_IO_FAILED", self.freeze)
        self.assertEqual(list(self.staging.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
