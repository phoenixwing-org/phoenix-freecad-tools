// SPDX-License-Identifier: Apache-2.0
import { type TestContext } from 'node:test';
import assert from 'node:assert/strict';
import { createServer, type IncomingHttpHeaders } from 'node:http';
import { chmodSync, existsSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, realpathSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { createHash } from 'node:crypto';
import { deflateSync } from 'node:zlib';
import { performance } from 'node:perf_hooks';
import { PftDeskClient, PftDeskError } from '../../src/adapters/pft-desk-client.js';
import { pftDeskHttpRead } from '../../src/adapters/pft-desk-http.js';

const REL = 'parts/Bracket.FCStd', SECOND = 'parts/Plate.FCStd';
const REVISION = 'a'.repeat(64), SOURCE_HASH = 'c'.repeat(64), DATABASE_HASH = 'd'.repeat(64);
const INSTANCE = '69a63215-e52f-4a53-a4a0-d0528b4bc27b', RUNTIME = 'sha256:' + 'e'.repeat(64);
const TIME = '2035-01-01T00:00:00.000Z', PRIVATE = 'synthetic-private-cache-detail-never-forwarded';
const QUERY = '/api/cad/cache/v1/query', THUMBNAIL = '/api/cad/cache/v1/thumbnail', FEATURES = '/api/runtime/features';
const CLI_SOURCE = `import fs from 'node:fs';
const value = JSON.parse(fs.readFileSync(new URL('./fixture.json', import.meta.url), 'utf8'));
fs.appendFileSync(value.log, JSON.stringify(process.argv.slice(2)) + '\\n');
console.log(JSON.stringify(value.ready));
`;
const hash = (bytes: Buffer) => createHash('sha256').update(bytes).digest('hex');
function chunk(kind: string, data: Buffer): Buffer {
  const name = Buffer.from(kind, 'ascii'), result = Buffer.alloc(data.length + 12);
  result.writeUInt32BE(data.length, 0); name.copy(result, 4); data.copy(result, 8);
  let crc = 0xffffffff;
  for (const value of Buffer.concat([name, data])) {
    crc ^= value;
    for (let bit = 0; bit < 8; bit++) crc = crc & 1 ? crc >>> 1 ^ 0xedb88320 : crc >>> 1;
  }
  result.writeUInt32BE((crc ^ 0xffffffff) >>> 0, result.length - 4); return result;
}
function png(width = 128, height = 96, malformed = ''): Buffer {
  const header = Buffer.alloc(13); header.writeUInt32BE(width, 0); header.writeUInt32BE(height, 4); header[8] = 8; header[9] = 6;
  const frame = Buffer.alloc((width * 4 + 1) * height);
  for (let row = 0; row < height; row++) {
    for (let column = 0; column < width; column++) {
      const start = row * (width * 4 + 1) + column * 4 + 1;
      frame[start] = 40; frame[start + 1] = 90; frame[start + 2] = 140; frame[start + 3] = 180;
    }
  }
  const image = Buffer.concat([Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]), chunk('IHDR', header),
    chunk('tEXt', Buffer.from('Comment\0' + PRIVATE)),
    chunk('IDAT', malformed === 'zlib' ? Buffer.from('not-a-zlib-stream') : deflateSync(malformed === 'frame' ? frame.subarray(1) : frame)),
    ...(malformed === 'missing-iend' ? [] : [chunk('IEND', Buffer.alloc(0))])]);
  if (malformed === 'crc') image[image.length - 1] = image[image.length - 1]! ^ 1;
  return malformed === 'trailing' ? Buffer.concat([image, Buffer.from('extra')]) : image;
}
function featurePayload() {
  return { schema: 'phoenix.runtime-features', schema_version: 1, service: 'phoenix-desk-tools', app_version: '0.5.0',
    observed_at: TIME, observation_only: true, evidence_scope: 'current_host_execution_gate', request_checks: 'not_performed',
    host: { profile: 'browser-service', instance_id: INSTANCE, runtime_id: RUNTIME }, native_execution: { state: 'busy' },
    features: { 'cad.thumbnail.regenerate': { state: 'blocked', code: 'native_busy', reason: PRIVATE },
      'cad.cache.read': { state: 'supported', schema: 'phoenix.cad-cache-read', schema_version: 1, native_required: false, reason: PRIVATE } } };
}
function source(total = 1) {
  return { kind: 'saved_cache', freshness: total ? 'stat_checked' : 'unverified', check_scope: 'recorded_source_files',
    dependency_coverage: 'recorded_only', checked_sources: total, total_sources: total,
    recorded_source_sha256: total ? SOURCE_HASH : null, content_hash_verified: false };
}
function metadata(rels: string[], image: Buffer, width: number, height: number) {
  return { schema: 'phoenix.cad-cache-read', schema_version: 1, ok: true, native_required: false, observed_at: TIME,
    scope: 'requested_file_metadata', complete: true, truncated: false, scan_id: null,
    database: { status: 'present', sha256: DATABASE_HASH, consistency: 'single_sqlite_read_transaction' },
    source_consistency: 'non_atomic_filesystem_stat', effects: { business_writes: false, temporary_coordination: 'own_sqlite_read_lease' },
    items: rels.map(rel => ({ rel, private_detail: PRIVATE,
      thumbnail: { state: 'record', reason: 'cache_record', revision: REVISION, updated_at: TIME,
        algorithm_version: null, source: source(), image: { sha256: hash(image), width, height, bytes: image.length, integrity: 'not_checked' } },
      geometry: { state: 'record', reason: 'cache_record', revision: REVISION, updated_at: TIME,
        algorithm_version: 1 as number | null, source: source(2), box_mm: { x_mm: 12, y_mm: 0, z_mm: 3.5 },
        units: 'mm', value_scope: 'cached_box_not_live_measurement' } })), detail: PRIVATE };
}
type Route = typeof QUERY | typeof THUMBNAIL | typeof FEATURES;
type Mode = 'normal' | 'feature-missing' | 'feature-404' | 'host-mismatch' | 'identity-before' | 'identity-after'
  | 'revision-changed' | 'omit-item' | 'explicit-missing' | 'bad-content-length' | 'missing-length' | 'body-hash-mismatch';
