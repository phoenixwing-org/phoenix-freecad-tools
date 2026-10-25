// SPDX-License-Identifier: Apache-2.0
import { test, type TestContext } from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { chmodSync, existsSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, realpathSync, rmSync, statSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { Client } from '@modelcontextprotocol/client';
import { StdioClientTransport } from '@modelcontextprotocol/client/stdio';

const INSTANCE = 'c87a5b31-4284-49a1-bcd6-3c8f1c792b62';
const RUNTIME = 'sha256:' + 'b'.repeat(64);
const PRIVATE_MARKER = 'synthetic-private-detail-never-public';
const DESK_TOOLS = ['desk_get_connection_status', 'desk_search_index', 'desk_query_references', 'desk_query_cached_metadata', 'desk_get_cached_thumbnail'];
const RESULT_LIMIT = 128 * 1024;
const CLI_SOURCE = `import fs from 'node:fs';
const fixture = JSON.parse(fs.readFileSync(new URL('./fixture.json', import.meta.url), 'utf8'));
fs.appendFileSync(fixture.log, JSON.stringify({ args: process.argv.slice(2) }) + '\\n');
console.log(JSON.stringify(fixture.ready));
`;
const bytes = (value: unknown) => Buffer.byteLength(JSON.stringify(value), 'utf8');
function record(value: unknown): Record<string, unknown> {
  assert.ok(value !== null && typeof value === 'object' && !Array.isArray(value));
  return value as Record<string, unknown>;
}
function freshness() {
  return { index_state: 'ready', index_pending: false, index_stale: false, index_reason: '',
    bom_index_state: 'ready', bom_index_pending: false, bom_index_stale: false, scan_id: 7, freshness_scope: 'file' };
}
function searchPayload(query: string, large = false) {
  const rows = Array.from({ length: large ? 80 : 1 }, (_, index) => {
    const filename = large ? `Item${String(index).padStart(3, '0')}.FCStd` : 'Bracket.FCStd';
    return { repo_rel_path: 'parts/' + filename, filename, asset_kind: 'part', part_number: 'SYN-ITEM',
      part_version: 'A', part_name: 'Neutral bracket', label: large ? 'x'.repeat(1024) : 'Neutral bracket',
      detail: PRIVATE_MARKER };
  });
  return { source: 'sqlite', query: query.trim().toLocaleLowerCase('en-US'), rows, limit: 200, limitReached: false,
    diagnostics: { state: 'ready', message: PRIVATE_MARKER, schemaVersion: 13, expectedSchemaVersion: 13,
      totalEntries: rows.length, missingEntries: 0, failedEntries: 0, failures: [{ rel: '../private', message: PRIVATE_MARKER }],
      lastScanAt: '2035-01-01T00:00:00.000Z',
      latestScan: { id: 7, status: 'done', root: PRIVATE_MARKER, at: '2035-01-01T00:00:00.000Z' } },
    detail: PRIVATE_MARKER };
}
function outgoingPayload(rel: string) {
  const reference = { id: 11, host_repo_rel_path: './' + rel, host_file_asset_id: 13,
    xlink_file_attr: '../private/Component.FCStd', target_basename: 'Component.FCStd',
    target_repo_rel_path: './parts/Component.FCStd', target_file_asset_id: 17, resolve_status: 'resolved',
    ref_kind: 'xlink_file_attr', detail: { root: PRIVATE_MARKER }, link_label: 'Component', rule_label: 'Component',
    label_mismatch: false, target_part_number: 'SYN-COMP', target_part_version: 'A', target_type_code: 'H',
    target_model_series: 'PUBLIC', target_part_name: 'Component', target_asset_label: 'Component' };
  return { host_repo_rel_path: './' + rel, count: 1, label_mismatch_count: 0, refs: [reference], items: [reference], ...freshness() };
}

/** Owns every process, HTTP request and filesystem item used by this fixture. */
async function fixture(t: TestContext) {
  const directory = realpathSync(mkdtempSync(path.join(tmpdir(), 'pft-desk-mcp-fixture-')));
  chmodSync(directory, 0o700);
  const repo = path.join(directory, 'candidate'), scripts = path.join(repo, 'scripts');
  const state = path.join(directory, 'state'), workspace = path.join(directory, 'workspace');
  const log = path.join(directory, 'cli-calls.jsonl'), cliEntry = path.join(scripts, 'desk-web-service.mjs');
  mkdirSync(scripts, { recursive: true }); mkdirSync(state, { mode: 0o700 }); mkdirSync(workspace, { mode: 0o700 });
  writeFileSync(cliEntry, CLI_SOURCE, { mode: 0o600 });
  let origin = '', large = false;
  const calls: Array<{ method: string | undefined; url: URL; bytes: number }> = [];
  const clients: Client[] = [];
  const identity = () => ({ schema_version: 1, service: 'phoenix-desk-tools', protocol_version: 1, browser_host_protocol: 1,
    app_version: '0.5.0', instance_id: INSTANCE, runtime_id: RUNTIME, pid: 12001,
    api_origin: origin, web_url: origin + '/', capabilities: ['browser-web', 'local-api'],
    started_at: '2035-01-01T00:00:00.000Z' });
  const server = createServer((request, response) => {
    const url = new URL(request.url ?? '/', origin);
    let value: unknown;
    if (request.method !== 'GET') { response.statusCode = 405; value = {}; }
    else if (url.pathname === '/api/desk-service') value = identity();
    else if (url.pathname === '/api/cad/index/search/') value = searchPayload(url.searchParams.get('q') ?? '', large);
    else if (url.pathname === '/api/cad/bom/xref/counts') value = {
      outgoing_count: 7, direct_incoming_count: 2, outgoing: 3, incoming: 2, flat_lines: 9, ...freshness(), detail: PRIVATE_MARKER,
    };
    else if (url.pathname === '/api/cad/bom/xref/outgoing') value = outgoingPayload(url.searchParams.get('rel') ?? '');
    else { response.statusCode = 404; value = {}; }
    const body = JSON.stringify(value);
    calls.push({ method: request.method, url, bytes: Buffer.byteLength(body, 'utf8') });
    response.setHeader('content-type', 'application/json'); response.end(body);
  });
  t.after(async () => {
    for (const client of clients.reverse()) await client.close();
    server.closeAllConnections();
    if (server.listening) await new Promise<void>(resolve => server.close(() => resolve()));
    rmSync(directory, { recursive: true, force: true });
  });
  await new Promise<void>((resolve, reject) => { server.once('error', reject); server.listen(0, '127.0.0.1', resolve); });
  const address = server.address(); assert.ok(address && typeof address !== 'string');
  origin = `http://127.0.0.1:${address.port}`;
  const ready = { state: 'ready', code: 'verified', reason: 'Synthetic verified service', api_origin: origin, web_url: origin + '/',
    instance_id: INSTANCE, runtime_id: RUNTIME, pid: 12001, reused: true, created: false,
    release_status: 'not-published', app_version: '0.5.0' };
  writeFileSync(path.join(scripts, 'fixture.json'), JSON.stringify({ ready, log }), { mode: 0o600 });
  assert.equal(statSync(directory).mode & 0o777, 0o700);
  assert.equal(statSync(state).mode & 0o777, 0o700);
  const cliCalls = (): Array<{ args: string[] }> => existsSync(log)
    ? readFileSync(log, 'utf8').trim().split('\n').filter(Boolean).map(line => JSON.parse(line)) : [];
  async function connect(scope: 'full' | 'chat' = 'full', deskConfigured = true, deskChatReads = false) {
    const deskEnv = deskConfigured ? { PHOENIX_DESK_NODE: realpathSync(process.execPath), PHOENIX_DESK_CLI: cliEntry,
      PHOENIX_DESK_STATE_DIR: state } : {};
    const transport = new StdioClientTransport({ command: realpathSync(process.execPath),
      args: [fileURLToPath(new URL('../dist/cli.js', import.meta.url))],
      env: { PHOENIX_FREECAD_URL: origin + '/', PHOENIX_FREECAD_TOOL_SCOPE: scope,
        PHOENIX_TIMEOUT_MS: '5000', ...deskEnv, ...(deskChatReads ? { PHOENIX_DESK_CHAT_READS: '1' } : {}) }, stderr: 'pipe' });
    const client = new Client({ name: 'phoenix-desk-stdio-fixture', version: '0.3.2' });
    clients.push(client); await client.connect(transport); return client;
  }
  function noPrivateFields(value: unknown) {
    const serialized = JSON.stringify(value);
    for (const forbidden of [directory, repo, cliEntry, state, workspace, origin, INSTANCE, RUNTIME, PRIVATE_MARKER,
      '"pid":', '"api_origin":', '"web_url":', '"instance_id":', '"runtime_id":', '"xlink_file_attr":', '"detail":', '"root":']) {
      assert.ok(!serialized.includes(forbidden), `unexpected private field ${forbidden}`);
    }
  }
  return { connect, directory, state, workspace, cliCalls, calls, noPrivateFields,
    setLarge: () => { large = true; } };
}
function success(result: Awaited<ReturnType<Client['callTool']>>, sourceKind: string) {
  assert.equal(result.isError, undefined);
  const value = record(result.structuredContent);
  assert.equal(value.ok, true); assert.equal(value.source_kind, sourceKind);
  assert.match(String(value.observed_at), /^\d{4}-\d{2}-\d{2}T/u);
  assert.equal(record(result.content[0]).type, 'text');
  assert.deepEqual(JSON.parse(String(record(result.content[0]).text)), value);
  return record(value.data);
}

test('Desk real stdio status, search and bounded reference batches preserve provenance and redact identity', { timeout: 30000 }, async t => {
  const f = await fixture(t), client = await f.connect(), list = await client.listTools();
  assert.equal(list.tools.length, 30);
  assert.deepEqual(list.tools.filter(tool => tool.name.startsWith('desk_')).map(tool => tool.name).sort(), [...DESK_TOOLS].sort());
  for (const tool of list.tools.filter(tool => DESK_TOOLS.includes(tool.name))) {
    assert.equal(tool.annotations?.readOnlyHint, true); assert.equal(tool.annotations?.destructiveHint, false);
  }
  assert.equal(f.cliCalls().length, 0); assert.equal(f.calls.length, 0);
  const status = await client.callTool({ name: 'desk_get_connection_status', arguments: {} });
  assert.equal(success(status, 'desk_service').verification, 'cli_and_live_identity'); f.noPrivateFields(status);
  const search = await client.callTool({ name: 'desk_search_index', arguments: { workspace: f.workspace, query: 'Bracket_%' } });
  const searchData = success(search, 'desk_workspace_index');
  assert.equal(searchData.data_source, 'stored_desk_index'); assert.equal(searchData.native_memory, false);
  assert.equal(searchData.query_semantics, 'sqlite_like_contains_percent_underscore_wildcards');
  assert.equal(record((searchData.rows as unknown[])[0]).repo_rel_path, 'parts/Bracket.FCStd'); f.noPrivateFields(search);
  const rels = ['assembly/Root.FCStd', 'parts/Second.FCStd'];
  for (const view of ['counts', 'outgoing'] as const) {
    const result = await client.callTool({ name: 'desk_query_references', arguments: { workspace: f.workspace, rels, view, limit: 5 } });
    const data = success(result, 'desk_workspace_index');
    assert.equal(data.data_source, 'stored_desk_index'); assert.equal(data.native_memory, false);
    assert.equal(data.batch_snapshot, 'not_atomic');
    assert.equal(data.quantity_semantics, 'reference_rows_unique_targets_and_flat_rows_not_manufacturing_quantity');
    assert.equal(data.view, view);
    const items = (data.results as unknown[]).map(record);
    assert.deepEqual(items.map(item => item.rel), rels);
    for (const item of items) {
      assert.equal(record(item.freshness).index_state, 'ready');
      if (view === 'counts') assert.deepEqual(item.counts, { outgoing_count: 7, direct_incoming_count: 2, outgoing: 3, incoming: 2, flat_lines: 9 });
      else { assert.equal(item.completeness, 'unknown'); assert.equal(item.count, 1); assert.equal(item.limit, 5);
        assert.equal(record((item.refs as unknown[])[0]).target_repo_rel_path, 'parts/Component.FCStd'); }
    }
    f.noPrivateFields(result);
  }
  assert.equal(f.cliCalls().length, 4);
  assert.ok(f.cliCalls().every(call => JSON.stringify(call.args) === JSON.stringify(['status', '--state-dir', f.state])));
  assert.equal(f.calls.length, 16); assert.ok(f.calls.every(call => call.method === 'GET'));
  for (const call of f.calls.filter(call => call.url.pathname !== '/api/desk-service')) {
    assert.equal(call.url.searchParams.get('workspace'), f.workspace);
  }
  assert.deepEqual(f.calls.filter(call => call.url.pathname.endsWith('/counts')).map(call => call.url.searchParams.get('rel')), rels);
  assert.deepEqual(f.calls.filter(call => call.url.pathname.endsWith('/outgoing')).map(call => call.url.searchParams.get('rel')), rels);
  assert.deepEqual(readdirSync(f.workspace), []); assert.deepEqual(readdirSync(f.state), []);
});

test('Desk real stdio rejects malformed schemas before any CLI or HTTP request', { timeout: 30000 }, async t => {
  const f = await fixture(t), client = await f.connect();
  const refs = { workspace: f.workspace, rels: ['parts/Bracket.FCStd'] };
  const cases: Array<[string, Record<string, unknown>]> = [
    ['desk_get_connection_status', { action: 'ensure' }], ['desk_get_connection_status', { extra: true }],
    ['desk_search_index', { query: 'Bracket' }], ['desk_search_index', { workspace: f.workspace, query: 'Bracket', action: 'scan' }],
    ['desk_search_index', { workspace: 'relative', query: 'Bracket' }],
    ['desk_search_index', { workspace: f.workspace + '/..', query: 'Bracket' }],
    ['desk_search_index', { workspace: f.workspace, query: ' ' }],
    ['desk_search_index', { workspace: f.workspace, query: 'x'.repeat(257) }],
    ['desk_search_index', { workspace: f.workspace, query: 'bad\u0000query' }],
    ['desk_query_references', { rels: ['parts/Bracket.FCStd'] }], ['desk_query_references', { ...refs, rels: [] }],
    ['desk_query_references', { ...refs, rels: Array.from({ length: 11 }, (_, index) => `parts/Item${index}.FCStd`) }],
    ...['../escape.FCStd', 'parts/../escape.FCStd', '/absolute.FCStd', './parts/Bracket.FCStd', 'parts\\Bracket.FCStd', 'parts/Model.step']
      .map(rel => ['desk_query_references', { ...refs, rels: [rel] }] as [string, Record<string, unknown>]),
    ['desk_query_references', { ...refs, rels: ['parts/Bracket.FCStd', 'parts/Bracket.FCStd'] }],
    ['desk_query_references', { ...refs, view: 'incoming' }], ['desk_query_references', { ...refs, action: 'refresh' }],
    ['desk_query_references', { ...refs, limit: 0 }], ['desk_query_references', { ...refs, limit: 101 }],
    ['desk_query_references', { ...refs, limit: 1.5 }], ['desk_query_references', { ...refs, limit: '5' }],
    ['desk_query_cached_metadata', { ...refs, refresh: true }],
    ['desk_query_cached_metadata', { ...refs, rels: ['parts/A.FCStd', 'parts/a.FCStd'] }],
    ['desk_query_cached_metadata', { ...refs, rels: ['.cache/A.FCStd'] }],
    ['desk_query_cached_metadata', { ...refs, rels: [] }],
    ['desk_get_cached_thumbnail', { workspace: f.workspace, rel: 'parts/A.FCStd' }],
    ['desk_get_cached_thumbnail', { workspace: f.workspace, rel: '../A.FCStd', revision: 'a'.repeat(64) }],
    ['desk_get_cached_thumbnail', { workspace: f.workspace, rel: 'parts/A.FCStd', revision: 'A'.repeat(64) }],
    ['desk_get_cached_thumbnail', { workspace: f.workspace, rel: 'parts/A.FCStd', revision: 'a'.repeat(64), force: true }],
  ];
  for (const [name, args] of cases) {
    const before = f.cliCalls().length, beforeHttp = f.calls.length;
    const result = await client.callTool({ name, arguments: args });
    assert.equal(result.isError, true, `${name}: ${JSON.stringify(args)}`);
    assert.equal(f.cliCalls().length - before, 0, 'invalid MCP arguments must not invoke CLI');
    assert.equal(f.calls.length - beforeHttp, 0, 'invalid MCP arguments must not make HTTP requests');
  }
  assert.equal(f.cliCalls().length, 0); assert.equal(f.calls.length, 0);
});

test('Desk configuration leaves default 25 tools and chat 19 tools offline without CLI discovery', { timeout: 30000 }, async t => {
  const f = await fixture(t);
  for (const [scope, configured, count] of [['full', false, 25], ['chat', true, 19]] as const) {
    const client = await f.connect(scope, configured), list = await client.listTools();
    assert.equal(list.tools.length, count); assert.equal(list.tools.some(tool => tool.name.startsWith('desk_')), false);
    const capabilities = await client.callTool({ name: 'get_capabilities', arguments: {} });
    assert.equal(capabilities.isError, undefined); assert.equal(capabilities.structuredContent?.desk, undefined);
    assert.equal(f.cliCalls().length, 0); assert.equal(f.calls.length, 0);
  }
});

test('Desk full CallToolResult budget rejects doubled text plus structured output without partial rows', { timeout: 30000 }, async t => {
  const f = await fixture(t), client = await f.connect(); f.setLarge();
  const raw = searchPayload('Bracket', true);
  const { diagnostics, rows, ...base } = raw;
  const { message: _message, failures: _failures, latestScan, ...publicDiagnostics } = diagnostics;
  const { root: _root, ...publicScan } = latestScan;
  const data = { ...base, rows: rows.map(({ detail: _detail, ...row }) => row),
    diagnostics: { ...publicDiagnostics, latestScan: publicScan }, data_source: 'stored_desk_index', native_memory: false,
    query_semantics: 'sqlite_like_contains_percent_underscore_wildcards', service_cpu_bound: false };
  const businessValue = { ok: true, source_kind: 'desk_workspace_index', observed_at: '2035-01-01T00:00:00.000Z', data };
  assert.ok(bytes(raw) < 256 * 1024, 'fixture HTTP DTO fits the HTTP budget');
  assert.ok(bytes(businessValue) < RESULT_LIMIT, 'business JSON alone fits 128 KiB');
  assert.ok(bytes({ content: [{ type: 'text', text: JSON.stringify(businessValue) }], structuredContent: businessValue }) > RESULT_LIMIT,
    'the complete MCP result exceeds 128 KiB because it has two copies');
  const result = await client.callTool({ name: 'desk_search_index', arguments: { workspace: f.workspace, query: 'Bracket' } });
  assert.equal(result.isError, true);
  const value = record(result.structuredContent);
  assert.equal(record(value.error).code, 'DESK_RESULT_TOO_LARGE');
  assert.deepEqual(Object.keys(value).sort(), ['error', 'ok']); assert.equal(value.ok, false);
  assert.deepEqual(JSON.parse(String(record(result.content[0]).text)), value);
  assert.ok(bytes(result) <= RESULT_LIMIT); assert.ok(!JSON.stringify(result).includes('x'.repeat(1024)));
  assert.equal(f.cliCalls().length, 1); assert.equal(f.calls.length, 3);
  assert.ok(f.calls.reduce((total, call) => total + call.bytes, 0) < 256 * 1024, 'identity and data HTTP bodies together fit their budget');
  f.noPrivateFields(result);
});


test('chat explicit Desk opt-in exposes 24 tools and reads only the requested stored index', { timeout: 30000 }, async t => {
  const f = await fixture(t), client = await f.connect('chat', true, true), tools = (await client.listTools()).tools;
  assert.equal(tools.length, 24);
  assert.deepEqual(tools.filter(tool => tool.name.startsWith('desk_')).map(tool => tool.name).sort(), [...DESK_TOOLS].sort());
  const capabilities = await client.callTool({ name: 'get_capabilities', arguments: {} });
  assert.equal(record(capabilities.structuredContent?.desk).explicit_chat_opt_in, true);
  assert.equal(f.cliCalls().length, 0); assert.equal(f.calls.length, 0);
  const reply = await client.callTool({ name: 'desk_search_index', arguments: { workspace: f.workspace, query: 'Neutral' } });
  const data = success(reply, 'desk_workspace_index');
  assert.equal(data.native_memory, false); f.noPrivateFields(reply);
  assert.equal(f.cliCalls().length, 1); assert.equal(f.calls.length, 3);
  assert.deepEqual(readdirSync(f.workspace), []);
  assert.ok(f.calls.every(call => call.method === 'GET'));
  assert.ok(f.calls.filter(call => call.url.pathname !== '/api/desk-service')
    .every(call => call.url.searchParams.get('workspace') === f.workspace));
  for (const name of ['execute_script', 'capture_view', 'set_selection', 'set_visibility', 'recompute_document', 'apply_part_changes']) {
    const result = await client.callTool({ name, arguments: {} }).then(value => ({ value }), error => ({ error }));
    assert.ok('error' in result || result.value.isError === true, name);
  }
  assert.equal(f.cliCalls().length, 1); assert.equal(f.calls.length, 3);
});
