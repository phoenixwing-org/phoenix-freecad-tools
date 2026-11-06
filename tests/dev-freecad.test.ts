// SPDX-License-Identifier: Apache-2.0
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import childProcess, { spawn as liveSpawnBinding } from 'node:child_process';
import { EventEmitter } from 'node:events';
import { syncBuiltinESMExports } from 'node:module';
import test from 'node:test';
import { launchDevelopment, prepareDevelopment } from '../scripts/dev-freecad.mjs';

function fixture(t: { after(callback: () => void): void }) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'pft-dev-launcher-test-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const repository = path.join(root, 'source'), profile = path.join(root, 'profile');
  const addon = path.join(repository, 'addon', 'PhoenixFreeCADTools');
  fs.mkdirSync(addon, { recursive: true });
  fs.writeFileSync(path.join(addon, 'InitGui.py'), '# synthetic fixture\n');
  const app = path.join(root, 'synthetic executable with spaces');
  fs.writeFileSync(app, '# never executed by these tests\n', { mode: 0o700 });
  return { root, repository, profile, addon, app };
}

function mockedProcess(t: { after(callback: () => void): void }, mode: 'spawn' | 'error' = 'spawn') {
  const originalSpawn = childProcess.spawn, originalKill = process.kill;
  const calls: unknown[][] = [], probes: [number, number | string | undefined][] = [];
  let onSpawn = () => {};
  const fake = ((...args: unknown[]) => {
    calls.push(args);
    onSpawn();
    const child = new EventEmitter() as EventEmitter & { pid: number; unref(): void };
    child.pid = 987654;
    child.unref = () => {};
    queueMicrotask(() => mode === 'spawn' ? child.emit('spawn') : child.emit('error', new Error('synthetic spawn rejected')));
    return child;
  }) as unknown as typeof childProcess.spawn;
  childProcess.spawn = fake;
  syncBuiltinESMExports();
  assert.equal(liveSpawnBinding, fake); // No external program can be executed.
  process.kill = ((pid: number, signal?: number | string) => { probes.push([pid, signal]); return true; }) as typeof process.kill;
  t.after(() => { childProcess.spawn = originalSpawn; process.kill = originalKill; syncBuiltinESMExports(); });
  return { calls, probes, onSpawn: (callback: () => void) => { onSpawn = callback; } };
}

test('prepare owns a private profile and returns separate argv without starting anything', t => {
  const f = fixture(t);
  const prepared = prepareDevelopment(f);
  assert.equal(prepared.launch.launched, false);
  assert.equal(prepared.launch.shell, false);
  assert.equal(prepared.launch.program, f.app);
  const vendor = 'org.phoenix.freecadtools.development.' + prepared.launch.owner_id.replaceAll('-', '');
  assert.deepEqual(prepared.launch.qt_identity, { vendor, application: 'PhoenixFreeCADDevelopment' });
  assert.deepEqual(prepared.launch.args, ['--set-config', 'ExeVendor=' + vendor, 'ExeName=PhoenixFreeCADDevelopment',
    '--user-cfg', path.join(f.profile, 'config', 'user.cfg'),
    '--system-cfg', path.join(f.profile, 'config', 'system.cfg'), path.join(f.profile, 'startup.FCMacro')]);
  assert.equal(prepared.environment.PFT_DEVELOPMENT_PORT, '9877');
  assert.equal(prepared.environment.FREECAD_USER_HOME, f.profile);
  assert.equal(prepared.environment.FREECAD_USER_DATA, f.profile);
  assert.equal(prepared.environment.FREECAD_USER_TEMP, path.join(f.profile, 'temp'));
  assert.equal(fs.statSync(f.profile).mode & 0o777, 0o700);
  assert.equal(fs.statSync(path.join(f.profile, 'config', 'qt')).mode & 0o777, 0o700);
  assert.equal(fs.statSync(path.join(f.profile, 'config', 'qt-system')).mode & 0o777, 0o700);
  assert.equal(fs.realpathSync(path.join(f.profile, 'Mod', 'PhoenixFreeCADTools')), fs.realpathSync(f.addon));
  assert.deepEqual(fs.readdirSync(path.join(f.profile, 'runtime')), []);
  const configurationValues = prepared.launch.args.slice(1, prepared.launch.args.indexOf('--user-cfg'));
  assert.deepEqual(configurationValues, ['ExeVendor=' + vendor, 'ExeName=PhoenixFreeCADDevelopment']);
  assert.equal(prepared.launch.args.at(-1), path.join(f.profile, 'startup.FCMacro'));
  assert.equal(prepared.launch.args.filter(value => value === '--set-config').length, 1);
  const macro = fs.readFileSync(path.join(f.profile, 'startup.FCMacro'), 'utf8');
  assert.match(macro, /write_readiness/);
  assert.doesNotMatch(macro, /openDocument|newDocument|start_server|ParamGet|saveParameter/);
});