type Call = { method: string | undefined; url: URL; headers: IncomingHttpHeaders; body: Buffer };
async function fixture(t: TestContext) {
  const directory = realpathSync(mkdtempSync(path.join(tmpdir(), 'pft-desk-cache-client-'))); chmodSync(directory, 0o700);
  const scripts = path.join(directory, 'candidate/scripts'), stateDir = path.join(directory, 'state');
  const workspace = path.join(directory, 'workspace'), log = path.join(directory, 'cli-calls.jsonl');
  mkdirSync(scripts, { recursive: true }); mkdirSync(stateDir, { mode: 0o700 }); mkdirSync(workspace, { mode: 0o700 });
  const cliEntry = path.join(scripts, 'desk-web-service.mjs'); writeFileSync(cliEntry, CLI_SOURCE, { mode: 0o600 });
  let origin = '', identityReads = 0, mode: Mode = 'normal';
  let image = png(), imageWidth = 128, imageHeight = 96;
  let overrideHeaders: Record<string, string | undefined> = {};
  let transformFeatures: ((value: ReturnType<typeof featurePayload>) => unknown) | undefined;
  let special: { route: Route; kind: 'failure' | 'redirect' | 'large' | 'timeout'; code?: string; status?: number } | undefined;
  const calls: Call[] = [];
  const server = createServer(async (request, response) => {
    const chunks: Buffer[] = []; for await (const body of request) chunks.push(Buffer.from(body));
    const url = new URL(request.url ?? '/', origin), body = Buffer.concat(chunks);
    calls.push({ method: request.method, url, headers: request.headers, body });
    response.setHeader('content-type', 'application/json');
    if (url.pathname === '/api/desk-service') {
      identityReads++;
      response.end(JSON.stringify({ schema_version: 1, service: 'phoenix-desk-tools', protocol_version: 1, browser_host_protocol: 1,
        app_version: '0.5.0', instance_id: mode === 'identity-before' || mode === 'identity-after' && identityReads > 1
          ? '97b0dbad-3a55-44fa-880f-d6c87cffb109' : INSTANCE,
        runtime_id: RUNTIME, pid: 12001, api_origin: origin, web_url: origin + '/', capabilities: ['browser-web', 'local-api'], started_at: TIME })); return;
    }
    if (special?.route === url.pathname) {
      if (special.kind === 'timeout') return;
      if (special.kind === 'redirect') { response.writeHead(302, { Location: origin + '/must-not-follow' }); response.end(); return; }
      if (special.kind === 'large') {
        response.setHeader('content-length', String(url.pathname === THUMBNAIL ? 4 * 1024 * 1024 + 1 : 128 * 1024 + 1));
        response.end(); return;
      }
      response.statusCode = special.status!;
      response.end(JSON.stringify({ schema: 'phoenix.cad-cache-read', schema_version: 1, ok: false,
        native_required: false, code: special.code, detail: PRIVATE, retryable: false })); return;
    }
    if (url.pathname === FEATURES) {
      if (mode === 'feature-404') { response.statusCode = 404; response.end('{}'); return; }
      const value = featurePayload();
      if (mode === 'host-mismatch') value.host.instance_id = '97b0dbad-3a55-44fa-880f-d6c87cffb109';
      if (mode === 'feature-missing') delete (value.features as Record<string, unknown>)['cad.cache.read'];
      response.end(JSON.stringify(transformFeatures ? transformFeatures(value) : value)); return;
    }
    if (url.pathname === QUERY) {
      const args = JSON.parse(body.toString('utf8')) as { workspace: string; rels: string[] };
      const value = metadata([...args.rels].reverse(), image, imageWidth, imageHeight);
      if (mode === 'revision-changed') value.items[0]!.thumbnail.revision = 'f'.repeat(64);
      if (mode === 'omit-item') value.items.pop();
      if (mode === 'explicit-missing') {
        const item = value.items.find(row => row.rel === SECOND)!;
        for (const entry of [item.thumbnail, item.geometry]) {
          Object.assign(entry, { state: 'missing', reason: 'cache_missing', revision: null, updated_at: null, source: source(0) });
        }
        Object.assign(item.thumbnail, { image: null }); Object.assign(item.geometry, { box_mm: null, algorithm_version: null });
      }
      response.end(JSON.stringify(value)); return;
    }
    if (url.pathname === THUMBNAIL) {
      const headers: Record<string, string> = { 'content-type': 'image/png', 'content-length': String(image.length),
        'cache-control': 'no-store', 'x-content-type-options': 'nosniff', 'x-desk-cache-schema': '1',
        'x-desk-cache-revision': REVISION, 'x-desk-image-sha256': hash(image), etag: '"sha256:' + hash(image) + '"',
        'x-desk-source-freshness': 'stat_checked' };
      for (const [key, value] of Object.entries(overrideHeaders)) { if (value === undefined) delete headers[key]; else headers[key] = value; }
      if (mode === 'bad-content-length') headers['content-length'] = String(image.length + 1);
      if (mode === 'missing-length') { delete headers['content-length']; headers['transfer-encoding'] = 'chunked'; }
      response.removeHeader('content-type'); response.writeHead(200, headers);
      const payload = Buffer.from(image); if (mode === 'body-hash-mismatch') payload[payload.length - 1] = payload[payload.length - 1]! ^ 1;
      response.end(payload); return;
    }
    response.statusCode = 404; response.end('{}');
  });
  t.after(async () => { server.closeAllConnections(); if (server.listening) await new Promise<void>(resolve => server.close(() => resolve()));
    rmSync(directory, { recursive: true, force: true }); });
  await new Promise<void>((resolve, reject) => { server.once('error', reject); server.listen(0, '127.0.0.1', resolve); });
  const address = server.address(); assert.ok(address && typeof address !== 'string'); origin = `http://127.0.0.1:${address.port}`;
  const ready = { state: 'ready', code: 'verified', reason: 'Synthetic service', api_origin: origin, web_url: origin + '/',
    instance_id: INSTANCE, runtime_id: RUNTIME, pid: 12001, reused: true, created: false, release_status: 'not-published', app_version: '0.5.0' };
  writeFileSync(path.join(scripts, 'fixture.json'), JSON.stringify({ ready, log }), { mode: 0o600 });
  const cliCalls = (): string[][] => existsSync(log) ? readFileSync(log, 'utf8').trim().split('\n').filter(Boolean).map(line => JSON.parse(line)) : [];
  return { workspace, stateDir, origin, calls, cliCalls,
    deskConfig: { nodeExecutable: realpathSync(process.execPath), cliEntry, stateDir },
    client: (timeout = 3000) => new PftDeskClient({ nodeExecutable: realpathSync(process.execPath), cliEntry, stateDir }, timeout),
    setMode: (value: Mode) => { mode = value; identityReads = 0; },
    setFeatures: (value: typeof transformFeatures) => { transformFeatures = value; },
    setImage: (value: Buffer, width = 128, height = 96) => { image = value; imageWidth = width; imageHeight = height; },
    setHeaders: (value: Record<string, string | undefined>) => { overrideHeaders = value; },
    special: (value: typeof special) => { special = value; },
    noPrivate: (value: unknown) => { const text = JSON.stringify(value);
      for (const item of [PRIVATE, directory, origin, INSTANCE, RUNTIME, '"pid":', '"detail":', '"private_detail":']) assert.ok(!text.includes(item), item); },
  };
}

export { fixture, png, hash, REL, SECOND, REVISION, PRIVATE, QUERY, THUMBNAIL, FEATURES };
