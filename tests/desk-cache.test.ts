// SPDX-License-Identifier: Apache-2.0
import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  PftDeskCacheError, pftCacheFailure, pftDecodeCacheMetadata, pftRequireCacheFeature,
  type PftDeskCacheErrorCode,
} from '../src/pft-desk-cache.js';

const SCHEMA = 'phoenix.cad-cache-read';
const REL = 'parts/Bracket.FCStd', SECOND = 'parts/Plate.FCStd';
const REVISION = 'a'.repeat(64), IMAGE_HASH = 'b'.repeat(64), SOURCE_HASH = 'c'.repeat(64), DATABASE_HASH = 'd'.repeat(64);
const EXPECTED = { instance: '69a63215-e52f-4a53-a4a0-d0528b4bc27b', runtime: 'sha256:' + 'e'.repeat(64) };
const TIME = '2035-01-01T00:00:00.000Z';
const PRIVATE = 'neutral-private-detail-must-not-be-forwarded';
function features(state: 'uninitialized' | 'idle' | 'busy' | 'unknown' = 'uninitialized') {
  const codes = { uninitialized: 'native_uninitialized', idle: 'request_validation_required', busy: 'native_busy', unknown: 'native_state_unknown' };
  return { schema: 'phoenix.runtime-features', schema_version: 1, service: 'phoenix-desk-tools', app_version: '0.5.0',
    observed_at: TIME, observation_only: true, evidence_scope: 'current_host_execution_gate', request_checks: 'not_performed',
    host: { profile: 'browser-service', instance_id: EXPECTED.instance, runtime_id: EXPECTED.runtime }, native_execution: { state },
    features: { 'cad.thumbnail.regenerate': { state: state === 'idle' ? 'requires_preflight' : 'blocked', code: codes[state], reason: PRIVATE },
      'cad.cache.read': { state: 'supported', schema: SCHEMA, schema_version: 1, native_required: false, reason: PRIVATE } } };
}
function evidence(freshness: 'unverified' | 'stat_checked' | 'stale' = 'stat_checked', total = 1) {
  return { kind: 'saved_cache', freshness, check_scope: 'recorded_source_files', dependency_coverage: 'recorded_only',
    checked_sources: freshness === 'unverified' ? 0 : total, total_sources: total,
    recorded_source_sha256: total ? SOURCE_HASH : null, content_hash_verified: false };
}
function item(rel = REL) {
  return { rel, thumbnail: { state: 'record', reason: 'cache_record', revision: REVISION, updated_at: TIME,
    algorithm_version: null, source: evidence(), image: { sha256: IMAGE_HASH, width: 128, height: 96, bytes: 100, integrity: 'not_checked' } },
  geometry: { state: 'record', reason: 'cache_record', revision: REVISION, updated_at: TIME,
    algorithm_version: 1, source: evidence('stat_checked', 2), box_mm: { x_mm: 12, y_mm: 0, z_mm: 3.5 },
    units: 'mm', value_scope: 'cached_box_not_live_measurement' } };
}
function metadata(rels = [REL]) {
  return { schema: SCHEMA, schema_version: 1, ok: true, native_required: false, observed_at: TIME,
    scope: 'requested_file_metadata', complete: true, truncated: false, scan_id: null,
    database: { status: 'present', sha256: DATABASE_HASH, consistency: 'single_sqlite_read_transaction' },
    source_consistency: 'non_atomic_filesystem_stat', effects: { business_writes: false, temporary_coordination: 'own_sqlite_read_lease' },
    items: rels.map(item) };
}
function set(target: unknown, path: Array<string | number>, value: unknown): void {
  let cursor = target as Record<string | number, unknown>;
  for (const key of path.slice(0, -1)) cursor = cursor[key] as Record<string | number, unknown>;
  cursor[path.at(-1)!] = value;
}
function failure(code = 'cache_missing') {
  return { schema: SCHEMA, schema_version: 1, ok: false, native_required: false, code, detail: PRIVATE, retryable: false };
}
function throwsCode(operation: () => unknown, code: PftDeskCacheErrorCode = 'DESK_CACHE_INVALID_RESPONSE'): void {
  assert.throws(operation, (error: unknown) => error instanceof PftDeskCacheError && error.code === code && !error.message.includes(PRIVATE));
}

