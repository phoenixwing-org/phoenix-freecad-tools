// SPDX-License-Identifier: Apache-2.0
/** One explicit source, an exact runtime gate and a native-confirmed drawing draft. */
import * as z from 'zod/v4';
import { PftPartApprovalCapabilitySchema, PftDrawingNativeNameSchema } from './pft-part-approval.js';
export { PftDrawingDesignResultSchema } from './pft-part-approval.js';

export const PFT_DRAWING_CAPABILITY = Object.freeze({ version: 1, operation: 'create_drawing',
  confirmation: 'native_gui_only', preview_method: 'prepare_part_changes', request_method: 'request_part_change_approval',
  status_method: 'get_part_change_status', paper: 'A4_landscape', projection: 'first_angle',
  views: Object.freeze(['front', 'top', 'axonometric']), source_scope: 'single_native_solid',
  saves_document: false, projection_verification: 'not_checked',
} as const);
export const PftDrawingCapabilitySchema = z.object({ version: z.literal(1), operation: z.literal('create_drawing'),
  confirmation: z.literal('native_gui_only'), preview_method: z.literal('prepare_part_changes'),
  request_method: z.literal('request_part_change_approval'), status_method: z.literal('get_part_change_status'),
  paper: z.literal('A4_landscape'), projection: z.literal('first_angle'),
  views: z.tuple([z.literal('front'), z.literal('top'), z.literal('axonometric')]),
  source_scope: z.literal('single_native_solid'), saves_document: z.literal(false),
  projection_verification: z.literal('not_checked'),
}).strict();
export const PftDrawingDocumentNameSchema = z.string().min(1).max(512)
  .refine(value => Array.from(value).length <= 256, '文档名称超过256个Unicode字符')
  .regex(/^[^\p{Cc}\p{Cf}\p{Cs}]+$(?![\s\S])/u);
export const PftDrawingInputSchema = z.object({ document: PftDrawingDocumentNameSchema,
  objects: z.array(PftDrawingNativeNameSchema).length(1), operation: z.literal('create_drawing'),
}).strict();
export type PftDrawingInput = z.output<typeof PftDrawingInputSchema>;
const previewRow = z.object({ object: PftDrawingNativeNameSchema, status: z.enum(['change', 'blocked']) }).passthrough();
export const PftDrawingPreviewSchema = z.object({ document: PftDrawingDocumentNameSchema,
  operation: z.literal('create_drawing'), token: z.string().regex(/^[A-Za-z0-9_-]{16,128}$/u), state: z.literal('prepared'),
  can_apply: z.boolean(), expires_at: z.string().min(1).max(128), ttl_seconds: z.literal(300),
  changed_count: z.union([z.literal(0), z.literal(5)]), rows: z.array(previewRow).min(1).max(5),
  requires_confirmation: z.literal(true), saves_document: z.literal(false), storage: z.literal('memory_only'),
  design_request: z.object({ source: PftDrawingNativeNameSchema, page: PftDrawingNativeNameSchema.nullable() }).strict(),
}).strict().superRefine((value, context) => {
  const names = value.rows.map(row => row.object);
  const valid = value.can_apply
    ? value.changed_count === 5 && value.design_request.page !== null && value.rows.length === 5
      && value.rows.every(row => row.status === 'change') && new Set(names).size === 5
      && !names.includes(value.design_request.source) && names.includes(value.design_request.page)
    : value.changed_count === 0 && value.design_request.page === null && value.rows.every(row => row.status === 'blocked');
  if (!valid) context.addIssue({ code: 'custom', message: '图纸预览必须明确五个新节点或完整阻断，不能把预览当应用结果' });
});
export type PftDrawingPreview = z.output<typeof PftDrawingPreviewSchema>;
type DrawingTransport = { status: () => Promise<Record<string, unknown>>;
  prepare: (params: Record<string, unknown>) => Promise<unknown> };
type DrawingErrorConstructor = new (code: string, message: string) => Error;

/** No script, direct apply, old-plugin fallback or retry is available on this path. */
export async function pftPrepareDrawing(input: PftDrawingInput, transport: DrawingTransport,
  ErrorClass: DrawingErrorConstructor): Promise<PftDrawingPreview> {
  const parsed = PftDrawingInputSchema.safeParse(input);
  if (!parsed.success) throw new ErrorClass('INVALID_ARGUMENT', '出图预览只接受明确文档和一个源对象，不接受属性、Pad参数、模板路径或确认标志。');
  const status = await transport.status();
  const required = ['prepare_part_changes', 'request_part_change_approval', 'get_part_change_status'];
  const methods = status.supported_methods;
  if (!Array.isArray(methods) || required.some(method => !methods.includes(method))
    || status.drawing_create === undefined || status.part_approvals === undefined) {
    throw new ErrorClass('UNSUPPORTED_CAPABILITY', '当前FreeCAD插件未声明完整的出图预览及原生确认能力；未派发预览。');
  }
  if (!PftDrawingCapabilitySchema.safeParse(status.drawing_create).success
    || !PftPartApprovalCapabilitySchema.safeParse(status.part_approvals).success) {
    throw new ErrorClass('INVALID_RESPONSE', 'FreeCAD插件的出图或原生确认能力声明格式无效；未派发预览。');
  }
  const value = await transport.prepare({ docName: parsed.data.document, objectNames: parsed.data.objects,
    operation: parsed.data.operation });
  let encoded: string | undefined;
  try { encoded = JSON.stringify(value); } catch { /* Reject non-JSON preview without forwarding it. */ }
  const preview = encoded !== undefined && Buffer.byteLength(encoded, 'utf8') <= 128 * 1024
    ? PftDrawingPreviewSchema.safeParse(value) : null;
  if (!preview?.success || preview.data.document !== parsed.data.document
    || preview.data.design_request.source !== parsed.data.objects[0]) {
    throw new ErrorClass('INVALID_RESPONSE', '出图预览无法验证或与明确目标不一致；未授予确认，请重新检查原生预览状态。');
  }
  return preview.data;
}
