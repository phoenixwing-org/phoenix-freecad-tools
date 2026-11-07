# SPDX-License-Identifier: Apache-2.0
"""Development-instance presentation only; never reload CAD/service controllers.

Default QSettings constructors use private INI paths. FreeCAD's explicitly named
NativeFormat constructors retain their native backend, under a distinct owned
development domain; not every Qt settings file is placed inside the profile.
"""
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import sys
import threading

COMMAND = 'PhoenixTools_ReloadDevelopmentUI'
UI_MODULES = ('pft_panel_host', 'pft_quick_toolbar', 'pft_thumbnail_views', 'pft_document_tools_views',
              'pft_appearance_controls', 'pft_appearance_views', 'pft_chat_transcript',
              'pft_chat_views', 'pft_mcp_settings_views', 'pft_workbench_views', 'pft_desk_views')
_SOURCE = Path(__file__).resolve().parent
_PASSIVE_DOCKS = frozenset(('PhoenixTools_DocumentToolsDock', 'PhoenixTools_ChatConnectionDialog'))


class PftDevelopmentError(RuntimeError):
    pass


def _enabled():
    if os.environ.get('PFT_DEVELOPMENT') != '1':
        raise PftDevelopmentError('此命令仅适用于独立开发实例。')


def _same_source(name, module):
    if Path(getattr(module, '__file__', '')).resolve() != _SOURCE.resolve() / (name + '.py'):
        raise PftDevelopmentError('已加载模块来自不同插件目录，请重启开发实例。')


def _owned_profile():
    _enabled()
    profile = Path(os.environ['PFT_DEVELOPMENT_PROFILE'])
    info = profile.lstat()
    if (profile.is_symlink() or not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o700):
        raise PftDevelopmentError('开发profile归属无法确认。')
    marker_file = profile / '.phoenix-development-profile.json'
    info = marker_file.lstat()
    if (not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) & 0o077
            or info.st_uid != os.getuid() or info.st_size > 4096):
        raise PftDevelopmentError('开发profile标记无法确认。')
    marker = json.loads(marker_file.read_text(encoding='utf-8'))
    if (marker.get('owner') != 'phoenix-freecad-tools-development' or marker.get('uid') != os.getuid()
            or marker.get('source') != str(_SOURCE)
            or marker.get('owner_id') != os.environ['PFT_DEVELOPMENT_OWNER_ID']):
        raise PftDevelopmentError('开发profile与当前源码不匹配。')
    return profile, marker


def _qt_enum(owner, group, name):
    value = getattr(owner, name, None)
    return value if value is not None else getattr(getattr(owner, group), name)


def configure_bootstrap(app, gui):
    profile, marker = _owned_profile()
    from PySide import QtCore
    vendor = 'org.phoenix.freecadtools.development.' + marker['owner_id'].replace('-', '')
    application = 'PhoenixFreeCADDevelopment'
    # MainWindow's explicit C++ QSettings constructor ignores setDefaultFormat.
    # Keep both initial startup and subsequent constructor calls in a separate
    # development native domain, without persisting any normal preferences.
    app.ConfigSet('ExeVendor', vendor)
    app.ConfigSet('ExeName', application)
    if app.ConfigGet('ExeVendor') != vendor or app.ConfigGet('ExeName') != application:
        raise PftDevelopmentError('开发 Qt 命名域未生效，请重启开发实例。')
    QtCore.QCoreApplication.setOrganizationName(vendor)
    QtCore.QCoreApplication.setOrganizationDomain(vendor)
    QtCore.QCoreApplication.setApplicationName(application)
    for relative in ('config', 'config/qt', 'config/qt-system'):
        folder = profile / relative
        info = folder.lstat()
        if (folder.is_symlink() or not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) != 0o700):
            raise PftDevelopmentError('开发 Qt 配置路径归属无法确认，请使用开发启动器重新准备。')
    settings = QtCore.QSettings
    format_ = _qt_enum(settings, 'Format', 'IniFormat')
    settings.setPath(format_, _qt_enum(settings, 'Scope', 'UserScope'), str(profile / 'config' / 'qt'))
    settings.setPath(format_, _qt_enum(settings, 'Scope', 'SystemScope'), str(profile / 'config' / 'qt-system'))
    settings.setDefaultFormat(format_)
    window = gui.getMainWindow()
    if window is not None:
        # UI properties only. Do not rewrite/persist any preference.
        window.setProperty('PhoenixTools_AutoStartSuppressed', True)
        window.setProperty('PhoenixTools_AutoStartChecked', True)


