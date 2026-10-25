// SPDX-License-Identifier: Apache-2.0
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { createHash } from 'node:crypto';
import test from 'node:test';
import { ADDON_NAME, INSTALL_MARKER, installAddon, uninstallAddon, resolveModDirectory } from '../scripts/install-addon.mjs';

function fixture(t: { after(callback: () => void): void }) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'phoenix-addon-test-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const modDir = path.join(root, 'FreeCAD', 'Mod'), sourceDir = path.join(root, 'source');
  fs.mkdirSync(sourceDir);
  fs.writeFileSync(path.join(sourceDir, 'InitGui.py'), '# independent test addon\n');
  fs.writeFileSync(path.join(sourceDir, 'server.py'), 'VERSION = 1\n');
  return { root, modDir, sourceDir, target: path.join(modDir, ADDON_NAME) };
}

test('install copies marked addon and leaves unrelated addons untouched', (t) => {
  const f = fixture(t), existing = path.join(f.modDir, 'OtherAddon');
  fs.mkdirSync(existing, { recursive: true });
  fs.writeFileSync(path.join(existing, 'InitGui.py'), 'original mcp');
  const result = installAddon(f);
  assert.equal(result.action, 'install');
  assert.equal(fs.lstatSync(f.target).isSymbolicLink(), false);
  assert.equal(fs.readFileSync(path.join(existing, 'InitGui.py'), 'utf8'), 'original mcp');
  const marker = JSON.parse(fs.readFileSync(path.join(f.target, INSTALL_MARKER), 'utf8'));
  assert.equal(marker.owner, 'phoenix-freecad-mcp');
  assert.equal(marker.addon, 'PhoenixFreeCADMCP');
  assert.equal(marker.schema_version, 1);
  assert.match(marker.files['server.py'], /^[a-f0-9]{64}$/u);
});

test('dry-run neither creates Mod nor backup folders', (t) => {
  const f = fixture(t);
  assert.equal(installAddon({ ...f, dryRun: true }).action, 'install');
  assert.equal(fs.existsSync(f.modDir), false);
  assert.equal(fs.existsSync(path.join(path.dirname(f.modDir), '.phoenix-freecad-mcp')), false);
});

test('same-content reinstall is a no-op and updates retain an external backup', (t) => {
  const f = fixture(t);
  installAddon(f);
  const before = fs.readFileSync(path.join(f.target, INSTALL_MARKER), 'utf8');
  assert.equal(installAddon(f).action, 'unchanged');
  assert.equal(fs.readFileSync(path.join(f.target, INSTALL_MARKER), 'utf8'), before);
  fs.writeFileSync(path.join(f.sourceDir, 'server.py'), 'VERSION = 2\n');
  const result = installAddon(f);
  assert.equal(result.action, 'update');
  assert.ok(result.backup && path.relative(f.modDir, result.backup).startsWith('..'));
  assert.equal(fs.readFileSync(path.join(result.backup, 'server.py'), 'utf8'), 'VERSION = 1\n');
  assert.equal(fs.readFileSync(path.join(f.target, 'server.py'), 'utf8'), 'VERSION = 2\n');
});

test('install and uninstall refuse unknown, modified or user-extended addon trees', (t) => {
  const f = fixture(t);
  fs.mkdirSync(f.target, { recursive: true });
  fs.writeFileSync(path.join(f.target, 'user.py'), 'keep');
  assert.throws(() => installAddon(f), /安装标记/u);
  assert.throws(() => uninstallAddon(f), /安装标记/u);
  fs.rmSync(f.target, { recursive: true });
  installAddon(f);
  fs.writeFileSync(path.join(f.target, 'user.py'), 'keep');
  assert.throws(() => installAddon(f), /用户新增/u);
  assert.throws(() => uninstallAddon(f), /用户新增/u);
  assert.equal(fs.readFileSync(path.join(f.target, 'user.py'), 'utf8'), 'keep');
  fs.unlinkSync(path.join(f.target, 'user.py'));
  fs.writeFileSync(path.join(f.target, '__proto__'), 'also keep');
  assert.throws(() => uninstallAddon(f), /用户新增/u);
  fs.unlinkSync(path.join(f.target, '__proto__'));
  fs.appendFileSync(path.join(f.target, 'server.py'), '# user changes\n');
  assert.throws(() => uninstallAddon(f), /修改/u);
});

