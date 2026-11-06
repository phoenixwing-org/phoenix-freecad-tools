// SPDX-License-Identifier: Apache-2.0
import { test, type TestContext } from 'node:test';
import assert from 'node:assert/strict';
import { createServer, type IncomingMessage } from 'node:http';
import { chmodSync, existsSync, mkdirSync, mkdtempSync, readFileSync, realpathSync, rmSync, symlinkSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { PftDeskClient, PftDeskError, type PftDeskConfig, type PftDeskReferencesInput } from '../src/adapters/pft-desk-client.js';

const INSTANCE = 'a72fce20-59f0-4abb-a822-e87a3e9e5221';
const RUNTIME = 'sha256:' + 'a'.repeat(64);
const PRIVATE_FIXTURE = '/Users/synthetic-private/fixture-only';
const CLI_SOURCE = `import fs from 'node:fs';
const configPath = new URL('./fixture.json', import.meta.url);
const config = JSON.parse(fs.readFileSync(configPath, 'utf8'));
fs.appendFileSync(config.log, JSON.stringify({ args: process.argv.slice(2), homePreserved: process.env.HOME === config.home,
  forbiddenEnvPresent: Object.keys(process.env).some(key => /^(NODE_OPTIONS|NODE_PATH|OPENAI_|CODEX_|PHOENIX_)/.test(key)), pid: process.pid }) + '\\n');
if (config.mode === 'hang') setInterval(() => {}, 1000);
else if (config.mode === 'stdout') { process.stdout.write('x'.repeat(65537)); setInterval(() => {}, 1000); }
else if (config.mode === 'stderr') { process.stderr.write('x'.repeat(8193)); setInterval(() => {}, 1000); }
else if (config.mode === 'duplicate') { console.log('{"state":"absent",' + JSON.stringify(config.result).slice(1)); }
else { console.log(JSON.stringify(config.result)); process.exitCode = config.exit; }
`;
function freshness(state: 'ready' | 'stale' | 'incomplete' | 'missing' = 'ready') {
  return { index_state: state, index_pending: state === 'incomplete' || state === 'missing', index_stale: state === 'stale',
    index_reason: state === 'ready' ? '' : state === 'stale' ? 'file_changed' : state === 'missing' ? 'index_missing' : 'scan_incomplete',
    bom_index_state: state, bom_index_pending: state === 'incomplete' || state === 'missing', bom_index_stale: state === 'stale',
    scan_id: state === 'missing' ? null : 7, freshness_scope: 'file' };
}
function searchPayload(query: string) {
  return { source: 'sqlite', query: query.trim().toLocaleLowerCase('en-US'), limit: 200, limitReached: false,
    rows: [{ repo_rel_path: 'parts/Bracket.FCStd', filename: 'Bracket.FCStd', asset_kind: 'part',
      part_number: '7305', part_version: '004', part_name: 'Example bracket', label: 'Example bracket', detail: PRIVATE_FIXTURE }],
    diagnostics: { state: 'ready', message: PRIVATE_FIXTURE, schemaVersion: 4, expectedSchemaVersion: 4, totalEntries: 1,
      missingEntries: 0, failedEntries: 0, failures: [{ rel: '../private', message: PRIVATE_FIXTURE }], lastScanAt: '',
      latestScan: { id: 7, status: 'done', root: PRIVATE_FIXTURE, at: '' } }, detail: PRIVATE_FIXTURE };
}
function outgoingPayload(rel: string, limit: number) {
  const reference = { id: 11, host_repo_rel_path: './' + rel, host_file_asset_id: 13, xlink_file_attr: '../private/Source.FCStd',
    target_basename: 'Component.FCStd', target_repo_rel_path: './parts/Component.FCStd', target_file_asset_id: 17,
    resolve_status: 'resolved', ref_kind: 'xlink_file_attr', detail: { root: PRIVATE_FIXTURE },
    link_label: 'Component', rule_label: 'Component', label_mismatch: false, target_part_number: '7306',
    target_part_version: '001', target_type_code: 'H', target_model_series: 'PUBLIC', target_part_name: 'Component', target_asset_label: 'Component' };
  const refs = [reference, { ...reference, id: 12 }].slice(0, limit);
  return { host_repo_rel_path: './' + rel, count: refs.length, label_mismatch_count: 0, refs, items: refs, ...freshness() };
}
async function fixture(t: TestContext) {
  const directory = realpathSync(mkdtempSync(path.join(tmpdir(), 'pft-desk-read-fixture-')));
  const repo = path.join(directory, 'candidate'), scripts = path.join(repo, 'scripts'), state = path.join(directory, 'state');
  const workspace = path.join(directory, 'workspace'), log = path.join(directory, 'calls.jsonl');
  mkdirSync(scripts, { recursive: true }); mkdirSync(state, { mode: 0o700 }); mkdirSync(workspace);
  const cliEntry = path.join(scripts, 'desk-web-service.mjs'), fixtureFile = path.join(scripts, 'fixture.json');
  writeFileSync(cliEntry, CLI_SOURCE, { mode: 0o600 });
  let mode = 'normal'; let identityCalls = 0;
  const calls: Array<{ path: string; url: URL; headers: IncomingMessage['headers']; method: string | undefined }> = [];
  let origin = '';
  const identity = () => ({ schema_version: 1, service: 'phoenix-desk-tools', protocol_version: 1, browser_host_protocol: 1,
    app_version: '0.5.0', instance_id: INSTANCE, runtime_id: RUNTIME, pid: 12001, api_origin: origin, web_url: origin + '/',
    capabilities: ['browser-web', 'local-api'], started_at: '2035-01-01T00:00:00.000Z' });
  const server = createServer((req, res) => {
    const url = new URL(req.url ?? '/', origin);
    calls.push({ path: url.pathname, url, headers: req.headers, method: req.method });
    res.setHeader('content-type', 'application/json');
    if (url.pathname === '/api/desk-service') {
      identityCalls++;
      if (mode === 'identity-timeout') return;
      if (mode === 'identity-redirect') { res.writeHead(302, { Location: 'http://example.invalid/' }); res.end(); return; }
      const result = identity();
      if (mode === 'identity-before' || mode === 'identity-after' && identityCalls > 1) result.instance_id = 'c95cbe59-d053-40c2-962e-13d2b04d48f7';
      if (mode === 'wrong-capabilities') result.capabilities = ['browser-web', 'local-api', 'other'];
      if (mode === 'bad-identity-time') result.started_at = PRIVATE_FIXTURE;
      if (mode === 'impossible-identity-time') result.started_at = '2035-02-31T12:00:00.000Z';
      res.end(JSON.stringify(result)); return;
    }
    if (mode === 'redirect') { res.writeHead(302, { Location: 'http://example.invalid/' }); res.end(); return; }
    if (mode === 'timeout') return;
    if (mode === 'http-error') { res.statusCode = 500; res.end(JSON.stringify({ detail: PRIVATE_FIXTURE })); return; }
    if (mode === 'large') { res.end(JSON.stringify({ detail: 'x'.repeat(256 * 1024) })); return; }
    if (mode === 'duplicate-json') { res.end('{"source":"sqlite","source":"bad"}'); return; }
    if (url.pathname === '/api/cad/index/search/') {
      const result = searchPayload(url.searchParams.get('q') ?? '');
      if (mode === 'unsafe-row') result.rows[0]!.repo_rel_path = '../../escape.FCStd';
      if (mode === 'invalid-field') result.rows[0]!.part_number = 7 as unknown as string;
      if (mode === 'too-many') result.rows = Array(201).fill(result.rows[0]);
      if (mode === 'bad-diag') result.diagnostics.state = 'unreadable';
      if (mode === 'object-state') result.diagnostics.state = {} as unknown as string;
      if (mode === 'path-timestamp') result.diagnostics.lastScanAt = PRIVATE_FIXTURE;
      if (mode === 'path-scan-time') result.diagnostics.latestScan.at = PRIVATE_FIXTURE;
      if (mode === 'duplicate-path') result.rows.push({ ...result.rows[0]!, repo_rel_path: './parts/Bracket.FCStd' });
      res.end(JSON.stringify(result)); return;
    }
    const rel = url.searchParams.get('rel') ?? '';
    if (url.pathname === '/api/cad/bom/xref/counts') {
      const result = { outgoing_count: 7, direct_incoming_count: 2, outgoing: 3, incoming: 2, flat_lines: 9,
        ...freshness(mode === 'stale' ? 'stale' : mode === 'incomplete' ? 'incomplete' : mode === 'missing' ? 'missing' : 'ready') };
      if (mode === 'bad-second' && rel === 'parts/Second.FCStd') result.outgoing_count = -1;
      if (mode === 'bad-freshness-flags') result.index_stale = true;
      if (mode === 'bad-freshness-scan') result.scan_id = null;
      if (mode === 'missing-bom') { result.bom_index_state = 'missing'; result.bom_index_pending = true; result.flat_lines = 0; }
      if (mode === 'object-freshness') result.index_state = {} as unknown as 'ready';
      res.end(JSON.stringify(result)); return;
    }
    if (url.pathname === '/api/cad/bom/xref/outgoing') {
      const result = outgoingPayload(rel, Number(url.searchParams.get('limit')));
      if (mode === 'unsafe-ref') result.refs[0]!.target_repo_rel_path = PRIVATE_FIXTURE + '/Model.FCStd';
      if (mode === 'bad-mismatch-count') result.label_mismatch_count = 1;
      if (mode === 'false-mismatch') result.refs[0]!.rule_label = 'Different expected label';
      if (mode === 'missing-link-label') for (const row of result.refs) row.link_label = '';
      if (mode === 'missing-rule-label') for (const row of result.refs) row.rule_label = '';
      res.end(JSON.stringify(result)); return;
    }
    res.statusCode = 404; res.end('{}');
  });
  await new Promise<void>(resolve => server.listen(0, '127.0.0.1', resolve));
  const address = server.address(); if (!address || typeof address === 'string') throw new Error('fixture address unavailable');
  origin = `http://127.0.0.1:${address.port}`;
  const ready = { state: 'ready', code: 'verified', reason: 'Synthetic verified service', api_origin: origin, web_url: origin + '/',
    instance_id: INSTANCE, runtime_id: RUNTIME, pid: 12001, reused: true, created: false, release_status: 'not-published', app_version: '0.5.0' };
  const writeCli = (changes: Record<string, unknown> = {}) => writeFileSync(fixtureFile, JSON.stringify({ mode: 'normal',
    result: ready, exit: 0, log, home: process.env.HOME, ...changes }), { mode: 0o600 });
  writeCli();
  const config: PftDeskConfig = { nodeExecutable: realpathSync(process.execPath), cliEntry, stateDir: state };
  const client = (timeout = 3000) => new PftDeskClient(config, timeout);
  t.after(async () => { server.closeAllConnections(); await new Promise<void>(resolve => server.close(() => resolve())); rmSync(directory, { recursive: true, force: true }); });
  return { config, client, workspace, state, repo, cliEntry, writeCli, ready, calls, origin,
    setMode: (value: string) => { mode = value; identityCalls = 0; },
    cliCalls: (): Array<{ args: string[]; homePreserved: boolean; forbiddenEnvPresent: boolean; pid: number }> =>
      existsSync(log) ? readFileSync(log, 'utf8').trim().split('\n').filter(Boolean).map(line => JSON.parse(line)) : [],
  };
}
function noPrivateFields(result: unknown, origin: string): void {
  const serialized = JSON.stringify(result);
  for (const privateValue of [PRIVATE_FIXTURE, origin, INSTANCE, RUNTIME, '12001', 'xlink_file_attr', '"detail"', '"message"', '"root"']) {
    // ref_kind is a public code; raw xlink_file_attr property is forbidden.
    if (privateValue === 'xlink_file_attr') assert.ok(!serialized.includes('"xlink_file_attr":'));
    else assert.ok(!serialized.includes(privateValue), `unexpected field ${privateValue}`);
  }
}

test('Desk requires explicit configuration and strict arguments before any CLI', async t => {
  await assert.rejects(new PftDeskClient(undefined).status(), { code: 'DESK_NOT_CONFIGURED' });
  const f = await fixture(t);
  await assert.rejects(f.client().searchIndex({ workspace: f.workspace, query: '   ' }), { code: 'INVALID_ARGUMENT' });
  for (const rels of [['../escape.FCStd'], ['/absolute.FCStd'], ['parts/Model.step'], ['parts/A.FCStd', 'parts/A.FCStd'], ['parts/../A.FCStd']]) {
    await assert.rejects(f.client().queryReferences({ workspace: f.workspace, rels, view: 'counts', limit: 5 }), { code: 'INVALID_ARGUMENT' });
  }
  await assert.rejects(f.client().queryReferences({ workspace: f.workspace, rels: ['parts/A.FCStd'], view: 'incoming', limit: 5 } as unknown as PftDeskReferencesInput), { code: 'INVALID_ARGUMENT' });
  assert.equal(f.cliCalls().length, 0); assert.equal(f.calls.length, 0);
});

test('Desk config rejects symlinks, public state directories and source overlap without changes', async t => {
  const f = await fixture(t), linkedCli = path.join(path.dirname(f.cliEntry), 'linked.mjs');
  symlinkSync(f.cliEntry, linkedCli);
  await assert.rejects(new PftDeskClient({ ...f.config, cliEntry: linkedCli }).status(), { code: 'UNSAFE_PATH' });
  chmodSync(f.state, 0o755);
  await assert.rejects(f.client().status(), { code: 'INVALID_STATE_DIRECTORY' });
  assert.equal((await import('node:fs')).statSync(f.state).mode & 0o777, 0o755);
  await assert.rejects(new PftDeskClient({ ...f.config, stateDir: path.join(f.repo, 'private') }).status(), { code: 'INVALID_STATE_DIRECTORY' });
  assert.equal(existsSync(path.join(f.repo, 'private')), false); assert.equal(f.cliCalls().length, 0);
});

test('Desk status invokes only official status argv with minimal environment and hides service identity', async t => {
  const f = await fixture(t), status = await f.client().status();
  assert.deepEqual(status, { state: 'ready', code: 'verified', app_version: '0.5.0', release_status: 'not-published', reused: true, created: false,
    protocol_version: 1, browser_host_protocol: 1, capabilities: ['browser-web', 'local-api'], verification: 'cli_and_live_identity' });
  noPrivateFields(status, f.origin);
  assert.deepEqual(f.cliCalls()[0]?.args, ['status', '--state-dir', f.state]);
  assert.equal(f.cliCalls()[0]?.homePreserved, true); assert.equal(f.cliCalls()[0]?.forbiddenEnvPresent, false);
  assert.deepEqual(f.calls.map(call => call.path), ['/api/desk-service']);
  assert.equal(f.calls[0]?.headers.origin, undefined); assert.equal(f.calls[0]?.headers.host, new URL(f.origin).host);
  assert.ok(f.calls.every(call => call.method === 'GET'));
});

test('Desk status is fresh and rejects nonready or created CLI states without forwarding reason', async t => {
  const f = await fixture(t), client = f.client(); await client.status();
  for (const state of ['absent', 'unavailable', 'incompatible'] as const) {
    f.writeCli({ exit: 1, result: { ...f.ready, state, code: 'fixture_failure', reason: PRIVATE_FIXTURE,
      api_origin: null, web_url: null, instance_id: null, pid: null, created: false, reused: false } });
    await assert.rejects(client.status(), (error: unknown) => error instanceof PftDeskError
      && error.code === 'DESK_' + state.toUpperCase() && !error.message.includes(PRIVATE_FIXTURE));
  }
  f.writeCli({ result: { ...f.ready, created: true, reused: false } });
  await assert.rejects(client.status(), { code: 'INVALID_RESPONSE' });
  f.writeCli({ result: { ...f.ready, state: {} } }); await assert.rejects(client.status(), { code: 'INVALID_RESPONSE' });
  assert.equal(f.cliCalls().length, 6); assert.equal(f.calls.length, 1);
});

test('Desk bounded search preserves LIKE semantics and drops absolute diagnostics and unknown details', async t => {
  const f = await fixture(t), result = await f.client().searchIndex({ workspace: f.workspace, query: ' Bracket_% ' });
  assert.equal(result.query, 'bracket_%'); assert.equal(result.rows[0]?.part_name, 'Example bracket');
  assert.equal(result.query_semantics, 'sqlite_like_contains_percent_underscore_wildcards'); assert.equal(result.service_cpu_bound, false);
  noPrivateFields(result, f.origin);
  assert.deepEqual(f.calls.map(call => call.path), ['/api/desk-service', '/api/cad/index/search/', '/api/desk-service']);
  assert.equal(f.calls[1]?.url.searchParams.get('workspace'), f.workspace); assert.equal(f.calls[1]?.url.searchParams.get('q'), ' Bracket_% ');
  assert.equal(f.cliCalls().length, 1);
});

test('Desk rejects identity changes before or after reads and exact capability mismatches', async t => {
  const f = await fixture(t);
  for (const mode of ['identity-before', 'identity-after', 'wrong-capabilities', 'bad-identity-time', 'impossible-identity-time']) {
    f.setMode(mode); const before = f.calls.length;
    await assert.rejects(f.client().searchIndex({ workspace: f.workspace, query: 'Bracket' }), { code: 'DESK_IDENTITY_MISMATCH' });
    assert.equal(f.calls.length - before, mode === 'identity-after' ? 3 : 1);
  }
});

test('Desk never follows redirects or forwards non-200 private response text', async t => {
  const f = await fixture(t);
  for (const [mode, code] of [['redirect', 'DESK_REDIRECT_FORBIDDEN'], ['identity-redirect', 'DESK_REDIRECT_FORBIDDEN'], ['http-error', 'DESK_HTTP_ERROR']]) {
    f.setMode(mode!); const before = f.calls.length;
    await assert.rejects(f.client().searchIndex({ workspace: f.workspace, query: 'Bracket' }), (error: unknown) =>
      error instanceof PftDeskError && error.code === code && !error.message.includes(PRIVATE_FIXTURE));
    assert.equal(f.calls.length - before, mode === 'identity-redirect' ? 1 : 2);
  }
});

test('Desk rejects unsafe, invalid or oversized DTOs and duplicate JSON keys', async t => {
  const f = await fixture(t);
  for (const [mode, code] of [['unsafe-row', 'INVALID_RESPONSE'], ['invalid-field', 'INVALID_RESPONSE'], ['too-many', 'INVALID_RESPONSE'],
    ['bad-diag', 'INDEX_UNAVAILABLE'], ['large', 'RESPONSE_TOO_LARGE'], ['duplicate-json', 'INVALID_RESPONSE'],
    ['object-state', 'INVALID_RESPONSE'], ['path-timestamp', 'INVALID_RESPONSE'], ['path-scan-time', 'INVALID_RESPONSE'], ['duplicate-path', 'DESK_INDEX_AMBIGUOUS']]) {
    f.setMode(mode!);
    await assert.rejects(f.client().searchIndex({ workspace: f.workspace, query: 'Bracket' }), { code });
  }
  f.writeCli({ mode: 'duplicate' }); await assert.rejects(f.client().status(), { code: 'INVALID_RESPONSE' });
});

test('Desk CLI timeout/output budgets kill and await only their own short-lived child', async t => {
  const f = await fixture(t);
  for (const [mode, code] of [['hang', 'DESK_TIMEOUT'], ['stdout', 'OUTPUT_TOO_LARGE'], ['stderr', 'OUTPUT_TOO_LARGE']]) {
    f.writeCli({ mode });
    await assert.rejects(f.client(500).status(), { code });
    const pid = f.cliCalls().at(-1)?.pid; assert.ok(pid);
    assert.throws(() => process.kill(pid, 0), (error: unknown) => typeof error === 'object' && error !== null && 'code' in error && error.code === 'ESRCH');
  }
  assert.equal(f.calls.length, 0);
});

test('Desk HTTP deadline rejects unfinished data without retrying or stopping the server', async t => {
  const f = await fixture(t); f.setMode('timeout');
  await assert.rejects(f.client(500).searchIndex({ workspace: f.workspace, query: 'Bracket' }), { code: 'DESK_TIMEOUT' });
  assert.equal(f.calls.length, 2); assert.equal(f.cliCalls().length, 1);
  f.setMode('normal'); assert.equal((await f.client().status()).state, 'ready');
});

test('Desk counts retain distinct backend meanings and stale/incomplete freshness without fabricating quantity', async t => {
  const f = await fixture(t);
  for (const state of ['stale', 'incomplete'] as const) {
    f.setMode(state);
    const result = await f.client().queryReferences({ workspace: f.workspace, rels: ['asm/Root.ASSY.FCStd'], view: 'counts', limit: 5 });
    assert.equal(result.view, 'counts'); if (result.view !== 'counts') throw new Error('wrong view');
    assert.deepEqual(result.results[0]?.counts, { outgoing_count: 7, direct_incoming_count: 2, outgoing: 3, incoming: 2, flat_lines: 9 });
    assert.equal(result.results[0]?.freshness.index_state, state); assert.equal(result.batch_snapshot, 'not_atomic');
    noPrivateFields(result, f.origin);
  }
  f.setMode('missing');
  await assert.rejects(f.client().queryReferences({ workspace: f.workspace, rels: ['parts/Absent.FCStd'], view: 'counts', limit: 5 }), { code: 'INDEX_UNAVAILABLE' });
});

test('Desk outgoing batch keeps unknown completeness, safe relative paths and no raw XLink details', async t => {
  const f = await fixture(t), result = await f.client().queryReferences({ workspace: f.workspace,
    rels: ['asm/Root.ASSY.FCStd', 'parts/Second.FCStd'], view: 'outgoing', limit: 1 });
  assert.equal(result.view, 'outgoing'); if (result.view !== 'outgoing') throw new Error('wrong view');
  assert.equal(result.results.length, 2); assert.ok(result.results.every(row => row.completeness === 'unknown' && row.count === 1));
  assert.equal(result.results[0]?.refs[0]?.target_repo_rel_path, 'parts/Component.FCStd');
  assert.equal(result.results[0]?.refs[0]?.host_repo_rel_path, 'asm/Root.ASSY.FCStd');
  noPrivateFields(result, f.origin); assert.equal(f.cliCalls().length, 1); assert.equal(f.calls.length, 6);
  assert.ok(f.calls.every(call => call.method === 'GET' && call.headers.origin === undefined));
});

test('Desk rejects an unsafe reference or failed later batch item instead of returning partial success', async t => {
  const f = await fixture(t);
  f.setMode('unsafe-ref');
  await assert.rejects(f.client().queryReferences({ workspace: f.workspace, rels: ['asm/Root.ASSY.FCStd'], view: 'outgoing', limit: 5 }), { code: 'INVALID_RESPONSE' });
  f.setMode('bad-second');
  await assert.rejects(f.client().queryReferences({ workspace: f.workspace, rels: ['asm/Root.ASSY.FCStd', 'parts/Second.FCStd'], view: 'counts', limit: 5 }), { code: 'INVALID_RESPONSE' });
  assert.equal(f.calls.filter(call => call.path === '/api/cad/bom/xref/counts').length, 2);
});


test('Desk freshness flags, scan identity and bounded mismatch summaries must agree with backend fields', async t => {
  const f = await fixture(t);
  for (const mode of ['bad-freshness-flags', 'bad-freshness-scan', 'object-freshness']) {
    f.setMode(mode);
    await assert.rejects(f.client().queryReferences({ workspace: f.workspace, rels: ['asm/Root.ASSY.FCStd'], view: 'counts', limit: 5 }), { code: 'INVALID_RESPONSE' });
  }
  f.setMode('bad-mismatch-count');
  await assert.rejects(f.client().queryReferences({ workspace: f.workspace, rels: ['asm/Root.ASSY.FCStd'], view: 'outgoing', limit: 5 }), { code: 'INVALID_RESPONSE' });
  f.setMode('missing-bom');
  const result = await f.client().queryReferences({ workspace: f.workspace, rels: ['asm/Root.ASSY.FCStd'], view: 'counts', limit: 5 });
  if (result.view !== 'counts') throw new Error('wrong view');
  assert.equal(result.results[0]?.freshness.index_state, 'ready');
  assert.equal(result.results[0]?.freshness.bom_index_state, 'missing');
  assert.equal(result.results[0]?.counts.flat_lines, 0); // Missing BOM state is retained, not silently promoted to ready.
});


test('Desk per-row mismatch flags follow the official two-label rule without changing metadata', async t => {
  const f = await fixture(t);
  f.setMode('false-mismatch');
  await assert.rejects(f.client().queryReferences({ workspace: f.workspace, rels: ['asm/Root.ASSY.FCStd'], view: 'outgoing', limit: 5 }), { code: 'INVALID_RESPONSE' });
  for (const mode of ['missing-link-label', 'missing-rule-label']) {
    f.setMode(mode);
    const result = await f.client().queryReferences({ workspace: f.workspace, rels: ['asm/Root.ASSY.FCStd'], view: 'outgoing', limit: 5 });
    if (result.view !== 'outgoing') throw new Error('wrong view');
    const row = result.results[0]; assert.ok(row);
    assert.equal(row.label_mismatch_count, 0);
    assert.ok(row.refs.every(reference => reference.label_mismatch === false));
    assert.ok(row.refs.every(reference => mode === 'missing-link-label'
      ? reference.link_label === '' && reference.rule_label === 'Component'
      : reference.link_label === 'Component' && reference.rule_label === ''));
  }
});
