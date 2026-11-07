// SPDX-License-Identifier: Apache-2.0
import * as z from 'zod/v4';

export const PFT_DRAWING_EXPORT_CAPABILITY = Object.freeze({ version: 1, formats: ['pdf'],
  confirmation: 'native_capture_and_publish', prepare_method: 'prepare_drawing_export',
  request_method: 'request_drawing_export_approval', status_method: 'get_drawing_export_status',
  storage: 'memory_only', saves_document: false, max_pdf_bytes: 8388608,
  preview_backend: 'macos_coregraphics', projection_verification: 'not_checked' } as const);
export const PftDrawingExportCapabilitySchema = z.object({ version: z.literal(1), formats: z.tuple([z.literal('pdf')]),
  confirmation: z.literal('native_capture_and_publish'), prepare_method: z.literal('prepare_drawing_export'),
  request_method: z.literal('request_drawing_export_approval'), status_method: z.literal('get_drawing_export_status'),
  storage: z.literal('memory_only'), saves_document: z.literal(false), max_pdf_bytes: z.literal(8388608),
  preview_backend: z.literal('macos_coregraphics'), projection_verification: z.literal('not_checked'),
}).strict();
const safeName = z.string().min(1).max(512).refine(value => Array.from(value).length <= 256)
  .regex(/^[^\p{Cc}\p{Cf}\p{Cs}]+$(?![\s\S])/u);
const requestId = z.string().regex(/^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$(?![\s\S])/u);
const token = z.string().regex(/^[A-Za-z0-9_-]{16,128}$(?![\s\S])/u);
const instant = z.iso.datetime({ offset: true });
const digest = z.string().regex(/^[0-9a-f]{64}$(?![\s\S])/u);
export const PftDrawingExportPrepareInputSchema = z.object({ document: safeName, page: safeName,
  format: z.literal('pdf'), requestId }).strict();