test('cache feature binding accepts every valid Native gate observation without granting Native authority', () => {
  for (const state of ['uninitialized', 'idle', 'busy', 'unknown'] as const) {
    assert.equal(pftRequireCacheFeature(features(state), EXPECTED), undefined);
  }
});

test('cache feature requires exact optional schema support and never infers it from transport readiness', () => {
  for (const value of [undefined, null, {}, { state: 'supported' },
    { state: 'supported', schema: SCHEMA, schema_version: 2, native_required: false, reason: 'Supported' },
    { state: 'supported', schema: SCHEMA, schema_version: 1, native_required: true, reason: 'Supported' }]) {
    const sample = features(); set(sample, ['features', 'cad.cache.read'], value);
    throwsCode(() => pftRequireCacheFeature(sample, EXPECTED), 'DESK_CACHE_FEATURE_UNSUPPORTED');
  }
});

test('cache feature binds browser instance and runtime to the fresh formal status identity', () => {
  for (const [key, value] of [['profile', 'api-service'], ['instance_id', '97b0dbad-3a55-44fa-880f-d6c87cffb109'], ['runtime_id', 'sha256:' + 'f'.repeat(64)]]) {
    const sample = features(); set(sample, ['host', key!], value);
    throwsCode(() => pftRequireCacheFeature(sample, EXPECTED), 'DESK_CACHE_IDENTITY_MISMATCH');
  }
  throwsCode(() => pftRequireCacheFeature(features(), { ...EXPECTED, instance: PRIVATE }), 'DESK_CACHE_IDENTITY_MISMATCH');
});

test('cache feature refuses malformed versioned observations and inconsistent Native assertions', () => {
  const cases: Array<[Array<string | number>, unknown]> = [
    [['schema'], 'other'], [['schema_version'], 2], [['service'], 'other'], [['app_version'], '0.6.0'],
    [['observation_only'], false], [['request_checks'], 'performed'], [['evidence_scope'], 'write_grant'],
    [['observed_at'], '2035-02-31T00:00:00Z'], [['observed_at'], PRIVATE], [['host'], null],
    [['native_execution', 'state'], 'ready'], [['features', 'cad.thumbnail.regenerate', 'state'], 'requires_preflight'],
    [['features', 'cad.thumbnail.regenerate', 'code'], 'request_validation_required'],
  ];
  for (const [path, value] of cases) {
    const sample = features(); set(sample, path, value); throwsCode(() => pftRequireCacheFeature(sample, EXPECTED));
  }
});

test('cache metadata is a detached public projection with saved-cache provenance and recorded-only stat evidence', () => {
  const sample = metadata();
  for (const path of [[], ['database'], ['effects'], ['items', 0], ['items', 0, 'thumbnail'], ['items', 0, 'thumbnail', 'source'],
    ['items', 0, 'thumbnail', 'image'], ['items', 0, 'geometry', 'box_mm']]) set(sample, [...path, 'private_detail'], PRIVATE);
  const result = pftDecodeCacheMetadata(sample, [REL]);
  assert.equal(result.data_source, 'stored_desk_cache'); assert.equal(result.native_memory, false); assert.equal(result.service_cpu_bound, false);
  assert.equal(result.items[0]?.thumbnail.image?.integrity, 'not_checked');
  assert.equal(result.items[0]?.thumbnail.source.content_hash_verified, false);
  assert.equal(result.items[0]?.geometry.source.dependency_coverage, 'recorded_only');
  assert.deepEqual(result.items[0]?.geometry.box_mm, { x_mm: 12, y_mm: 0, z_mm: 3.5 });
  assert.ok(!JSON.stringify(result).includes(PRIVATE));
  sample.items[0]!.geometry.box_mm.x_mm = 999; sample.items[0]!.thumbnail.image.sha256 = 'f'.repeat(64);
  assert.equal(result.items[0]?.geometry.box_mm?.x_mm, 12); assert.equal(result.items[0]?.thumbnail.image?.sha256, IMAGE_HASH);
});