def _check_idle(app, gui, widgets):
    _enabled()
    if threading.current_thread() is not threading.main_thread():
        raise PftDevelopmentError('开发界面重载必须在 GUI 主线程执行。')
    if app.getActiveTransaction() is not None:
        raise PftDevelopmentError('有未完成事务，请自行完成或取消后重载。')
    documents = app.listDocuments()
    if type(documents) is not dict:
        raise PftDevelopmentError('无法核验文档状态，请重启开发实例。')
    for name, document in documents.items():
        if document.HasPendingTransaction is not False or gui.getDocument(name).getInEdit() is not None:
            raise PftDevelopmentError('有未完成事务或编辑状态，请自行结束后重载。')
    bridge = sys.modules.get('pft_bridge')
    if bridge is not None:
        _same_source('pft_bridge', bridge)
    businesses = [sys.modules.get('pft_business'), getattr(bridge, '_business', None)]
    for business in {id(value): value for value in businesses if value is not None}.values():
        _same_source('pft_business', business)
        with business._lock:
            if any(record.get('state') in ('prepared', 'awaiting_confirmation', 'applying')
                   for record in business._plans.values()):
                raise PftDevelopmentError('存在零件或图纸预览，请先自行完成或取消。')
    runtimes = [sys.modules.get('pft_runtime'), getattr(bridge, '_runtime', None)]
    for runtime in {id(value): value for value in runtimes if value is not None}.values():
        _same_source('pft_runtime', runtime)
        with runtime._lock:
            if any(record.get('state') in ('queued', 'starting', 'running') for record in runtime._scripts.values()):
                raise PftDevelopmentError('有正在执行的工具请求，请等待完成后重载。')
    window = gui.getMainWindow()
    children = window.findChildren(widgets.QWidget)
    docks = []
    for child in children:
        if getattr(child, '_pft_session', None) is not None:
            if child._pft_session.state in ('ready', 'previewing'):
                raise PftDevelopmentError('有外观预览，请先自行确定或取消。')
        if isinstance(child, widgets.QDialog) and child.isVisible():
            raise PftDevelopmentError('有打开的对话框，请自行关闭后重载。')
        if child.objectName() == 'PhoenixTools_ChatConnectionDialog':
            controller, process = getattr(child, '_controller', None), getattr(child, '_process', None)
            if (controller is None or process is None or getattr(controller, '_connected', True)
                    or getattr(controller, '_phase', None) not in ('disconnected', 'error')
                    or getattr(process, '_session', True) is not None):
                raise PftDevelopmentError('聊天仍连接或状态未知，请自行断开后重载。')
        if isinstance(child, widgets.QDockWidget) and child.objectName() in _PASSIVE_DOCKS:
            if child.parent() is not window:
                raise PftDevelopmentError('面板归属无法确认，请重启开发实例。')
            expected = 'pft_chat_views' if child.objectName() == 'PhoenixTools_ChatConnectionDialog' else 'pft_document_tools_views'
            module = sys.modules.get(expected)
            if module is None:
                raise PftDevelopmentError('面板来源无法确认，请重启开发实例。')
            _same_source(expected, module)
            docks.append(child)
    return docks


def reload_ui(app, gui, widgets=None):
    """Explicit development command; refuses busy state without cancelling it."""
    if widgets is None:
        from PySide import QtWidgets as widgets
    docks = _check_idle(app, gui, widgets)
    prepared = []
    for name in UI_MODULES:
        previous = sys.modules.get(name)
        if previous is None:
            continue
        _same_source(name, previous)
        filename = _SOURCE / (name + '.py')
        # Catch syntax errors before any existing dock is closed.
        code = compile(filename.read_bytes(), str(filename), 'exec')
        prepared.append((name, previous, filename, code))
    for dock in docks:
        if dock.close() is False:
            raise PftDevelopmentError('面板未关闭，未重载模块。')
    loaded = []
    try:
        for name, previous, filename, code in prepared:
            spec = importlib.util.spec_from_file_location(name, filename)
            module = importlib.util.module_from_spec(spec)
            sys.modules[name] = module
            loaded.append((name, previous))
            exec(code, module.__dict__)
            _same_source(name, module)
    except Exception:
        for name, previous in reversed(loaded):
            sys.modules[name] = previous
        raise PftDevelopmentError('界面重载失败；已恢复模块注册，请重启开发实例核对。') from None
    return {'reloaded': [name for name, *_ in prepared], 'closed_passive_docks': len(docks),
            'restart_required_for': ['bridge', 'protocol', 'controllers', 'transactions']}


