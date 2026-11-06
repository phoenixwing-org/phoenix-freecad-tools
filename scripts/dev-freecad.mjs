// SPDX-License-Identifier: Apache-2.0
// A separate, explicitly owned development profile. Never installs the addon.
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { createHash, randomUUID } from 'node:crypto';
import { spawn } from 'node:child_process';
import { fileURLToPath, pathToFileURL } from 'node:url';

const REPOSITORY = fs.realpathSync(fileURLToPath(new URL('../', import.meta.url)));
const MARKER = '.phoenix-development-profile.json';
const OWNER = 'phoenix-freecad-tools-development';
const uid = () => process.getuid();
const hash = data => createHash('sha256').update(data).digest('hex');
const present = filename => { try { fs.lstatSync(filename); return true; } catch (error) { if (error.code === 'ENOENT') return false; throw error; } };

function ownedDirectory(filename) {
  const info = fs.lstatSync(filename);
  if (info.isSymbolicLink() || !info.isDirectory() || info.uid !== uid() || (info.mode & 0o777) !== 0o700) {
    throw new Error('开发目录必须为当前用户所有的真实 0700 目录。');
  }
}

function makeParents(filename) {
  if (present(filename)) {
    const info = fs.lstatSync(filename);
    if (!info.isDirectory() || info.isSymbolicLink()) throw new Error('开发目录的祖先必须是真实目录。');
    return;
  }
  makeParents(path.dirname(filename));
  fs.mkdirSync(filename, { mode: 0o700 });
}

function resolvedLocation(filename) {
  // Resolve the existing prefix without creating any missing path component.
  // A symlinked ancestor must not disguise a destination inside public source.
  const missing = [];
  let current = filename;
  while (!present(current)) {
    missing.unshift(path.basename(current));
    const parent = path.dirname(current);
    if (parent === current) throw new Error('无法核验开发profile的真实位置。');
    current = parent;
  }
  return path.join(fs.realpathSync(current), ...missing);
}

function within(parent, candidate) {
  const relative = path.relative(parent, candidate);
  return relative === '' || relative !== '..' && !relative.startsWith('..' + path.sep) && !path.isAbsolute(relative);
}

function checkOwned(filename, maximum = 1024 * 1024) {
  const info = fs.lstatSync(filename);
  if (!info.isFile() || info.isSymbolicLink() || info.uid !== uid() || (info.mode & 0o077) || info.size > maximum) {
    throw new Error('开发配置文件归属或权限不符合约定，未覆盖。');
  }
}

function readOwned(filename, maximum = 1024 * 1024) {
  checkOwned(filename, maximum);
  return fs.readFileSync(filename, 'utf8');
}

function writeNew(filename, value) {
  fs.writeFileSync(filename, value, { flag: 'wx', mode: 0o600 });
}

function replaceOwned(filename, value) {
  readOwned(filename);
  const temporary = filename + '.' + randomUUID() + '.tmp';
  writeNew(temporary, value);
  fs.renameSync(temporary, filename);
}

function json(value) { return JSON.stringify(value, null, 2) + '\n'; }

const USER_CONFIG = `<?xml version="1.0" encoding="UTF-8"?>
<FCParameters><FCParamGroup Name="Root"><FCParamGroup Name="BaseApp"><FCParamGroup Name="Preferences">
<FCParamGroup Name="General"><FCText Name="AutoloadModule">StartWorkbench</FCText></FCParamGroup>
<FCParamGroup Name="Mod"><FCParamGroup Name="PhoenixTools"><FCBool Name="AutoStartMCP" Value="0"/></FCParamGroup></FCParamGroup>
</FCParamGroup></FCParamGroup></FCParamGroup></FCParameters>
`;
const SYSTEM_CONFIG = '<?xml version="1.0" encoding="UTF-8"?><FCParameters><FCParamGroup Name="Root"/></FCParameters>\n';
const STARTUP = `# Managed Phoenix development startup; no CAD or service operations.
import FreeCAD as App
import FreeCADGui as Gui
import pft_development
pft_development.write_readiness(App, Gui)
`;