test('cache metadata reorders a complete exact response to the requested order and does not invent omitted items', () => {
  const result = pftDecodeCacheMetadata(metadata([SECOND, REL]), [REL, SECOND]);
  assert.deepEqual(result.items.map(row => row.rel), [REL, SECOND]);
  throwsCode(() => pftDecodeCacheMetadata(metadata(), [REL, SECOND]));
  throwsCode(() => pftDecodeCacheMetadata(metadata([SECOND]), [REL]));
  throwsCode(() => pftDecodeCacheMetadata(metadata([REL, REL]), [REL, SECOND]));
  throwsCode(() => pftDecodeCacheMetadata(metadata(['parts/bracket.FCStd']), [REL]));
});

test('cache request binding rejects unsafe, oversized and case or normalization ambiguous relative paths', () => {
  const invalid = ['', '../Escape.FCStd', '/Absolute.FCStd', './Bracket.FCStd', 'parts/.hidden/Bracket.FCStd',
    'parts\\Bracket.FCStd', 'parts/../Bracket.FCStd', 'parts/node_modules/Bracket.FCStd', 'parts/__PYCACHE__/Bracket.FCStd',
    'parts/Bracket.FCStd\u0000', 'parts/Bracke\u0301t.FCStd', 'parts/File.step', 'parts/' + 'x'.repeat(1020) + '.FCStd'];
  for (const rel of invalid) throwsCode(() => pftDecodeCacheMetadata(metadata(), [rel]), 'DESK_CACHE_INVALID_ARGUMENT');
  for (const rels of [[], [REL, REL], [REL, REL.toUpperCase()], Array.from({ length: 11 }, (_, n) => `Item${n}.FCStd`)]) {
    throwsCode(() => pftDecodeCacheMetadata(metadata(), rels), 'DESK_CACHE_INVALID_ARGUMENT');
  }
});

test('cache metadata does not normalize unsafe or ambiguous response paths', () => {
  for (const rel of ['../Escape.FCStd', './parts/Bracket.FCStd', 'parts/Bracke\u0301t.FCStd']) {
    throwsCode(() => pftDecodeCacheMetadata(metadata([rel]), [REL]));
  }
  throwsCode(() => pftDecodeCacheMetadata(metadata([REL, REL.toUpperCase()]), [REL, SECOND]));
});

test('cache metadata preserves unavailable states and null values without fabricated dimensions', () => {
  for (const state of ['missing', 'stale', 'unsupported', 'invalid']) {
    const sample = metadata();
    for (const field of ['thumbnail', 'geometry']) {
      set(sample, ['items', 0, field, 'state'], state); set(sample, ['items', 0, field, 'reason'], 'cache_' + state);
      set(sample, ['items', 0, field, 'updated_at'], null); set(sample, ['items', 0, field, 'revision'], null);
      set(sample, ['items', 0, field, 'source'], evidence(state === 'stale' ? 'stale' : 'unverified', 0));
    }
    set(sample, ['items', 0, 'thumbnail', 'image'], null); set(sample, ['items', 0, 'geometry', 'box_mm'], null);
    const result = pftDecodeCacheMetadata(sample, [REL]);
    assert.equal(result.items[0]?.thumbnail.state, state); assert.equal(result.items[0]?.thumbnail.image, null);
    assert.equal(result.items[0]?.geometry.box_mm, null); assert.equal(result.items[0]?.geometry.updated_at, null);
  }
});

