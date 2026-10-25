// SPDX-License-Identifier: Apache-2.0
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { createHash } from 'node:crypto';
import { fileURLToPath, pathToFileURL } from 'node:url';
import test from 'node:test';
import { ADDON_NAME, INSTALL_MARKER, installAddon, uninstallAddon, resolveModDirectory } from '../scripts/install-addon.mjs';

const LEGACY_MARKER = '.phoenix-freecad-mcp-install.json';

function fixture(t: { after(callback: () => void): void }) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'phoenix-addon-test-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const modDir = path.join(root, 'FreeCAD', 'Mod'), sourceDir = path.join(root, 'source');
  fs.mkdirSync(sourceDir);
  fs.writeFileSync(path.join(sourceDir, 'InitGui.py'), '# independent test addon\n');
  fs.writeFileSync(path.join(sourceDir, 'server.py'), 'VERSION = 1\n');
  return { root, modDir, sourceDir, target: path.join(modDir, ADDON_NAME) };
}

/** Exercise package-relative defaults without using real package materials or a user Mod tree. */
async function packagedFixture(t: { after(callback: () => void): void }) {
  const f = fixture(t), packageRoot = path.join(f.root, 'pkg');
  const packageSourceDir = path.join(packageRoot, 'addon', ADDON_NAME);
  fs.mkdirSync(packageSourceDir, { recursive: true });
  fs.mkdirSync(path.join(packageRoot, 'scripts'));
  fs.mkdirSync(path.join(packageRoot, 'docs'));
  fs.copyFileSync(fileURLToPath(new URL('../scripts/install-addon.mjs', import.meta.url)), path.join(packageRoot, 'scripts', 'install-addon.mjs'));
  for (const name of ['InitGui.py', 'server.py']) fs.copyFileSync(path.join(f.sourceDir, name), path.join(packageSourceDir, name));
  const documents = [
    { source: 'LICENSE', target: 'LICENSE', text: 'Synthetic fixture license.\n' },
    { source: 'docs/ADDON-NOTICE.txt', target: 'NOTICE', text: 'Synthetic fixture addon notice.\n' },
    { source: 'docs/ADDON-MANAGED-INSTALL.md', target: 'README.md', text: '# Synthetic fixture install instructions\n' },
  ];
  for (const document of documents) fs.writeFileSync(path.join(packageRoot, document.source), document.text);
  const installer = await import(pathToFileURL(path.join(packageRoot, 'scripts', 'install-addon.mjs')).href);
  return { ...f, packageRoot, packageSourceDir, documents, installer, storage: path.join(path.dirname(f.modDir), '.phoenix-freecad-tools') };
}

test('default package install owns the addon license, notice and install instructions by content hash', async (t) => {
  const f = await packagedFixture(t);
  assert.equal(f.installer.installAddon({ modDir: f.modDir }).action, 'install');
  const marker = JSON.parse(fs.readFileSync(path.join(f.target, INSTALL_MARKER), 'utf8'));
  assert.deepEqual(Object.keys(marker.files), ['InitGui.py', 'LICENSE', 'NOTICE', 'README.md', 'server.py'].sort((a, b) => a.localeCompare(b)));
  assert.deepEqual(marker.directories, []);
  for (const document of f.documents) {
    assert.equal(fs.readFileSync(path.join(f.target, document.target), 'utf8'), document.text);
    assert.equal(marker.files[document.target], createHash('sha256').update(document.text).digest('hex'));
  }
  assert.equal(fs.existsSync(path.join(f.target, 'docs')), false);
});