test('owned profile reuse preserves its preferences and does not read normal user preferences', t => {
  const f = fixture(t);
  const first = prepareDevelopment(f);
  const config = path.join(f.profile, 'config', 'user.cfg');
  fs.writeFileSync(config, '<synthetic-development-preferences/>');
  const next = prepareDevelopment(f);
  assert.equal(next.launch.owner_id, first.launch.owner_id);
  assert.deepEqual(next.launch.qt_identity, first.launch.qt_identity);
  assert.notEqual(next.launch.launch_id, first.launch.launch_id);
  assert.equal(fs.readFileSync(config, 'utf8'), '<synthetic-development-preferences/>');
});

test('unknown profiles and mismatched source ownership are refused', t => {
  const f = fixture(t);
  fs.mkdirSync(f.profile, { mode: 0o700 });
  fs.writeFileSync(path.join(f.profile, 'keep.txt'), 'user-owned fixture');
  assert.throws(() => prepareDevelopment(f));
  assert.equal(fs.readFileSync(path.join(f.profile, 'keep.txt'), 'utf8'), 'user-owned fixture');
  fs.rmSync(f.profile, { recursive: true });
  prepareDevelopment(f);
  const marker = path.join(f.profile, '.phoenix-development-profile.json');
  const value = JSON.parse(fs.readFileSync(marker, 'utf8'));
  fs.writeFileSync(marker, JSON.stringify({ ...value, source: '/synthetic/other/source' }));
  assert.throws(() => prepareDevelopment(f), /归属或源码/);
});

test('profiles anywhere inside the repository or addon tree are rejected before creating directories', t => {
  const f = fixture(t);
  for (const profile of [f.repository, f.addon, path.join(f.repository, 'docs', 'dev-profile'),
    path.join(f.addon, 'development', 'private-profile')]) {
    assert.throws(() => prepareDevelopment({ ...f, profile }), /仓库和插件源码之外/);
  }
  assert.equal(fs.existsSync(path.join(f.repository, 'docs')), false);
  assert.equal(fs.existsSync(path.join(f.addon, 'development')), false);
  assert.deepEqual(fs.readdirSync(f.addon), ['InitGui.py']);
});

test('symlinked existing ancestors cannot disguise a source-tree profile location', t => {
  const f = fixture(t);
  const alias = path.join(f.root, 'alias-to-repository');
  fs.symlinkSync(f.repository, alias, 'dir');
  assert.throws(() => prepareDevelopment({ ...f, profile: path.join(alias, 'new-docs', 'dev-profile') }), /仓库和插件源码之外/);
  assert.equal(fs.existsSync(path.join(f.repository, 'new-docs')), false);
  const directAlias = path.join(f.root, 'alias-to-addon');
  fs.symlinkSync(f.addon, directAlias, 'dir');
  assert.throws(() => prepareDevelopment({ ...f, profile: directAlias }), /仓库和插件源码之外/);
  assert.deepEqual(fs.readdirSync(f.addon), ['InitGui.py']);
});

test('managed macro edits, configuration symlinks and broad profile permissions are preserved and refused', t => {
  const f = fixture(t);
  prepareDevelopment(f);
  const macro = path.join(f.profile, 'startup.FCMacro');
  const original = fs.readFileSync(macro, 'utf8');
  fs.writeFileSync(macro, '# user change\n');
  assert.throws(() => prepareDevelopment(f), /已变化/);
  assert.equal(fs.readFileSync(macro, 'utf8'), '# user change\n');
  fs.writeFileSync(macro, original);
  const config = path.join(f.profile, 'config', 'user.cfg');
  fs.unlinkSync(config);
  fs.symlinkSync(macro, config);
  assert.throws(() => prepareDevelopment(f), /归属或权限/);
  fs.unlinkSync(config);
  fs.chmodSync(f.profile, 0o755);
  assert.throws(() => prepareDevelopment(f), /0700/);
});