test('normal bytecode does not block uninstall and remains in the external backup', (t) => {
  const f = fixture(t);
  installAddon(f);
  fs.mkdirSync(path.join(f.target, '__pycache__'));
  fs.writeFileSync(path.join(f.target, '__pycache__', 'server.cpython-313.pyc'), Buffer.from([0, 1, 2]));
  const result = uninstallAddon({ modDir: f.modDir });
  assert.equal(result.action, 'uninstall');
  assert.equal(fs.existsSync(f.target), false);
  assert.ok(result.backup && fs.existsSync(path.join(result.backup, '__pycache__', 'server.cpython-313.pyc')));
  assert.equal(uninstallAddon({ modDir: f.modDir }).action, 'absent');
});

test('symlinks and unknown cache content are protected', (t) => {
  const f = fixture(t);
  fs.symlinkSync(path.join(f.sourceDir, 'server.py'), path.join(f.sourceDir, 'link.py'));
  assert.throws(() => installAddon(f), /符号链接/u);
  fs.unlinkSync(path.join(f.sourceDir, 'link.py'));
  installAddon(f);
  fs.mkdirSync(path.join(f.target, '__pycache__'));
  fs.writeFileSync(path.join(f.target, '__pycache__', 'user.txt'), 'keep');
  assert.throws(() => uninstallAddon({ modDir: f.modDir }), /非生成文件/u);
  fs.unlinkSync(path.join(f.target, '__pycache__', 'user.txt'));
  fs.symlinkSync(path.join(f.sourceDir, 'server.py'), path.join(f.target, '__pycache__', 'evil.pyc'));
  assert.throws(() => uninstallAddon({ modDir: f.modDir }), /非生成文件/u);
});

test('uninstall dry-run keeps installed files and automatic discovery is conservative', (t) => {
  const f = fixture(t);
  installAddon(f);
  assert.equal(uninstallAddon({ modDir: f.modDir, dryRun: true }).action, 'uninstall');
  assert.ok(fs.existsSync(path.join(f.target, 'server.py')));
  const home = path.join(f.root, 'home'), known = path.join(home, 'Library/Application Support/FreeCAD/v1-1/Mod');
  assert.throws(() => resolveModDirectory(undefined, { platform: 'darwin', home }), /--mod-dir/u);
  fs.mkdirSync(known, { recursive: true });
  assert.equal(resolveModDirectory(undefined, { platform: 'darwin', home }), fs.realpathSync(known));
  assert.throws(() => resolveModDirectory(undefined, { platform: 'linux', home }), /候选/u);
});

/** Independently constructs the previously released install identity. */
function legacyFixture(f: ReturnType<typeof fixture>) {
  const target = path.join(f.modDir, 'PhoenixMCP');
  fs.mkdirSync(target, { recursive: true });
  const contents = { 'InitGui.py': '# previous workbench\n', 'phoenix_bridge.py': 'VERSION = "legacy"\n' };
  for (const [name, value] of Object.entries(contents)) fs.writeFileSync(path.join(target, name), value);
  const marker = { schema_version: 1, owner: 'phoenix-freecad-mcp', addon: 'PhoenixMCP', installed_at: '2026-10-24T00:00:00.000Z',
    files: Object.fromEntries(Object.entries(contents).map(([name, value]) => [name, createHash('sha256').update(value).digest('hex')])), directories: [] };
  fs.writeFileSync(path.join(target, INSTALL_MARKER), JSON.stringify(marker));
  return { target, marker };
}

test('owned legacy install migrates to the new directory with unchanged legacy backup', (t) => {
  const f = fixture(t), legacy = legacyFixture(f);
  fs.mkdirSync(path.join(legacy.target, '__pycache__'));
  fs.writeFileSync(path.join(legacy.target, '__pycache__', 'phoenix_bridge.cpython-313.pyc'), Buffer.from([0, 1]));
  const result = installAddon(f);
  assert.equal(result.action, 'migrate');
  assert.equal(result.migratedFrom, legacy.target);
  assert.equal(fs.existsSync(legacy.target), false);
  assert.equal(fs.readFileSync(path.join(f.target, 'server.py'), 'utf8'), 'VERSION = 1\n');
  assert.equal(fs.existsSync(path.join(f.target, 'phoenix_bridge.py')), false);
  assert.ok(result.backup && path.relative(f.modDir, result.backup).startsWith('..'));
  assert.deepEqual(JSON.parse(fs.readFileSync(path.join(result.backup, INSTALL_MARKER), 'utf8')), legacy.marker);
  assert.equal(fs.readFileSync(path.join(result.backup, 'phoenix_bridge.py'), 'utf8'), 'VERSION = "legacy"\n');
  assert.ok(fs.existsSync(path.join(result.backup, '__pycache__', 'phoenix_bridge.cpython-313.pyc')));
  const marker = JSON.parse(fs.readFileSync(path.join(f.target, INSTALL_MARKER), 'utf8'));
  assert.equal(marker.owner, 'phoenix-freecad-mcp');
  assert.equal(marker.addon, ADDON_NAME);
  assert.equal(installAddon(f).action, 'unchanged');
});

