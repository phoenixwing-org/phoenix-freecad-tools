// SPDX-License-Identifier: Apache-2.0
import { test, type TestContext } from 'node:test';
import assert from 'node:assert/strict';
import { createServer, type IncomingHttpHeaders } from 'node:http';
import { chmodSync, existsSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, realpathSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { createHash } from 'node:crypto';
import { deflateSync } from 'node:zlib';
import { performance } from 'node:perf_hooks';
import { PftDeskClient, PftDeskError } from '../src/adapters/pft-desk-client.js';
import { pftDeskHttpRead } from '../src/adapters/pft-desk-http.js';

import { fixture, png, hash, REL, SECOND, REVISION, PRIVATE, QUERY, THUMBNAIL, FEATURES } from './helpers/desk-cache-fixture.js';

async function rejectsCode(operation: Promise<unknown>, code: string): Promise<void> {
  await assert.rejects(operation, (error: unknown) => error instanceof PftDeskError && error.code === code
    && !error.message.includes(PRIVATE) && !JSON.stringify(error).includes(PRIVATE));
}
const dataCalls = (calls: Call[]) => calls.filter(call => call.url.pathname === QUERY || call.url.pathname === THUMBNAIL);

test('cache client uses a strict bounded POST, preserves request order and keeps real planar zero separate from missing values', async t => {
  const f = await fixture(t); f.setMode('explicit-missing');
  const result = await f.client().queryCachedMetadata({ workspace: f.workspace, rels: [REL, SECOND] });
  assert.deepEqual(result.items.map(row => row.rel), [REL, SECOND]);
  assert.deepEqual(result.items[0]?.geometry.box_mm, { x_mm: 12, y_mm: 0, z_mm: 3.5 });
  assert.equal(result.items[1]?.geometry.state, 'missing'); assert.equal(result.items[1]?.geometry.box_mm, null);
  assert.equal(result.items[1]?.thumbnail.image, null); assert.equal(result.data_source, 'stored_desk_cache'); assert.equal(result.native_memory, false);
  assert.equal(result.items[0]?.geometry.source.content_hash_verified, false); f.noPrivate(result);
  assert.deepEqual(f.calls.map(call => [call.method, call.url.pathname]), [['GET', '/api/desk-service'], ['GET', FEATURES], ['POST', QUERY], ['GET', '/api/desk-service']]);
  const query = f.calls[2]!;
  assert.deepEqual(JSON.parse(query.body.toString()), { workspace: f.workspace, rels: [REL, SECOND] });
  assert.equal(query.headers['content-type'], 'application/json'); assert.equal(query.headers['content-length'], String(query.body.length));
  assert.equal(query.headers.origin, undefined); assert.equal(query.url.search, '');
  assert.deepEqual(f.cliCalls(), [['status', '--state-dir', f.stateDir]]);
  assert.deepEqual(readdirSync(f.workspace), []); assert.deepEqual(readdirSync(f.stateDir), []);
});

test('cache client never fabricates an omitted metadata item', async t => {
  const f = await fixture(t); f.setMode('omit-item');
  await rejectsCode(f.client().queryCachedMetadata({ workspace: f.workspace, rels: [REL, SECOND] }), 'DESK_CACHE_INVALID_RESPONSE');
  assert.equal(dataCalls(f.calls).length, 1); assert.equal(f.cliCalls().length, 1);
});

test('cache client rejects missing feature or mismatched feature host before any business data request', async t => {
  const f = await fixture(t);
  for (const [mode, code] of [['feature-missing', 'DESK_CACHE_FEATURE_UNSUPPORTED'], ['feature-404', 'DESK_CACHE_FEATURE_UNSUPPORTED'],
    ['host-mismatch', 'DESK_CACHE_IDENTITY_MISMATCH']] as const) {
    f.setMode(mode); const before = f.calls.length;
    await rejectsCode(f.client().getCachedThumbnail({ workspace: f.workspace, rel: REL, revision: REVISION }), code);
    assert.deepEqual(f.calls.slice(before).map(call => call.url.pathname), ['/api/desk-service', FEATURES]);
  }
  assert.equal(dataCalls(f.calls).length, 0); assert.equal(f.cliCalls().length, 3);
});

test('cache client rejects a changed exact revision before requesting any PNG', async t => {
  const f = await fixture(t); f.setMode('revision-changed');
  await rejectsCode(f.client().getCachedThumbnail({ workspace: f.workspace, rel: REL, revision: REVISION }), 'DESK_CACHE_CHANGED');
  assert.deepEqual(dataCalls(f.calls).map(call => call.url.pathname), [QUERY]);
});

test('cache client verifies cached PNG hashes and structure, strips metadata and reports saved-cache provenance', async t => {
  const f = await fixture(t), original = png(), result = await f.client().getCachedThumbnail({ workspace: f.workspace, rel: REL, revision: REVISION });
  const sanitized = Buffer.from(result.data, 'base64');
  assert.equal(result.cached_image_sha256, hash(original)); assert.equal(result.sanitized_image_sha256, hash(sanitized));
  assert.notEqual(result.cached_image_sha256, result.sanitized_image_sha256); assert.equal(sanitized.includes(Buffer.from(PRIVATE)), false);
  assert.equal(result.image_metadata_removed, true); assert.equal(result.image_integrity, 'sha256_verified_and_png_structure_checked');
  assert.equal(result.data_source, 'stored_desk_cache'); assert.equal(result.native_memory, false);
  assert.equal(result.image_scope, 'saved_thumbnail_not_live_view'); assert.deepEqual([result.width, result.height], [128, 96]);
  assert.equal(result.source.dependency_coverage, 'recorded_only'); f.noPrivate(result);
  assert.deepEqual(f.calls.map(call => [call.method, call.url.pathname]), [['GET', '/api/desk-service'], ['GET', FEATURES], ['POST', QUERY], ['GET', THUMBNAIL], ['GET', '/api/desk-service']]);
  const image = f.calls[3]!;
  assert.equal(image.headers.accept, 'image/png'); assert.equal(image.body.length, 0);
  assert.deepEqual(Object.fromEntries(image.url.searchParams), { workspace: f.workspace, rel: REL, revision: REVISION });
});

test('cache PNG requires every cache identity, freshness and HTTP safety header', async t => {
  const f = await fixture(t);
  const cases: Array<[string, string | undefined]> = [['content-type', 'image/jpeg'], ['cache-control', 'public'],
    ['x-content-type-options', undefined], ['x-desk-cache-schema', '2'], ['x-desk-cache-revision', 'f'.repeat(64)],
    ['x-desk-image-sha256', 'b'.repeat(64)], ['etag', '"wrong"'], ['x-desk-source-freshness', 'unverified'],
    ['content-length', '000' + png().length]];
  for (const [key, value] of cases) {
    f.setHeaders({ [key]: value });
    await rejectsCode(f.client().getCachedThumbnail({ workspace: f.workspace, rel: REL, revision: REVISION }), 'DESK_CACHE_INVALID_RESPONSE');
  }
  assert.equal(f.cliCalls().length, cases.length);
});

test('cache PNG rejects absent length, truncated HTTP framing, unsupported encoding and body hash mismatch', async t => {
  const f = await fixture(t);
  for (const [mode, code] of [['missing-length', 'DESK_CACHE_INVALID_RESPONSE'], ['bad-content-length', 'DESK_CONNECTION_FAILED'],
    ['body-hash-mismatch', 'DESK_CACHE_INVALID_RESPONSE']] as const) {
    f.setMode(mode);
    await rejectsCode(f.client().getCachedThumbnail({ workspace: f.workspace, rel: REL, revision: REVISION }), code);
  }
  f.setMode('normal'); f.setHeaders({ 'content-encoding': 'gzip' });
  await rejectsCode(f.client().getCachedThumbnail({ workspace: f.workspace, rel: REL, revision: REVISION }), 'INVALID_RESPONSE');
});

test('matching metadata and HTTP hashes do not bypass complete PNG structure or dimension verification', async t => {
  const f = await fixture(t);
  for (const malformed of ['crc', 'zlib', 'frame', 'missing-iend', 'trailing']) {
    f.setImage(png(128, 96, malformed));
    await rejectsCode(f.client().getCachedThumbnail({ workspace: f.workspace, rel: REL, revision: REVISION }), 'PNG_INVALID');
  }
  f.setImage(png(), 127, 96);
  await rejectsCode(f.client().getCachedThumbnail({ workspace: f.workspace, rel: REL, revision: REVISION }), 'PNG_DIMENSIONS_MISMATCH');
});

test('cache client refuses formal identity changes before reads or after a completed PNG without returning it', async t => {
  const f = await fixture(t);
  for (const mode of ['identity-before', 'identity-after'] as const) {
    f.setMode(mode); const before = f.calls.length;
    await rejectsCode(f.client().getCachedThumbnail({ workspace: f.workspace, rel: REL, revision: REVISION }), 'DESK_IDENTITY_MISMATCH');
    assert.equal(f.calls.length - before, mode === 'identity-before' ? 1 : 5);
  }
});

test('cache client maps versioned metadata and PNG failures without exposing backend detail', async t => {
  const f = await fixture(t);
  for (const [route, code, status, expected] of [[QUERY, 'cache_busy', 409, 'DESK_CACHE_BUSY'],
    [QUERY, 'cache_missing', 404, 'DESK_CACHE_MISSING'], [QUERY, 'read_failed', 503, 'DESK_CACHE_READ_FAILED'],
    [THUMBNAIL, 'cache_changed', 409, 'DESK_CACHE_CHANGED'], [THUMBNAIL, 'cache_invalid', 409, 'DESK_CACHE_INVALID'],
    [QUERY, 'unknown', 409, 'DESK_CACHE_INVALID_RESPONSE']] as const) {
    f.special({ route, kind: 'failure', code, status });
    await rejectsCode(f.client().getCachedThumbnail({ workspace: f.workspace, rel: REL, revision: REVISION }), expected);
  }
});

test('cache client never follows feature, metadata or PNG redirects or accepts declared oversized bodies', async t => {
  const f = await fixture(t);
  for (const route of [FEATURES, QUERY, THUMBNAIL] as const) {
    f.special({ route, kind: 'redirect' });
    await rejectsCode(f.client().getCachedThumbnail({ workspace: f.workspace, rel: REL, revision: REVISION }), 'DESK_REDIRECT_FORBIDDEN');
  }
  for (const route of [QUERY, THUMBNAIL] as const) {
    f.special({ route, kind: 'large' });
    await rejectsCode(f.client().getCachedThumbnail({ workspace: f.workspace, rel: REL, revision: REVISION }), 'RESPONSE_TOO_LARGE');
  }
  assert.equal(f.calls.some(call => call.url.pathname === '/must-not-follow'), false);
});

test('cache HTTP deadline aborts one unfinished query without automatic retries or stopping the owned server', async t => {
  const f = await fixture(t); f.special({ route: QUERY, kind: 'timeout' });
  await rejectsCode(f.client(500).queryCachedMetadata({ workspace: f.workspace, rels: [REL] }), 'DESK_TIMEOUT');
  assert.equal(f.cliCalls().length, 1); assert.equal(f.calls.length, 3);
  f.special(undefined); assert.equal((await f.client().queryCachedMetadata({ workspace: f.workspace, rels: [REL] })).items.length, 1);
  assert.equal(f.cliCalls().length, 2);
});

test('cache client rejects NFC, case ambiguity, hidden paths and malformed revisions before any CLI request', async t => {
  const f = await fixture(t);
  for (const rels of [[], [REL, REL], [REL, REL.toUpperCase()], ['parts/Bracke\u0301t.FCStd'], ['../Escape.FCStd'],
    ['./Bracket.FCStd'], ['parts/.hidden/Bracket.FCStd'], ['parts/node_modules/Bracket.FCStd'], ['parts/__PYCACHE__/Bracket.FCStd'],
    ['parts\\Bracket.FCStd'], ['parts/Model.step'], Array.from({ length: 11 }, (_, index) => `parts/Item${index}.FCStd`)]) {
    await rejectsCode(f.client().queryCachedMetadata({ workspace: f.workspace, rels }), 'INVALID_ARGUMENT');
  }
  for (const revision of ['', 'A'.repeat(64), 'a'.repeat(63), REVISION + '\u0000']) {
    await rejectsCode(f.client().getCachedThumbnail({ workspace: f.workspace, rel: REL, revision }), 'INVALID_ARGUMENT');
  }
  assert.equal(f.cliCalls().length, 0); assert.equal(f.calls.length, 0);
});

test('fixed HTTP reader rejects invalid method, body and route combinations before network dispatch', async t => {
  const f = await fixture(t), body = Buffer.from('{}');
  const cases = [
    { route: QUERY, method: 'GET' as const }, { route: FEATURES, method: 'POST' as const, body },
    { route: FEATURES, method: 'GET' as const, body }, { route: QUERY, method: 'POST' as const },
    { route: QUERY, method: 'POST' as const, body: Buffer.alloc(32 * 1024 + 1) },
    { route: '/api/cad/cache/v1/regenerate', method: 'GET' as const },
  ];
  for (const { route, ...options } of cases) {
    await rejectsCode(pftDeskHttpRead(f.origin, route, performance.now() + 1000, { remaining: 256 * 1024 }, { maximum: 128 * 1024, ...options }), 'INVALID_ARGUMENT');
  }
  assert.equal(f.calls.length, 0); assert.equal(f.cliCalls().length, 0);
});