export const PftDrawingExportTokenInputSchema = z.object({ token }).strict();
const summaryCommon = { document: safeName, page: safeName, expires_at: instant };
const preparingSummary = z.object({ ...summaryCommon, source: z.literal('') }).strict();
const capturedSummary = z.object({ ...summaryCommon, source: safeName,
  view_count: z.number().int().min(1).max(3), dimension_count: z.number().int().min(0).max(8),
  page_open: z.boolean(), paper_mm: z.tuple([z.number().finite().positive().max(10000), z.number().finite().positive().max(10000)]),
  projection_verification: z.literal('not_checked').optional(), manual_review_required: z.literal(true).optional(),
}).strict();
const coreMessages: Record<string,string> = {
  "DRAWING_EXPORT_INVALID_PARAMS": "Drawing export requires an explicit document, page, PDF format and request ID.",
  "DRAWING_EXPORT_NOT_FOUND": "The export token is not retained in this bridge memory.",
  "DRAWING_EXPORT_REQUEST_CONFLICT": "The request ID already belongs to a different export request.",
  "DRAWING_EXPORT_REQUEST_RETIRED": "This request was retired; it will not be prepared or captured again.",
  "DRAWING_EXPORT_CAPACITY": "The bounded export request registry is full.",
  "DRAWING_EXPORT_BUSY": "Complete or cancel the other native export approval first.",
  "DRAWING_EXPORT_STOPPED": "The owning bridge lifecycle stopped or changed.",
  "DRAWING_EXPORT_EXPIRED": "The native export approval expired.",
  "DRAWING_EXPORT_NATIVE_CONFIRMATION_REQUIRED": "Only the owned native approval callback can perform this step.",
  "DRAWING_EXPORT_GUI_REQUIRED": "This operation requires the FreeCAD GUI thread.",
  "DRAWING_EXPORT_DOCUMENT_CHANGED": "The bound document or its filename changed.",
  "DRAWING_EXPORT_PREVIEW_INVALID": "The frozen PDF preview does not match this export plan.",
  "DRAWING_EXPORT_UI_UNAVAILABLE": "The native export approval view could not be opened.",
  "DRAWING_EXPORT_POST_PUBLISH_CHANGED": "The file outcome is retained, but CAD protection could not be verified afterwards.",
  "DRAWING_EXPORT_FAILED": "Drawing export could not be verified. Inspect its separate capture, publication and cleanup outcomes."
};
const fixedErrorCodes = [
  "DRAWING_EXPORT_BUSY",
  "DRAWING_EXPORT_CAPACITY",
  "DRAWING_EXPORT_CAPTURE_FAILED",
  "DRAWING_EXPORT_CAPTURE_UNVERIFIED",
  "DRAWING_EXPORT_DOCUMENT_CHANGED",
  "DRAWING_EXPORT_EXPIRED",
  "DRAWING_EXPORT_FAILED",
  "DRAWING_EXPORT_GUI_REQUIRED",
  "DRAWING_EXPORT_INVALID_PARAMS",
  "DRAWING_EXPORT_NATIVE_CONFIRMATION_REQUIRED",
  "DRAWING_EXPORT_NOT_FOUND",
  "DRAWING_EXPORT_PAGE_OPEN_REQUIRED",
  "DRAWING_EXPORT_POST_PUBLISH_CHANGED",
  "DRAWING_EXPORT_PREVIEW_INVALID",
  "DRAWING_EXPORT_REQUEST_CONFLICT",
  "DRAWING_EXPORT_REQUEST_RETIRED",
  "DRAWING_EXPORT_STAGE_UNVERIFIED",
  "DRAWING_EXPORT_STALE",
  "DRAWING_EXPORT_STOPPED",
  "DRAWING_EXPORT_UI_UNAVAILABLE",
  "DRAWING_EXPORT_UNSUPPORTED",
  "EXPORT_CLEANUP_FAILED",
  "EXPORT_DIRECTORY_FD_REQUIRED",
  "EXPORT_FAILED",
  "EXPORT_PLAN_INVALID",
  "EXPORT_TARGET_CHANGED",
  "EXPORT_TARGET_TOO_LARGE",
  "INVALID_EXPORT_DATA",
  "INVALID_EXPORT_DIRECTORY",
  "INVALID_EXPORT_PATH",
  "INVALID_EXPORT_POLICY",
  "INVALID_EXPORT_TARGET",
  "OVERWRITE_REQUIRED",
  "PDF_BACKEND_UNAVAILABLE",
  "PDF_BUSY",
  "PDF_CANCELLED",
  "PDF_ENCRYPTED",
  "PDF_INPUT_MISMATCH",
  "PDF_INPUT_TOO_LARGE",
  "PDF_INVALID_DOCUMENT",
  "PDF_INVALID_PAPER",
  "PDF_INVALID_REQUEST",
  "PDF_INVALID_RESPONSE",
  "PDF_OUTPUT_TOO_LARGE",
  "PDF_PAGE_COUNT",
  "PDF_PROCESS_FAILED",
  "PDF_PROTOCOL_INVALID",
  "PDF_RENDER_FAILED",
  "PDF_RUNTIME_UNAVAILABLE",
  "PDF_TIMEOUT",
  "PDF_UNSUPPORTED_BACKEND",
  "PDF_USER_UNIT_UNSUPPORTED",
  "PDF_WRITE_FAILED"
] as const;
const fixedError = z.object({ code: z.enum(fixedErrorCodes), message: z.string().max(1024) }).strict()
  .superRefine((value, context) => {
    if (value.message !== (coreMessages[value.code] ?? coreMessages.DRAWING_EXPORT_FAILED))
      context.addIssue({ code: 'custom', message: '导出错误只能使用固定公开文案' });
  });
export const PftDrawingExportDataSchema = z.object({ document: safeName, page: safeName, format: z.literal('pdf'),
  request_id: requestId, token, state: z.enum(['preparing','prepared','awaiting_capture','capturing','rendering','preview_ready',
    'target_ready','publishing','published','unknown','failed','cancelled','expired','cancelling']),
  expires_at: instant, summary: z.union([preparingSummary,capturedSummary]),
  byte_count: z.number().int().min(1).max(8388608).nullable(), sha256: digest.nullable(),
  target: z.string().min(1).max(4096).regex(/^[^\p{Cc}\p{Cf}\p{Cs}]+$(?![\s\S])/u).nullable(), target_exists: z.boolean().nullable(),
  capture_state: z.enum(['not_captured','unknown','captured']), publication_state: z.enum(['not_published','published','unknown']),
  cleanup_state: z.enum(['complete','unknown']), cad_protection_state: z.enum(['not_checked','verified','unverified']),
  native_capture_confirmation: z.boolean(), native_publication_confirmation: z.boolean(),
  storage: z.literal('memory_only'), saves_document: z.literal(false), requires_confirmation: z.literal(true),
  error: fixedError.nullable(),
}).strict().superRefine((value, context) => {
  const issue = (message: string) => context.addIssue({ code: 'custom', message });
  if (value.summary.document !== value.document || value.summary.page !== value.page || value.summary.expires_at !== value.expires_at)
    issue('摘要必须绑定同一文档、页及过期时间');
  if ((value.byte_count === null) !== (value.sha256 === null) || (value.target === null) !== (value.target_exists === null))
    issue('字节摘要及目标状态必须成对返回');
  if (['rendering','preview_ready','target_ready','publishing','published'].includes(value.state)
      && (value.capture_state !== 'captured' || !value.native_capture_confirmation || value.sha256 === null))
    issue('预览或发布必须具有绑定的原生捕获字节');
  if (['target_ready','publishing','published'].includes(value.state) && value.target === null)
    issue('目标阶段必须保留原生选择结果');
  if (value.native_publication_confirmation && (!value.native_capture_confirmation || value.capture_state !== 'captured' || value.sha256 === null || value.target === null))
    issue('发布确认必须绑定已捕获PDF和原生选择的目标');
  if (value.state === 'published' && value.publication_state !== 'published') issue('已发布必须有明确的文件发布结果');
  if (value.publication_state === 'published' && (!value.native_publication_confirmation || value.state !== 'published'))
    issue('明确已发布必须保留原生发布确认及终态');
  if (value.state === 'failed' && value.error === null) issue('失败必须返回固定错误码');
  if (['preparing','prepared','awaiting_capture'].includes(value.state) && (value.native_capture_confirmation || value.native_publication_confirmation))
    issue('尚未捕获不能声称已原生确认');
});
export type PftDrawingExportData = z.output<typeof PftDrawingExportDataSchema>;
export type PftDrawingExportPrepareInput = z.input<typeof PftDrawingExportPrepareInputSchema>;
export type PftDrawingExportMethod = 'prepare_drawing_export' | 'request_drawing_export_approval' | 'get_drawing_export_status';
export const PftDrawingExportSuccessSchema = z.object({ ok: z.literal(true), source_kind: z.literal('bridge_export_memory'),
  observed_at: z.string(), data: PftDrawingExportDataSchema }).strict();