export function prepareDevelopment({ profile = path.join(os.homedir(), 'Library', 'Application Support', 'PhoenixFreeCADTools', 'Development'),
  app = '/Applications/FreeCAD.app', repository = REPOSITORY, port = 9877 } = {}) {
  if (!Number.isInteger(port) || port < 1024 || port > 65535) throw new Error('开发端口必须为 1024 至 65535 的整数。');
  const repo = fs.realpathSync(repository);
  const source = fs.realpathSync(path.join(repo, 'addon', 'PhoenixFreeCADTools'));
  if (!fs.statSync(path.join(source, 'InitGui.py')).isFile()) throw new Error('未找到开发工作台源码。');
  const root = path.resolve(profile);
  const realLocation = resolvedLocation(root);
  if (realLocation === path.parse(realLocation).root || realLocation === fs.realpathSync(os.homedir())
      || within(repo, realLocation) || within(source, realLocation)) {
    throw new Error('开发profile必须位于仓库和插件源码之外的独立专属目录；禁止使用任何源码子目录。');
  }
  const program = app.endsWith('.app') ? path.join(path.resolve(app), 'Contents', 'MacOS', 'FreeCAD') : path.resolve(app);
  fs.accessSync(program, fs.constants.X_OK);
  const fresh = !present(root);
  if (fresh) {
    makeParents(path.dirname(root));
    fs.mkdirSync(root, { mode: 0o700 });
    writeNew(path.join(root, MARKER), json({ schema_version: 1, owner: OWNER, uid: uid(),
      owner_id: randomUUID(), repository: repo, source }));
  }
  ownedDirectory(root);
  const marker = JSON.parse(readOwned(path.join(root, MARKER), 4096));
  if (marker.schema_version !== 1 || marker.owner !== OWNER || marker.uid !== uid()
      || marker.repository !== repo || marker.source !== source || typeof marker.owner_id !== 'string'
      || !/^[a-f0-9-]{36}$/.test(marker.owner_id)) throw new Error('开发profile归属或源码不匹配，未复用。');
  for (const relative of ['Mod', 'temp', 'runtime', 'config', 'config/qt', 'config/qt-system']) {
    const folder = path.join(root, relative);
    if (!present(folder)) fs.mkdirSync(folder, { mode: 0o700 });
    ownedDirectory(folder);
  }
  const link = path.join(root, 'Mod', 'PhoenixFreeCADTools');
  if (!present(link)) fs.symlinkSync(source, link, 'dir');
  else if (!fs.lstatSync(link).isSymbolicLink() || fs.realpathSync(link) !== source) throw new Error('开发Mod入口已被其他内容占用，未覆盖。');
  const macro = path.join(root, 'startup.FCMacro');
  const manifestFile = path.join(root, 'owned-manifest.json');
  const identity = { schema_version: 1, owner: OWNER, owner_id: marker.owner_id, source };
  if (fresh) {
    writeNew(macro, STARTUP);
    writeNew(manifestFile, json({ ...identity, managed_files: { 'startup.FCMacro': hash(STARTUP) },
      preserved_configuration: ['config/user.cfg', 'config/system.cfg'], addon_symlink: 'Mod/PhoenixFreeCADTools' }));
  } else {
    const manifest = JSON.parse(readOwned(manifestFile, 8192));
    if (Object.entries(identity).some(([key, value]) => manifest[key] !== value)
        || manifest.managed_files?.['startup.FCMacro'] !== hash(STARTUP)
        || hash(readOwned(macro)) !== hash(STARTUP)) throw new Error('开发宏或owned manifest已变化，未覆盖。');
  }
  for (const [relative, contents] of [['config/user.cfg', USER_CONFIG], ['config/system.cfg', SYSTEM_CONFIG]]) {
    const filename = path.join(root, relative);
    if (!present(filename)) writeNew(filename, contents);
    else checkOwned(filename); // Preserve preferences without reading their contents.
  }
  const launchId = randomUUID();
  const receipt = path.join(root, 'runtime', 'readiness-' + launchId + '.json');
  // FreeCAD's C++ QSettings(vendor, application) constructor always uses
  // NativeFormat on macOS. Its development domain is separate from normal
  // FreeCAD; only default-constructor INI settings are redirected into profile.
  const qtIdentity = { vendor: 'org.phoenix.freecadtools.development.' + marker.owner_id.replaceAll('-', ''),
    application: 'PhoenixFreeCADDevelopment' };
  const environment = {};
  for (const key of ['PATH', 'HOME', 'USER', 'LOGNAME', 'LANG', 'LC_ALL', 'LC_CTYPE', 'TZ', 'TMPDIR', 'DISPLAY']) {
    if (typeof process.env[key] === 'string') environment[key] = process.env[key];
  }
  Object.assign(environment, { FREECAD_USER_HOME: root, FREECAD_USER_DATA: root,
    FREECAD_USER_TEMP: path.join(root, 'temp'), PFT_DEVELOPMENT: '1', PFT_DEVELOPMENT_PORT: String(port),
    PFT_DEVELOPMENT_PROFILE: root, PFT_DEVELOPMENT_RECEIPT: receipt,
    PFT_DEVELOPMENT_OWNER_ID: marker.owner_id, PYTHONDONTWRITEBYTECODE: '1' });
  // --set-config is multitoken. The following named --user-cfg option ends
  // its values; the final positional macro must never be swallowed by it.
  const args = ['--set-config', 'ExeVendor=' + qtIdentity.vendor, 'ExeName=' + qtIdentity.application,
    '--user-cfg', path.join(root, 'config', 'user.cfg'),
    '--system-cfg', path.join(root, 'config', 'system.cfg'), macro];
  const launch = { ...identity, launch_id: launchId, prepared: true, launched: false,
    profile: root, program, args, env: Object.fromEntries(Object.entries(environment).filter(([key]) => key.startsWith('FREECAD_') || key.startsWith('PFT_'))),
    readiness_receipt: receipt, process_record: path.join(root, 'runtime', 'instance.json'),
    qt_identity: qtIdentity, qt_settings_scope: 'development_native_domain_and_private_default_ini',
    cwd: root, shell: false, detached: true };
  const launchFile = path.join(root, 'launch.json');
  if (present(launchFile)) {
    const previous = JSON.parse(readOwned(launchFile));
    if (Object.entries(identity).some(([key, value]) => previous[key] !== value)) throw new Error('启动清单不属于此开发profile，未覆盖。');
    replaceOwned(launchFile, json(launch));
  } else writeNew(launchFile, json(launch));
  return { launch, environment };
}

