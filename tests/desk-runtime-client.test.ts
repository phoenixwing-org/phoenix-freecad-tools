// SPDX-License-Identifier: Apache-2.0
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readdirSync } from 'node:fs';
import { PftDeskError, type PftDeskStatusOptions } from '../src/adapters/pft-desk-client.js';
import { fixture, FEATURES, PRIVATE } from './helpers/desk-cache-fixture.js';

const options = { includeRuntimeFeatures: true };
async function rejectsCode(operation: Promise<unknown>, code: string) {
  await assert.rejects(operation, (error: unknown) => error instanceof PftDeskError && error.code === code
    && !error.message.includes(PRIVATE) && !JSON.stringify(error).includes(PRIVATE));
}

test('runtime observation is opt-in; default and false keep the original status shape and requests', async t => {
  const f = await fixture(t); f.setMode('feature-404');
  for (const input of [{}, { includeRuntimeFeatures: false }, Object.create({ includeRuntimeFeatures: true }) as PftDeskStatusOptions]) {
    const result = await f.client().status(input);
    assert.equal(result.state, 'ready'); assert.equal('runtime_features' in result, false); f.noPrivate(result);
  }
  assert.deepEqual(f.calls.map(call => call.url.pathname), Array(3).fill('/api/desk-service'));
  assert.equal(f.cliCalls().length, 3);
});

test('runtime status rejects non-boolean or extra inputs before configuration or CLI access', async t => {
  const f = await fixture(t);
  for (const input of [null, [], false, 'true', { includeRuntimeFeatures: 'true' }, { includeRuntimeFeatures: 1 },
    { includeRuntimeFeatures: null }, { includeRuntimeFeatures: undefined }, { includeRuntimeFeatures: true, action: 'ensure' }]) {
    await rejectsCode(f.client().status(input as PftDeskStatusOptions), 'INVALID_ARGUMENT');
  }
  assert.equal(f.calls.length, 0); assert.equal(f.cliCalls().length, 0);
});

test('explicit runtime status binds the feature observation and post-read identity without reading workspace data', async t => {
  const f = await fixture(t), result = await f.client().status(options);
  assert.equal(result.state, 'ready'); assert.equal(result.runtime_features?.state, 'observed');
  assert.ok(result.runtime_features?.state === 'observed');
  const value = result.runtime_features.observation;
  assert.equal(value.observation_only, true); assert.equal(value.request_checks, 'not_performed');
  assert.deepEqual(value.native_execution, { state: 'busy' });
  assert.deepEqual(value.features['cad.thumbnail.regenerate'], { state: 'blocked', code: 'native_busy' });
  assert.deepEqual(value.features['cad.cache.read'], { state: 'supported', schema: 'phoenix.cad-cache-read', schema_version: 1, native_required: false });
  f.noPrivate(result); assert.equal(JSON.stringify(result).includes('reason'), false);
  assert.deepEqual(f.calls.map(call => [call.method, call.url.pathname]), [
    ['GET', '/api/desk-service'], ['GET', FEATURES], ['GET', '/api/desk-service'],
  ]);
  assert.deepEqual(f.cliCalls(), [['status', '--state-dir', f.stateDir]]);
  assert.deepEqual(readdirSync(f.workspace), []); assert.deepEqual(readdirSync(f.stateDir), []);
  assert.ok(f.calls.every(call => call.body.length === 0 && !call.url.search && call.headers.origin === undefined));
});

test('all four Native states remain observations and idle never becomes an execution grant', async t => {
  const f = await fixture(t);
  for (const [state, code] of [['uninitialized', 'native_uninitialized'], ['idle', 'request_validation_required'],
    ['busy', 'native_busy'], ['unknown', 'native_state_unknown']]) {
    f.setFeatures(value => { value.native_execution.state = state!;
      Object.assign(value.features['cad.thumbnail.regenerate'], { state: state === 'idle' ? 'requires_preflight' : 'blocked', code }); return value; });
    const result = await f.client().status(options); assert.ok(result.runtime_features?.state === 'observed');
    assert.equal(result.runtime_features.observation.native_execution.state, state);
    assert.equal(result.runtime_features.observation.features['cad.thumbnail.regenerate'].state, state === 'idle' ? 'requires_preflight' : 'blocked');
    assert.equal(result.runtime_features.observation.request_checks, 'not_performed'); f.noPrivate(result);
  }
  assert.equal(f.calls.length, 12); assert.equal(f.cliCalls().length, 4);
});