test('package materials participate in no-op detection, updates, backups and recoverable uninstall', async (t) => {
  const f = await packagedFixture(t);
  f.installer.installAddon({ modDir: f.modDir });
  const markerPath = path.join(f.target, INSTALL_MARKER);
  let previousMarker = fs.readFileSync(markerPath, 'utf8');
  assert.equal(f.installer.installAddon({ modDir: f.modDir }).action, 'unchanged');
  assert.equal(fs.readFileSync(markerPath, 'utf8'), previousMarker);
  assert.deepEqual(fs.readdirSync(f.storage), []);
  for (const document of f.documents) {
    const updated = document.text + 'Synthetic revision two.\n';
    fs.writeFileSync(path.join(f.packageRoot, document.source), updated);
    const result = f.installer.installAddon({ modDir: f.modDir });
    assert.equal(result.action, 'update');
    assert.ok(result.backup && path.relative(f.modDir, result.backup).startsWith('..'));
    assert.equal(fs.readFileSync(path.join(result.backup, document.target), 'utf8'), document.text);
    assert.equal(fs.readFileSync(path.join(result.backup, INSTALL_MARKER), 'utf8'), previousMarker);
    assert.equal(fs.readFileSync(path.join(f.target, document.target), 'utf8'), updated);
    previousMarker = fs.readFileSync(markerPath, 'utf8');
    assert.equal(JSON.parse(previousMarker).files[document.target], createHash('sha256').update(updated).digest('hex'));
    assert.equal(f.installer.installAddon({ modDir: f.modDir }).action, 'unchanged');
    assert.equal(fs.readFileSync(markerPath, 'utf8'), previousMarker);
  }
  const uninstalled = f.installer.uninstallAddon({ modDir: f.modDir });
  assert.equal(uninstalled.action, 'uninstall');
  assert.equal(fs.existsSync(f.target), false);
  assert.equal(fs.readFileSync(path.join(uninstalled.backup, INSTALL_MARKER), 'utf8'), previousMarker);
  for (const document of f.documents) assert.equal(fs.readFileSync(path.join(uninstalled.backup, document.target), 'utf8'), document.text + 'Synthetic revision two.\n');
});

test('a locally edited managed README blocks update and uninstall without creating a backup', async (t) => {
  const f = await packagedFixture(t);
  f.installer.installAddon({ modDir: f.modDir });
  const marker = fs.readFileSync(path.join(f.target, INSTALL_MARKER), 'utf8');
  const local = '# Preserve these synthetic local instructions.\n';
  fs.writeFileSync(path.join(f.target, 'README.md'), local);
  fs.appendFileSync(path.join(f.packageRoot, 'docs', 'ADDON-MANAGED-INSTALL.md'), 'Upstream revision.\n');
  assert.throws(() => f.installer.installAddon({ modDir: f.modDir }), /修改/u);
  assert.throws(() => f.installer.uninstallAddon({ modDir: f.modDir }), /修改/u);
  assert.equal(fs.readFileSync(path.join(f.target, 'README.md'), 'utf8'), local);
  assert.equal(fs.readFileSync(path.join(f.target, INSTALL_MARKER), 'utf8'), marker);
  assert.deepEqual(fs.readdirSync(f.storage), []);
});

test('an existing owned install without materials upgrades with its original tree intact in backup', async (t) => {
  const f = await packagedFixture(t);
  f.installer.installAddon({ modDir: f.modDir, sourceDir: f.sourceDir });
  const oldMarker = fs.readFileSync(path.join(f.target, INSTALL_MARKER), 'utf8');
  assert.deepEqual(Object.keys(JSON.parse(oldMarker).files), ['InitGui.py', 'server.py']);
  const result = f.installer.installAddon({ modDir: f.modDir });
  assert.equal(result.action, 'update');
  assert.equal(fs.readFileSync(path.join(result.backup, INSTALL_MARKER), 'utf8'), oldMarker);
  for (const name of ['InitGui.py', 'server.py']) assert.equal(fs.readFileSync(path.join(result.backup, name), 'utf8'), fs.readFileSync(path.join(f.sourceDir, name), 'utf8'));
  for (const document of f.documents) {
    assert.equal(fs.existsSync(path.join(result.backup, document.target)), false);
    assert.equal(fs.readFileSync(path.join(f.target, document.target), 'utf8'), document.text);
  }
  assert.equal(f.installer.installAddon({ modDir: f.modDir }).action, 'unchanged');
});

test('an explicit custom source is copied as supplied without requiring package materials', async (t) => {
  const f = await packagedFixture(t);
  fs.rmSync(path.join(f.packageRoot, 'docs'), { recursive: true });
  fs.unlinkSync(path.join(f.packageRoot, 'LICENSE'));
  fs.writeFileSync(path.join(f.sourceDir, 'README.md'), 'Synthetic custom source instructions.\n');
  assert.equal(f.installer.installAddon({ modDir: f.modDir, sourceDir: f.sourceDir }).action, 'install');
  assert.deepEqual(fs.readdirSync(f.target).sort(), [INSTALL_MARKER, 'InitGui.py', 'README.md', 'server.py'].sort());
  assert.equal(fs.readFileSync(path.join(f.target, 'README.md'), 'utf8'), 'Synthetic custom source instructions.\n');
});

