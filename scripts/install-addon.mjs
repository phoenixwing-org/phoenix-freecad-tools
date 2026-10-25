// SPDX-License-Identifier: Apache-2.0
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { createHash, randomUUID } from 'node:crypto';
import { fileURLToPath, pathToFileURL } from 'node:url';

export const ADDON_NAME = 'PhoenixFreeCADMCP';
// Recognized only to migrate an owned installation, never as a runtime alias.
const LEGACY_ADDON_NAME = 'PhoenixMCP';
export const INSTALL_MARKER = '.phoenix-freecad-mcp-install.json';
const OWNER = 'phoenix-freecad-mcp';
const packageRoot = fileURLToPath(new URL('../', import.meta.url));
const defaultSource = path.join(packageRoot, 'addon', ADDON_NAME);
const present = (filename) => { try { fs.lstatSync(filename); return true; } catch (error) { if (error.code === 'ENOENT') return false; throw error; } };
const same = (left, right) => JSON.stringify(left) === JSON.stringify(right);
const safeRelative = (value) => typeof value === 'string' && !!value && !value.includes('\\') && !value.startsWith('/')
  && !/^[A-Za-z]:/.test(value) && !/[\u0000-\u001f\u007f]/u.test(value) && value.split('/').every((part) => !!part && part !== '.' && part !== '..');

function realDirectory(filename) {
  const stat = fs.lstatSync(filename);
  if (!stat.isDirectory() || stat.isSymbolicLink()) throw new Error('拒绝非目录或符号链接：' + filename);
  return fs.realpathSync(filename);
}

/** Snapshot every file and directory; never follow symlinks. Only regular
 * Python bytecode directly inside __pycache__ is ignored, and backups retain it. */
function snapshot(root) {
  realDirectory(root);
  const files = Object.create(null), directories = [];
  function walk(folder, prefix = '') {
    for (const name of fs.readdirSync(folder).sort()) {
      const relative = prefix ? prefix + '/' + name : name;
      if (!safeRelative(relative)) throw new Error('插件包含不安全路径。');
      const filename = path.join(folder, name), stat = fs.lstatSync(filename);
      if (stat.isSymbolicLink()) throw new Error('插件包含符号链接，保留原目录：' + relative);
      if (relative === INSTALL_MARKER) { if (!stat.isFile()) throw new Error('安装标记不是普通文件。'); continue; }
      if (stat.isDirectory() && name === '__pycache__') {
        for (const cached of fs.readdirSync(filename)) {
          const cachedStat = fs.lstatSync(path.join(filename, cached));
          if (!cachedStat.isFile() || cachedStat.isSymbolicLink() || !cached.endsWith('.pyc')) {
            throw new Error('__pycache__ 中存在非生成文件，保留原目录：' + relative + '/' + cached);
          }
        }
        continue;
      }
      if (stat.isDirectory()) { directories.push(relative); walk(filename, relative); }
      else if (stat.isFile()) files[relative] = createHash('sha256').update(fs.readFileSync(filename)).digest('hex');
      else throw new Error('插件包含非普通文件，保留原目录：' + relative);
    }
  }
  walk(root);
  return { files: Object.fromEntries(Object.entries(files).sort(([a], [b]) => a.localeCompare(b))), directories: directories.sort() };
}

function readOwned(root, expectedAddon = ADDON_NAME) {
  realDirectory(root);
  const filename = path.join(root, INSTALL_MARKER);
  if (!present(filename) || fs.lstatSync(filename).isSymbolicLink() || !fs.lstatSync(filename).isFile()) {
    throw new Error('已有 ' + expectedAddon + ' 无本项目安装标记；保留原目录。');
  }
  let marker;
  try { if (fs.statSync(filename).size > 1024 * 1024) throw new Error(); marker = JSON.parse(fs.readFileSync(filename, 'utf8')); }
  catch { throw new Error('安装标记无效；保留原目录。'); }
  if (marker?.owner !== OWNER || marker.schema_version !== 1 || marker.addon !== expectedAddon
    || !marker.files || typeof marker.files !== 'object' || Array.isArray(marker.files) || !Array.isArray(marker.directories)
    || !Object.keys(marker.files).length || Object.entries(marker.files).some(([name, hash]) => !safeRelative(name) || name === INSTALL_MARKER || typeof hash !== 'string' || !/^[a-f0-9]{64}$/.test(hash))
    || marker.directories.some((name) => !safeRelative(name)) || new Set(marker.directories).size !== marker.directories.length) {
    throw new Error('安装标记不属于本项目或清单无效；保留原目录。');
  }
  const expected = { files: Object.fromEntries(Object.entries(marker.files).sort(([a], [b]) => a.localeCompare(b))), directories: [...marker.directories].sort() };
  if (!same(snapshot(root), expected)) throw new Error('插件内有用户新增、删除或修改的内容；保留原目录，请先备份并核对。');
  return { marker, expected };
}

