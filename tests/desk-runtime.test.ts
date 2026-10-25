// SPDX-License-Identifier: Apache-2.0
import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  pftDecodeRuntimeFeatures, PftDeskRuntimeError,
  type PftDeskNativeGateState, type PftDeskRuntimeErrorCode,
} from '../src/pft-desk-runtime.js';
import { pftRequireCacheFeature, PftDeskCacheError } from '../src/pft-desk-cache.js';

const EXPECTED = { instance: '11111111-1111-4111-8111-111111111111', runtime: 'sha256:' + 'a'.repeat(64) };
const TIME = '2035-01-01T00:00:00.000Z';
const HIDDEN = 'synthetic-extra-detail-must-not-be-forwarded';
const CODES = {
  uninitialized: 'native_uninitialized', idle: 'request_validation_required',
  busy: 'native_busy', unknown: 'native_state_unknown',
};
function features(state: PftDeskNativeGateState = 'uninitialized'): Record<string, unknown> {
  return { schema: 'phoenix.runtime-features', schema_version: 1, service: 'phoenix-desk-tools', app_version: '0.5.0',
    observed_at: TIME, observation_only: true, evidence_scope: 'current_host_execution_gate', request_checks: 'not_performed',
    host: { profile: 'browser-service', instance_id: EXPECTED.instance, runtime_id: EXPECTED.runtime },
    native_execution: { state }, features: {
      'cad.thumbnail.regenerate': { state: state === 'idle' ? 'requires_preflight' : 'blocked', code: CODES[state], reason: HIDDEN },
      'cad.cache.read': { state: 'supported', schema: 'phoenix.cad-cache-read', schema_version: 1, native_required: false, reason: HIDDEN },
    } };
}
function at(target: Record<string, unknown>, path: readonly string[]): Record<string, unknown> {
  let cursor = target;
  for (const key of path) cursor = cursor[key] as Record<string, unknown>;
  return cursor;
}
function set(target: Record<string, unknown>, path: readonly string[], value: unknown): void {
  at(target, path.slice(0, -1))[path.at(-1)!] = value;
}
function throwsCode(operation: () => unknown, code: PftDeskRuntimeErrorCode = 'DESK_RUNTIME_INVALID_RESPONSE'): void {
  assert.throws(operation, (error: unknown) => error instanceof PftDeskRuntimeError && error.code === code
    && !error.message.includes(HIDDEN) && !error.message.includes(EXPECTED.instance) && !error.message.includes(EXPECTED.runtime));
}

test('runtime observations retain all four gate states without promoting idle to a Native grant', () => {
  for (const state of ['uninitialized', 'idle', 'busy', 'unknown'] as const) {
    const result = pftDecodeRuntimeFeatures(features(state), EXPECTED);
    assert.deepEqual(result, { schema: 'phoenix.runtime-features', schema_version: 1, app_version: '0.5.0', observed_at: TIME,
      observation_only: true, evidence_scope: 'current_host_execution_gate', request_checks: 'not_performed',
      native_execution: { state }, features: {
        'cad.thumbnail.regenerate': { state: state === 'idle' ? 'requires_preflight' : 'blocked', code: CODES[state] },
        'cad.cache.read': { state: 'supported', schema: 'phoenix.cad-cache-read', schema_version: 1, native_required: false },
      } });
  }
});

test('only absent cache capability becomes not_declared and never blocks a valid runtime observation', () => {
  const sample = features('idle'); delete at(sample, ['features'])['cad.cache.read'];
  const result = pftDecodeRuntimeFeatures(sample, EXPECTED);
  assert.deepEqual(result.features['cad.cache.read'], { state: 'not_declared' });
  assert.equal(result.features['cad.thumbnail.regenerate'].state, 'requires_preflight');
});

test('present malformed or future cache declarations remain explicit incompatibility errors', () => {
  const valid = at(features(), ['features', 'cad.cache.read']);
  for (const cache of [undefined, null, false, [], {}, { ...valid, state: 'not_declared' },
    { ...valid, state: 'unsupported' }, { ...valid, schema: 'future-cache' }, { ...valid, schema_version: 2 },
    { ...valid, schema_version: '1' }, { ...valid, native_required: true }, { ...valid, native_required: 0 },
    { ...valid, reason: '' }, { ...valid, reason: HIDDEN + '\n' }, { ...valid, reason: 'x'.repeat(2049) }]) {
    const sample = features(); set(sample, ['features', 'cad.cache.read'], cache);
    throwsCode(() => pftDecodeRuntimeFeatures(sample, EXPECTED), 'DESK_RUNTIME_CACHE_FEATURE_INCOMPATIBLE');
  }
});