export async function launchDevelopment(prepared) {
  const { launch, environment } = prepared;
  const record = launch.process_record;
  const lock = path.join(launch.profile, 'runtime', 'launch.lock');
  try { writeNew(lock, json({ owner_id: launch.owner_id, launcher_pid: process.pid })); }
  catch (error) {
    if (error.code === 'EEXIST') {
      throw new Error('开发启动锁已存在，未启动新实例。请核对该profile的runtime/launch.lock与instance.json；确认相关开发进程均已退出后，再人工移除遗留启动锁。不会自动删除未知锁。');
    }
    throw error;
  }
  const lockIdentity = fs.lstatSync(lock);
  let child, spawned = false, recorded = false;
  try {
    // The same exclusive lock covers the current PID recheck, spawn and record.
    // A launcher that waited before acquiring it must observe the latest record.
    if (present(record)) {
      const previous = JSON.parse(readOwned(record, 8192));
      if (previous.owner_id !== launch.owner_id || previous.source !== launch.source || !Number.isInteger(previous.pid) || previous.pid <= 0) {
        throw new Error('开发进程记录不符合归属约定，未启动。');
      }
      try { process.kill(previous.pid, 0); throw new Error('此开发profile已有存活进程，未启动重复实例。'); }
      catch (error) { if (error.code !== 'ESRCH') throw error; }
    }
    child = spawn(launch.program, launch.args, { cwd: launch.cwd, env: environment,
      shell: false, detached: true, stdio: 'ignore' });
    await new Promise((resolve, reject) => { child.once('spawn', resolve); child.once('error', reject); });
    spawned = true;
    child.unref();
    const state = { owner: OWNER, owner_id: launch.owner_id, source: launch.source,
      launch_id: launch.launch_id, pid: child.pid, program: launch.program, started_at: new Date().toISOString() };
    try {
      if (present(record)) replaceOwned(record, json(state)); else writeNew(record, json(state));
      recorded = true;
    } catch {
      throw new Error('开发进程已启动（PID ' + child.pid + '），但归属记录保存失败；保留启动锁，请先核验该开发进程，不要直接重试。');
    }
    return { ...launch, launched: true, pid: child.pid };
  } finally {
    // If a process exists without its record, retain the lock to prevent an
    // unsafe retry. Otherwise delete only this call's exact inode. Never stop it.
    if (!spawned || recorded) {
      try {
        const current = fs.lstatSync(lock);
        if (current.dev === lockIdentity.dev && current.ino === lockIdentity.ino) fs.unlinkSync(lock);
      } catch (error) { if (error.code !== 'ENOENT') throw error; }
    }
  }
}

export async function main(argv = process.argv.slice(2)) {
  const options = {};
  let prepareOnly = false;
  for (let index = 0; index < argv.length; index++) {
    const arg = argv[index];
    if (arg === '--prepare') prepareOnly = true;
    else if (['--profile', '--app', '--port'].includes(arg) && argv[index + 1]) {
      const value = argv[++index];
      options[arg.slice(2)] = arg === '--port' ? Number(value) : value;
    } else throw new Error('用法：node scripts/dev-freecad.mjs [--prepare] [--profile 专属目录] [--app FreeCAD.app] [--port 9877]');
  }
  const prepared = prepareDevelopment(options);
  const result = prepareOnly ? prepared.launch : await launchDevelopment(prepared);
  process.stdout.write(json(result));
  return result;
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  main().catch(error => { process.stderr.write(String(error.message) + '\n'); process.exitCode = 1; });
}