test('missing cache database requires all row time, revision, source and value evidence to be absent', () => {
  const sample = metadata();
  set(sample, ['database'], { status: 'missing', sha256: null, consistency: 'not_opened' });
  for (const field of ['thumbnail', 'geometry']) {
    set(sample, ['items', 0, field, 'state'], 'missing'); set(sample, ['items', 0, field, 'revision'], null);
    set(sample, ['items', 0, field, 'updated_at'], null); set(sample, ['items', 0, field, 'source'], evidence('unverified', 0));
  }
  set(sample, ['items', 0, 'thumbnail', 'image'], null); set(sample, ['items', 0, 'geometry', 'box_mm'], null);
  set(sample, ['items', 0, 'geometry', 'algorithm_version'], null);
  const result = pftDecodeCacheMetadata(sample, [REL]);
  assert.equal(result.database.status, 'missing');
  for (const cache of [result.items[0]!.thumbnail, result.items[0]!.geometry]) {
    assert.equal(cache.revision, null); assert.equal(cache.updated_at, null); assert.equal(cache.algorithm_version, null);
    assert.deepEqual(cache.source, evidence('unverified', 0));
  }
  const cases: Array<[Array<string | number>, unknown]> = [
    [['database', 'sha256'], DATABASE_HASH], [['database', 'consistency'], 'single_sqlite_read_transaction'],
    [['items', 0, 'thumbnail', 'image'], item().thumbnail.image], [['items', 0, 'geometry', 'box_mm'], item().geometry.box_mm],
    [['items', 0, 'geometry', 'algorithm_version'], 1],
  ];
  for (const field of ['thumbnail', 'geometry']) {
    const prefix = ['items', 0, field];
    cases.push([[...prefix, 'state'], 'unsupported'], [[...prefix, 'revision'], REVISION], [[...prefix, 'updated_at'], TIME],
      [[...prefix, 'source'], evidence()], [[...prefix, 'source', 'total_sources'], 1],
      [[...prefix, 'source'], { ...evidence('unverified', 0), total_sources: 1, checked_sources: 1 }],
      [[...prefix, 'source', 'recorded_source_sha256'], SOURCE_HASH]);
  }
  for (const [path, value] of cases) {
    const changed = structuredClone(sample); set(changed, path, value); throwsCode(() => pftDecodeCacheMetadata(changed, [REL]));
  }
});

test('cached records require an actual saved-row timestamp instead of accepting null evidence', () => {
  for (const field of ['thumbnail', 'geometry']) {
    const sample = metadata(); set(sample, ['items', 0, field, 'updated_at'], null);
    throwsCode(() => pftDecodeCacheMetadata(sample, [REL]));
  }
});

test('cache metadata rejects version, scope, completeness and effects escalation', () => {
  const cases: Array<[Array<string | number>, unknown]> = [
    [['schema'], 'other'], [['schema_version'], 2], [['ok'], false], [['native_required'], true], [['observed_at'], null],
    [['observed_at'], '2035-02-31T00:00:00Z'], [['scope'], 'workspace'], [['complete'], false], [['truncated'], true], [['scan_id'], 4],
    [['source_consistency'], 'atomic'], [['effects', 'business_writes'], true], [['effects', 'temporary_coordination'], 'none'],
    [['database', 'sha256'], null], [['database', 'status'], 'unknown'], [['items'], null],
  ];
  for (const [path, value] of cases) { const sample = metadata(); set(sample, path, value); throwsCode(() => pftDecodeCacheMetadata(sample, [REL])); }
});

test('cache source evidence rejects content-hash or complete dependency claims and inconsistent counts', () => {
  const cases: Array<[string, unknown]> = [['kind', 'live_cad'], ['freshness', 'fresh'], ['check_scope', 'all_files'],
    ['dependency_coverage', 'complete'], ['content_hash_verified', true], ['total_sources', 501], ['total_sources', -1],
    ['checked_sources', 0], ['checked_sources', 2], ['recorded_source_sha256', null], ['recorded_source_sha256', 'BAD']];
  for (const [key, value] of cases) {
    const sample = metadata(); set(sample, ['items', 0, 'thumbnail', 'source', key], value); throwsCode(() => pftDecodeCacheMetadata(sample, [REL]));
  }
  const sample = metadata(); set(sample, ['items', 0, 'thumbnail', 'state'], 'stale');
  set(sample, ['items', 0, 'thumbnail', 'image'], null); throwsCode(() => pftDecodeCacheMetadata(sample, [REL]));
});

test('cache record revisions, nullable fields and enum types remain strict', () => {
  const cases: Array<[Array<string | number>, unknown]> = [
    [['state'], {}], [['state'], 0], [['reason'], PRIVATE + '/path'], [['revision'], null], [['revision'], REVISION.toUpperCase()],
    [['updated_at'], 0], [['source'], null], [['source', 'freshness'], 'unverified'], [['algorithm_version'], 1],
  ];
  for (const [path, value] of cases) { const sample = metadata(); set(sample, ['items', 0, 'thumbnail', ...path], value); throwsCode(() => pftDecodeCacheMetadata(sample, [REL])); }
});