test('runtime envelope rejects unsupported versions, false observation assertions and unavailable structures', () => {
  for (const sample of [null, undefined, false, 0, 'ready', [], {}]) throwsCode(() => pftDecodeRuntimeFeatures(sample, EXPECTED));
  const cases: Array<[string[], unknown]> = [
    [['schema'], 'future-runtime'], [['schema_version'], 2], [['schema_version'], '1'], [['service'], 'other-service'],
    [['app_version'], '0.6.0'], [['observation_only'], false], [['observation_only'], 1],
    [['evidence_scope'], 'global_execution_grant'], [['request_checks'], 'performed'],
    [['host'], null], [['native_execution'], []], [['features'], null],
  ];
  for (const [path, value] of cases) {
    const sample = features(); set(sample, path, value); throwsCode(() => pftDecodeRuntimeFeatures(sample, EXPECTED));
  }
});

test('runtime timestamps validate calendar dates, time and explicit timezone without inventing freshness', () => {
  for (const value of ['2035-02-31T00:00:00Z', '2035-02-29T00:00:00Z', '1900-02-29T00:00:00Z',
    '2035-04-31T00:00:00Z', '2035-00-01T00:00:00Z', '2035-13-01T00:00:00Z', '2035-01-00T00:00:00Z',
    '2035-01-01T24:00:00Z', '2035-01-01T00:60:00Z', '2035-01-01T00:00:60Z',
    '2035-01-01T00:00:00', '2035-01-01', '2035-01-01T00:00:00+24:00',
    '2035-01-01T00:00:00.1234567890Z', TIME + '\n', null, 0]) {
    const sample = features(); sample.observed_at = value; throwsCode(() => pftDecodeRuntimeFeatures(sample, EXPECTED));
  }
  for (const value of ['2000-02-29T00:00:00Z', '2036-02-29T12:34:56.123456789+08:00', '2001-01-01T00:00:00-04:30']) {
    const sample = features(); sample.observed_at = value;
    assert.equal(pftDecodeRuntimeFeatures(sample, EXPECTED).observed_at, value);
  }
});

test('runtime host must match both formal-status instance and runtime under the browser profile', () => {
  const cases: Array<[string, unknown]> = [
    ['profile', 'api-service'], ['profile', null], ['instance_id', null], ['runtime_id', null],
    ['instance_id', '22222222-2222-4222-8222-222222222222'], ['runtime_id', 'sha256:' + 'b'.repeat(64)],
  ];
  for (const [key, value] of cases) {
    const sample = features(); set(sample, ['host', key], value);
    throwsCode(() => pftDecodeRuntimeFeatures(sample, EXPECTED), 'DESK_RUNTIME_IDENTITY_MISMATCH');
  }
  for (const expected of [null, {}, { ...EXPECTED, instance: '' }, { ...EXPECTED, instance: HIDDEN },
    { ...EXPECTED, runtime: 'a'.repeat(64) }, { ...EXPECTED, runtime: 'sha256:' + 'A'.repeat(64) }]) {
    throwsCode(() => pftDecodeRuntimeFeatures(features(), expected as typeof EXPECTED), 'DESK_RUNTIME_IDENTITY_MISMATCH');
  }
});

test('runtime gate and regeneration feature must agree, including every non-idle blocked state', () => {
  for (const state of ['uninitialized', 'idle', 'busy', 'unknown'] as const) {
    for (const other of ['uninitialized', 'idle', 'busy', 'unknown'] as const) {
      if (state === other) continue;
      const sample = features(state); set(sample, ['features', 'cad.thumbnail.regenerate', 'code'], CODES[other]);
      throwsCode(() => pftDecodeRuntimeFeatures(sample, EXPECTED));
    }
    const sample = features(state);
    set(sample, ['features', 'cad.thumbnail.regenerate', 'state'], state === 'idle' ? 'blocked' : 'requires_preflight');
    throwsCode(() => pftDecodeRuntimeFeatures(sample, EXPECTED));
  }
  for (const value of ['ready', 'allowed', '__proto__', 'constructor', false, 0, null]) {
    const sample = features(); set(sample, ['native_execution', 'state'], value);
    throwsCode(() => pftDecodeRuntimeFeatures(sample, EXPECTED));
  }
});

