// SPDX-License-Identifier: Apache-2.0
/** Isolated configuration tests: no real FreeCAD, user settings or shell commands. */
import { test, type TestContext } from 'node:test';
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { chmodSync, existsSync, mkdirSync, mkdtempSync, realpathSync, rmSync, writeFileSync } from 'node:fs';
import { createServer as createHttpServer } from 'node:http';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { Client } from '@modelcontextprotocol/client';
import { getDefaultEnvironment, StdioClientTransport } from '@modelcontextprotocol/client/stdio';
import { pftResolveMcpSettingsPath } from '../src/pft-mcp-settings.js';

const root = fileURLToPath(new URL('../', import.meta.url));
// Test-harness only: after building, the same fixtures can exercise the packaged CLI.
// This does not add an environment override to the production configuration.
const entryKind = process.env.PFT_TEST_MCP_ENTRY === 'dist' ? 'dist' : 'source';
const entry = fileURLToPath(new URL(entryKind === 'dist' ? '../dist/cli.js' : '../src/cli.ts', import.meta.url));
const posixOnly = process.platform === 'win32' ? 'Existing Windows settings require an ACL verifier; covered by parser/loader tests.' : false;

type Fixture = ReturnType<typeof fixture>;
function fixture(t: TestContext) {
  const directory = realpathSync(mkdtempSync(path.join(tmpdir(), 'pft-mcp-config-integration-')));
  chmodSync(directory, 0o700);
  t.after(() => rmSync(directory, { recursive: true, force: true }));
  // Preserve the SDK safe inherited environment, including the actual HOME.
  // XDG is a supported configuration-root setting, separate from HOME.
  const env = { ...getDefaultEnvironment(), XDG_CONFIG_HOME: path.join(directory, 'xdg'), PHOENIX_TIMEOUT_MS: '1200' };
  const configPath = pftResolveMcpSettingsPath({ platform: process.platform, home: directory, env });
  const mcp = path.dirname(configPath), product = path.dirname(mcp);
  const preload = path.join(directory, 'fixture-homedir.mjs');
  const allowedPortsPath = path.join(directory, 'allowed-bridge-ports.json');
  const allowedPorts = new Set<number>();
  const allowPort = (port: number) => {
    assert.ok(Number.isSafeInteger(port) && port >= 1024 && port <= 65535);
    allowedPorts.add(port); writeFileSync(allowedPortsPath, JSON.stringify([...allowedPorts]), { mode: 0o600 });
  };
  writeFileSync(allowedPortsPath, '[]', { mode: 0o600 });
  writeFileSync(preload, `import os from 'node:os';\nimport fs from 'node:fs';\nimport http from 'node:http';\nimport {syncBuiltinESMExports} from 'node:module';\nconst fixtureRoot=${JSON.stringify(directory)};\nos.homedir=()=>fixtureRoot;\nconst allowedPorts=JSON.parse(fs.readFileSync(${JSON.stringify(allowedPortsPath)},'utf8'));\nconst request=http.request;\nhttp.request=function(url,...args){\n if(!(url instanceof URL) || url.protocol!=='http:' || url.hostname!=='127.0.0.1' || !allowedPorts.includes(Number(url.port||80))) throw new Error('Fixture denied external bridge');\n return Reflect.apply(request,http,[url,...args]);\n};\nfor(const operation of ['lstatSync','openSync']){\n const original=fs[operation];\n fs[operation]=function(filename,...args){\n  if(typeof filename==='string' && /[/\\\\]PhoenixTools(?:[/\\\\]|$)/u.test(filename) && !filename.startsWith(fixtureRoot+'/')) throw new Error('Fixture denied external settings access');\n  return Reflect.apply(original,fs,[filename,...args]);\n };\n}\nsyncBuiltinESMExports();\n`, { mode: 0o600 });
  const args = ['--import', pathToFileURL(preload).href, ...(entryKind === 'source' ? ['--import', 'tsx'] : []), entry];
  const write = (text: string) => {
    mkdirSync(mcp, { recursive: true, mode: 0o700 });
    chmodSync(product, 0o700); chmodSync(mcp, 0o700);
    writeFileSync(configPath, text, { mode: 0o600 }); chmodSync(configPath, 0o600);
  };
  return { directory, configPath, product, env, args, write, allowPort,
    setPort: (port: number) => { allowPort(port); write(JSON.stringify({ schema_version: 1, bridge: { preferred_port: port } })); } };
}

