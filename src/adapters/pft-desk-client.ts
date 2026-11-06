// SPDX-License-Identifier: Apache-2.0
/** Explicit, business-read-only Desk adapter. The trusted official CLI owns
 * manifest/Node verification; this adapter never starts or discovers services.
 * Desk readers may acquire a temporary SQLite sidecar lease. Identity checks
 * bind one observed local instance; they are not client authentication.
 */
import { spawn } from 'node:child_process';
import { constants, accessSync, lstatSync, type Stats } from 'node:fs';
import { homedir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { performance } from 'node:perf_hooks';

export type PftDeskConfig = { nodeExecutable: string; cliEntry: string; stateDir: string };
export { PftDeskError } from '../pft-desk-error.js';
import { PftDeskError } from '../pft-desk-error.js';
import { pftDeskHttpRead } from './pft-desk-http.js';
import { PftDeskCacheError, pftCacheFailure } from '../pft-desk-cache.js';
import { PftPngError } from '../pft-png.js';
import { pftDecodeRuntimeFeatures, PftDeskRuntimeError, type PftDeskRuntimeObservation } from '../pft-desk-runtime.js';
import { pftReadCachedMetadata, pftReadCachedThumbnail, type PftDeskCacheSession } from './pft-desk-cache-reader.js';
export type PftDeskRuntimeFeatureStatus =
  | { state: 'observed'; observation: PftDeskRuntimeObservation }
  | { state: 'not_declared'; reason_code: 'endpoint_absent' };
export type PftDeskStatusOptions = { includeRuntimeFeatures?: boolean | undefined };
export type PftDeskStatus = {
  state: 'ready'; code: 'verified'; app_version: '0.5.0'; release_status: 'not-published';
  reused: true; created: false; protocol_version: 1; browser_host_protocol: 1;
  capabilities: ['browser-web', 'local-api']; verification: 'cli_and_live_identity';
  runtime_features?: PftDeskRuntimeFeatureStatus;
};
export type PftDeskIndexRow = {
  repo_rel_path: string; filename: string; asset_kind: string | null; part_number: string | null;
  part_version: string | null; part_name: string | null; label: string | null;
};
export type PftDeskIndexDiagnostics = {
  state: 'missing' | 'incompatible' | 'unreadable' | 'empty' | 'attention' | 'ready';
  schemaVersion: number | null; expectedSchemaVersion: number; totalEntries: number; missingEntries: number;
  failedEntries: number; lastScanAt: string; latestScan: { id: number; status: string; at: string } | null;
};
export type PftDeskIndexSearchResult = {
  source: 'sqlite'; query: string; rows: PftDeskIndexRow[]; limit: 200; limitReached: boolean;
  diagnostics: PftDeskIndexDiagnostics; data_source: 'stored_desk_index'; native_memory: false;
  query_semantics: 'sqlite_like_contains_percent_underscore_wildcards'; service_cpu_bound: false;
};
export type PftDeskFreshness = {
  index_state: 'ready' | 'missing' | 'incomplete' | 'stale'; index_pending: boolean; index_stale: boolean;
  index_reason: string; bom_index_state: 'ready' | 'missing' | 'incomplete' | 'stale';
  bom_index_pending: boolean; bom_index_stale: boolean; scan_id: number | null; freshness_scope: 'file' | 'workspace';
};
export type PftDeskReference = {
  host_repo_rel_path: string; target_repo_rel_path: string | null; target_basename: string;
  resolve_status: string; ref_kind: string; link_label: string; rule_label: string; label_mismatch: boolean;
  target_part_number: string; target_part_version: string; target_type_code: string;
  target_model_series: string; target_part_name: string; target_asset_label: string;
};
export type PftDeskReferenceCounts = {
  outgoing_count: number; direct_incoming_count: number; outgoing: number; incoming: number; flat_lines: number;
};
export type PftDeskReferenceCountsItem = { rel: string; counts: PftDeskReferenceCounts; freshness: PftDeskFreshness };
export type PftDeskReferenceOutgoingItem = {
  rel: string; count: number; label_mismatch_count: number; refs: PftDeskReference[]; freshness: PftDeskFreshness;
  limit: number; completeness: 'unknown';
};
type PftDeskReferenceCommon = {
  data_source: 'stored_desk_index'; native_memory: false; batch_snapshot: 'not_atomic';
  quantity_semantics: 'reference_rows_unique_targets_and_flat_rows_not_manufacturing_quantity';
};
export type PftDeskReferencesResult = PftDeskReferenceCommon & (
  { view: 'counts'; results: PftDeskReferenceCountsItem[] } |
  { view: 'outgoing'; results: PftDeskReferenceOutgoingItem[] }
);
export type PftDeskSearchInput = { workspace: string; query: string };
export type PftDeskReferencesInput = { workspace: string; rels: string[]; view: 'counts' | 'outgoing'; limit: number };

type Snapshot = { origin: string; webUrl: string; instance: string; runtime: string; pid: number };
type Budget = { remaining: number };
const STDOUT_LIMIT = 64 * 1024, STDERR_LIMIT = 8 * 1024, HTTP_LIMIT = 256 * 1024;
const OS_KEYS = ['PATH', 'SystemRoot', 'SYSTEMROOT', 'WINDIR', 'HOME', 'USERPROFILE', 'LOCALAPPDATA', 'APPDATA', 'LANG', 'LC_ALL', 'LC_CTYPE', 'TZ'] as const;
const BAD_TEXT = /[\p{Cc}\p{Cf}\p{Cs}]/u;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/iu;
const RUNTIME = /^sha256:[a-f0-9]{64}$/u;
const OWN_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..');
const record = (value: unknown): value is Record<string, unknown> => value !== null && typeof value === 'object' && !Array.isArray(value);
const text = (value: unknown, maximum = 1024, empty = true): value is string => typeof value === 'string'
  && value.length <= maximum && (empty || value.length > 0) && !BAD_TEXT.test(value);
const integer = (value: unknown, minimum = 0): value is number => typeof value === 'number' && Number.isSafeInteger(value) && value >= minimum;
function timestamp(value: unknown, empty = true): value is string {
  if (!text(value, 64, empty)) return false;
  if (!value) return empty;
  const match = /^(\d{4})-(\d{2})-(\d{2})[T ](?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d(?:\.\d{1,9})?(?:Z|[+-](?:[01]\d|2[0-3]):[0-5]\d)?$/u.exec(value);
  if (!match || !Number.isFinite(Date.parse(value))) return false;
  const year = Number(match[1]), month = Number(match[2]), day = Number(match[3]);
  const leap = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
  const days = [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
  return month >= 1 && month <= 12 && day >= 1 && day <= days[month - 1]!;
}
const invalid = () => new PftDeskError('INVALID_RESPONSE', 'Desk 返回了不兼容或无法完整验证的数据。');
const timeout = () => new PftDeskError('DESK_TIMEOUT', 'Desk 读取超过截止时间；不会重试或停止后台服务。');
function remaining(deadline: number): number {
  const value = Math.floor(deadline - performance.now());
  if (value <= 0) throw timeout();
  return value;
}
function exactKeys(value: unknown, keys: readonly string[]): value is Record<string, unknown> {
  return record(value) && Object.keys(value).length === keys.length && keys.every(key => Object.hasOwn(value, key));
}
function absolute(value: unknown): string {
  if (!text(value, 4096, false) || value !== value.trim() || !path.isAbsolute(value)
    || value.slice(path.parse(value).root.length).split(path.sep).some(part => part === '.' || part === '..')) {
    throw new PftDeskError('CONFIGURATION_INVALID', 'Desk 路径必须是明确的绝对路径，且不能包含控制字符或点目录。');
  }
  return path.normalize(value);
}
function inspect(filename: string, allowMissing = false): Stats | undefined {
  const root = path.parse(filename).root;
  let current = root, missing = false;
  const components = filename.slice(root.length).split(path.sep).filter(Boolean);
  for (let index = 0; index < components.length; index++) {
    current = path.join(current, components[index]!);
    let info: Stats;
    try { info = lstatSync(current); }
    catch (error) {
      if (record(error) && error.code === 'ENOENT' && allowMissing) { missing = true; continue; }
      throw new PftDeskError('PATH_UNAVAILABLE', '无法读取指定的 Desk 路径。');
    }
    if (info.isSymbolicLink() || (index < components.length - 1 && !info.isDirectory())) {
      throw new PftDeskError('UNSAFE_PATH', 'Desk 路径不能经过符号链接，祖先必须是真实目录。');
    }
  }
  if (missing) return undefined;
  try { return lstatSync(filename); } catch { throw new PftDeskError('PATH_UNAVAILABLE', 'Desk 路径在校验期间变化。'); }
}
function overlaps(first: string, second: string): boolean {
  return first === second || first.startsWith(second + path.sep) || second.startsWith(first + path.sep);
}
function configuration(value: PftDeskConfig | undefined): PftDeskConfig {
  if (value === undefined) throw new PftDeskError('DESK_NOT_CONFIGURED', '尚未明确配置 Desk 的 Node、正式 CLI 和私有状态目录。');
  const nodeExecutable = absolute(value.nodeExecutable), cliEntry = absolute(value.cliEntry), stateDir = absolute(value.stateDir);
  const node = inspect(nodeExecutable), cli = inspect(cliEntry);
  if (!node?.isFile() || !cli?.isFile() || path.basename(cliEntry) !== 'desk-web-service.mjs' || path.basename(path.dirname(cliEntry)) !== 'scripts') {
    throw new PftDeskError('CONFIGURATION_INVALID', 'Node 必须是普通可执行文件，CLI 必须是正式 scripts/desk-web-service.mjs 文件。');
  }
  try { accessSync(nodeExecutable, constants.X_OK); } catch { throw new PftDeskError('CONFIGURATION_INVALID', '指定的 Node 文件不可执行。'); }
  if (stateDir === path.parse(stateDir).root || stateDir === path.resolve(homedir())
    || overlaps(stateDir, path.dirname(path.dirname(cliEntry))) || overlaps(stateDir, OWN_ROOT)) {
    throw new PftDeskError('INVALID_STATE_DIRECTORY', 'Desk 状态目录必须独立于主目录、源码、插件和它们的父目录。');
  }
  const state = inspect(stateDir, true);
  if (state && (!state.isDirectory() || process.platform !== 'win32' && (state.mode & 0o077) !== 0
    || typeof process.getuid === 'function' && state.uid !== process.getuid())) {
    throw new PftDeskError('INVALID_STATE_DIRECTORY', '已有 Desk 状态目录必须真实、归当前用户所有且仅当前用户可访问。');
  }
  return { nodeExecutable, cliEntry, stateDir };
}
function workspacePath(value: unknown, stateDir: string): string {
  const workspace = absolute(value);
  if (!inspect(workspace)?.isDirectory() || overlaps(workspace, stateDir)) {
    throw new PftDeskError('INVALID_ARGUMENT', '必须明确指定已有且独立于 Desk 状态目录的工作空间。');
  }
  return workspace;
}
function relative(value: unknown, fcstd = false, legacy = false): string {
  if (!text(value, 1024, false)) throw invalid();
  const normalized = legacy && value.startsWith('./') ? value.slice(2) : value;
  if (!normalized || normalized !== normalized.trim() || /[\\:]/u.test(normalized) || path.posix.isAbsolute(normalized)
    || normalized.split('/').some(part => !part || part === '.' || part === '..') || fcstd && !/\.fcstd$/iu.test(normalized)) throw invalid();
  return normalized;
}
function nullableText(value: unknown): string | null {
  if (value === null) return null;
  if (!text(value)) throw invalid();
  return value;
}
function boolean(value: unknown): boolean { if (typeof value !== 'boolean') throw invalid(); return value; }
function count(value: unknown): number { if (!integer(value)) throw invalid(); return value; }

/** Duplicate-key, depth and finite-number checks happen before any projection. */
function parse(bytes: Buffer): unknown {
  try {
    const source = new TextDecoder('utf-8', { fatal: true }).decode(bytes);
    let offset = 0, nodes = 0;
    function whitespace(): void { while (/[ \t\r\n]/u.test(source[offset] ?? '\0')) offset++; }
    function stringValue(): string {
      const start = offset++;
      for (; offset < source.length; offset++) {
        if (source[offset] === '\\') { offset++; continue; }
        if (source[offset] === '"') { offset++; return JSON.parse(source.slice(start, offset)) as string; }
      }
      throw invalid();
    }
    function value(depth: number): unknown {
      if (++nodes > 20000 || depth > 32) throw invalid();
      whitespace();
      const char = source[offset];
      if (char === '"') return stringValue();
      if (char === '{') {
        offset++; const result: Record<string, unknown> = Object.create(null) as Record<string, unknown>;
        whitespace(); if (source[offset] === '}') { offset++; return result; }
        while (true) {
          whitespace(); if (source[offset] !== '"') throw invalid();
          const key = stringValue(); whitespace();
          if (Object.hasOwn(result, key) || source[offset++] !== ':') throw invalid();
          result[key] = value(depth + 1); whitespace();
          const delimiter = source[offset++];
          if (delimiter === '}') return result;
          if (delimiter !== ',') throw invalid();
        }
      }
      if (char === '[') {
        offset++; const result: unknown[] = [];
        whitespace(); if (source[offset] === ']') { offset++; return result; }
        while (true) {
          result.push(value(depth + 1)); whitespace(); const delimiter = source[offset++];
          if (delimiter === ']') return result;
          if (delimiter !== ',') throw invalid();
        }
      }
      for (const [token, parsed] of [['true', true], ['false', false], ['null', null]] as const) {
        if (source.startsWith(token, offset)) { offset += token.length; return parsed; }
      }
      const match = /^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?/u.exec(source.slice(offset));
      if (!match) throw invalid();
      offset += match[0].length; const number = Number(match[0]);
      if (!Number.isFinite(number)) throw invalid();
      return number;
    }
    const result = value(0); whitespace(); if (offset !== source.length) throw invalid(); return result;
  } catch { throw invalid(); }
}

function cliSnapshot(value: unknown, exitCode: number | null): Snapshot {
  if (!record(value) || typeof value.state !== 'string' || !['ready', 'absent', 'unavailable', 'incompatible'].includes(value.state)
    || !text(value.code, 128, false) || !/^[A-Za-z][A-Za-z0-9_]{0,127}$/u.test(value.code)
    || !text(value.reason, 2048, false) || value.app_version !== '0.5.0' || value.release_status !== 'not-published'
    || typeof value.created !== 'boolean' || typeof value.reused !== 'boolean'
    || value.runtime_id !== null && (typeof value.runtime_id !== 'string' || !RUNTIME.test(value.runtime_id))
    || Object.hasOwn(value, 'lock_present') && typeof value.lock_present !== 'boolean') throw invalid();
  if (value.state !== 'ready') {
    if (exitCode !== (value.code === 'invalid_arguments' ? 2 : 1) || value.code === 'verified' || value.created || value.reused
      || ['api_origin', 'web_url', 'instance_id', 'pid'].some(key => value[key] !== null)) throw invalid();
    const errors = {
      absent: ['DESK_ABSENT', '指定命名空间没有已就绪的 Desk 后台；不会自动启动。'],
      unavailable: ['DESK_UNAVAILABLE', 'Desk 登记或后台不可确认；不会重试、接管或删除登记。'],
      incompatible: ['DESK_INCOMPATIBLE', 'Desk 构建、登记或后台不兼容；不会启动或接管服务。'],
    } as const;
    const selected = value.state === 'absent' ? errors.absent : value.state === 'unavailable' ? errors.unavailable : errors.incompatible;
    throw new PftDeskError(selected[0], selected[1]);
  }
  if (exitCode !== 0 || value.code !== 'verified' || value.created !== false || value.reused !== true
    || !text(value.api_origin, 64, false) || !/^http:\/\/127\.0\.0\.1:([1-9][0-9]{0,4})$/u.test(value.api_origin)
    || !integer(value.pid, 1) || typeof value.instance_id !== 'string' || !UUID.test(value.instance_id)
    || typeof value.runtime_id !== 'string' || !RUNTIME.test(value.runtime_id)
    || value.web_url !== value.api_origin && value.web_url !== value.api_origin + '/') throw invalid();
  const port = Number(value.api_origin.slice(value.api_origin.lastIndexOf(':') + 1));
  if (!integer(port, 1) || port > 65535) throw invalid();
  return { origin: value.api_origin, webUrl: value.web_url, instance: value.instance_id, runtime: value.runtime_id, pid: value.pid };
}
function identity(value: unknown, expected: Snapshot): void {
  if (!record(value) || value.schema_version !== 1 || value.service !== 'phoenix-desk-tools' || value.protocol_version !== 1
    || value.browser_host_protocol !== 1 || value.app_version !== '0.5.0' || value.instance_id !== expected.instance
    || value.runtime_id !== expected.runtime || value.pid !== expected.pid || value.api_origin !== expected.origin
    || value.web_url !== expected.webUrl || !timestamp(value.started_at, false) || !/[TZ]|[+-]\d{2}:\d{2}$/u.test(value.started_at) || !Array.isArray(value.capabilities) || value.capabilities.length !== 2
    || !value.capabilities.includes('browser-web') || !value.capabilities.includes('local-api')) {
    throw new PftDeskError('DESK_IDENTITY_MISMATCH', 'Desk 实例身份、版本或能力在读取期间不匹配，结果未被接受。');
  }
}
function diagnostics(value: unknown): PftDeskIndexDiagnostics {
  if (!record(value) || typeof value.state !== 'string' || !['missing', 'incompatible', 'unreadable', 'empty', 'attention', 'ready'].includes(value.state)
    || value.schemaVersion !== null && !integer(value.schemaVersion) || !integer(value.expectedSchemaVersion, 1)
    || !integer(value.totalEntries) || !integer(value.missingEntries) || !integer(value.failedEntries)
    || !timestamp(value.lastScanAt)) throw invalid();
  let latestScan: PftDeskIndexDiagnostics['latestScan'] = null;
  if (value.latestScan !== null) {
    const scan = value.latestScan;
    if (!record(scan) || !integer(scan.id, 1) || !text(scan.status, 64, false) || !/^[a-z][a-z0-9_-]*$/u.test(scan.status)
      || !timestamp(scan.at)) throw invalid();
    latestScan = { id: scan.id, status: scan.status, at: scan.at };
  }
  return { state: value.state as PftDeskIndexDiagnostics['state'], schemaVersion: value.schemaVersion,
    expectedSchemaVersion: value.expectedSchemaVersion, totalEntries: value.totalEntries, missingEntries: value.missingEntries,
    failedEntries: value.failedEntries, lastScanAt: value.lastScanAt, latestScan };
}
function searchResult(value: unknown, query: string): PftDeskIndexSearchResult {
  if (!record(value) || value.source !== 'sqlite' || value.query !== query.trim().toLocaleLowerCase('en-US')
    || !Array.isArray(value.rows) || value.rows.length > 200 || value.limit !== 200 || typeof value.limitReached !== 'boolean'
    || value.limitReached !== (value.rows.length === 200)) throw invalid();
  const rows: PftDeskIndexRow[] = value.rows.map(raw => {
    if (!record(raw) || !text(raw.filename, 512, false) || /[\\/:]/u.test(raw.filename)) throw invalid();
    const rel = relative(raw.repo_rel_path, false, true);
    if (path.posix.basename(rel) !== raw.filename) throw invalid();
    return { repo_rel_path: rel, filename: raw.filename, asset_kind: nullableText(raw.asset_kind),
      part_number: nullableText(raw.part_number), part_version: nullableText(raw.part_version),
      part_name: nullableText(raw.part_name), label: nullableText(raw.label) };
  });
  if (new Set(rows.map(row => row.repo_rel_path)).size !== rows.length) {
    throw new PftDeskError('DESK_INDEX_AMBIGUOUS', 'Desk 索引包含规范化后重复的相对路径；未猜测合并或重复计数。');
  }
  const checkedDiagnostics = diagnostics(value.diagnostics);
  if (['missing', 'incompatible', 'unreadable'].includes(checkedDiagnostics.state)) {
    throw new PftDeskError('INDEX_UNAVAILABLE', 'Desk 索引不可用；没有把失败结果当成空查询成功。');
  }
  return { source: 'sqlite', query: value.query, rows, limit: 200, limitReached: value.limitReached,
    diagnostics: checkedDiagnostics, data_source: 'stored_desk_index', native_memory: false,
    query_semantics: 'sqlite_like_contains_percent_underscore_wildcards', service_cpu_bound: false };
}
function freshness(value: Record<string, unknown>): PftDeskFreshness {
  const states = ['ready', 'missing', 'incomplete', 'stale'];
  const reasons = ['', 'index_missing', 'scan_missing', 'scan_incomplete', 'partial_refresh', 'scan_stale', 'asset_missing',
    'asset_path_ambiguous', 'snapshot_metadata_missing', 'unsafe_asset_path', 'file_changed', 'file_unreadable'];
  if (typeof value.index_state !== 'string' || !states.includes(value.index_state) || typeof value.bom_index_state !== 'string' || !states.includes(value.bom_index_state)
    || typeof value.index_reason !== 'string' || !reasons.includes(value.index_reason)
    || value.scan_id !== null && !integer(value.scan_id, 1) || typeof value.freshness_scope !== 'string' || !['file', 'workspace'].includes(value.freshness_scope)) throw invalid();
  const result: PftDeskFreshness = { index_state: value.index_state as PftDeskFreshness['index_state'],
    index_pending: boolean(value.index_pending), index_stale: boolean(value.index_stale), index_reason: value.index_reason,
    bom_index_state: value.bom_index_state as PftDeskFreshness['bom_index_state'], bom_index_pending: boolean(value.bom_index_pending),
    bom_index_stale: boolean(value.bom_index_stale), scan_id: value.scan_id, freshness_scope: value.freshness_scope as PftDeskFreshness['freshness_scope'] };
  const expectedPending = (state: string) => state === 'missing' || state === 'incomplete';
  if (result.index_pending !== expectedPending(result.index_state) || result.index_stale !== (result.index_state === 'stale')
    || result.bom_index_pending !== expectedPending(result.bom_index_state) || result.bom_index_stale !== (result.bom_index_state === 'stale')
    || result.bom_index_state !== 'missing' && result.bom_index_state !== result.index_state
    || result.index_state === 'ready' && result.index_reason !== '' || result.index_state !== 'ready' && !result.index_reason
    || result.index_state !== 'missing' && (result.scan_id === null || result.freshness_scope !== 'file')) throw invalid();
  if (result.index_state === 'missing') throw new PftDeskError('INDEX_UNAVAILABLE', 'Desk 引用索引或目标记录缺失，零计数尚未确认。');
  return result;
}
function countsResult(value: unknown, rel: string): PftDeskReferenceCountsItem {
  if (!record(value)) throw invalid();
  const counts = { outgoing_count: count(value.outgoing_count), direct_incoming_count: count(value.direct_incoming_count),
    outgoing: count(value.outgoing), incoming: count(value.incoming), flat_lines: count(value.flat_lines) };
  if (counts.outgoing > counts.outgoing_count || counts.incoming !== counts.direct_incoming_count) throw invalid();
  return { rel, counts, freshness: freshness(value) };
}
function outgoingResult(value: unknown, rel: string, limit: number): PftDeskReferenceOutgoingItem {
  if (!record(value) || relative(value.host_repo_rel_path, true, true) !== rel || !Array.isArray(value.refs)
    || value.refs.length > limit || value.count !== value.refs.length || !integer(value.label_mismatch_count)
    || value.label_mismatch_count > value.refs.length) throw invalid();
  const refs: PftDeskReference[] = value.refs.map(raw => {
    if (!record(raw) || relative(raw.host_repo_rel_path, true, true) !== rel
      || !text(raw.target_basename, 512) || /[\\/:]/u.test(raw.target_basename)
      || !text(raw.resolve_status, 64, false) || !/^[a-z][a-z0-9_]*$/u.test(raw.resolve_status)
      || !text(raw.ref_kind, 64, false) || !/^[a-z][a-z0-9_]*$/u.test(raw.ref_kind)) throw invalid();
    const result: PftDeskReference = { host_repo_rel_path: rel,
      target_repo_rel_path: raw.target_repo_rel_path === null ? null : relative(raw.target_repo_rel_path, true, true),
      target_basename: raw.target_basename, resolve_status: raw.resolve_status, ref_kind: raw.ref_kind,
      link_label: '', rule_label: '', label_mismatch: boolean(raw.label_mismatch), target_part_number: '', target_part_version: '',
      target_type_code: '', target_model_series: '', target_part_name: '', target_asset_label: '' };
    for (const field of ['link_label', 'rule_label', 'target_part_number', 'target_part_version', 'target_type_code',
      'target_model_series', 'target_part_name', 'target_asset_label'] as const) {
      if (!text(raw[field])) throw invalid(); result[field] = raw[field];
    }
    if (raw.label_mismatch !== Boolean(result.link_label && result.rule_label && result.link_label !== result.rule_label)) throw invalid();
    return result;
  });
  if (value.label_mismatch_count !== refs.filter(row => row.label_mismatch).length) throw invalid();
  return { rel, count: refs.length, label_mismatch_count: value.label_mismatch_count, refs, freshness: freshness(value), limit, completeness: 'unknown' };
}

export class PftDeskClient {
  private readonly config: PftDeskConfig | undefined;
  private readonly timeoutMs: number;
  constructor(config: PftDeskConfig | undefined, timeoutMs = 15000) {
    if (config !== undefined && !exactKeys(config, ['nodeExecutable', 'cliEntry', 'stateDir'])) {
      throw new PftDeskError('CONFIGURATION_INVALID', 'Desk 配置只接受 Node、正式 CLI 和独立状态目录三个字段。');
    }
    if (!integer(timeoutMs, 100) || timeoutMs > 120000) throw new PftDeskError('INVALID_ARGUMENT', 'Desk 截止时间必须是100到120000毫秒的整数。');
    this.config = config === undefined ? undefined : { ...config };
    this.timeoutMs = timeoutMs;
  }
  private async cli(config: PftDeskConfig, deadline: number): Promise<Snapshot> {
    const env: NodeJS.ProcessEnv = {};
    for (const key of OS_KEYS) if (typeof process.env[key] === 'string') env[key] = process.env[key];
    remaining(deadline);
    const bytes = await new Promise<{ code: number | null; stdout: Buffer }>((resolve, reject) => {
      let child: ReturnType<typeof spawn>;
      try { child = spawn(config.nodeExecutable, [config.cliEntry, 'status', '--state-dir', config.stateDir], {
        cwd: path.dirname(path.dirname(config.cliEntry)), env, shell: false, detached: false, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'],
      }); } catch { reject(new PftDeskError('PROCESS_FAILED', 'Desk 状态 CLI 无法启动。')); return; }
      const chunks: Buffer[] = []; let stdout = 0, stderr = 0, failure: PftDeskError | undefined;
      const stop = (error: PftDeskError): void => {
        if (failure) return;
        failure = error;
        try { child.kill('SIGKILL'); } catch { /* Only this owned CLI child was addressed. */ }
      };
      const timer = setTimeout(() => stop(timeout()), Math.max(1, Math.floor(deadline - performance.now())));
      child.stdout?.on('data', (chunk: Buffer) => {
        stdout += chunk.length;
        if (stdout > STDOUT_LIMIT) stop(new PftDeskError('OUTPUT_TOO_LARGE', 'Desk 状态 CLI 输出超过大小限制。'));
        else if (!failure) chunks.push(chunk);
      });
      child.stderr?.on('data', (chunk: Buffer) => {
        stderr += chunk.length;
        if (stderr > STDERR_LIMIT) stop(new PftDeskError('OUTPUT_TOO_LARGE', 'Desk 状态 CLI 输出超过大小限制。'));
      });
      child.stdout?.once('error', () => stop(new PftDeskError('PROCESS_FAILED', 'Desk 状态 CLI 输出连接异常。')));
      child.stderr?.once('error', () => stop(new PftDeskError('PROCESS_FAILED', 'Desk 状态 CLI 输出连接异常。')));
      child.once('error', () => { failure ??= new PftDeskError('PROCESS_FAILED', 'Desk 状态 CLI 无法正常执行。'); });
      child.once('close', code => {
        clearTimeout(timer);
        if (failure) reject(failure);
        else if (code === null || ![0, 1, 2].includes(code)) reject(new PftDeskError('PROCESS_FAILED', 'Desk 状态 CLI 未正常结束。'));
        else resolve({ code, stdout: Buffer.concat(chunks, stdout) });
      });
    });
    remaining(deadline);
    return cliSnapshot(parse(bytes.stdout), bytes.code);
  }
  private async get(snapshot: Snapshot, route: string, deadline: number, budget: Budget, maximum = HTTP_LIMIT): Promise<unknown> {
    const response = await pftDeskHttpRead(snapshot.origin, route, deadline, budget, { maximum });
    if (!/^application\/(?:[\w.-]+\+)?json(?:\s*;|\s*$)/iu.test(response.headers['content-type'] ?? '')) throw invalid();
    return parse(response.bytes);
  }
  private async verify(snapshot: Snapshot, deadline: number, budget: Budget): Promise<void> {
    identity(await this.get(snapshot, '/api/desk-service', deadline, budget, 16 * 1024), snapshot);
  }
  private cacheArguments(input: unknown, image = false): { config: PftDeskConfig; workspace: string; rels: string[]; revision?: string } {
    const keys = image ? ['workspace', 'rel', 'revision'] : ['workspace', 'rels'];
    if (!exactKeys(input, keys)) throw new PftDeskError('INVALID_ARGUMENT', '缓存读取只接受明确工作空间、相对路径和必要的修订值。');
    const values = image ? [input.rel] : input.rels;
    if (!Array.isArray(values) || values.length < 1 || values.length > 10) throw new PftDeskError('INVALID_ARGUMENT', '缓存读取需要1到10个明确目标。');
    let rels: string[];
    try {
      rels = values.map(value => relative(value, true));
      if (rels.some(value => value !== value.normalize('NFC') || value.split('/').some(part => part.startsWith('.')
        || ['node_modules', '__pycache__'].includes(part.toLowerCase())))
        || new Set(rels.map(value => value.toLowerCase())).size !== rels.length) throw invalid();
    } catch { throw new PftDeskError('INVALID_ARGUMENT', '缓存目标必须是唯一、规范的相对FCStd路径。'); }
    if (image && (typeof input.revision !== 'string' || !/^[a-f0-9]{64}$/u.test(input.revision))) {
      throw new PftDeskError('INVALID_ARGUMENT', '缩略图必须指定前次元数据的精确修订值。');
    }
    const config = configuration(this.config), workspace = workspacePath(input.workspace, config.stateDir);
    return { config, workspace, rels, ...(image ? { revision: input.revision as string } : {}) };
  }
  private async cacheRead<T>(config: PftDeskConfig, image: boolean, operation: (session: PftDeskCacheSession) => Promise<T>): Promise<T> {
    const deadline = performance.now() + this.timeoutMs;
    const snapshot = await this.cli(config, deadline), budget = { remaining: HTTP_LIMIT + (image ? 4 * 1024 * 1024 : 0) };
    try {
      await this.verify(snapshot, deadline, budget);
      const session: PftDeskCacheSession = {
        expectedIdentity: { instance: snapshot.instance, runtime: snapshot.runtime },
        json: async (route, body) => {
          const response = await pftDeskHttpRead(snapshot.origin, route, deadline, budget,
            { maximum: 128 * 1024, allowFailure: true, ...(body ? { method: 'POST', body } : {}) });
          if (route === '/api/runtime/features' && response.status === 404) throw new PftDeskCacheError('DESK_CACHE_FEATURE_UNSUPPORTED');
          if (!/^application\/(?:[\w.-]+\+)?json(?:\s*;|\s*$)/iu.test(response.headers['content-type'] ?? '')) throw invalid();
          const value = parse(response.bytes);
          if (response.status !== 200) throw pftCacheFailure(value, response.status);
          return value;
        },
        png: async route => {
          const response = await pftDeskHttpRead(snapshot.origin, route, deadline, budget,
            { maximum: 4 * 1024 * 1024, kind: 'png', allowFailure: true });
          if (response.status !== 200) {
            if (response.bytes.length > 16 * 1024 || !/^application\/json(?:\s*;|\s*$)/iu.test(response.headers['content-type'] ?? '')) throw invalid();
            throw pftCacheFailure(parse(response.bytes), response.status);
          }
          return response;
        },
      };
      const result = await operation(session);
      await this.verify(snapshot, deadline, budget);
      return result;
    } catch (error) {
      if (error instanceof PftDeskCacheError || error instanceof PftPngError) throw new PftDeskError(error.code, error.message);
      throw error;
    }
  }
  async queryCachedMetadata(input: { workspace: string; rels: string[] }) {
    const args = this.cacheArguments(input);
    return this.cacheRead(args.config, false, session => pftReadCachedMetadata(session, args.workspace, args.rels));
  }
  async getCachedThumbnail(input: { workspace: string; rel: string; revision: string }) {
    const args = this.cacheArguments(input, true);
    return this.cacheRead(args.config, true, session => pftReadCachedThumbnail(session, args.workspace, args.rels[0]!, args.revision!));
  }
  async status(options: PftDeskStatusOptions = {}): Promise<PftDeskStatus> {
    if (!record(options) || Object.keys(options).some(key => key !== 'includeRuntimeFeatures')
      || Object.hasOwn(options, 'includeRuntimeFeatures') && typeof options.includeRuntimeFeatures !== 'boolean') {
      throw new PftDeskError('INVALID_ARGUMENT', 'Desk 状态只接受可选的布尔值 includeRuntimeFeatures。');
    }
    const deadline = performance.now() + this.timeoutMs, config = configuration(this.config);
    const snapshot = await this.cli(config, deadline), budget = { remaining: HTTP_LIMIT };
    await this.verify(snapshot, deadline, budget);
    const status: PftDeskStatus = { state: 'ready', code: 'verified', app_version: '0.5.0', release_status: 'not-published',
      reused: true, created: false, protocol_version: 1, browser_host_protocol: 1,
      capabilities: ['browser-web', 'local-api'], verification: 'cli_and_live_identity' };
    if (!Object.hasOwn(options, 'includeRuntimeFeatures') || options.includeRuntimeFeatures !== true) return status;
    const response = await pftDeskHttpRead(snapshot.origin, '/api/runtime/features', deadline, budget,
      { maximum: 64 * 1024, allowFailure: true });
    let observation: PftDeskRuntimeFeatureStatus;
    if (response.status === 404) {
      observation = { state: 'not_declared', reason_code: 'endpoint_absent' };
    } else {
      if (response.status !== 200) {
        throw new PftDeskError('DESK_RUNTIME_HTTP_ERROR', 'Desk 运行态能力读取失败；未返回服务明细，不会自动重试。');
      }
      if (!/^application\/(?:[\w.-]+\+)?json(?:\s*;|\s*$)/iu.test(response.headers['content-type'] ?? '')) throw invalid();
      try {
        observation = { state: 'observed', observation: pftDecodeRuntimeFeatures(parse(response.bytes),
          { instance: snapshot.instance, runtime: snapshot.runtime }) };
      } catch (error) {
        if (error instanceof PftDeskRuntimeError) throw new PftDeskError(error.code, error.message);
        throw error;
      }
    }
    // An observation is returned only while still bound to the same verified instance.
    await this.verify(snapshot, deadline, budget);
    status.runtime_features = observation;
    return status;
  }
  async searchIndex(input: PftDeskSearchInput): Promise<PftDeskIndexSearchResult> {
    if (!exactKeys(input, ['workspace', 'query']) || !text(input.query, 256, false) || !input.query.trim()) {
      throw new PftDeskError('INVALID_ARGUMENT', '索引搜索需要明确工作空间和1到256字的非空查询；不接受额外字段。');
    }
    const deadline = performance.now() + this.timeoutMs, config = configuration(this.config), workspace = workspacePath(input.workspace, config.stateDir);
    const snapshot = await this.cli(config, deadline), budget = { remaining: HTTP_LIMIT };
    await this.verify(snapshot, deadline, budget);
    const params = new URLSearchParams({ workspace, q: input.query });
    const result = await this.get(snapshot, '/api/cad/index/search/?' + params.toString(), deadline, budget);
    await this.verify(snapshot, deadline, budget);
    return searchResult(result, input.query);
  }
  async queryReferences(input: PftDeskReferencesInput): Promise<PftDeskReferencesResult> {
    if (!exactKeys(input, ['workspace', 'rels', 'view', 'limit']) || !Array.isArray(input.rels) || input.rels.length < 1 || input.rels.length > 10
      || new Set(input.rels).size !== input.rels.length || typeof input.view !== 'string' || !['counts', 'outgoing'].includes(input.view) || !integer(input.limit, 1) || input.limit > 100) {
      throw new PftDeskError('INVALID_ARGUMENT', '引用查询需要1到10个不同的相对FCStd路径、counts或outgoing视图及1到100的limit。');
    }
    let rels: string[];
    try { rels = input.rels.map(rel => relative(rel, true)); } catch { throw new PftDeskError('INVALID_ARGUMENT', '引用目标必须是安全的相对FCStd路径，不能含跳出目录或链接地址。'); }
    const deadline = performance.now() + this.timeoutMs, config = configuration(this.config), workspace = workspacePath(input.workspace, config.stateDir);
    const snapshot = await this.cli(config, deadline), budget = { remaining: HTTP_LIMIT };
    const common: PftDeskReferenceCommon = { data_source: 'stored_desk_index', native_memory: false, batch_snapshot: 'not_atomic',
      quantity_semantics: 'reference_rows_unique_targets_and_flat_rows_not_manufacturing_quantity' };
    const counts: PftDeskReferenceCountsItem[] = [], outgoing: PftDeskReferenceOutgoingItem[] = [];
    for (const rel of rels) {
      await this.verify(snapshot, deadline, budget);
      const params = new URLSearchParams({ workspace, rel, limit: String(input.limit) });
      const value = await this.get(snapshot, '/api/cad/bom/xref/' + input.view + '?' + params.toString(), deadline, budget);
      await this.verify(snapshot, deadline, budget);
      if (input.view === 'counts') counts.push(countsResult(value, rel));
      else outgoing.push(outgoingResult(value, rel, input.limit));
    }
    return input.view === 'counts' ? { ...common, view: 'counts', results: counts } : { ...common, view: 'outgoing', results: outgoing };
  }
}
