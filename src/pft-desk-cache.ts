// SPDX-License-Identifier: Apache-2.0
/** Public Desk cache wire validation only. No transport, database, CAD or image processing. */
import { pftDecodeRuntimeFeatures, PftDeskRuntimeError } from './pft-desk-runtime.js';
const SCHEMA = 'phoenix.cad-cache-read';
const HASH = /^[a-f0-9]{64}$/u;
const BAD_TEXT = /[\p{Cc}\p{Cf}\p{Cs}]/u;
const STATES = ['record', 'missing', 'stale', 'unsupported', 'invalid'] as const;
const MESSAGES = {
  DESK_CACHE_FEATURE_UNSUPPORTED: '当前 Desk 未声明兼容的既有缓存只读能力。',
  DESK_CACHE_IDENTITY_MISMATCH: 'Desk 缓存能力的宿主身份与已验证连接不一致。',
  DESK_CACHE_INVALID_RESPONSE: 'Desk 缓存返回了不兼容或互相矛盾的数据。',
  DESK_CACHE_INVALID_ARGUMENT: '缓存查询需要有限且明确的安全相对 FCStd 路径。',
  DESK_CACHE_UNSAFE_PATH: '缓存读取路径边界无法确认，未接受结果。',
  DESK_CACHE_BUSY: '既有缓存正在使用中；不会回收其他读取者的锁。',
  DESK_CACHE_UNSUPPORTED_STORAGE: '既有缓存存储布局不受支持；不会迁移或修复。',
  DESK_CACHE_CHANGED: '缓存或已记录来源发生变化，请重新核对元数据。',
  DESK_CACHE_RESOURCE_LIMIT: '缓存读取超过资源上限，请缩小查询范围。',
  DESK_CACHE_TIMEOUT: '缓存读取超过截止时间；不会自动重试。',
  DESK_CACHE_CANCELLED: '缓存读取已取消；不会自动重试。',
  DESK_CACHE_READ_FAILED: '既有缓存无法读取；不会创建或修复缓存。',
  DESK_CACHE_CLOSE_FAILED: '缓存读取关闭状态无法确认，请核对后处理。',
  DESK_CACHE_MISSING: '指定的既有缓存不存在；不会生成或读取模型作为回退。',
  DESK_CACHE_STALE: '指定的既有缓存已过期；不会自动更新。',
  DESK_CACHE_UNSUPPORTED: '指定的既有缓存版本不受支持；不会迁移。',
  DESK_CACHE_INVALID: '指定的既有缓存校验未通过；不会修改缓存。',
} as const;
export type PftDeskCacheErrorCode = keyof typeof MESSAGES;
export class PftDeskCacheError extends Error {
  readonly code: PftDeskCacheErrorCode;
  constructor(code: PftDeskCacheErrorCode) {
    const accepted = Object.hasOwn(MESSAGES, code) ? code : 'DESK_CACHE_INVALID_RESPONSE';
    super(MESSAGES[accepted]); this.name = 'PftDeskCacheError'; this.code = accepted;
  }
}
export type PftDeskCacheState = typeof STATES[number];
export type PftDeskCacheSource = {
  kind: 'saved_cache'; freshness: 'unverified' | 'stat_checked' | 'stale';
  check_scope: 'recorded_source_files'; dependency_coverage: 'recorded_only';
  checked_sources: number; total_sources: number; recorded_source_sha256: string | null; content_hash_verified: false;
};
type Common = {
  state: PftDeskCacheState; reason: string; revision: string | null; updated_at: string | null;
  source: PftDeskCacheSource;
};
export type PftDeskCacheThumbnail = Common & {
  algorithm_version: null;
  image: { sha256: string; width: number; height: number; bytes: number; integrity: 'not_checked' } | null;
};
export type PftDeskCacheGeometry = Common & {
  algorithm_version: 1 | null; box_mm: { x_mm: number; y_mm: number; z_mm: number } | null;
  units: 'mm'; value_scope: 'cached_box_not_live_measurement';
};
export type PftDeskCacheItem = { rel: string; thumbnail: PftDeskCacheThumbnail; geometry: PftDeskCacheGeometry };
export type PftDeskCacheMetadata = {
  schema: 'phoenix.cad-cache-read'; schema_version: 1; ok: true; native_required: false;
  observed_at: string; scope: 'requested_file_metadata'; complete: true; truncated: false; scan_id: null;
  database: { status: 'present' | 'missing'; sha256: string | null; consistency: 'single_sqlite_read_transaction' | 'not_opened' };
  source_consistency: 'non_atomic_filesystem_stat';
  effects: { business_writes: false; temporary_coordination: 'own_sqlite_read_lease' };
  items: PftDeskCacheItem[]; data_source: 'stored_desk_cache'; native_memory: false; service_cpu_bound: false;
};
const invalid = (): PftDeskCacheError => new PftDeskCacheError('DESK_CACHE_INVALID_RESPONSE');
function record(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}
function object(value: unknown): Record<string, unknown> { if (!record(value)) throw invalid(); return value; }
function text(value: unknown, maximum: number): value is string {
  return typeof value === 'string' && value.length > 0 && value.length <= maximum && !BAD_TEXT.test(value);
}
function integer(value: unknown, minimum: number, maximum: number): value is number {
  return typeof value === 'number' && Number.isSafeInteger(value) && value >= minimum && value <= maximum;
}
function hash(value: unknown): value is string { return typeof value === 'string' && HASH.test(value); }
function nullableHash(value: unknown): string | null { if (value === null || hash(value)) return value; throw invalid(); }
function timestamp(value: unknown): value is string {
  if (!text(value, 64)) return false;
  const match = /^(\d{4})-(\d{2})-(\d{2})T([0-1]\d|2[0-3]):[0-5]\d:[0-5]\d(?:\.\d{1,9})?(?:Z|[+-](?:[0-1]\d|2[0-3]):[0-5]\d)$/u.exec(value);
  if (!match || !Number.isFinite(Date.parse(value))) return false;
  const year = Number(match[1]), month = Number(match[2]), day = Number(match[3]);
  const days = [31, year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0) ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
  return month >= 1 && month <= 12 && day >= 1 && day <= days[month - 1]!;
}
function relative(value: unknown): value is string {
  return text(value, 1024) && value === value.trim() && value === value.normalize('NFC')
    && !/[\\:]/u.test(value) && /\.fcstd$/iu.test(value)
    && value.split('/').every(part => part && !part.startsWith('.') && !['node_modules', '__pycache__'].includes(part.toLowerCase()));
}
function rels(value: unknown): string[] {
  if (!Array.isArray(value) || value.length < 1 || value.length > 10 || !value.every(relative)
    || new Set(value.map(rel => rel.toLowerCase())).size !== value.length) {
    throw new PftDeskCacheError('DESK_CACHE_INVALID_ARGUMENT');
  }
  return [...value];
}