export function modDirectoryCandidates(platform = process.platform, home = os.homedir(), env = process.env) {
  if (platform === 'darwin') return [path.join(home, 'Library/Application Support/FreeCAD/v1-1/Mod'), path.join(home, 'Library/Application Support/FreeCAD/Mod')];
  if (platform === 'win32') {
    const root = env.APPDATA || path.join(home, 'AppData', 'Roaming');
    return [path.join(root, 'FreeCAD/v1-1/Mod'), path.join(root, 'FreeCAD/Mod')];
  }
  return [path.join(home, '.local/share/FreeCAD/v1-1/Mod'), path.join(home, '.local/share/FreeCAD/Mod'), path.join(home, '.FreeCAD/Mod')];
}

export function resolveModDirectory(explicit, options = {}) {
  if (explicit !== undefined) {
    if (typeof explicit !== 'string' || !explicit.trim() || /[\u0000-\u001f\u007f]/u.test(explicit)) throw new Error('--mod-dir 需要有效目录。');
    return path.resolve(explicit);
  }
  const platform = options.platform || process.platform;
  const candidates = modDirectoryCandidates(platform, options.home || os.homedir(), options.env || process.env);
  if (platform === 'darwin' && present(candidates[0])) return realDirectory(candidates[0]);
  throw new Error('未自动选择 FreeCAD Mod 目录。请传 --mod-dir；候选目录（请在 FreeCAD 中核实）：\n' + candidates.map((item) => '  ' + item).join('\n'));
}

function context(modDir) {
  const resolved = resolveModDirectory(modDir);
  if (present(resolved)) realDirectory(resolved);
  const target = path.join(resolved, ADDON_NAME);
  return { modDir: resolved, target };
}

function storageDirectory(modDir) {
  const folder = path.join(path.dirname(modDir), '.phoenix-freecad-mcp');
  if (present(folder)) realDirectory(folder);
  else fs.mkdirSync(folder, { mode: 0o700 });
  return folder;
}

const uniqueName = (prefix) => prefix + '-' + new Date().toISOString().replace(/[:.]/g, '-') + '-' + randomUUID();

export function installAddon({ modDir, sourceDir = defaultSource, dryRun = false } = {}) {
  const ctx = context(modDir), legacyTarget = path.join(ctx.modDir, LEGACY_ADDON_NAME);
  if (present(ctx.target) && present(legacyTarget)) throw new Error('新旧插件目录同时存在；保留两者，请先核对安装。');
  const source = realDirectory(sourceDir);
  if (present(path.join(source, INSTALL_MARKER))) throw new Error('源码目录含安装标记，请使用干净的 addon/' + ADDON_NAME + ' 源码。');
  const desired = snapshot(source);
  if (!desired.files['InitGui.py']) throw new Error('插件源码缺少 InitGui.py。');
  const migrating = present(legacyTarget);
  const previousTarget = migrating ? legacyTarget : ctx.target;
  const previousName = migrating ? LEGACY_ADDON_NAME : ADDON_NAME;
  let old;
  if (present(previousTarget)) {
    old = readOwned(previousTarget, previousName);
    if (!migrating && same(old.expected, desired)) {
      if (present(legacyTarget)) throw new Error('新旧插件目录同时存在；保留两者，请先核对安装。');
      return { ...ctx, action: 'unchanged', dryRun };
    }
  }
  const action = migrating ? 'migrate' : old ? 'update' : 'install';
  const migration = migrating ? { migratedFrom: legacyTarget } : {};
  if (dryRun) return { ...ctx, ...migration, action, dryRun: true, backup: old ? '更新前将在 Mod 目录之外备份旧版本。' : null };
  fs.mkdirSync(ctx.modDir, { recursive: true });
  realDirectory(ctx.modDir);
  const storage = storageDirectory(ctx.modDir), stage = path.join(storage, uniqueName('stage'));
  let backup;
  fs.mkdirSync(stage, { mode: 0o700 });
  try {
    for (const directory of desired.directories) fs.mkdirSync(path.join(stage, directory), { recursive: true });
    for (const relative of Object.keys(desired.files)) {
      const filename = path.join(stage, relative);
      fs.mkdirSync(path.dirname(filename), { recursive: true });
      fs.copyFileSync(path.join(source, relative), filename, fs.constants.COPYFILE_EXCL);
    }
    if (!same(snapshot(stage), desired)) throw new Error('复制期间源码变化；未替换插件。');
    const marker = { schema_version: 1, owner: OWNER, addon: ADDON_NAME, installed_at: new Date().toISOString(), ...desired };
    fs.writeFileSync(path.join(stage, INSTALL_MARKER), JSON.stringify(marker, null, 2) + '\n', { flag: 'wx', mode: 0o600 });
    if (old) {
      if (present(ctx.target) && present(legacyTarget)) throw new Error('安装期间新旧目录同时出现；保留两者。');
      const current = readOwned(previousTarget, previousName);
      if (!same(current.expected, old.expected) || !same(current.marker, old.marker)) throw new Error('安装期间旧插件已变化；保留原目录。');
      backup = path.join(storage, uniqueName('backup-' + previousName));
      fs.renameSync(previousTarget, backup);
      try { readOwned(backup, previousName); }
      catch (error) { fs.renameSync(backup, previousTarget); backup = undefined; throw error; }
    }
    try {
      if (present(ctx.target) || present(legacyTarget)) throw new Error('安装期间目的目录已出现；保留现有目录。');
      fs.renameSync(stage, ctx.target);
    } catch (error) {
      if (backup && !present(previousTarget)) { fs.renameSync(backup, previousTarget); backup = undefined; }
      throw error;
    }
    return { ...ctx, ...migration, action, dryRun: false, ...(backup ? { backup } : {}) };
  } finally {
    if (present(stage)) fs.rmSync(stage, { recursive: true });
  }
}