async function bridge(t: TestContext, marker: string) {
  const calls: string[] = [];
  const server = createHttpServer(async (req, res) => {
    try {
      assert.equal(req.method, 'POST'); assert.equal(req.url, '/rpc');
      let text = '';
      for await (const chunk of req) {
        text += String(chunk); if (Buffer.byteLength(text) > 4096) throw new Error('Fixture request budget exceeded');
      }
      const request = JSON.parse(text) as { method: string; params: Record<string, unknown> };
      assert.deepEqual(request.params, {});
      calls.push(request.method);
      assert.ok(['get_status', 'list_documents'].includes(request.method));
      const data = request.method === 'get_status'
        ? { protocol_version: 1, running: true, addon_version: '0.3.2', supported_methods: ['get_status', 'list_documents'], fixture_marker: marker }
        : ['PublicFixture'];
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: true, data }));
    } catch {
      res.writeHead(400, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: false, error: { code: 'FIXTURE_REQUEST_REJECTED', message: 'Unexpected fixture request' } }));
    }
  });
  await new Promise<void>((resolve, reject) => { server.once('error', reject); server.listen(0, '127.0.0.1', resolve); });
  t.after(async () => { server.closeAllConnections(); await new Promise<void>(resolve => server.close(() => resolve())); });
  const address = server.address(); assert.ok(address && typeof address === 'object');
  return { calls, port: address.port, url: `http://127.0.0.1:${address.port}/` };
}

async function runCli(f: Fixture, extraEnv: Record<string, string> = {}, args = ['--check']) {
  return new Promise<{ code: number | null; stdout: string; stderr: string }>((resolve, reject) => {
    const child = spawn(process.execPath, [...f.args, ...args], { cwd: root, env: { ...f.env, ...extraEnv }, shell: false,
      stdio: ['ignore', 'pipe', 'pipe'] });
    let stdout = '', stderr = '', terminalError: Error | undefined;
    const timer = setTimeout(() => { terminalError = new Error('Isolated CLI deadline exceeded'); child.kill('SIGKILL'); }, 10000);
    child.stdout.on('data', chunk => { stdout += String(chunk); if (stdout.length > 65536) { terminalError = new Error('CLI stdout budget exceeded'); child.kill('SIGKILL'); } });
    child.stderr.on('data', chunk => { stderr += String(chunk); if (stderr.length > 65536) { terminalError = new Error('CLI stderr budget exceeded'); child.kill('SIGKILL'); } });
    child.once('error', error => { clearTimeout(timer); reject(error); });
    child.once('close', code => { clearTimeout(timer); if (terminalError) reject(terminalError); else resolve({ code, stdout, stderr }); });
  });
}
async function session(t: TestContext, f: Fixture, toolScope: 'full' | 'chat' = 'full') {
  const transport = new StdioClientTransport({ command: process.execPath, args: f.args, cwd: root,
    env: { ...f.env, PHOENIX_FREECAD_TOOL_SCOPE: toolScope }, stderr: 'pipe' });
  const client = new Client({ name: 'pft-shared-config-fixture', version: '0.3.2' });
  let stderr = '';
  transport.stderr?.on('data', chunk => { stderr += String(chunk); });
  t.after(() => client.close());
  await client.connect(transport, { timeout: 10000 });
  return { client, stderr: () => stderr };
}
function parseCheck(result: Awaited<ReturnType<typeof runCli>>) {
  assert.equal(result.code, 0, result.stderr); assert.equal(result.stderr, '');
  const output = JSON.parse(result.stdout) as { ok: boolean; data: Record<string, unknown> };
  assert.equal(output.ok, true); return output.data;
}
function record(value: unknown): Record<string, unknown> {
  assert.ok(value !== null && typeof value === 'object' && !Array.isArray(value));
  return value as Record<string, unknown>;
}
function diagnostic(data: Record<string, unknown>, f: Fixture, port: number, source = 'user_config') {
  assert.deepEqual(data.mcp_connection_configuration, { version: 1, scope: 'single_bridge', source,
    config_path: source === 'environment' ? null : f.configPath,
    preferred_port: port, target_url: `http://127.0.0.1:${port}/`, discovery_supported: false });
}

test(`isolated ${entryKind} CLI --check reads the saved private port and reports its source`, { skip: posixOnly, timeout: 20000 }, async (t) => {
  const f = fixture(t), fake = await bridge(t, 'saved-config'); f.setPort(fake.port);
  const data = parseCheck(await runCli(f));
  assert.equal(data.fixture_marker, 'saved-config'); diagnostic(data, f, fake.port);
  assert.deepEqual(fake.calls, ['get_status']);
});