test('old runtime endpoint and missing optional cache declaration are explicit negative observations', async t => {
  const f = await fixture(t); f.setMode('feature-404');
  const absent = await f.client().status(options);
  assert.deepEqual(absent.runtime_features, { state: 'not_declared', reason_code: 'endpoint_absent' });
  assert.deepEqual(f.calls.map(call => call.url.pathname), ['/api/desk-service', FEATURES, '/api/desk-service']);
  f.setMode('feature-missing'); const withoutCache = await f.client().status(options);
  assert.ok(withoutCache.runtime_features?.state === 'observed');
  assert.deepEqual(withoutCache.runtime_features.observation.features['cad.cache.read'], { state: 'not_declared' });
  f.noPrivate(absent); f.noPrivate(withoutCache);
});

test('changed feature host or formal instance prevents success before or after the observation', async t => {
  const f = await fixture(t);
  for (const [mode, code, count] of [['host-mismatch', 'DESK_RUNTIME_IDENTITY_MISMATCH', 2],
    ['identity-before', 'DESK_IDENTITY_MISMATCH', 1], ['identity-after', 'DESK_IDENTITY_MISMATCH', 3]] as const) {
    f.setMode(mode); const before = f.calls.length;
    await rejectsCode(f.client().status(options), code); assert.equal(f.calls.length - before, count);
  }
  f.setMode('identity-after'); f.special({ route: FEATURES, kind: 'failure', status: 404 });
  await rejectsCode(f.client().status(options), 'DESK_IDENTITY_MISMATCH');
});

test('unknown schemas and contradictory native claims fail instead of returning ready with invented support', async t => {
  const f = await fixture(t);
  for (const change of [
    (value: Record<string, unknown>) => { value.schema_version = 2; },
    (value: Record<string, unknown>) => { value.observation_only = false; },
    (value: Record<string, unknown>) => { value.request_checks = 'passed'; },
  ]) {
    f.setFeatures(value => { change(value); return value; });
    await rejectsCode(f.client().status(options), 'DESK_RUNTIME_INVALID_RESPONSE');
  }
  f.setFeatures(value => { value.features['cad.cache.read'].schema_version = 2; return value; });
  await rejectsCode(f.client().status(options), 'DESK_RUNTIME_CACHE_FEATURE_INCOMPATIBLE');
  assert.equal(f.calls.length, 8); assert.equal(f.cliCalls().length, 4);
});

test('runtime transport rejects failures, redirects and oversized responses with no fallback or auto retry', async t => {
  const f = await fixture(t);
  for (const [kind, code] of [['failure', 'DESK_RUNTIME_HTTP_ERROR'], ['redirect', 'DESK_REDIRECT_FORBIDDEN'],
    ['large', 'RESPONSE_TOO_LARGE']] as const) {
    f.special({ route: FEATURES, kind, status: 503 });
    await rejectsCode(f.client().status(options), code);
  }
  f.special(undefined); f.setFeatures(value => ({ ...value, extra: 'x'.repeat(64 * 1024) }));
  await rejectsCode(f.client().status(options), 'RESPONSE_TOO_LARGE');
  assert.equal(f.calls.length, 8); assert.equal(f.cliCalls().length, 4);
  assert.equal(f.calls.some(call => call.url.pathname === '/must-not-follow'), false);
});

test('runtime deadline aborts only this observation and a later explicit request still works', async t => {
  const f = await fixture(t); f.special({ route: FEATURES, kind: 'timeout' });
  await rejectsCode(f.client(500).status(options), 'DESK_TIMEOUT');
  assert.equal(f.calls.length, 2); assert.equal(f.cliCalls().length, 1);
  f.special(undefined); assert.equal((await f.client().status(options)).state, 'ready');
  assert.equal(f.calls.length, 5); assert.equal(f.cliCalls().length, 2);
});