for (const source of ['LICENSE', 'docs/ADDON-NOTICE.txt', 'docs/ADDON-MANAGED-INSTALL.md']) {
  for (const invalid of ['missing', 'empty', 'oversized', 'symlink'] as const) {
    test(`default package rejects ${invalid} material ${source} before creating Mod or storage`, async (t) => {
      const f = await packagedFixture(t), filename = path.join(f.packageRoot, source);
      if (invalid === 'missing') fs.unlinkSync(filename);
      if (invalid === 'empty') fs.writeFileSync(filename, '');
      if (invalid === 'oversized') fs.writeFileSync(filename, Buffer.alloc(2 * 1024 * 1024 + 1, 120));
      if (invalid === 'symlink') {
        const held = path.join(f.root, 'held-material');
        fs.renameSync(filename, held);
        fs.symlinkSync(held, filename);
      }
      const reason = invalid === 'missing' ? /缺少必需分发材料/u : invalid === 'symlink' ? /分发材料不允许符号链接/u : /分发材料大小无效/u;
      for (const dryRun of [false, true]) {
        assert.throws(() => f.installer.installAddon({ modDir: f.modDir, dryRun }), reason);
        assert.equal(fs.existsSync(f.modDir), false);
        assert.equal(fs.existsSync(f.storage), false);
        assert.equal(fs.existsSync(path.dirname(f.modDir)), false);
      }
    });
  }
}

test('default package rejects a symlinked docs parent before creating Mod or storage', async (t) => {
  const f = await packagedFixture(t), docs = path.join(f.packageRoot, 'docs'), held = path.join(f.root, 'held-docs');
  fs.renameSync(docs, held);
  fs.symlinkSync(held, docs, 'dir');
  for (const dryRun of [false, true]) {
    assert.throws(() => f.installer.installAddon({ modDir: f.modDir, dryRun }), /分发材料不允许符号链接/u);
    assert.equal(fs.existsSync(f.modDir), false);
    assert.equal(fs.existsSync(f.storage), false);
    assert.equal(fs.existsSync(path.dirname(f.modDir)), false);
  }
  assert.equal(fs.readFileSync(path.join(held, 'ADDON-NOTICE.txt'), 'utf8'), f.documents[1].text);
});

test('conflicting addon README is rejected before creating Mod or storage', async (t) => {
  const f = await packagedFixture(t), local = 'Synthetic conflicting addon README.\n';
  fs.writeFileSync(path.join(f.packageSourceDir, 'README.md'), local);
  for (const dryRun of [false, true]) {
    assert.throws(() => f.installer.installAddon({ modDir: f.modDir, dryRun }), /插件源码与分发材料冲突：README\.md/u);
    assert.equal(fs.existsSync(f.modDir), false);
    assert.equal(fs.existsSync(f.storage), false);
    assert.equal(fs.existsSync(path.dirname(f.modDir)), false);
  }
  assert.equal(fs.readFileSync(path.join(f.packageSourceDir, 'README.md'), 'utf8'), local);
});

test('addon directories cannot occupy the managed document filenames', async (t) => {
  for (const name of ['LICENSE', 'NOTICE', 'README.md']) {
    const f = await packagedFixture(t);
    fs.mkdirSync(path.join(f.packageSourceDir, name));
    for (const dryRun of [false, true]) {
      assert.throws(() => f.installer.installAddon({ modDir: f.modDir, dryRun }), /插件源码与分发材料冲突/u);
      assert.equal(fs.existsSync(f.modDir), false);
      assert.equal(fs.existsSync(f.storage), false);
      assert.equal(fs.existsSync(path.dirname(f.modDir)), false);
    }
  }
});

test('material changes during staging abort the update and leave the installed tree untouched', async (t) => {
  const f = await packagedFixture(t);
  f.installer.installAddon({ modDir: f.modDir });
  const marker = fs.readFileSync(path.join(f.target, INSTALL_MARKER), 'utf8');
  const material = fs.realpathSync(path.join(f.packageRoot, 'docs', 'ADDON-MANAGED-INSTALL.md'));
  fs.appendFileSync(material, 'Synthetic pending update.\n');
  const copyFile = fs.copyFileSync;
  let changed = false;
  try {
    fs.copyFileSync = (source, target, mode) => {
      if (String(source) === material) { changed = true; fs.appendFileSync(material, 'Synthetic concurrent edit.\n'); }
      return copyFile(source, target, mode);
    };
    assert.throws(() => f.installer.installAddon({ modDir: f.modDir }), /复制期间源码变化/u);
  } finally { fs.copyFileSync = copyFile; }
  assert.equal(changed, true);
  assert.equal(fs.readFileSync(path.join(f.target, INSTALL_MARKER), 'utf8'), marker);
  assert.equal(fs.readFileSync(path.join(f.target, 'README.md'), 'utf8'), f.documents[2].text);
  assert.deepEqual(fs.readdirSync(f.storage), []);
});