const methods = ['prepare_drawing_export','request_drawing_export_approval','get_drawing_export_status'] as const;
const transportErrors = new Set(['HTTP_ERROR','INVALID_RESPONSE','RESPONSE_TOO_LARGE','CONNECTION_FAILED','BRIDGE_TIMEOUT']);
const uncertain = '导出状态尚未核实；保留原requestId和token查询，不自动重试、重新捕获或推定文件已发布。';
type Transport = { status(): Promise<Record<string,unknown>>; call(method: PftDrawingExportMethod, params: Record<string,unknown>): Promise<unknown> };
type ErrorConstructor = new(code: string,message: string) => Error;
/** Independent file-export state. A memory result is never a CAD transaction or a fresh filesystem check. */
export async function pftDrawingExport(method: PftDrawingExportMethod, input: unknown, transport: Transport, ErrorClass: ErrorConstructor): Promise<PftDrawingExportData> {
  if (!methods.includes(method)) throw new ErrorClass('INVALID_ARGUMENT','不支持此图纸导出方法。');
  const parsedInput = method === 'prepare_drawing_export' ? PftDrawingExportPrepareInputSchema.safeParse(input) : PftDrawingExportTokenInputSchema.safeParse(input);
  if (!parsedInput.success) throw new ErrorClass('INVALID_ARGUMENT','图纸导出仅接受明确文档、页、PDF格式与requestId，或原token；不接受路径或确认字段。');
  const status = await transport.status();
  const supported = status.supported_methods;
  if (!Array.isArray(supported) || !methods.every(value => supported.includes(value)) || !Object.hasOwn(status,'drawing_export'))
    throw new ErrorClass('UNSUPPORTED_CAPABILITY','FreeCAD未声明完整原生PDF导出确认链，未派发请求。');
  if (!PftDrawingExportCapabilitySchema.safeParse(status.drawing_export).success)
    throw new ErrorClass('INVALID_RESPONSE','FreeCAD图纸导出能力格式无效，未派发请求。');
  const value = parsedInput.data;
  const params = 'document' in value ? { docName: value.document,pageName: value.page,format: value.format,requestId: value.requestId } : { token: value.token };
  let response: unknown;
  try { response = await transport.call(method,params); }
  catch (error) {
    if (error instanceof ErrorClass) {
      if (transportErrors.has(String(Reflect.get(error,'code')))) throw new ErrorClass(String(Reflect.get(error,'code')),uncertain);
      throw error;
    }
    throw new ErrorClass('INVALID_RESPONSE',uncertain);
  }
  let size = Infinity;
  try { size = Buffer.byteLength(JSON.stringify(response),'utf8'); } catch { /* No raw response in diagnostics. */ }
  const parsed = size <= 16384 ? PftDrawingExportDataSchema.safeParse(response) : null;
  if (!parsed?.success || ('document' in value ? parsed.data.document !== value.document || parsed.data.page !== value.page
      || parsed.data.format !== value.format || parsed.data.request_id !== value.requestId : parsed.data.token !== value.token))
    throw new ErrorClass('INVALID_RESPONSE',uncertain);
  return parsed.data;
}
