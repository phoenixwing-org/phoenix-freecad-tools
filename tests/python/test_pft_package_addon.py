# SPDX-License-Identifier: Apache-2.0
"""Check distributed bytes, extraction/loading, and refusal paths without FreeCAD."""
from hashlib import sha256
import importlib.util
from io import BytesIO
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("package_addon", ROOT / "scripts/package-addon.py")
packager = importlib.util.module_from_spec(spec)
spec.loader.exec_module(packager)


class AddonPackageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="phoenix-package-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "source"
        for name in ("package.json", "package-lock.json", "src/server.ts", *packager.DOCUMENTS.values()):
            self.copy(name)
        for name in packager.ADDON_FILES:
            self.copy("addon/PhoenixFreeCADTools/" + name)

    def copy(self, name):
        target = self.root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, target)

    def archive(self):
        version, data = packager.build_archive(self.root)
        self.assertEqual(version, "0.3.2")
        return data, zipfile.ZipFile(BytesIO(data))

    def test_archive_covers_every_shipped_module_and_contains_verifiable_legal_files(self):
        source = ROOT / "addon/PhoenixFreeCADTools"
        required = {p.relative_to(source).as_posix() for p in source.rglob("*") if p.is_file() and "__pycache__" not in p.parts}
        self.assertEqual(set(packager.ADDON_FILES), required)
        _, archive = self.archive()
        with archive:
            names = archive.namelist()
            self.assertTrue(all(name.startswith("PhoenixFreeCADTools/") for name in names))
            self.assertEqual(len(names), len(set(names)))
            manifest = json.loads(archive.read("PhoenixFreeCADTools/MANIFEST.json"))
            self.assertEqual(manifest["version"], "0.3.2")
            self.assertEqual(set(names), {"PhoenixFreeCADTools/" + name for name in manifest["files"]} | {"PhoenixFreeCADTools/MANIFEST.json"})
            for name, digest in manifest["files"].items():
                self.assertEqual(sha256(archive.read("PhoenixFreeCADTools/" + name)).hexdigest(), digest)
            for name in ("LICENSE", "NOTICE"):
                self.assertEqual(archive.read("PhoenixFreeCADTools/" + name), (ROOT / packager.DOCUMENTS[name]).read_bytes())
            self.assertIn(b"Phoenix Tools", archive.read("PhoenixFreeCADTools/README.md"))
            for entry in archive.infolist():
                self.assertEqual(entry.date_time, (1980, 1, 1, 0, 0, 0))
                self.assertTrue(stat.S_ISREG(entry.external_attr >> 16))
                self.assertFalse(entry.filename.startswith("/"))
                self.assertNotIn("..", Path(entry.filename).parts)

    def test_unlisted_private_files_and_caches_never_change_package(self):
        before, _ = self.archive()
        addon = self.root / "addon/PhoenixFreeCADTools"
        for relative in (".env", ".phoenix-freecad-tools-install.json", ".phoenix-freecad-mcp-install.json", "user_extra.py", "__pycache__/InitGui.pyc", "production.FCStd", "session.log"):
            target = addon / relative
            target.parent.mkdir(exist_ok=True)
            target.write_text("synthetic private fixture - must not be packaged")
        os.utime(addon / "InitGui.py", (1, 1))
        after, _ = self.archive()
        self.assertEqual(before, after)

    def test_source_symlinks_and_parent_symlinks_are_refused(self):
        addon = self.root / "addon/PhoenixFreeCADTools"
        original = addon / "InitGui.py"
        saved = addon / "other.py"
        original.rename(saved)
        original.symlink_to(saved)
        with self.assertRaisesRegex(ValueError, "symlink"):
            packager.build_archive(self.root)
        original.unlink()
        saved.rename(original)
        resources = addon / "Resources"
        external = self.root / "external-resources"
        resources.rename(external)
        resources.symlink_to(external, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "symlink"):
            packager.build_archive(self.root)

    def test_missing_module_and_version_drift_prevent_packaging(self):
        version_file = self.root / "addon/PhoenixFreeCADTools/package.xml"
        content = version_file.read_text()
        version_file.write_text(content.replace("0.3.2", "0.1.0"))
        with self.assertRaisesRegex(ValueError, "versions must agree"):
            packager.build_archive(self.root)
        version_file.write_text(content)
        (self.root / "addon/PhoenixFreeCADTools/pft_business.py").unlink()
        with self.assertRaisesRegex(ValueError, "missing"):
            packager.build_archive(self.root)

    def test_checksum_repeatability_and_existing_output_protection(self):
        output = Path(self.temporary.name) / "packages"
        filename, checksum = packager.package_addon(output, self.root)
        self.assertEqual(checksum.split()[0], sha256((output / filename).read_bytes()).hexdigest())
        self.assertEqual(packager.package_addon(output, self.root), (filename, checksum))
        (output / filename).write_bytes(b"existing user file")
        with self.assertRaisesRegex(ValueError, "retained"):
            packager.package_addon(output, self.root)
        self.assertEqual((output / filename).read_bytes(), b"existing user file")

    def test_extracted_addon_registers_workbench_without_starting_service(self):
        _, archive = self.archive()
        mod = Path(self.temporary.name) / "FreeCAD/Mod"
        with archive:
            archive.extractall(mod)
        addon = mod / "PhoenixFreeCADTools"
        result = subprocess.run([sys.executable, "-c", '''
from pathlib import Path
import sys, types
addon = Path(sys.argv[1])
registered = []
sys.modules['FreeCAD'] = types.SimpleNamespace()
sys.modules['FreeCADGui'] = types.SimpleNamespace(addWorkbench=registered.append)
bootstrap = addon / 'InitGui.py'
exec(compile(bootstrap.read_text(), str(bootstrap), 'exec'), {'__name__': 'FreeCADLoader'}, {'Workbench': type('WorkbenchBase', (), {})})
assert len(registered) == 1
assert registered[0].MenuText == 'Phoenix Tools'
assert Path(registered[0].Icon).is_file()
import pft_bridge as bridge
assert bridge.get_status()['running'] is False
assert bridge.get_status()['addon_version'] == '0.3.2'
''', str(addon)], capture_output=True, text=True, timeout=15, cwd=mod)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
