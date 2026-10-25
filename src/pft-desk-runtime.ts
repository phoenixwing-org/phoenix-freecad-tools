// SPDX-License-Identifier: Apache-2.0
/** Pure, bound observation of Desk's versioned runtime features. No authority or I/O. */
const SCHEMA = 'phoenix.runtime-features';
const CACHE_SCHEMA = 'phoenix.cad-cache-read';
const CACHE_FEATURE = 'cad.cache.read';
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/iu;
const BAD_TEXT = /[\p{Cc}\p{Cf}\p{Cs}]/u;
const MESSAGES = {
  DESK_RUNTIME_INVALID_RESPONSE: 'Desk 运行能力返回了不兼容或互相矛盾的数据。',
  DESK_RUNTIME_IDENTITY_MISMATCH: 'Desk 运行能力的宿主身份与已验证连接不一致。',
  DESK_RUNTIME_CACHE_FEATURE_INCOMPATIBLE: 'Desk 声明的既有缓存只读能力不兼容。',
} as const;
export type PftDeskRuntimeErrorCode = keyof typeof MESSAGES;
export class PftDeskRuntimeError extends Error {
  readonly code: PftDeskRuntimeErrorCode;
  constructor(code: PftDeskRuntimeErrorCode) {
    const accepted = Object.hasOwn(MESSAGES, code) ? code : 'DESK_RUNTIME_INVALID_RESPONSE';
    super(MESSAGES[accepted]); this.name = 'PftDeskRuntimeError'; this.code = accepted;
  }
}
export type PftDeskNativeGateState = 'uninitialized' | 'idle' | 'busy' | 'unknown';
const NATIVE_CODES = {
  uninitialized: 'native_uninitialized', idle: 'request_validation_required',
  busy: 'native_busy', unknown: 'native_state_unknown',
} as const;
type CacheFeature = { state: 'not_declared' } | {
  state: 'supported'; schema: 'phoenix.cad-cache-read'; schema_version: 1; native_required: false;
};
export type PftDeskRuntimeObservation = {
  schema: 'phoenix.runtime-features'; schema_version: 1; app_version: '0.5.0'; observed_at: string;
  observation_only: true; evidence_scope: 'current_host_execution_gate'; request_checks: 'not_performed';
  native_execution: { state: PftDeskNativeGateState };
  features: {
    'cad.thumbnail.regenerate': { state: 'blocked' | 'requires_preflight'; code: typeof NATIVE_CODES[PftDeskNativeGateState] };
    'cad.cache.read': CacheFeature;
  };
};
const invalid = (): PftDeskRuntimeError => new PftDeskRuntimeError('DESK_RUNTIME_INVALID_RESPONSE');
function record(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}
function object(value: unknown): Record<string, unknown> { if (!record(value)) throw invalid(); return value; }
function text(value: unknown, maximum: number): value is string {
  return typeof value === 'string' && value.length > 0 && value.length <= maximum && !BAD_TEXT.test(value);
}
function timestamp(value: unknown): value is string {
  if (!text(value, 64)) return false;
  const match = /^(\d{4})-(\d{2})-(\d{2})T([0-1]\d|2[0-3]):[0-5]\d:[0-5]\d(?:\.\d{1,9})?(?:Z|[+-](?:[0-1]\d|2[0-3]):[0-5]\d)$/u.exec(value);
  if (!match || !Number.isFinite(Date.parse(value))) return false;
  const year = Number(match[1]), month = Number(match[2]), day = Number(match[3]);
  const days = [31, year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0) ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
  return month >= 1 && month <= 12 && day >= 1 && day <= days[month - 1]!;
}
function decode(value: unknown, expected: { instance: string; runtime: string }): PftDeskRuntimeObservation {
  const data = object(value);
  if (data.schema !== SCHEMA || data.schema_version !== 1 || data.service !== 'phoenix-desk-tools'
    || data.app_version !== '0.5.0' || !timestamp(data.observed_at) || data.observation_only !== true
    || data.evidence_scope !== 'current_host_execution_gate' || data.request_checks !== 'not_performed') throw invalid();
  const host = object(data.host), native = object(data.native_execution), features = object(data.features);
  if (!record(expected) || !text(expected.instance, 64) || !UUID.test(expected.instance)
    || !text(expected.runtime, 71) || !/^sha256:[a-f0-9]{64}$/u.test(expected.runtime)
    || host.profile !== 'browser-service' || host.instance_id !== expected.instance || host.runtime_id !== expected.runtime) {
    throw new PftDeskRuntimeError('DESK_RUNTIME_IDENTITY_MISMATCH');
  }
  if (typeof native.state !== 'string' || !Object.hasOwn(NATIVE_CODES, native.state)) throw invalid();
  const state = native.state as PftDeskNativeGateState;
  const regenerate = object(features['cad.thumbnail.regenerate']);
  if (!Object.hasOwn(features, 'cad.thumbnail.regenerate')
    || regenerate.state !== (state === 'idle' ? 'requires_preflight' : 'blocked')
    || regenerate.code !== NATIVE_CODES[state] || !text(regenerate.reason, 2048)) throw invalid();
  let cache: CacheFeature = { state: 'not_declared' };
  if (Object.hasOwn(features, CACHE_FEATURE)) {
    const source = features[CACHE_FEATURE];
    if (!record(source) || source.state !== 'supported' || source.schema !== CACHE_SCHEMA
      || source.schema_version !== 1 || source.native_required !== false || !text(source.reason, 2048)) {
      throw new PftDeskRuntimeError('DESK_RUNTIME_CACHE_FEATURE_INCOMPATIBLE');
    }
    cache = { state: 'supported', schema: CACHE_SCHEMA, schema_version: 1, native_required: false };
  }
  return { schema: SCHEMA, schema_version: 1, app_version: '0.5.0', observed_at: data.observed_at,
    observation_only: true, evidence_scope: 'current_host_execution_gate', request_checks: 'not_performed',
    native_execution: { state }, features: {
      'cad.thumbnail.regenerate': { state: state === 'idle' ? 'requires_preflight' : 'blocked', code: NATIVE_CODES[state] },
      'cad.cache.read': cache,
    } };
}

/** Missing cache support is an observation; a present incompatible declaration is an error. */
export function pftDecodeRuntimeFeatures(value: unknown, expected: { instance: string; runtime: string }): PftDeskRuntimeObservation {
  try { return decode(value, expected); }
  catch (error) {
    if (error instanceof PftDeskRuntimeError) throw error;
    // Never forward a host-provided getter/exception or stringify unknown data.
    throw invalid();
  }
}