/** Bind the optional capability to the fresh formal-status identity. Native generation may be blocked. */
export function pftRequireCacheFeature(value: unknown, expected: { instance: string; runtime: string }): void {
  try {
    const observation = pftDecodeRuntimeFeatures(value, expected);
    if (observation.features['cad.cache.read'].state !== 'supported') {
      throw new PftDeskCacheError('DESK_CACHE_FEATURE_UNSUPPORTED');
    }
  } catch (error) {
    if (error instanceof PftDeskCacheError) throw error;
    if (error instanceof PftDeskRuntimeError) {
      if (error.code === 'DESK_RUNTIME_IDENTITY_MISMATCH') throw new PftDeskCacheError('DESK_CACHE_IDENTITY_MISMATCH');
      if (error.code === 'DESK_RUNTIME_CACHE_FEATURE_INCOMPATIBLE') throw new PftDeskCacheError('DESK_CACHE_FEATURE_UNSUPPORTED');
    }
    throw invalid();
  }
}

function source(value: unknown): PftDeskCacheSource {
  const data = object(value);
  if (data.kind !== 'saved_cache' || typeof data.freshness !== 'string' || !['unverified', 'stat_checked', 'stale'].includes(data.freshness)
    || data.check_scope !== 'recorded_source_files' || data.dependency_coverage !== 'recorded_only' || data.content_hash_verified !== false
    || !integer(data.total_sources, 0, 500) || !integer(data.checked_sources, 0, data.total_sources)) throw invalid();
  const recorded = nullableHash(data.recorded_source_sha256);
  if (data.freshness === 'stat_checked' && (data.total_sources < 1 || data.checked_sources !== data.total_sources || recorded === null)) throw invalid();
  return { kind: 'saved_cache', freshness: data.freshness as PftDeskCacheSource['freshness'],
    check_scope: 'recorded_source_files', dependency_coverage: 'recorded_only', checked_sources: data.checked_sources,
    total_sources: data.total_sources, recorded_source_sha256: recorded, content_hash_verified: false };
}
function common(value: Record<string, unknown>): Common {
  if (typeof value.state !== 'string' || !(STATES as readonly string[]).includes(value.state)
    || typeof value.reason !== 'string' || !/^[a-z][a-z0-9_]{0,79}$/u.test(value.reason)
    || value.updated_at !== null && !timestamp(value.updated_at)) throw invalid();
  const state = value.state as PftDeskCacheState, revision = nullableHash(value.revision), evidence = source(value.source);
  if (state === 'record' && (revision === null || value.updated_at === null || evidence.freshness !== 'stat_checked')
    || state === 'stale' && evidence.freshness !== 'stale') throw invalid();
  return { state, reason: value.reason, revision, updated_at: value.updated_at as string | null, source: evidence };
}
function thumbnail(value: unknown): PftDeskCacheThumbnail {
  const data = object(value), base = common(data);
  if (data.algorithm_version !== null) throw invalid();
  let image: PftDeskCacheThumbnail['image'] = null;
  if (base.state === 'record') {
    const raw = object(data.image);
    if (base.source.total_sources !== 1 || !hash(raw.sha256) || !integer(raw.width, 1, 4096) || !integer(raw.height, 1, 4096)
      || !integer(raw.bytes, 33, 4 * 1024 * 1024) || raw.integrity !== 'not_checked') throw invalid();
    image = { sha256: raw.sha256, width: raw.width, height: raw.height, bytes: raw.bytes, integrity: 'not_checked' };
  } else if (data.image !== null) throw invalid();
  return { ...base, algorithm_version: null, image };
}
function geometry(value: unknown): PftDeskCacheGeometry {
  const data = object(value), base = common(data);
  if (data.algorithm_version !== null && data.algorithm_version !== 1 || data.units !== 'mm'
    || data.value_scope !== 'cached_box_not_live_measurement') throw invalid();
  let box: PftDeskCacheGeometry['box_mm'] = null;
  if (base.state === 'record') {
    const raw = object(data.box_mm), dimensions = [raw.x_mm, raw.y_mm, raw.z_mm];
    if (data.algorithm_version !== 1 || !dimensions.every(n => typeof n === 'number' && Number.isFinite(n) && n >= 0)
      || !dimensions.some(n => Number(n) > 0)) throw invalid();
    box = { x_mm: raw.x_mm as number, y_mm: raw.y_mm as number, z_mm: raw.z_mm as number };
  } else if (data.box_mm !== null) throw invalid();
  return { ...base, algorithm_version: data.algorithm_version, box_mm: box, units: 'mm', value_scope: 'cached_box_not_live_measurement' };
}

