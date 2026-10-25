// SPDX-License-Identifier: Apache-2.0
import * as z from 'zod/v4';

/** Protocol bounds, not limits asserted by FreeCAD's native recompute API. */
export const PFT_MAX_RECOMPUTE_RESULT_BYTES = 4 * 1024;
export const PFT_RECOMPUTE_CAPABILITY = Object.freeze({
  version: 1, native_result_kind: 'object_count', object_status_check: 'strict', max_objects: 10000,
  native_completion: 'not_verified', geometry_validity: 'not_verified', geometry_freshness: 'not_verified',
} as const);
export const PftRecomputeCapabilitySchema = z.object({
  version: z.literal(1), native_result_kind: z.literal('object_count'), object_status_check: z.literal('strict'),
  max_objects: z.literal(10000), native_completion: z.literal('not_verified'),
  geometry_validity: z.literal('not_verified'), geometry_freshness: z.literal('not_verified'),
}).strict();
const documentName = z.string().min(1).max(512)
  .refine(value => Array.from(value).length <= 256, '文档名称超过 Unicode 字符数量上限')
  .regex(/^[^\p{Cc}\p{Cf}\p{Cs}]+$/u);
export const PftRecomputeDataSchema = z.object({
  document: documentName, saved: z.literal(false), recompute_invoked: z.literal(true),
  native_object_count: z.number().int().min(0).max(Number.MAX_SAFE_INTEGER),
  checked_object_count: z.number().int().min(0).max(10000),
  object_status: z.literal('no_invalid_or_pending'), native_completion: z.literal('not_verified'),
  geometry_validity: z.literal('not_verified'), geometry_freshness: z.literal('not_verified'),
}).strict();
export type PftRecomputeData = z.output<typeof PftRecomputeDataSchema>;
/** The SDK deliberately bypasses success output validation for structured MCP errors. */
export const PftRecomputeSuccessSchema = z.object({ ok: z.literal(true), source_kind: z.literal('live_freecad_gui'),
  observed_at: z.string(), data: PftRecomputeDataSchema }).strict();

const uncertainResult = 'FreeCAD 重算请求可能已执行，结果未能验证；请检查当前文档，不要自动重试。';
const transportErrors = new Set(['HTTP_ERROR', 'INVALID_RESPONSE', 'RESPONSE_TOO_LARGE', 'CONNECTION_FAILED', 'BRIDGE_TIMEOUT']);
type PftRecomputeTransport = { status: () => Promise<Record<string, unknown>>;
  recompute: (params: Record<string, unknown>) => Promise<unknown> };
type PftRecomputeErrorConstructor = new (code: string, message: string) => Error;

/** One explicit invocation; counts and object state do not prove native completion or Shape validity. */
export async function pftRecomputeDocument(document: string, transport: PftRecomputeTransport,
  ErrorClass: PftRecomputeErrorConstructor): Promise<PftRecomputeData> {
  if (!documentName.safeParse(document).success) {
    throw new ErrorClass('INVALID_ARGUMENT', '重算需要有效的明确文档内部名称。');
  }
  const status = await transport.status();
  if (!Array.isArray(status.supported_methods) || !status.supported_methods.includes('recompute_document')
    || status.recompute_result === undefined) {
    throw new ErrorClass('UNSUPPORTED_CAPABILITY', '当前 FreeCAD 插件未声明兼容的重算结果能力；不会派发重算，请更新并重新加载插件。');
  }
  if (!PftRecomputeCapabilitySchema.safeParse(status.recompute_result).success) {
    throw new ErrorClass('INVALID_RESPONSE', 'FreeCAD 插件重算结果能力声明格式无效；未派发重算。');
  }
  let value: unknown;
  try { value = await transport.recompute({ docName: document }); }
  catch (error) {
    if (error instanceof ErrorClass) {
      const code = Reflect.get(error, 'code');
      if (typeof code === 'string' && transportErrors.has(code)) throw new ErrorClass(code, uncertainResult);
      throw error;
    }
    throw new ErrorClass('INVALID_RESPONSE', uncertainResult);
  }
  let encoded: string | undefined;
  try { encoded = JSON.stringify(value); } catch { /* Non-JSON results remain unverified. */ }
  const parsed = encoded !== undefined && Buffer.byteLength(encoded, 'utf8') <= PFT_MAX_RECOMPUTE_RESULT_BYTES
    ? PftRecomputeDataSchema.safeParse(value) : null;
  if (!parsed?.success || parsed.data.document !== document) {
    throw new ErrorClass('INVALID_RESPONSE', uncertainResult);
  }
  return parsed.data;
}
