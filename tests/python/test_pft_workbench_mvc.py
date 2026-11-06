# SPDX-License-Identifier: Apache-2.0
"""Prevent GUI dependencies and document writes from leaking across MVC boundaries."""
import ast
from pathlib import Path
import unittest

ADDON = Path(__file__).resolve().parents[2] / "addon" / "PhoenixFreeCADTools"


def parsed(name):
    return ast.parse((ADDON / (name + ".py")).read_text())


def imported_modules(tree):
    modules = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            modules.append(node.module or "")
    return modules


class MvcBoundaryTests(unittest.TestCase):
    def test_model_and_controller_import_without_qt_or_freecad_dependencies(self):
        for name in ("pft_workbench_model", "pft_workbench_controller"):
            tree = parsed(name)
            self.assertTrue(ast.get_docstring(tree))
            imports = imported_modules(tree)
            self.assertFalse(any(module.startswith(("PySide", "FreeCAD", "pft_workbench_views", "pft_workbench_tools")) for module in imports), imports)
        self.assertNotIn("pft_workbench_controller", imported_modules(parsed("pft_workbench_model")))

    def test_presentation_preferences_have_no_qt_or_document_dependencies(self):
        tree = parsed("pft_workbench_settings")
        self.assertFalse(any(module.startswith(("PySide", "FreeCAD", "pft_workbench_views"))
                             for module in imported_modules(tree)))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                self.assertNotIn(node.attr, {"ActiveDocument", "getDocument", "listDocuments", "Shape"})

    def test_view_has_no_direct_document_mutations_or_freecad_imports(self):
        tree = parsed("pft_workbench_views")
        self.assertTrue(ast.get_docstring(tree))
        self.assertFalse(any(module.startswith("FreeCAD") for module in imported_modules(tree)))
        forbidden = {"addProperty", "openTransaction", "commitTransaction", "abortTransaction", "setattr", "save", "saveAs", "closeDocument"}
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                function = node.func.attr if isinstance(node.func, ast.Attribute) else node.func.id if isinstance(node.func, ast.Name) else ""
                self.assertNotIn(function, forbidden)
            if isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                self.assertFalse(any(isinstance(target, ast.Attribute) for target in targets))


if __name__ == "__main__":
    unittest.main()
