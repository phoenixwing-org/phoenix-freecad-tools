# SPDX-License-Identifier: Apache-2.0
"""Bootstrap the regular GUI module safely when FreeCAD separates exec globals and locals."""

import importlib.util as _pft_import_util
import os as _pft_os
import sys as _pft_sys

try:
    _pft_source_file = __file__
except NameError:
    import inspect as _pft_inspect
    _pft_source_file = _pft_inspect.currentframe().f_code.co_filename

_pft_addon_dir = _pft_os.path.dirname(_pft_os.path.abspath(_pft_source_file))
if _pft_addon_dir not in _pft_sys.path:
    _pft_sys.path.insert(0, _pft_addon_dir)

_pft_module_name = "pft_workbench_gui"
_pft_module_spec = _pft_import_util.spec_from_file_location(
    _pft_module_name, _pft_os.path.join(_pft_addon_dir, "pft_workbench_gui.py")
)
_pft_gui_module = _pft_import_util.module_from_spec(_pft_module_spec)
_pft_gui_module._bootstrap_workbench_base = locals().get("Workbench") or globals().get("Workbench")
_pft_previous_module = _pft_sys.modules.get(_pft_module_name)
_pft_sys.modules[_pft_module_name] = _pft_gui_module
try:
    _pft_module_spec.loader.exec_module(_pft_gui_module)
except Exception:
    if _pft_previous_module is None:
        _pft_sys.modules.pop(_pft_module_name, None)
    else:
        _pft_sys.modules[_pft_module_name] = _pft_previous_module
    raise