test('thumbnail metadata validates bounded image identity without pretending to verify PNG body integrity', () => {
  for (const [key, value] of [['sha256', 'not-a-hash'], ['width', 0], ['width', 4097], ['height', 1.5], ['bytes', 32],
    ['bytes', 4 * 1024 * 1024 + 1], ['integrity', 'verified']] as Array<[string, unknown]>) {
    const sample = metadata(); set(sample, ['items', 0, 'thumbnail', 'image', key], value); throwsCode(() => pftDecodeCacheMetadata(sample, [REL]));
  }
  const sample = metadata(); set(sample, ['items', 0, 'thumbnail', 'source'], evidence('stat_checked', 2));
  throwsCode(() => pftDecodeCacheMetadata(sample, [REL]));
});

test('geometry metadata accepts planar boxes and rejects invalid dimensions, units and algorithm versions', () => {
  for (const [path, value] of [[['box_mm'], { x_mm: 0, y_mm: 0, z_mm: 0 }], [['box_mm', 'x_mm'], -1], [['box_mm', 'y_mm'], Infinity],
    [['box_mm', 'x_mm'], '12'], [['box_mm', 'x_mm'], null], [['units'], 'inch'], [['value_scope'], 'live'],
    [['algorithm_version'], null], [['algorithm_version'], 2]] as Array<[Array<string | number>, unknown]>) {
    const sample = metadata(); set(sample, ['items', 0, 'geometry', ...path], value); throwsCode(() => pftDecodeCacheMetadata(sample, [REL]));
  }
  assert.equal(pftDecodeCacheMetadata(metadata(), [REL]).items[0]?.geometry.box_mm?.y_mm, 0);
});

test('typed cache failures retain distinct fixed codes and do not forward backend detail', () => {
  const mapping: Array<[string, number, PftDeskCacheErrorCode]> = [
    ['invalid_arguments', 400, 'DESK_CACHE_INVALID_ARGUMENT'], ['unsafe_path', 400, 'DESK_CACHE_UNSAFE_PATH'],
    ['unsafe_path', 409, 'DESK_CACHE_UNSAFE_PATH'], ['cache_busy', 409, 'DESK_CACHE_BUSY'], ['unsupported_storage', 409, 'DESK_CACHE_UNSUPPORTED_STORAGE'],
    ['cache_changed', 409, 'DESK_CACHE_CHANGED'], ['resource_limit', 413, 'DESK_CACHE_RESOURCE_LIMIT'], ['timeout', 408, 'DESK_CACHE_TIMEOUT'],
    ['cancelled', 408, 'DESK_CACHE_CANCELLED'], ['read_failed', 503, 'DESK_CACHE_READ_FAILED'], ['close_failed', 503, 'DESK_CACHE_CLOSE_FAILED'],
    ['cache_missing', 404, 'DESK_CACHE_MISSING'], ['cache_stale', 409, 'DESK_CACHE_STALE'],
    ['cache_unsupported', 409, 'DESK_CACHE_UNSUPPORTED'], ['cache_invalid', 409, 'DESK_CACHE_INVALID'],
  ];
  for (const [code, status, expected] of mapping) {
    const error = pftCacheFailure(failure(code), status);
    assert.ok(error instanceof PftDeskCacheError); assert.equal(error.code, expected);
    assert.ok(!error.message.includes(PRIVATE)); assert.ok(!JSON.stringify(error).includes(PRIVATE));
  }
});

test('unversioned, unknown or status-inconsistent cache failures are rejected with a fixed invalid-response error', () => {
  for (const [key, value] of [['schema', 'other'], ['schema_version', 2], ['ok', true], ['native_required', true],
    ['retryable', true], ['code', 'unknown'], ['code', 'toString'], ['detail', {}], ['detail', 'x'.repeat(2049)]] as Array<[string, unknown]>) {
    const sample = failure(); set(sample, [key], value); assert.equal(pftCacheFailure(sample, 404).code, 'DESK_CACHE_INVALID_RESPONSE');
  }
  for (const status of [200, 409, 503, NaN, 404.5]) assert.equal(pftCacheFailure(failure(), status).code, 'DESK_CACHE_INVALID_RESPONSE');
  for (const value of [null, [], PRIVATE]) assert.equal(pftCacheFailure(value, 404).code, 'DESK_CACHE_INVALID_RESPONSE');
  assert.equal(new PftDeskCacheError(PRIVATE as PftDeskCacheErrorCode).code, 'DESK_CACHE_INVALID_RESPONSE');
});