def register(app, gui, workbench_class):
    if os.environ.get('PFT_DEVELOPMENT') != '1':
        return False
    configure_bootstrap(app, gui)
    if getattr(workbench_class, '_pft_development_registered', False):
        return True
    class ReloadCommand:
        def GetResources(self):
            return {'MenuText': '重载开发界面', 'ToolTip': '仅重载被动界面；桥接、协议、事务与控制器改动须重启此开发实例。', 'CmdType': 'NoTransaction'}
        def IsActive(self):
            return os.environ.get('PFT_DEVELOPMENT') == '1'
        def Activated(self):
            try:
                result = reload_ui(app, gui)
                app.Console.PrintMessage('[Phoenix Development] 已重载 %d 个界面模块。\n' % len(result['reloaded']))
            except PftDevelopmentError as error:
                app.Console.PrintError('[Phoenix Development] %s\n' % error)
            except Exception:
                app.Console.PrintError('[Phoenix Development] 无法确认开发界面状态，请重启开发实例。\n')
    gui.addCommand(COMMAND, ReloadCommand())
    if not getattr(workbench_class, '_pft_development_registered', False):
        original = workbench_class.Initialize
        def initialize(self):
            original(self)
            if getattr(self, '_pft_development_ui_initialized', False):
                return
            self.appendMenu('Phoenix', [COMMAND])
            self.appendToolbar('Phoenix 开发', [COMMAND])
            self._pft_development_ui_initialized = True
        workbench_class.Initialize = initialize
        workbench_class._pft_development_registered = True
    return True


def write_readiness(app, gui):
    """Startup macro: activate UI, label the window and write one owned receipt."""
    profile, marker = _owned_profile()
    module_paths = {}
    for name in ('pft_development', 'pft_workbench_gui', 'pft_bridge'):
        module = sys.modules.get(name)
        if module is None:
            raise PftDevelopmentError('开发插件模块未加载。')
        _same_source(name, module)
        module_paths[name] = str(Path(module.__file__).resolve())
    configure_bootstrap(app, gui)
    gui.activateWorkbench('PftWorkbench')
    window = gui.getMainWindow()
    version = getattr(sys.modules['pft_bridge'], 'ADDON_VERSION', None)
    if type(version) is not str or re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?', version) is None:
        raise PftDevelopmentError('无法确认开发插件版本。')
    purpose = os.environ.get('PFT_DEVELOPMENT_PURPOSE', '通用').strip()
    if (not purpose or len(purpose) > 80 or any(ord(char) < 32 or ord(char) == 127 or char in '|｜' for char in purpose)):
        raise PftDevelopmentError('开发用途不符合标题格式。')
    label = 'Phoenix ' + version + ' 开发｜' + purpose
    port = os.environ.get('PFT_DEVELOPMENT_PORT', '')
    if port.isascii() and port.isdecimal() and 1024 <= int(port) <= 65535:
        label += '｜' + port
    from PySide import QtCore
    receiver = getattr(window, '_pft_development_title_receiver', None)
    if receiver is None:
        class _PftDevelopmentTitleReceiver(QtCore.QObject):
            def __init__(self):
                super().__init__(window)
                self.prefix = ''

            @QtCore.Slot(str)
            def update(self, native_title):
                # Preserve the native document/modified title for display only;
                # never put it in the readiness receipt or other diagnostics.
                if not native_title.startswith(self.prefix):
                    window.setWindowTitle(self.prefix + native_title)

        receiver = _PftDevelopmentTitleReceiver()
        window.windowTitleChanged.connect(receiver.update)
        window._pft_development_title_receiver = receiver
    receiver.prefix = label + ' — '
    receiver.update(window.windowTitle())
    window.showNormal()
    window.raise_()
    window.activateWindow()
    from PySide import QtGui
    platform = str(QtGui.QGuiApplication.platformName())
    visible = window.isVisible() is True
    receipt = Path(os.environ['PFT_DEVELOPMENT_RECEIPT'])
    runtime = profile / 'runtime'
    runtime_info = runtime.lstat()
    if (runtime.is_symlink() or not stat.S_ISDIR(runtime_info.st_mode)
            or runtime_info.st_uid != os.getuid() or stat.S_IMODE(runtime_info.st_mode) != 0o700
            or receipt.parent != runtime or not receipt.name.startswith('readiness-')
            or receipt.suffix != '.json'):
        raise PftDevelopmentError('开发就绪记录路径不符合归属约定。')
    payload = {'schema_version': 1, 'owner_id': marker['owner_id'], 'pid': os.getpid(),
               'ready': visible, 'window_visible': visible, 'qt_platform': platform,
               'workbench': 'PftWorkbench', 'module_paths': module_paths,
               'automatic_service_start': False, 'addon_version': version, 'purpose': purpose}
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0)
    with os.fdopen(os.open(str(receipt), flags, 0o600), 'w', encoding='utf-8') as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    return payload