/** Project only public fields and bind every returned item to the explicit request; never invent missing rows. */
export function pftDecodeCacheMetadata(value: unknown, requestedRels: readonly string[]): PftDeskCacheMetadata {
  const requested = rels(requestedRels), data = object(value);
  if (data.schema !== SCHEMA || data.schema_version !== 1 || data.ok !== true || data.native_required !== false
    || !timestamp(data.observed_at) || data.scope !== 'requested_file_metadata' || data.complete !== true || data.truncated !== false
    || data.scan_id !== null || data.source_consistency !== 'non_atomic_filesystem_stat') throw invalid();
  const database = object(data.database), effects = object(data.effects);
  if (database.status !== 'present' && database.status !== 'missing'
    || database.consistency !== (database.status === 'present' ? 'single_sqlite_read_transaction' : 'not_opened')
    || (database.status === 'present' ? !hash(database.sha256) : database.sha256 !== null)
    || effects.business_writes !== false || effects.temporary_coordination !== 'own_sqlite_read_lease'
    || !Array.isArray(data.items) || data.items.length !== requested.length) throw invalid();
  const items = new Map<string, PftDeskCacheItem>(), folded = new Set<string>();
  for (const value of data.items) {
    const item = object(value);
    if (!relative(item.rel) || !requested.includes(item.rel) || items.has(item.rel) || folded.has(item.rel.toLowerCase())) throw invalid();
    const t = thumbnail(item.thumbnail), g = geometry(item.geometry);
    if (database.status === 'missing' && (g.algorithm_version !== null || [t, g].some(cache => cache.state !== 'missing'
      || cache.revision !== null || cache.updated_at !== null || cache.source.freshness !== 'unverified'
      || cache.source.total_sources !== 0 || cache.source.checked_sources !== 0
      || cache.source.recorded_source_sha256 !== null))) throw invalid();
    items.set(item.rel, { rel: item.rel, thumbnail: t, geometry: g }); folded.add(item.rel.toLowerCase());
  }
  return { schema: SCHEMA, schema_version: 1, ok: true, native_required: false, observed_at: data.observed_at,
    scope: 'requested_file_metadata', complete: true, truncated: false, scan_id: null,
    database: { status: database.status, sha256: database.sha256 as string | null, consistency: database.consistency as PftDeskCacheMetadata['database']['consistency'] },
    source_consistency: 'non_atomic_filesystem_stat', effects: { business_writes: false, temporary_coordination: 'own_sqlite_read_lease' },
    items: requested.map(rel => items.get(rel)!), data_source: 'stored_desk_cache', native_memory: false, service_cpu_bound: false };
}
const FAILURES: Record<string, { statuses: readonly number[]; code: PftDeskCacheErrorCode }> = {
  invalid_arguments: { statuses: [400], code: 'DESK_CACHE_INVALID_ARGUMENT' },
  unsafe_path: { statuses: [400, 409], code: 'DESK_CACHE_UNSAFE_PATH' },
  cache_busy: { statuses: [409], code: 'DESK_CACHE_BUSY' },
  unsupported_storage: { statuses: [409], code: 'DESK_CACHE_UNSUPPORTED_STORAGE' },
  cache_changed: { statuses: [409], code: 'DESK_CACHE_CHANGED' },
  resource_limit: { statuses: [413], code: 'DESK_CACHE_RESOURCE_LIMIT' },
  timeout: { statuses: [408], code: 'DESK_CACHE_TIMEOUT' }, cancelled: { statuses: [408], code: 'DESK_CACHE_CANCELLED' },
  read_failed: { statuses: [503], code: 'DESK_CACHE_READ_FAILED' }, close_failed: { statuses: [503], code: 'DESK_CACHE_CLOSE_FAILED' },
  cache_missing: { statuses: [404], code: 'DESK_CACHE_MISSING' }, cache_stale: { statuses: [409], code: 'DESK_CACHE_STALE' },
  cache_unsupported: { statuses: [409], code: 'DESK_CACHE_UNSUPPORTED' }, cache_invalid: { statuses: [409], code: 'DESK_CACHE_INVALID' },
};
/** Map only a versioned, status-consistent failure. Backend detail is never forwarded. */
export function pftCacheFailure(value: unknown, httpStatus: number): PftDeskCacheError {
  if (!record(value) || value.schema !== SCHEMA || value.schema_version !== 1 || value.ok !== false || value.native_required !== false
    || value.retryable !== false || !text(value.detail, 2048) || typeof value.code !== 'string' || !Object.hasOwn(FAILURES, value.code)) return invalid();
  const selected = FAILURES[value.code]!;
  return integer(httpStatus, 400, 599) && selected.statuses.includes(httpStatus) ? new PftDeskCacheError(selected.code) : invalid();
}
