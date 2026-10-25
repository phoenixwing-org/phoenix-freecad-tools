# SPDX-License-Identifier: Apache-2.0
"""Bootstrap the regular GUI module safely when FreeCAD separates exec globals and locals."""

import importlib.util as _phoenix_import_util
import os as _phoenix_os
import sys as _phoenix_sys

try:
    _phoenix_source_file = __file__
except NameError:
    import inspect as _phoenix_inspect
    _phoenix_source_file = _phoenix_inspect.currentframe().f_code.co_filename

_phoenix_addon_dir = _phoenix_os.path.dirname(_phoenix_os.path.abspath(_phoenix_source_file))
if _phoenix_addon_dir not in _phoenix_sys.path:
    _phoenix_sys.path.insert(0, _phoenix_addon_dir)

_phoenix_module_name = "phoenix_workbench_gui"
_phoenix_module_spec = _phoenix_import_util.spec_from_file_location(
    _phoenix_module_name, _phoenix_os.path.join(_phoenix_addon_dir, "phoenix_workbench_gui.py")
)
_phoenix_gui_module = _phoenix_import_util.module_from_spec(_phoenix_module_spec)
_phoenix_gui_module._bootstrap_workbench_base = locals().get("Workbench") or globals().get("Workbench")
_phoenix_previous_module = _phoenix_sys.modules.get(_phoenix_module_name)
_phoenix_sys.modules[_phoenix_module_name] = _phoenix_gui_module
try:
    _phoenix_module_spec.loader.exec_module(_phoenix_gui_module)
except Exception:
    if _phoenix_previous_module is None:
        _phoenix_sys.modules.pop(_phoenix_module_name, None)
    else:
        _phoenix_sys.modules[_phoenix_module_name] = _phoenix_previous_module
    raise
