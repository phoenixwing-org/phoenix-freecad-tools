// SPDX-License-Identifier: Apache-2.0
import { test, type TestContext } from 'node:test';
import assert from 'node:assert/strict';
import { realpathSync, readdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { Client } from '@modelcontextprotocol/client';
import { StdioClientTransport } from '@modelcontextprotocol/client/stdio';
import { fixture, FEATURES } from './helpers/desk-cache-fixture.js';

function record(value: unknown): Record<string, unknown> {
  assert.ok(value !== null && typeof value === 'object' && !Array.isArray(value)); return value as Record<string, unknown>;
}
async function connection(t: TestContext, scope: 'full' | 'chat') {
  const f = await fixture(t), client = new Client({ name: 'pft-neutral-runtime-stdio', version: '0.3.2' });
  const transport = new StdioClientTransport({ command: realpathSync(process.execPath),
    args: [fileURLToPath(new URL('../dist/cli.js', import.meta.url))],
    env: { PHOENIX_FREECAD_URL: f.origin + '/', PHOENIX_FREECAD_TOOL_SCOPE: scope,
      ...(scope === 'chat' ? { PHOENIX_DESK_CHAT_READS: '1' } : {}),
      PHOENIX_DESK_NODE: f.deskConfig.nodeExecutable, PHOENIX_DESK_CLI: f.deskConfig.cliEntry, PHOENIX_DESK_STATE_DIR: f.stateDir }, stderr: 'pipe' });
  await client.connect(transport); t.after(() => client.close()); return { f, client };
}

test('full and explicitly enabled chat expose runtime observation through the existing read-only status tool', async t => {
  for (const scope of ['full', 'chat'] as const) {
    const { f, client } = await connection(t, scope), tools = await client.listTools();
    assert.equal(tools.tools.length, scope === 'full' ? 30 : 24);
    const tool = tools.tools.find(tool => tool.name === 'desk_get_connection_status')!;
    assert.equal(tool.annotations?.readOnlyHint, true); assert.equal(tool.annotations?.destructiveHint, false);
    assert.equal(f.calls.length, 0); assert.equal(f.cliCalls().length, 0);
    const basic = await client.callTool({ name: tool.name, arguments: {} });
    assert.equal(basic.isError, undefined); assert.equal('runtime_features' in record(record(basic.structuredContent).data), false);
    assert.deepEqual(f.calls.map(call => call.url.pathname), ['/api/desk-service']);
    const result = await client.callTool({ name: tool.name, arguments: { includeRuntimeFeatures: true } });
    assert.equal(result.isError, undefined); const value = record(result.structuredContent), status = record(value.data);
    assert.equal(value.source_kind, 'desk_service'); assert.equal(status.state, 'ready');
    const feature = record(status.runtime_features); assert.equal(feature.state, 'observed');
    assert.equal(record(feature.observation).request_checks, 'not_performed');
    assert.deepEqual(JSON.parse(String(record(result.content[0]).text)), value); f.noPrivate(result);
    assert.equal(JSON.stringify(result).includes('reason'), false);
    assert.ok(Buffer.byteLength(JSON.stringify(result)) < 128 * 1024);
    assert.deepEqual(f.calls.slice(1).map(call => call.url.pathname), ['/api/desk-service', FEATURES, '/api/desk-service']);
    assert.deepEqual(readdirSync(f.workspace), []); assert.deepEqual(readdirSync(f.stateDir), []);
  }
});

test('stdio runtime status validates strict boolean input and distinguishes absent from incompatible features', async t => {
  const { f, client } = await connection(t, 'chat');
  for (const args of [{ includeRuntimeFeatures: 'true' }, { includeRuntimeFeatures: null }, { includeRuntimeFeatures: 1 },
    { includeRuntimeFeatures: true, action: 'ensure' }, { includeRuntimeFeatures: true, workspace: f.workspace }]) {
    const result = await client.callTool({ name: 'desk_get_connection_status', arguments: args }); assert.equal(result.isError, true);
  }
  assert.equal(f.calls.length, 0); assert.equal(f.cliCalls().length, 0);
  f.setMode('feature-404');
  const absent = await client.callTool({ name: 'desk_get_connection_status', arguments: { includeRuntimeFeatures: true } });
  assert.equal(absent.isError, undefined);
  assert.deepEqual(record(record(absent.structuredContent).data).runtime_features, { state: 'not_declared', reason_code: 'endpoint_absent' });
  f.noPrivate(absent); f.setMode('normal'); f.setFeatures(value => { value.schema_version = 2; return value; });
  const invalid = await client.callTool({ name: 'desk_get_connection_status', arguments: { includeRuntimeFeatures: true } });
  assert.equal(invalid.isError, true); assert.equal(record(record(invalid.structuredContent).error).code, 'DESK_RUNTIME_INVALID_RESPONSE');
  f.noPrivate(invalid); assert.equal(f.cliCalls().length, 2); assert.equal(f.calls.length, 5);
});