test('launch rechecks the latest live PID after acquiring its exclusive lock', async t => {
  const f = fixture(t), prepared = prepareDevelopment(f), mocked = mockedProcess(t);
  const lock = path.join(f.profile, 'runtime', 'launch.lock');
  const originalWrite = fs.writeFileSync;
  fs.writeFileSync = ((filename: fs.PathOrFileDescriptor, ...args: unknown[]) => {
    const result = (originalWrite as Function)(filename, ...args);
    if (filename === lock) {
      originalWrite(prepared.launch.process_record, JSON.stringify({ owner_id: prepared.launch.owner_id,
        source: prepared.launch.source, pid: 123456 }), { mode: 0o600 });
    }
    return result;
  }) as typeof fs.writeFileSync;
  t.after(() => { fs.writeFileSync = originalWrite; });
  await assert.rejects(launchDevelopment(prepared), /已有存活进程/);
  assert.deepEqual(mocked.probes, [[123456, 0]]);
  assert.equal(mocked.calls.length, 0);
  assert.equal(fs.existsSync(lock), false);
});

test('a synthetic launch holds the lock through spawn and ownership record creation', async t => {
  const f = fixture(t), prepared = prepareDevelopment(f), mocked = mockedProcess(t);
  const lock = path.join(f.profile, 'runtime', 'launch.lock');
  mocked.onSpawn(() => assert.equal(fs.existsSync(lock), true));
  const result = await launchDevelopment(prepared);
  assert.equal(result.pid, 987654);
  assert.equal(result.launched, true);
  assert.equal(mocked.calls.length, 1);
  assert.equal(fs.existsSync(lock), false);
  assert.equal(JSON.parse(fs.readFileSync(prepared.launch.process_record, 'utf8')).pid, 987654);
});

test('a stale or unknown launch lock is preserved with explicit manual recovery instructions', async t => {
  const f = fixture(t), prepared = prepareDevelopment(f), mocked = mockedProcess(t);
  const lock = path.join(f.profile, 'runtime', 'launch.lock');
  const stale = JSON.stringify({ owner_id: prepared.launch.owner_id, launcher_pid: 987653 });
  fs.writeFileSync(lock, stale, { mode: 0o600 });
  await assert.rejects(launchDevelopment(prepared), /人工移除遗留启动锁/);
  assert.equal(fs.readFileSync(lock, 'utf8'), stale);
  assert.equal(mocked.calls.length, 0);
  assert.equal(mocked.probes.length, 0);
});

test('a failed synthetic spawn removes only its owned launch lock', async t => {
  const f = fixture(t), prepared = prepareDevelopment(f), mocked = mockedProcess(t, 'error');
  await assert.rejects(launchDevelopment(prepared), /synthetic spawn rejected/);
  assert.equal(mocked.calls.length, 1);
  assert.equal(fs.existsSync(path.join(f.profile, 'runtime', 'launch.lock')), false);
  assert.equal(fs.existsSync(prepared.launch.process_record), false);
});

test('a process whose ownership record could not be saved retains its lock against unsafe retry', async t => {
  const f = fixture(t), prepared = prepareDevelopment(f), mocked = mockedProcess(t);
  const originalWrite = fs.writeFileSync;
  fs.writeFileSync = ((filename: fs.PathOrFileDescriptor, ...args: unknown[]) => {
    if (filename === prepared.launch.process_record) throw Object.assign(new Error('synthetic record failure'), { code: 'EIO' });
    return (originalWrite as Function)(filename, ...args);
  }) as typeof fs.writeFileSync;
  t.after(() => { fs.writeFileSync = originalWrite; });
  await assert.rejects(launchDevelopment(prepared), /PID 987654.*保留启动锁/);
  assert.equal(mocked.calls.length, 1);
  assert.equal(fs.existsSync(path.join(f.profile, 'runtime', 'launch.lock')), true);
});

test('cleanup preserves a lock replaced by another inode during synthetic spawn', async t => {
  const f = fixture(t), prepared = prepareDevelopment(f), mocked = mockedProcess(t);
  const lock = path.join(f.profile, 'runtime', 'launch.lock');
  mocked.onSpawn(() => {
    const replacement = lock + '.replacement';
    fs.writeFileSync(replacement, 'synthetic replacement lock', { mode: 0o600 });
    fs.renameSync(replacement, lock);
  });
  await launchDevelopment(prepared);
  assert.equal(fs.readFileSync(lock, 'utf8'), 'synthetic replacement lock');
  assert.equal(mocked.calls.length, 1);
});