test('legacy migration dry-run has no effects and default uninstall ignores the legacy identity', (t) => {
  const f = fixture(t), legacy = legacyFixture(f);
  const before = fs.readdirSync(f.modDir);
  assert.equal(installAddon({ ...f, dryRun: true }).action, 'migrate');
  assert.deepEqual(fs.readdirSync(f.modDir), before);
  assert.equal(fs.existsSync(f.target), false);
  assert.equal(fs.existsSync(path.join(path.dirname(f.modDir), '.phoenix-freecad-mcp')), false);
  assert.equal(uninstallAddon({ modDir: f.modDir }).action, 'absent');
  assert.deepEqual(JSON.parse(fs.readFileSync(path.join(legacy.target, INSTALL_MARKER), 'utf8')), legacy.marker);
});

test('two existing identities are rejected before either tree is changed', (t) => {
  const f = fixture(t);
  installAddon(f);
  const legacy = legacyFixture(f), current = fs.readFileSync(path.join(f.target, INSTALL_MARKER), 'utf8');
  assert.throws(() => installAddon(f), /新旧插件目录同时存在/u);
  assert.throws(() => installAddon({ ...f, dryRun: true }), /新旧插件目录同时存在/u);
  assert.equal(fs.readFileSync(path.join(f.target, INSTALL_MARKER), 'utf8'), current);
  assert.deepEqual(JSON.parse(fs.readFileSync(path.join(legacy.target, INSTALL_MARKER), 'utf8')), legacy.marker);
});

test('legacy migration protects unknown ownership, modified content and additional user files', (t) => {
  for (const change of ['marker', 'owner', 'content', 'extra'] as const) {
    const f = fixture(t), legacy = legacyFixture(f);
    if (change === 'marker') fs.unlinkSync(path.join(legacy.target, INSTALL_MARKER));
    if (change === 'owner') fs.writeFileSync(path.join(legacy.target, INSTALL_MARKER), JSON.stringify({ ...legacy.marker, owner: 'unrelated-project' }));
    if (change === 'content') fs.appendFileSync(path.join(legacy.target, 'phoenix_bridge.py'), '# local change\n');
    if (change === 'extra') fs.writeFileSync(path.join(legacy.target, 'user.py'), '# keep\n');
    assert.throws(() => installAddon(f));
    assert.ok(fs.existsSync(legacy.target));
    assert.equal(fs.existsSync(f.target), false);
    assert.equal(fs.existsSync(path.join(path.dirname(f.modDir), '.phoenix-freecad-mcp')), false);
  }
});

test('legacy directory symlinks are not followed or replaced', (t) => {
  const f = fixture(t), legacy = legacyFixture(f), held = path.join(f.root, 'held-legacy');
  fs.renameSync(legacy.target, held);
  fs.symlinkSync(held, legacy.target, 'dir');
  assert.throws(() => installAddon(f), /符号链接/u);
  assert.equal(fs.lstatSync(legacy.target).isSymbolicLink(), true);
  assert.equal(fs.existsSync(f.target), false);
  assert.equal(fs.readFileSync(path.join(held, 'phoenix_bridge.py'), 'utf8'), 'VERSION = "legacy"\n');
});

test('a publication failure restores the legacy directory at its original location', (t) => {
  const f = fixture(t), legacy = legacyFixture(f), rename = fs.renameSync;
  try {
    fs.renameSync = (from, to) => {
      if (path.basename(String(from)).startsWith('stage-') && String(to) === f.target) throw new Error('synthetic publication failure');
      return rename(from, to);
    };
    assert.throws(() => installAddon(f), /synthetic publication failure/u);
  } finally { fs.renameSync = rename; }
  assert.equal(fs.existsSync(f.target), false);
  assert.deepEqual(JSON.parse(fs.readFileSync(path.join(legacy.target, INSTALL_MARKER), 'utf8')), legacy.marker);
  assert.equal(fs.readFileSync(path.join(legacy.target, 'phoenix_bridge.py'), 'utf8'), 'VERSION = "legacy"\n');
  assert.deepEqual(fs.readdirSync(path.join(path.dirname(f.modDir), '.phoenix-freecad-mcp')), []);
});
