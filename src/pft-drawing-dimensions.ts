// SPDX-License-Identifier: Apache-2.0
/** Explicit cached projection references, a detached preview, and native-only confirmation. */
import * as z from 'zod/v4';
import { PftPartApprovalCapabilitySchema, PftDrawingNativeNameSchema, PftDrawingDimensionEdgeSchema,
  PftDrawingDimensionKindSchema, PftDrawingDimensionsRequestSchema, PftDrawingDimensionsDesignResultSchema } from './pft-part-approval.js';
import { PftDrawingDocumentNameSchema } from './pft-drawing.js';
export { PftDrawingDimensionsDesignResultSchema } from './pft-part-approval.js';
export const PFT_DRAWING_DIMENSIONS_CAPABILITY = Object.freeze({ version: 1, operation: 'add_drawing_dimensions',
  confirmation: 'native_gui_only', preview_method: 'prepare_part_changes', request_method: 'request_part_change_approval',
  status_method: 'get_part_change_status', kinds: Object.freeze(['Distance', 'DistanceX', 'DistanceY', 'Diameter', 'Radius']),
  max_dimensions: 8, edge_index_base: 0, maximum_edge_index: 2047, position_scope: 'view_relative_paper_mm',
  measurement_unit: 'mm', saves_document: false, projection_verification: 'not_checked',
} as const);
export const PftDrawingDimensionsCapabilitySchema = z.object({ version: z.literal(1), operation: z.literal('add_drawing_dimensions'),
  confirmation: z.literal('native_gui_only'), preview_method: z.literal('prepare_part_changes'),
  request_method: z.literal('request_part_change_approval'), status_method: z.literal('get_part_change_status'),
  kinds: z.tuple([z.literal('Distance'), z.literal('DistanceX'), z.literal('DistanceY'), z.literal('Diameter'), z.literal('Radius')]),
  max_dimensions: z.literal(8), edge_index_base: z.literal(0), maximum_edge_index: z.literal(2047),
  position_scope: z.literal('view_relative_paper_mm'), measurement_unit: z.literal('mm'), saves_document: z.literal(false),
  projection_verification: z.literal('not_checked'),
}).strict();
export const PftDrawingDimensionInputItemSchema = z.object({ edge: PftDrawingDimensionEdgeSchema, kind: PftDrawingDimensionKindSchema,
  xMm: z.number().finite().min(-1000).max(1000), yMm: z.number().finite().min(-1000).max(1000) }).strict();
export const PftDrawingDimensionsInputSchema = z.object({ document: PftDrawingDocumentNameSchema,
  operation: z.literal('add_drawing_dimensions'), page: PftDrawingNativeNameSchema, view: PftDrawingNativeNameSchema,
  dimensions: z.array(PftDrawingDimensionInputItemSchema).min(1).max(8),
}).strict().superRefine((value, context) => {
  if (value.page === value.view || new Set(value.dimensions.map(item => item.edge + ':' + item.kind)).size !== value.dimensions.length)
    context.addIssue({ code: 'custom', message: '页和视图须不同，同一边与尺寸类型不能重复' });
});
export type PftDrawingDimensionsInput = z.output<typeof PftDrawingDimensionsInputSchema>;
const previewRow = z.object({ object: PftDrawingNativeNameSchema, field: z.literal('TechDraw dimension'), old: z.null(),
  new: z.number().finite().positive(), status: z.literal('change'), edge: PftDrawingDimensionEdgeSchema,
  kind: PftDrawingDimensionKindSchema, value_mm: z.number().finite().positive().max(1e9), x_mm: z.number().finite().min(-1000).max(1000),
  y_mm: z.number().finite().min(-1000).max(1000), source: z.literal('native_cached_projected_geometry'), note: z.string().min(1).max(2048),
}).strict();
const blockedRow = z.object({ object: PftDrawingNativeNameSchema, field: z.literal('TechDraw dimension'), old: z.null(), new: z.null(),
  status: z.literal('blocked'), reason: z.string().min(1).max(2048) }).strict();
