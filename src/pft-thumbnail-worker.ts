// SPDX-License-Identifier: Apache-2.0
/** Isolated worker wire contract. No file, process or native CAD access. */
import * as z from 'zod/v4';
import { isAbsolute } from 'node:path';
import { createHash } from 'node:crypto';
import { sanitizePftThumbnailWorkerPng } from './pft-png.js';

export const PFT_THUMBNAIL_WORKER_METHODS = ['get_thumbnail_worker_status', 'render_document_thumbnail', 'get_thumbnail_job', 'cancel_thumbnail_job'] as const;
export const PFT_THUMBNAIL_WORKER_MAX_PNG = 4 * 1024 * 1024;
export const PFT_THUMBNAIL_WORKER_RESPONSE_BYTES = 6 * 1024 * 1024;
export const PFT_THUMBNAIL_RENDER_PROFILE = 'native-isometric-white-v1';
const uuid = z.string().regex(/^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/u);
const hash = z.string().regex(/^[a-f0-9]{64}$/u);
const dimension = z.number().int().min(1).max(1024);
const safeText = (maximum: number) => z.string().min(1).max(maximum).regex(/^[^\p{Cc}\p{Cf}\p{Cs}]+$/u);
export const PftThumbnailJobInputSchema = z.object({ workerId: uuid, sessionId: uuid, jobId: uuid }).strict();
export const PftThumbnailRenderInputSchema = PftThumbnailJobInputSchema.extend({
  file: safeText(4096).refine(value => isAbsolute(value) && value === value.trim(), '需要明确的绝对文件路径'),
  expectedSha256: hash, width: dimension.default(512), height: dimension.default(512),
}).strict().refine(value => value.width * value.height <= 1048576);
export type PftThumbnailJobInput = z.input<typeof PftThumbnailJobInputSchema>;
export type PftThumbnailRenderInput = z.input<typeof PftThumbnailRenderInputSchema>;

const workerStatus = z.object({ schema_version: z.literal(1), worker_id: uuid, session_id: uuid,
  state: z.enum(['ready', 'busy', 'quarantined', 'stopped']), active_job_id: uuid.nullable(), saves_cad: z.literal(false),
  render_profile: z.literal(PFT_THUMBNAIL_RENDER_PROFILE), supported_file_scope: z.literal('trusted-native-self-contained'),
  jobs_retained: z.literal(20), max_jobs: z.literal(256), max_input_bytes: z.literal(67108864), max_png_bytes: z.literal(PFT_THUMBNAIL_WORKER_MAX_PNG),
  dimensions: z.object({ max_width: z.literal(1024), max_height: z.literal(1024), max_pixels: z.literal(1048576) }).strict(),
}).strict().refine(value => value.state === 'busy' ? value.active_job_id !== null
  : !['ready', 'stopped'].includes(value.state) || value.active_job_id === null);
export type PftThumbnailWorkerStatus = z.output<typeof workerStatus>;
const state = z.enum(['queued', 'freezing', 'opening', 'rendering', 'closing', 'done', 'failed', 'cancelled']);
const revision = z.object({ sha256: hash, bytes: z.number().int().min(0).max(67108864) }).strict();
const image = z.object({ png_base64: z.string().min(4).max(Math.ceil(PFT_THUMBNAIL_WORKER_MAX_PNG / 3) * 4),
  sha256: hash, width: dimension, height: dimension, bytes: z.number().int().min(1).max(PFT_THUMBNAIL_WORKER_MAX_PNG),
  camera_restored: z.literal(true), documents_closed: z.literal(true), saved_cad: z.literal(false), worker_reusable: z.literal(true),
  render_profile: z.literal(PFT_THUMBNAIL_RENDER_PROFILE),
}).strict();
const failure = z.object({ code: z.string().regex(/^[A-Z][A-Z0-9_]{0,63}$/u), message: safeText(512) }).strict();
const snapshot = z.object({ schema_version: z.literal(1), worker_id: uuid, session_id: uuid, job_id: uuid,
  state, phase: state, cancel_requested: z.boolean(), input_revision: revision.nullable(), result: image.nullable(), error: failure.nullable(),
}).strict().refine(value => value.phase === value.state).refine(value => {
  if (value.state === 'done') return value.result !== null && value.input_revision !== null && value.error === null;
  if (value.result !== null) return false;
  if (value.state === 'failed') return value.error !== null;
  return value.state === 'cancelled' || value.error === null;
});
export type PftThumbnailJob = z.output<typeof snapshot>;
export class PftThumbnailWorkerValidationError extends Error {
  constructor(readonly code: 'INVALID_ARGUMENT' | 'INVALID_RESPONSE') {
    super(code === 'INVALID_ARGUMENT' ? '缩略图 worker 请求需要明确的本次身份、任务、文件摘要和有界尺寸。'
      : '缩略图 worker 返回不兼容、身份不符或未通过图像完整性校验的结果。');
    this.name = 'PftThumbnailWorkerValidationError';
  }
}
const invalid = (): never => { throw new PftThumbnailWorkerValidationError('INVALID_RESPONSE'); };
export function pftThumbnailRenderInput(value: unknown): z.output<typeof PftThumbnailRenderInputSchema> {
  try { return PftThumbnailRenderInputSchema.parse(value); }
  catch { throw new PftThumbnailWorkerValidationError('INVALID_ARGUMENT'); }
}
export function pftThumbnailJobInput(value: unknown): PftThumbnailJobInput {
  try { return PftThumbnailJobInputSchema.parse(value); }
  catch { throw new PftThumbnailWorkerValidationError('INVALID_ARGUMENT'); }
}
export function pftDecodeThumbnailWorkerStatus(value: unknown): PftThumbnailWorkerStatus {
  try { return workerStatus.parse(value); }
  catch { return invalid(); }
}
/** Identity matching is a routing guard, not authentication or proof of ownership. */
export function pftDecodeThumbnailJob(value: unknown, expected: PftThumbnailJobInput & { expectedSha256?: string; width?: number; height?: number }): PftThumbnailJob {
  try {
    const job = snapshot.parse(value);
    if (job.worker_id !== expected.workerId || job.session_id !== expected.sessionId || job.job_id !== expected.jobId) return invalid();
    if (expected.expectedSha256 !== undefined && job.input_revision !== null && job.input_revision.sha256 !== expected.expectedSha256) return invalid();
    if (job.result !== null) {
      const result = job.result, base64 = result.png_base64;
      if (base64.length % 4 || !/^[A-Za-z0-9+/]*={0,2}$/u.test(base64)) return invalid();
      const bytes = Buffer.from(base64, 'base64');
      if (bytes.length !== result.bytes || bytes.toString('base64') !== base64
        || createHash('sha256').update(bytes).digest('hex') !== result.sha256
        || expected.width !== undefined && result.width !== expected.width || expected.height !== undefined && result.height !== expected.height) return invalid();
      const checked = sanitizePftThumbnailWorkerPng(bytes, result.width, result.height);
      if (!checked.bytes.equals(bytes) || checked.sha256 !== result.sha256) return invalid();
    }
    return job;
  } catch { return invalid(); }
}