test('install copies marked addon and leaves unrelated addons untouched', (t) => {
  const f = fixture(t), existing = path.join(f.modDir, 'OtherAddon');
  fs.mkdirSync(existing, { recursive: true });
  fs.writeFileSync(path.join(existing, 'InitGui.py'), 'original mcp');
  const result = installAddon(f);
  assert.equal(result.action, 'install');
  assert.equal(fs.lstatSync(f.target).isSymbolicLink(), false);
  assert.equal(fs.readFileSync(path.join(existing, 'InitGui.py'), 'utf8'), 'original mcp');
  const marker = JSON.parse(fs.readFileSync(path.join(f.target, INSTALL_MARKER), 'utf8'));
  assert.equal(marker.owner, 'phoenix-freecad-tools');
  assert.equal(marker.addon, 'PhoenixFreeCADTools');
  assert.equal(marker.schema_version, 1);
  assert.match(marker.files['server.py'], /^[a-f0-9]{64}$/u);
});

test('dry-run neither creates Mod nor backup folders', (t) => {
  const f = fixture(t);
  assert.equal(installAddon({ ...f, dryRun: true }).action, 'install');
  assert.equal(fs.existsSync(f.modDir), false);
  assert.equal(fs.existsSync(path.join(path.dirname(f.modDir), '.phoenix-freecad-tools')), false);
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
function legacyFixture(f: ReturnType<typeof fixture>, addon: 'PhoenixMCP' | 'PhoenixFreeCADMCP' = 'PhoenixMCP') {
  const target = path.join(f.modDir, addon);
  fs.mkdirSync(target, { recursive: true });
  const contents = { 'InitGui.py': '# previous workbench\n', 'phoenix_bridge.py': 'VERSION = "legacy"\n' };
  for (const [name, value] of Object.entries(contents)) fs.writeFileSync(path.join(target, name), value);
  const marker = { schema_version: 1, owner: 'phoenix-freecad-mcp', addon, installed_at: '2026-10-24T00:00:00.000Z',
    files: Object.fromEntries(Object.entries(contents).map(([name, value]) => [name, createHash('sha256').update(value).digest('hex')])), directories: [] };
  fs.writeFileSync(path.join(target, LEGACY_MARKER), JSON.stringify(marker));
  return { target, marker };
}

for (const legacyAddon of ['PhoenixMCP', 'PhoenixFreeCADMCP'] as const) {
test(legacyAddon + ': owned legacy install migrates to the new directory with unchanged legacy backup', (t) => {
  const f = fixture(t), legacy = legacyFixture(f, legacyAddon);
  fs.mkdirSync(path.join(legacy.target, '__pycache__'));
  fs.writeFileSync(path.join(legacy.target, '__pycache__', 'phoenix_bridge.cpython-313.pyc'), Buffer.from([0, 1]));
  const result = installAddon(f);
  assert.equal(result.action, 'migrate');
  assert.equal(result.migratedFrom, legacy.target);
  assert.equal(fs.existsSync(legacy.target), false);
  assert.equal(fs.readFileSync(path.join(f.target, 'server.py'), 'utf8'), 'VERSION = 1\n');
  assert.equal(fs.existsSync(path.join(f.target, 'phoenix_bridge.py')), false);
  assert.ok(result.backup && path.relative(f.modDir, result.backup).startsWith('..'));
  assert.deepEqual(JSON.parse(fs.readFileSync(path.join(result.backup, LEGACY_MARKER), 'utf8')), legacy.marker);
  assert.equal(fs.readFileSync(path.join(result.backup, 'phoenix_bridge.py'), 'utf8'), 'VERSION = "legacy"\n');
  assert.ok(fs.existsSync(path.join(result.backup, '__pycache__', 'phoenix_bridge.cpython-313.pyc')));
  const marker = JSON.parse(fs.readFileSync(path.join(f.target, INSTALL_MARKER), 'utf8'));
  assert.equal(marker.owner, 'phoenix-freecad-tools');
  assert.equal(marker.addon, ADDON_NAME);
  assert.equal(installAddon(f).action, 'unchanged');
});

test(legacyAddon + ': legacy migration dry-run has no effects and default uninstall ignores the legacy identity', (t) => {
  const f = fixture(t), legacy = legacyFixture(f, legacyAddon);
  const before = fs.readdirSync(f.modDir);
  assert.equal(installAddon({ ...f, dryRun: true }).action, 'migrate');
  assert.deepEqual(fs.readdirSync(f.modDir), before);
  assert.equal(fs.existsSync(f.target), false);
  assert.equal(fs.existsSync(path.join(path.dirname(f.modDir), '.phoenix-freecad-tools')), false);
  assert.equal(uninstallAddon({ modDir: f.modDir }).action, 'absent');
  assert.deepEqual(JSON.parse(fs.readFileSync(path.join(legacy.target, LEGACY_MARKER), 'utf8')), legacy.marker);
});

test(legacyAddon + ': two existing identities are rejected before either tree is changed', (t) => {
  const f = fixture(t);
  installAddon(f);
  const legacy = legacyFixture(f, legacyAddon), current = fs.readFileSync(path.join(f.target, INSTALL_MARKER), 'utf8');
  assert.throws(() => installAddon(f), /新旧插件目录同时存在/u);
  assert.throws(() => installAddon({ ...f, dryRun: true }), /新旧插件目录同时存在/u);
  assert.equal(fs.readFileSync(path.join(f.target, INSTALL_MARKER), 'utf8'), current);
  assert.deepEqual(JSON.parse(fs.readFileSync(path.join(legacy.target, LEGACY_MARKER), 'utf8')), legacy.marker);
});

test(legacyAddon + ': legacy migration protects unknown ownership, modified content and additional user files', (t) => {
  for (const change of ['marker', 'owner', 'new-owner', 'content', 'extra'] as const) {
    const f = fixture(t), legacy = legacyFixture(f, legacyAddon);
    if (change === 'marker') fs.unlinkSync(path.join(legacy.target, LEGACY_MARKER));
    if (change === 'new-owner') fs.writeFileSync(path.join(legacy.target, LEGACY_MARKER), JSON.stringify({ ...legacy.marker, owner: 'phoenix-freecad-tools' }));
    if (change === 'owner') fs.writeFileSync(path.join(legacy.target, LEGACY_MARKER), JSON.stringify({ ...legacy.marker, owner: 'unrelated-project' }));
    if (change === 'content') fs.appendFileSync(path.join(legacy.target, 'phoenix_bridge.py'), '# local change\n');
    if (change === 'extra') fs.writeFileSync(path.join(legacy.target, 'user.py'), '# keep\n');
    assert.throws(() => installAddon(f));
    assert.ok(fs.existsSync(legacy.target));
    assert.equal(fs.existsSync(f.target), false);
    assert.equal(fs.existsSync(path.join(path.dirname(f.modDir), '.phoenix-freecad-tools')), false);
  }
});

test(legacyAddon + ': legacy directory symlinks are not followed or replaced', (t) => {
  const f = fixture(t), legacy = legacyFixture(f, legacyAddon), held = path.join(f.root, 'held-legacy');
  fs.renameSync(legacy.target, held);
  fs.symlinkSync(held, legacy.target, 'dir');
  assert.throws(() => installAddon(f), /符号链接/u);
  assert.equal(fs.lstatSync(legacy.target).isSymbolicLink(), true);
  assert.equal(fs.existsSync(f.target), false);
  assert.equal(fs.readFileSync(path.join(held, 'phoenix_bridge.py'), 'utf8'), 'VERSION = "legacy"\n');
});

test(legacyAddon + ': a publication failure restores the legacy directory at its original location', (t) => {
  const f = fixture(t), legacy = legacyFixture(f, legacyAddon), rename = fs.renameSync;
  try {
    fs.renameSync = (from, to) => {
      if (path.basename(String(from)).startsWith('stage-') && String(to) === f.target) throw new Error('synthetic publication failure');
      return rename(from, to);
    };
    assert.throws(() => installAddon(f), /synthetic publication failure/u);
  } finally { fs.renameSync = rename; }
  assert.equal(fs.existsSync(f.target), false);
  assert.deepEqual(JSON.parse(fs.readFileSync(path.join(legacy.target, LEGACY_MARKER), 'utf8')), legacy.marker);
  assert.equal(fs.readFileSync(path.join(legacy.target, 'phoenix_bridge.py'), 'utf8'), 'VERSION = "legacy"\n');
  assert.deepEqual(fs.readdirSync(path.join(path.dirname(f.modDir), '.phoenix-freecad-tools')), []);
});

}

test('both legacy identities conflict without changing either tree or historical backups', (t) => {
  const f = fixture(t), one = legacyFixture(f, 'PhoenixMCP'), two = legacyFixture(f, 'PhoenixFreeCADMCP');
  const previousStorage = path.join(path.dirname(f.modDir), '.phoenix-freecad-mcp');
  fs.mkdirSync(previousStorage);
  fs.writeFileSync(path.join(previousStorage, 'keep.txt'), 'historical backup');
  const before = [one, two].map(({ target }) => fs.readFileSync(path.join(target, LEGACY_MARKER), 'utf8'));
  assert.throws(() => installAddon(f), /新旧插件目录同时存在/u);
  assert.throws(() => installAddon({ ...f, dryRun: true }), /新旧插件目录同时存在/u);
  assert.equal(uninstallAddon(f).action, 'absent');
  assert.deepEqual([one, two].map(({ target }) => fs.readFileSync(path.join(target, LEGACY_MARKER), 'utf8')), before);
  assert.equal(fs.readFileSync(path.join(previousStorage, 'keep.txt'), 'utf8'), 'historical backup');
  assert.equal(fs.existsSync(path.join(path.dirname(f.modDir), '.phoenix-freecad-tools')), false);
});

test('source refuses either installation marker and current ownership cannot use the old owner', (t) => {
  for (const marker of [INSTALL_MARKER, LEGACY_MARKER]) {
    const f = fixture(t);
    fs.writeFileSync(path.join(f.sourceDir, marker), '{}');
    assert.throws(() => installAddon(f), /源码目录含安装标记/u);
    assert.equal(fs.existsSync(f.modDir), false);
  }
  const f = fixture(t);
  installAddon(f);
  const markerPath = path.join(f.target, INSTALL_MARKER);
  const marker = JSON.parse(fs.readFileSync(markerPath, 'utf8'));
  fs.writeFileSync(markerPath, JSON.stringify({ ...marker, owner: 'phoenix-freecad-mcp' }));
  assert.throws(() => installAddon(f), /不属于本项目/u);
  assert.throws(() => uninstallAddon(f), /不属于本项目/u);
  assert.equal(fs.existsSync(f.target), true);
});

test('a legacy identity appearing during copy prevents publication and preserves the original tree', (t) => {
  const f = fixture(t), original = legacyFixture(f, 'PhoenixFreeCADMCP'), copyFile = fs.copyFileSync;
  let appeared = false;
  try {
    fs.copyFileSync = (source, target, mode) => {
      const result = copyFile(source, target, mode);
      if (!appeared) { appeared = true; legacyFixture(f, 'PhoenixMCP'); }
      return result;
    };
    assert.throws(() => installAddon(f), /安装期间新旧目录/u);
  } finally { fs.copyFileSync = copyFile; }
  assert.deepEqual(JSON.parse(fs.readFileSync(path.join(original.target, LEGACY_MARKER), 'utf8')), original.marker);
  assert.equal(fs.existsSync(path.join(f.modDir, 'PhoenixMCP')), true);
  assert.equal(fs.existsSync(f.target), false);
});

test('a publication race never overwrites a recreated legacy directory during rollback', (t) => {
  const f = fixture(t), legacy = legacyFixture(f, 'PhoenixFreeCADMCP'), rename = fs.renameSync;
  try {
    fs.renameSync = (from, to) => {
      if (path.basename(String(from)).startsWith('stage-') && String(to) === f.target) {
        fs.mkdirSync(legacy.target);
        fs.writeFileSync(path.join(legacy.target, 'user.txt'), 'preserve race');
        throw new Error('synthetic publication race');
      }
      return rename(from, to);
    };
    assert.throws(() => installAddon(f), /synthetic publication race/u);
  } finally { fs.renameSync = rename; }
  assert.equal(fs.readFileSync(path.join(legacy.target, 'user.txt'), 'utf8'), 'preserve race');
  const storage = path.join(path.dirname(f.modDir), '.phoenix-freecad-tools');
  const backups = fs.readdirSync(storage).filter((name) => name.startsWith('backup-'));
  assert.equal(backups.length, 1);
  assert.deepEqual(JSON.parse(fs.readFileSync(path.join(storage, backups[0], LEGACY_MARKER), 'utf8')), legacy.marker);
  assert.equal(fs.existsSync(f.target), false);
});