export const PftDrawingDimensionsPreviewSchema = z.object({ document: PftDrawingDocumentNameSchema,
  operation: z.literal('add_drawing_dimensions'), token: z.string().regex(/^[A-Za-z0-9_-]{16,128}$/u), state: z.literal('prepared'),
  can_apply: z.boolean(), expires_at: z.string().min(1).max(128), ttl_seconds: z.literal(300), changed_count: z.number().int().min(0).max(8),
  rows: z.array(z.union([previewRow, blockedRow])).min(1).max(8), requires_confirmation: z.literal(true), saves_document: z.literal(false),
  storage: z.literal('memory_only'), design_request: PftDrawingDimensionsRequestSchema,
}).strict().superRefine((value, context) => {
  if (!value.can_apply) {
    if (value.changed_count !== 0 || !value.rows.every(row => row.status === 'blocked') || value.rows.length !== 1
      || value.rows[0]!.object !== value.design_request.page) context.addIssue({ code: 'custom', message: '阻断预览不能携带部分成功或测量值' });
    return;
  }
  const rows = value.rows;
  if (value.changed_count !== value.design_request.dimensions.length || rows.length !== value.changed_count
    || !rows.every(row => row.status === 'change' && row.new === row.value_mm)) {
    context.addIssue({ code: 'custom', message: '尺寸预览数量、测量值和请求不一致' }); return;
  }
  const dimensions = rows.map(row => { if (row.status !== 'change') throw new Error('unreachable');
    return { name: row.object, edge: row.edge, kind: row.kind, value_mm: row.value_mm, x_mm: row.x_mm, y_mm: row.y_mm }; });
  if (!PftDrawingDimensionsDesignResultSchema.safeParse({ kind: 'drawing_dimensions', page: value.design_request.page,
    view: value.design_request.view, dimensions, measurement_verification: 'native_reference_and_value_checked',
    projection_verification: 'not_checked', manual_review_required: true }).success
    || dimensions.some(({ name: _name, value_mm: _measurement, ...item }, i) => JSON.stringify(item) !== JSON.stringify(value.design_request.dimensions[i])))
    context.addIssue({ code: 'custom', message: '尺寸预览必须逐项绑定计划新名、投影边、类型和偏移' });
});
export type PftDrawingDimensionsPreview = z.output<typeof PftDrawingDimensionsPreviewSchema>;
type Transport = { status: () => Promise<Record<string, unknown>>; prepare: (params: Record<string, unknown>) => Promise<unknown> };
type ErrorConstructor = new (code: string, message: string) => Error;
export async function pftPrepareDrawingDimensions(input: PftDrawingDimensionsInput, transport: Transport,
  ErrorClass: ErrorConstructor): Promise<PftDrawingDimensionsPreview> {
  const parsed = PftDrawingDimensionsInputSchema.safeParse(input);
  if (!parsed.success) throw new ErrorClass('INVALID_ARGUMENT', '尺寸预览仅接受明确文档、页、视图及1至8项投影边/类型/纸面偏移，不接受外部确认或数值覆盖。');
  const status = await transport.status();
  const methods = status.supported_methods;
  if (!Array.isArray(methods) || ['prepare_part_changes', 'request_part_change_approval', 'get_part_change_status'].some(m => !methods.includes(m))
    || status.drawing_dimensions === undefined || status.part_approvals === undefined)
    throw new ErrorClass('UNSUPPORTED_CAPABILITY', '当前插件未声明尺寸预览和原生确认能力，未派发请求。');
  if (!PftDrawingDimensionsCapabilitySchema.safeParse(status.drawing_dimensions).success || !PftPartApprovalCapabilitySchema.safeParse(status.part_approvals).success)
    throw new ErrorClass('INVALID_RESPONSE', '尺寸能力或原生确认声明格式无效，未派发请求。');
  const value = parsed.data;
  const request = { page: value.page, view: value.view,
    dimensions: value.dimensions.map(({ edge, kind, xMm, yMm }) => ({ edge, kind, x_mm: xMm, y_mm: yMm })) };
  const response = await transport.prepare({ docName: value.document, objectNames: [value.page], viewName: value.view,
    operation: value.operation, dimensions: request.dimensions });
  let encoded: string | undefined;
  try { encoded = JSON.stringify(response); } catch { /* Never return unvalidated payload. */ }
  const preview = encoded !== undefined && Buffer.byteLength(encoded, 'utf8') <= 128 * 1024 ? PftDrawingDimensionsPreviewSchema.safeParse(response) : null;
  if (!preview?.success || preview.data.document !== value.document || JSON.stringify(preview.data.design_request) !== JSON.stringify(request))
    throw new ErrorClass('INVALID_RESPONSE', '尺寸预览无法验证或与明确目标不一致；保留原状态，不自动重试。');
  return preview.data;
}
