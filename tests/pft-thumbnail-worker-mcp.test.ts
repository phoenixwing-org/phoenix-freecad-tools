// SPDX-License-Identifier: Apache-2.0
import { test, type TestContext } from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { fileURLToPath } from 'node:url';
import { Client } from '@modelcontextprotocol/client';
import { StdioClientTransport, getDefaultEnvironment } from '@modelcontextprotocol/client/stdio';
import { PFT_THUMBNAIL_WORKER_METHODS } from '../src/pft-thumbnail-worker.js';
import { IDS, INPUT_HASH, png, status, job } from './helpers/pft-thumbnail-worker-fixture.js';
const root = fileURLToPath(new URL('../', import.meta.url));
const source = fileURLToPath(new URL('../src/cli.ts', import.meta.url));
const args = ['--import', 'tsx', source];
type Call = { method: string; params: Record<string, unknown> };
const record = (value: unknown): Record<string, unknown> => { assert.ok(value && typeof value === 'object' && !Array.isArray(value)); return value as Record<string, unknown>; };
async function fixture(t: TestContext, scope: 'worker' | 'full' | 'chat' = 'worker') {
  const calls: Call[] = [];
  const state: { reply: unknown; error: string | null } = { reply: job(), error: null };
  const server = createServer(async (req, res) => {
    let text = ''; for await (const chunk of req) text += chunk;
    const body = JSON.parse(text) as Call; calls.push(body); res.setHeader('Content-Type', 'application/json');
    if (body.method === 'get_status') { res.end(JSON.stringify({ ok: true, data: { protocol_version: 1, supported_methods: [...PFT_THUMBNAIL_WORKER_METHODS] } })); return; }
    if (body.method === 'get_thumbnail_worker_status') { res.end(JSON.stringify({ ok: true, data: status() })); return; }
    if (state.error) { res.end(JSON.stringify({ ok: false, error: { code: state.error, message: 'Synthetic worker envelope' } })); return; }
    res.end(JSON.stringify({ ok: true, data: state.reply }));
  });
  await new Promise<void>(resolve => server.listen(0, '127.0.0.1', resolve));
  t.after(async () => { server.closeAllConnections(); await new Promise<void>(resolve => server.close(() => resolve())); });
  const env = { ...getDefaultEnvironment(), PHOENIX_FREECAD_URL: `http://127.0.0.1:${(server.address() as { port: number }).port}/`,
    PHOENIX_FREECAD_TOOL_SCOPE: scope === 'worker' ? 'chat' : scope, ...(scope === 'worker' ? { PHOENIX_FREECAD_SCOPE: 'worker' } : {}) };
  const transport = new StdioClientTransport({ command: process.execPath, args, cwd: root, env, stderr: 'pipe' });
  const client = new Client({ name: 'pft-thumbnail-worker-sdk-fixture', version: '0.3.2' });
  let stderr = ''; transport.stderr?.on('data', chunk => { stderr += String(chunk); });
  t.after(() => client.close()); await client.connect(transport, { timeout: 10000 });
  return { calls, state, client, stderr: () => stderr, env };
}

test('official SDK worker stdio registers exactly four independent tools and does no discovery IO', { timeout: 20000 }, async (t) => {
  const f = await fixture(t), tools = await f.client.listTools();
  assert.deepEqual(tools.tools.map(tool => tool.name).sort(), [...PFT_THUMBNAIL_WORKER_METHODS].sort());
  assert.equal(f.calls.length, 0);
  assert.equal(tools.tools.find(tool => tool.name === 'get_thumbnail_job')?.annotations?.readOnlyHint, true);
  assert.equal(tools.tools.find(tool => tool.name === 'render_document_thumbnail')?.annotations?.readOnlyHint, false);
  await assert.rejects(f.client.callTool({ name: 'get_capabilities', arguments: {} }), { code: -32602 });
  assert.equal(f.calls.length, 0);
  const actual = await f.client.callTool({ name: 'get_thumbnail_worker_status', arguments: {} });
  assert.equal(actual.isError, undefined); assert.deepEqual(record(actual.structuredContent).data, status());
  assert.deepEqual(f.calls.map(call => call.method), ['get_status', 'get_thumbnail_worker_status']);
  assert.equal(f.stderr(), '');
});

