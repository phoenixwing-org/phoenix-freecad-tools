import { test, type TestContext } from 'node:test';
import assert from 'node:assert/strict';
import { chmodSync, existsSync, lstatSync, mkdirSync, mkdtempSync, realpathSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { loadConfig, readConfig } from '../src/config.js';
import { PftMcpSettingsError, pftResolveMcpSettingsPath, type PftMcpSettingsIO } from '../src/pft-mcp-settings.js';

test('本机配置只允许受支持的 IPv4 根地址，拒绝远端与 URL 凭据', () => {
  assert.equal(readConfig({}).timeoutMs, 15000);
  assert.equal(readConfig({ PHOENIX_FREECAD_URL: 'http://localhost:9876/' }).freecadUrl, 'http://localhost:9876/');
  for (const PHOENIX_FREECAD_URL of ['http://[::1]:9876/', 'http://localhost/ignored', 'https://localhost/', 'http://example.com/', 'http://secret@localhost/', 'http://localhost/?token=secret']) {
    assert.throws(() => readConfig({ PHOENIX_FREECAD_URL }));
  }
  assert.throws(() => readConfig({ PHOENIX_TIMEOUT_MS: 'NaN' }));
});


test('工具范围默认保持full，chat必须明确选择，拒绝未知或空范围', () => {
  assert.equal(readConfig({}).toolScope, 'full');
  assert.equal(readConfig({ PHOENIX_FREECAD_TOOL_SCOPE: 'full' }).toolScope, 'full');
  assert.equal(readConfig({ PHOENIX_FREECAD_TOOL_SCOPE: 'chat' }).toolScope, 'chat');
  for (const PHOENIX_FREECAD_TOOL_SCOPE of ['', 'CHAT', ' chat', 'unsafe']) {
    assert.throws(() => readConfig({ PHOENIX_FREECAD_TOOL_SCOPE }));
  }
});


test('Desk 三个可选路径必须一起明确配置；解析不访问路径或启动后台', () => {
  assert.equal(readConfig({}).desk, undefined);
  const configured = { PHOENIX_DESK_NODE: '/opt/example/node', PHOENIX_DESK_CLI: '/opt/example/desk/scripts/desk-web-service.mjs',
    PHOENIX_DESK_STATE_DIR: '/var/example/private-desk' };
  assert.deepEqual(readConfig(configured).desk, { nodeExecutable: configured.PHOENIX_DESK_NODE,
    cliEntry: configured.PHOENIX_DESK_CLI, stateDir: configured.PHOENIX_DESK_STATE_DIR });
  for (const invalid of [ { PHOENIX_DESK_NODE: configured.PHOENIX_DESK_NODE },
    { ...configured, PHOENIX_DESK_CLI: '' }, { ...configured, PHOENIX_DESK_STATE_DIR: 'relative' },
    { ...configured, PHOENIX_DESK_NODE: '/opt/../example/node' },
    { ...configured, PHOENIX_DESK_STATE_DIR: '/var/example/\u200bhidden' },
    { ...configured, PHOENIX_DESK_CLI: ' /opt/example/desk/scripts/desk-web-service.mjs' } ]) {
    assert.throws(() => readConfig(invalid));
  }
});


test('聊天 Desk 数据工具须精确 opt-in 且路径完整，错误标志不得降级启用', () => {
  const paths = { PHOENIX_DESK_NODE: '/opt/example/node', PHOENIX_DESK_CLI: '/opt/example/desk/scripts/desk-web-service.mjs',
    PHOENIX_DESK_STATE_DIR: '/var/example/private-desk' };
  assert.equal(readConfig({ ...paths, PHOENIX_FREECAD_TOOL_SCOPE: 'chat' }).deskChatReads, undefined);
  assert.equal(readConfig({ ...paths, PHOENIX_DESK_CHAT_READS: '0' }).deskChatReads, false);
  assert.equal(readConfig({ ...paths, PHOENIX_DESK_CHAT_READS: '1' }).deskChatReads, true);
  assert.throws(() => readConfig({ PHOENIX_DESK_CHAT_READS: '1' }));
  for (const PHOENIX_DESK_CHAT_READS of ['', 'true', 'yes', ' 1', '2', 'CHAT']) {
    assert.throws(() => readConfig({ ...paths, PHOENIX_DESK_CHAT_READS }));
  }
});


function settingsFixture(t: TestContext) {
  const directory = realpathSync(mkdtempSync(path.join(tmpdir(), 'pft-load-config-')));
  chmodSync(directory, 0o700);
  t.after(() => rmSync(directory, { recursive: true, force: true }));
  const options = { platform: 'linux' as const, home: directory };
  const configPath = pftResolveMcpSettingsPath({ ...options, env: {} });
  const mcp = path.dirname(configPath), product = path.dirname(mcp);
  const write = (contents: string) => {
    mkdirSync(mcp, { recursive: true, mode: 0o700 });
    chmodSync(product, 0o700); chmodSync(mcp, 0o700);
    writeFileSync(configPath, contents, { mode: 0o600 }); chmodSync(configPath, 0o600);
  };
  const port = (value: number) => write(JSON.stringify({ schema_version: 1, bridge: { preferred_port: value } }));
  return { directory, options, configPath, product, write, port };
}
function trappedSettingsIO() {
  let calls = 0;
  const trapped = (): never => { calls++; throw new Error('Settings IO must not be called'); };
  const io: PftMcpSettingsIO = { lstat: trapped, open: trapped, fstat: trapped, read: trapped, close: trapped };
  return { io, count: () => calls };
}

test('loadConfig explicit URL performs zero settings IO, even with invalid location options', async () => {
  const trap = trappedSettingsIO();
  const config = await loadConfig({ PHOENIX_FREECAD_URL: 'http://localhost:12001/', PHOENIX_TIMEOUT_MS: '3210' },
    { home: 'invalid-relative-home', io: trap.io });
  assert.equal(trap.count(), 0);
  assert.equal(config.freecadUrl, 'http://localhost:12001/');
  assert.equal(config.timeoutMs, 3210);
  assert.deepEqual(config.connectionConfiguration, { version: 1, scope: 'single_bridge', source: 'environment',
    config_path: null, preferred_port: 12001, target_url: 'http://localhost:12001/', discovery_supported: false });
  const httpDefault = await loadConfig({ PHOENIX_FREECAD_URL: 'http://localhost/' }, { io: trap.io });
  assert.equal(httpDefault.connectionConfiguration?.preferred_port, 80);
  assert.equal(trap.count(), 0);
});

test('loadConfig missing settings uses 9876 without creating or chmodding anything', async (t) => {
  const f = settingsFixture(t), before = lstatSync(f.directory, { bigint: true });
  const config = await loadConfig({}, f.options);
  assert.equal(config.freecadUrl, 'http://127.0.0.1:9876/');
  assert.deepEqual(config.connectionConfiguration, { version: 1, scope: 'single_bridge', source: 'default',
    config_path: f.configPath, preferred_port: 9876, target_url: config.freecadUrl, discovery_supported: false });
  assert.equal(existsSync(f.product), false);
  const after = lstatSync(f.directory, { bigint: true });
  assert.equal(after.mode, before.mode); assert.equal(after.mtimeNs, before.mtimeNs);
});

test('loadConfig existing private settings controls the bridge port while retaining other environment options', async (t) => {
  const f = settingsFixture(t); f.port(12002);
  const before = lstatSync(f.configPath, { bigint: true });
  const config = await loadConfig({ PHOENIX_TIMEOUT_MS: '2100', PHOENIX_FREECAD_TOOL_SCOPE: 'chat' }, f.options);
  assert.equal(config.freecadUrl, 'http://127.0.0.1:12002/');
  assert.equal(config.timeoutMs, 2100); assert.equal(config.toolScope, 'chat');
  assert.equal(config.connectionConfiguration?.source, 'user_config');
  assert.equal(config.connectionConfiguration?.config_path, f.configPath);
  assert.equal(config.connectionConfiguration?.preferred_port, 12002);
  assert.equal(config.connectionConfiguration?.scope, 'single_bridge');
  assert.equal(config.connectionConfiguration?.discovery_supported, false);
  const after = lstatSync(f.configPath, { bigint: true });
  assert.equal(after.ino, before.ino); assert.equal(after.mode, before.mode); assert.equal(after.mtimeNs, before.mtimeNs);
});

test('loadConfig existing invalid, unsupported or unsafe configuration never falls back', async (t) => {
  const f = settingsFixture(t);
  for (const [contents, code] of [
    ['{broken', 'MCP_SETTINGS_INVALID'],
    ['{"schema_version":3,"bridge":{"preferred_port":12003}}', 'MCP_SETTINGS_VERSION_UNSUPPORTED'],
    ['{"schema_version":2,"bridge":{"preferred_port":12003}}', 'MCP_SETTINGS_INVALID'],
    ['{"schema_version":1,"bridge":{"preferred_port":12003.0}}', 'MCP_SETTINGS_INVALID'],
  ] as const) {
    f.write(contents);
    await assert.rejects(loadConfig({}, f.options), (error: unknown) => error instanceof PftMcpSettingsError && error.code === code);
  }
  f.port(12003); chmodSync(f.configPath, 0o640);
  await assert.rejects(loadConfig({}, f.options), (error: unknown) => error instanceof PftMcpSettingsError && error.code === 'MCP_SETTINGS_UNSAFE');
});

test('loadConfig explicit URL ignores existing bad settings and validates invalid environment before any IO', async (t) => {
  const f = settingsFixture(t); f.write('private malformed fixture');
  const config = await loadConfig({ PHOENIX_FREECAD_URL: 'http://127.0.0.1:12004/' }, f.options);
  assert.equal(config.freecadUrl, 'http://127.0.0.1:12004/');
  assert.equal(config.connectionConfiguration?.source, 'environment');
  assert.equal(config.connectionConfiguration?.config_path, null);
  const trap = trappedSettingsIO();
  for (const env of [ { PHOENIX_FREECAD_URL: '' }, { PHOENIX_FREECAD_URL: 'http://remote.invalid/' },
    { PHOENIX_FREECAD_TOOL_SCOPE: '' }, { PHOENIX_TIMEOUT_MS: 'not-a-number' } ]) {
    await assert.rejects(loadConfig(env, { io: trap.io }));
  }
  assert.equal(trap.count(), 0);
});

test('v2 discovery optin resolves a runtime location without creating or selecting an instance', async (t) => {
  const f = settingsFixture(t);
  f.write(JSON.stringify({ schema_version: 2, bridge: { preferred_port: 12010 }, discovery: { enabled: true } }));
  const before = lstatSync(f.configPath, { bigint: true });
  const config = await loadConfig({}, f.options);
  const runtimeDirectory = path.join(f.directory, '.local', 'state', 'PhoenixTools', 'mcp', 'runtime', 'instances');
  assert.deepEqual(config.instanceDiscovery, { runtimeDirectory });
  assert.equal(config.freecadUrl, 'http://127.0.0.1:12010/');
  assert.deepEqual(config.connectionConfiguration, { version: 2, scope: 'instance_discovery', source: 'user_config',
    config_path: f.configPath, preferred_port: 12010, target_url: null, discovery_supported: true,
    runtime_directory: runtimeDirectory });
  assert.equal(existsSync(path.join(f.directory, '.local')), false);
  const after = lstatSync(f.configPath, { bigint: true });
  assert.equal(after.ino, before.ino); assert.equal(after.mtimeNs, before.mtimeNs); assert.equal(after.mode, before.mode);
});

test('v2 disabled remains direct and does not evaluate invalid discovery runtime roots', async (t) => {
  const f = settingsFixture(t);
  f.write(JSON.stringify({ schema_version: 2, bridge: { preferred_port: 12011 }, discovery: { enabled: false } }));
  const config = await loadConfig({ XDG_RUNTIME_DIR: 'invalid-relative-runtime' }, f.options);
  assert.equal(config.instanceDiscovery, undefined);
  assert.deepEqual(config.connectionConfiguration, { version: 1, scope: 'single_bridge', source: 'user_config',
    config_path: f.configPath, preferred_port: 12011, target_url: 'http://127.0.0.1:12011/', discovery_supported: false });
});

test('explicit URL wins over enabled discovery with zero shared reads or runtime resolution', async () => {
  const trap = trappedSettingsIO();
  const config = await loadConfig({ PHOENIX_FREECAD_URL: 'http://127.0.0.1:12012/', XDG_RUNTIME_DIR: 'invalid-relative-runtime' },
    { home: 'invalid-relative-home', platform: 'linux', io: trap.io });
  assert.equal(trap.count(), 0);
  assert.equal(config.instanceDiscovery, undefined);
  assert.equal(config.connectionConfiguration?.scope, 'single_bridge');
  assert.equal(config.connectionConfiguration?.source, 'environment');
});

test('discovery chat and worker fail before resolver rather than choosing any default instance', async (t) => {
  const f = settingsFixture(t);
  f.write(JSON.stringify({ schema_version: 2, bridge: { preferred_port: 12013 }, discovery: { enabled: true } }));
  for (const env of [{ PHOENIX_FREECAD_TOOL_SCOPE: 'chat' }, { PHOENIX_FREECAD_SCOPE: 'worker' }]) {
    await assert.rejects(loadConfig({ ...env, XDG_RUNTIME_DIR: 'invalid-relative-runtime' },
      { ...f.options, home: 'invalid-relative-home', paths: { configPath: f.configPath } }),
    /不支持自动发现/u);
    const explicit = await loadConfig({ ...env, PHOENIX_FREECAD_URL: 'http://127.0.0.1:12014/' },
      { ...f.options, io: trappedSettingsIO().io });
    assert.equal(explicit.instanceDiscovery, undefined);
  }
});

test('enabled discovery propagates invalid explicit runtime roots without namespace fallback', async (t) => {
  const f = settingsFixture(t);
  f.write(JSON.stringify({ schema_version: 2, bridge: { preferred_port: 12015 }, discovery: { enabled: true } }));
  await assert.rejects(loadConfig({ XDG_RUNTIME_DIR: 'invalid-relative-runtime' }, f.options));
});

test('loadConfig reads changes only on a new load and never mutates a previously resolved target', async (t) => {
  const f = settingsFixture(t); f.port(12005);
  const first = await loadConfig({}, f.options); f.port(12006);
  const second = await loadConfig({}, f.options);
  assert.equal(first.freecadUrl, 'http://127.0.0.1:12005/');
  assert.equal(first.connectionConfiguration?.preferred_port, 12005);
  assert.equal(second.freecadUrl, 'http://127.0.0.1:12006/');
  assert.equal(second.connectionConfiguration?.preferred_port, 12006);
});


test('worker scope is explicit and isolated while legacy full/chat configuration stays compatible', () => {
  assert.equal(readConfig({ PHOENIX_FREECAD_SCOPE: 'worker' }).toolScope, 'worker');
  assert.equal(readConfig({ PHOENIX_FREECAD_SCOPE: 'worker', PHOENIX_FREECAD_TOOL_SCOPE: 'chat' }).toolScope, 'worker');
  for (const PHOENIX_FREECAD_SCOPE of ['', 'full', 'chat', 'WORKER', 'worker ']) assert.throws(() => readConfig({ PHOENIX_FREECAD_SCOPE }));
  assert.throws(() => readConfig({ PHOENIX_FREECAD_SCOPE: 'worker', PHOENIX_FREECAD_TOOL_SCOPE: 'invalid' }));
});