test('runtime requires the known regeneration declaration and bounded plain reason without returning it', () => {
  for (const feature of [undefined, null, {}, [], { state: 'blocked', code: 'native_uninitialized' },
    { state: 'blocked', code: 'native_uninitialized', reason: '' },
    { state: 'blocked', code: 'native_uninitialized', reason: 'x'.repeat(2049) },
    { state: 'blocked', code: 'native_uninitialized', reason: '\u0000' }]) {
    const sample = features(); set(sample, ['features', 'cad.thumbnail.regenerate'], feature);
    throwsCode(() => pftDecodeRuntimeFeatures(sample, EXPECTED));
  }
  const sample = features(); delete at(sample, ['features'])['cad.thumbnail.regenerate'];
  throwsCode(() => pftDecodeRuntimeFeatures(sample, EXPECTED));
});

test('runtime returns a detached whitelist without host identity, raw text or unknown features', () => {
  const sample = features();
  for (const path of [[], ['host'], ['native_execution'], ['features'], ['features', 'cad.thumbnail.regenerate'], ['features', 'cad.cache.read']]) {
    at(sample, path).extra_detail = HIDDEN;
  }
  at(sample, ['features'])['future.unrelated.feature'] = { state: 'supported', reason: HIDDEN };
  const result = pftDecodeRuntimeFeatures(sample, EXPECTED);
  for (const forbidden of [HIDDEN, EXPECTED.instance, EXPECTED.runtime, '"host":', '"service":', '"reason":', 'extra_detail', 'future.unrelated.feature']) {
    assert.ok(!JSON.stringify(result).includes(forbidden));
  }
  set(sample, ['native_execution', 'state'], 'idle'); set(sample, ['features', 'cad.cache.read', 'schema_version'], 2);
  assert.equal(result.native_execution.state, 'uninitialized');
  assert.deepEqual(result.features['cad.cache.read'], { state: 'supported', schema: 'phoenix.cad-cache-read', schema_version: 1, native_required: false });
});

test('runtime ignores unknown getters and does not coerce known malformed fields or leak getter failures', () => {
  const sample = features(); let reads = 0;
  const bomb = { toString() { reads++; throw new Error(HIDDEN); } };
  Object.defineProperty(sample, 'extra_detail', { enumerable: true, get() { reads++; throw new Error(HIDDEN); } });
  Object.defineProperty(at(sample, ['features']), 'future.feature', { enumerable: true, get() { reads++; throw new Error(HIDDEN); } });
  pftDecodeRuntimeFeatures(sample, EXPECTED); assert.equal(reads, 0);
  sample.observed_at = bomb; throwsCode(() => pftDecodeRuntimeFeatures(sample, EXPECTED)); assert.equal(reads, 0);
  const throwing = features(); Object.defineProperty(throwing, 'schema', { get() { throw new Error(HIDDEN); } });
  throwsCode(() => pftDecodeRuntimeFeatures(throwing, EXPECTED));
});

test('cache guard retains its existing public error contract while sharing the runtime decoder', () => {
  const absent = features(); delete at(absent, ['features'])['cad.cache.read'];
  const incompatible = features(); set(incompatible, ['features', 'cad.cache.read', 'schema_version'], 2);
  const mismatch = features(); set(mismatch, ['host', 'runtime_id'], 'sha256:' + 'b'.repeat(64));
  const invalid = features(); invalid.observation_only = false;
  for (const [sample, code] of [[absent, 'DESK_CACHE_FEATURE_UNSUPPORTED'], [incompatible, 'DESK_CACHE_FEATURE_UNSUPPORTED'],
    [mismatch, 'DESK_CACHE_IDENTITY_MISMATCH'], [invalid, 'DESK_CACHE_INVALID_RESPONSE']] as const) {
    assert.throws(() => pftRequireCacheFeature(sample, EXPECTED), (error: unknown) => error instanceof PftDeskCacheError
      && error.code === code && !error.message.includes(HIDDEN));
  }
});