/** Uninstall moves the verified owned tree outside Mod. The recoverable backup
 * includes bytecode; unrelated addons are never selected or modified. */
export function uninstallAddon({ modDir, dryRun = false } = {}) {
  const ctx = context(modDir);
  if (!present(ctx.target)) return { ...ctx, action: 'absent', dryRun };
  const previous = readOwned(ctx.target);
  if (dryRun) return { ...ctx, action: 'uninstall', dryRun: true, backup: '将把已核验插件移到 Mod 目录之外，保留恢复副本。' };
  const storage = storageDirectory(ctx.modDir), backup = path.join(storage, uniqueName('uninstalled-' + ADDON_NAME));
  const current = readOwned(ctx.target);
  if (!same(previous.marker, current.marker)) throw new Error('卸载期间插件变化；保留原目录。');
  fs.renameSync(ctx.target, backup);
  try { readOwned(backup); }
  catch (error) { if (!present(ctx.target)) fs.renameSync(backup, ctx.target); throw error; }
  return { ...ctx, action: 'uninstall', dryRun: false, backup };
}

export function addonCli(operation, args = process.argv.slice(2)) {
  try {
    let modDir, dryRun = false;
    for (let i = 0; i < args.length; i++) {
      const argument = args[i];
      if (argument === '--help' || argument === '-h') {
        console.log(`用法：node scripts/${operation === 'install' ? 'install' : 'uninstall'}-addon.mjs [--mod-dir /absolute/FreeCAD/Mod] [--dry-run]\n默认仅自动使用 macOS 已存在的 FreeCAD/v1-1/Mod。备份位于 Mod 目录外的 .phoenix-freecad-mcp。`);
        return;
      }
      if (argument === '--dry-run') dryRun = true;
      else if (argument === '--mod-dir') { if (modDir !== undefined || !args[i + 1] || args[i + 1].startsWith('--')) throw new Error('--mod-dir 需要唯一目录参数。'); modDir = args[++i]; }
      else if (argument.startsWith('--mod-dir=')) { if (modDir !== undefined) throw new Error('--mod-dir 不能重复。'); modDir = argument.slice(10); }
      else throw new Error('未知参数：' + argument);
    }
    const result = operation === 'install' ? installAddon({ modDir, dryRun }) : uninstallAddon({ modDir, dryRun });
    console.log(JSON.stringify(result, null, 2));
    if (!dryRun && ['install', 'migrate'].includes(result.action)) console.log('文件已安装。当前 FreeCAD 会话需从 Python 控制台加载；脚本不会重启 FreeCAD。');
  } catch (error) { console.error(error instanceof Error ? error.message : String(error)); process.exitCode = 1; }
}

if (process.argv[1] && pathToFileURL(path.resolve(process.argv[1])).href === import.meta.url) addonCli('install');