test('official SDK typed worker maps literal render/get/cancel, queries expiry and never restarts a job', { timeout: 20000 }, async (t) => {
  const f = await fixture(t), file = '/synthetic/literal `$(not executed)` "name".FCStd';
  const queued = await f.client.callTool({ name: 'render_document_thumbnail', arguments: { ...IDS, file, expectedSha256: INPUT_HASH } });
  assert.equal(record(record(queued.structuredContent).data).state, 'queued');
  assert.deepEqual(f.calls.at(-1), { method: 'render_document_thumbnail', params: { ...IDS, file, expectedSha256: INPUT_HASH, width: 512, height: 512 } });
  f.state.reply = { ...job('opening'), cancel_requested: true };
  const cancel = await f.client.callTool({ name: 'cancel_thumbnail_job', arguments: IDS });
  const pending = record(record(cancel.structuredContent).data); assert.equal(pending.state, 'opening'); assert.equal(pending.cancel_requested, true);
  f.state.reply = job('done'); const done = await f.client.callTool({ name: 'get_thumbnail_job', arguments: IDS });
  assert.equal(record(record(done.structuredContent).data).state, 'done');
  f.state.error = 'JOB_RESULT_EXPIRED'; const expired = await f.client.callTool({ name: 'get_thumbnail_job', arguments: IDS });
  assert.equal(expired.isError, true); assert.equal(record(record(expired.structuredContent).error).code, 'JOB_RESULT_EXPIRED');
  assert.equal(f.calls.filter(call => call.method === 'render_document_thumbnail').length, 1);
  assert.equal(f.stderr(), '');
});

test('official SDK worker rejects invalid and unknown fields before any bridge dispatch', { timeout: 20000 }, async (t) => {
  const f = await fixture(t);
  for (const [name, arguments_] of [
    ['get_thumbnail_worker_status', { workerId: IDS.workerId }], ['get_thumbnail_job', { ...IDS, width: 10 }],
    ['cancel_thumbnail_job', { ...IDS, sessionId: 'missing' }],
    ['render_document_thumbnail', { ...IDS, file: 'relative.FCStd', expectedSha256: INPUT_HASH }],
    ['render_document_thumbnail', { ...IDS, file: '/synthetic/sample.FCStd', expectedSha256: INPUT_HASH, width: 1025 }],
    ['render_document_thumbnail', { ...IDS, file: '/synthetic/sample.FCStd', expectedSha256: INPUT_HASH, saved_cad: true }],
  ] as const) {
    const result = await f.client.callTool({ name, arguments: arguments_ }); assert.equal(result.isError, true, name);
  }
  assert.equal(f.calls.length, 0);
  f.state.reply = { ...job('done'), session_id: IDS.workerId };
  const foreign = await f.client.callTool({ name: 'get_thumbnail_job', arguments: IDS });
  assert.equal(foreign.isError, true); assert.equal(record(record(foreign.structuredContent).error).code, 'INVALID_RESPONSE');
});

test('official SDK worker transports a valid PNG over 3MiB only once in the MCP result', { timeout: 20000 }, async (t) => {
  const f = await fixture(t), image = png(1024, 1024); assert.ok(image.length > 3 * 1024 * 1024);
  f.state.reply = job('done', image);
  const result = await f.client.callTool({ name: 'get_thumbnail_job', arguments: IDS }); assert.equal(result.isError, undefined);
  const thumbnail = record(record(record(result.structuredContent).data).result);
  assert.equal(thumbnail.bytes, image.length); assert.equal(thumbnail.png_base64, image.toString('base64'));
  const serialized = JSON.stringify(result);
  assert.equal(serialized.split(image.toString('base64')).length, 2); assert.ok(Buffer.byteLength(serialized) < 6 * 1024 * 1024);
  assert.equal(f.stderr(), '');
});

for (const scope of ['full', 'chat'] as const) {
  test(`official SDK source keeps existing ${scope} tools and does not expose the worker surface`, { timeout: 20000 }, async (t) => {
    const f = await fixture(t, scope), tools = await f.client.listTools(); assert.equal(tools.tools.length, scope === 'full' ? 28 : 22);
    assert.equal(tools.tools.some(tool => (PFT_THUMBNAIL_WORKER_METHODS as readonly string[]).includes(tool.name)), false);
    const capabilities = await f.client.callTool({ name: 'get_capabilities', arguments: {} });
    assert.equal(record(capabilities.structuredContent).connection_checked, false); assert.equal(f.calls.length, 0);
  });
}

test('worker CLI --check uses typed status and --demo fails without dispatch', { timeout: 20000 }, async (t) => {
  const f = await fixture(t), run = promisify(execFile);
  const check = await run(process.execPath, [...args, '--check'], { cwd: root, env: f.env, timeout: 10000 });
  assert.equal(check.stderr, ''); assert.equal(record(JSON.parse(check.stdout).data).worker_id, IDS.workerId);
  assert.deepEqual(f.calls.map(call => call.method), ['get_status', 'get_thumbnail_worker_status']);
  const before = f.calls.length;
  await assert.rejects(run(process.execPath, [...args, '--demo'], { cwd: root, env: f.env, timeout: 10000 }));
  assert.equal(f.calls.length, before);
});