test(`isolated ${entryKind} CLI explicit URL bypasses malformed shared settings`, { skip: posixOnly, timeout: 20000 }, async (t) => {
  const f = fixture(t), fake = await bridge(t, 'explicit-url'); f.allowPort(fake.port); f.write('Malformed private fixture, never returned');
  const data = parseCheck(await runCli(f, { PHOENIX_FREECAD_URL: fake.url }));
  assert.equal(data.fixture_marker, 'explicit-url'); diagnostic(data, f, fake.port, 'environment');
  assert.deepEqual(fake.calls, ['get_status']);
});

test(`isolated ${entryKind} CLI refuses bad existing settings before any bridge request`, { skip: posixOnly, timeout: 30000 }, async (t) => {
  const f = fixture(t), fake = await bridge(t, 'must-not-be-used');
  for (const [contents, code] of [
    ['private fixture contents must never be reflected', 'MCP_SETTINGS_INVALID'],
    [JSON.stringify({ schema_version: 3, bridge: { preferred_port: fake.port } }), 'MCP_SETTINGS_VERSION_UNSUPPORTED'],
  ] as const) {
    f.write(contents); const result = await runCli(f);
    assert.equal(result.code, 1); assert.equal(result.stdout, '');
    const error = JSON.parse(result.stderr) as { ok: boolean; error: { code: string; message: string } };
    assert.equal(error.ok, false); assert.equal(error.error.code, code);
    assert.equal(result.stderr.includes(contents), false);
    assert.equal(result.stderr.includes(f.configPath), false);
  }
  f.setPort(fake.port); chmodSync(f.configPath, 0o640);
  const unsafe = await runCli(f);
  assert.equal(unsafe.code, 1); assert.equal(unsafe.stdout, '');
  assert.equal(JSON.parse(unsafe.stderr).error.code, 'MCP_SETTINGS_UNSAFE');
  assert.deepEqual(fake.calls, []);
});

for (const toolScope of ['full', 'chat'] as const) {
  test(`official SDK ${entryKind} stdio ${toolScope} uses shared diagnostics and pins its resolved target`, { skip: posixOnly, timeout: 30000 }, async (t) => {
    const f = fixture(t), first = await bridge(t, 'first'), second = await bridge(t, 'second'); f.setPort(first.port);
    const { client, stderr } = await session(t, f, toolScope);
    const tools = await client.listTools(); assert.equal(tools.tools.length, toolScope === 'full' ? 25 : 19);
    const capabilities = await client.callTool({ name: 'get_capabilities', arguments: {} });
    assert.equal(record(capabilities.structuredContent).connection_checked, false);
    assert.deepEqual(first.calls, []); assert.deepEqual(second.calls, []);
    const status = await client.callTool({ name: 'get_connection_status', arguments: {} });
    assert.equal(status.isError, undefined);
    const data = record(record(status.structuredContent).data);
    assert.equal(data.fixture_marker, 'first'); diagnostic(data, f, first.port);
    const documents = await client.callTool({ name: 'list_documents', arguments: {} });
    assert.deepEqual(record(documents.structuredContent).data, ['PublicFixture']);
    f.setPort(second.port);
    const sameSession = await client.callTool({ name: 'get_connection_status', arguments: {} });
    const stillFirst = record(record(sameSession.structuredContent).data);
    assert.equal(stillFirst.fixture_marker, 'first'); diagnostic(stillFirst, f, first.port);
    assert.deepEqual(second.calls, []);
    const nextStartup = parseCheck(await runCli(f));
    assert.equal(nextStartup.fixture_marker, 'second'); diagnostic(nextStartup, f, second.port);
    assert.deepEqual(first.calls, ['get_status', 'get_status', 'list_documents', 'get_status']);
    assert.deepEqual(second.calls, ['get_status']); assert.equal(stderr(), '');
  });
}

test(`official SDK ${entryKind} stdio missing settings keeps offline discovery side-effect free`, { skip: posixOnly, timeout: 20000 }, async (t) => {
  const f = fixture(t); assert.equal(existsSync(f.product), false);
  const { client, stderr } = await session(t, f);
  assert.equal((await client.listTools()).tools.length, 25);
  const capabilities = await client.callTool({ name: 'get_capabilities', arguments: {} });
  assert.equal(record(capabilities.structuredContent).connection_checked, false);
  // Do not call status: an absent setting uses 9876, which might be a user's live bridge.
  assert.equal(existsSync(f.product), false); assert.equal(existsSync(f.configPath), false);
  assert.equal(stderr(), '');
});
