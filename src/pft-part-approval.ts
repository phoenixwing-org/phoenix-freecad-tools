// SPDX-License-Identifier: Apache-2.0
import * as z from 'zod/v4';

export const PFT_PART_APPROVAL_CAPABILITY = Object.freeze({ version: 1,
  request_method: 'request_part_change_approval', status_method: 'get_part_change_status',
  confirmation: 'native_gui_only', max_pending: 1, storage: 'memory_only', ttl_seconds: 300, max_status_bytes: 4096,
} as const);
export const PftPartApprovalCapabilitySchema = z.object({ version: z.literal(1),
  request_method: z.literal('request_part_change_approval'), status_method: z.literal('get_part_change_status'),
  confirmation: z.literal('native_gui_only'), max_pending: z.literal(1), storage: z.literal('memory_only'),
  ttl_seconds: z.literal(300), max_status_bytes: z.literal(4096),
}).strict();
const token = z.string().regex(/^[A-Za-z0-9_-]{16,128}$/u);
export const PftPartApprovalInputSchema = z.object({ token }).strict();
const documentName = z.string().min(1).max(512)
  .refine(value => Array.from(value).length <= 256, '文档名称超过 Unicode 字符数量上限')
  .regex(/^[^\p{Cc}\p{Cf}\p{Cs}]+$(?![\s\S])/u);
/** Native Body/Pad names use the bounded design contract, independently of document names. */
export const PftPadNativeNameSchema = z.string().min(1).max(128)
  .refine(value => Array.from(value).length <= 64, 'Pad对象名称超过64个Unicode字符')
  .regex(/^[^\p{Cc}\p{Cf}\p{Cs}]+$/u)
  .refine(value => value === value.trim() && value !== '.' && value !== '..'
    && !value.includes('/') && !value.includes('\\') && !value.includes(':'), 'Pad对象名称不能包含路径字符或首尾空白');
const changedCount = z.number().int().min(0).max(500);
const fixedErrorCodes = ['PART_CHANGE_FAILED', 'PART_APPROVAL_UI_UNAVAILABLE', 'PART_APPROVAL_NO_CHANGES',
  'PART_PREVIEW_BLOCKED', 'PART_PLAN_STALE', 'DOCUMENT_NOT_FOUND', 'DOCUMENT_NOT_ACTIVE', 'GUI_THREAD_REQUIRED',
  'PART_TRANSACTION_STATE_UNAVAILABLE', 'PART_TRANSACTION_OPEN_UNVERIFIED', 'PART_TRANSACTION_CHANGED',
  'PART_COMMIT_UNVERIFIED', 'PART_ROLLBACK_UNVERIFIED', 'PART_PROPERTY_VERIFY_FAILED',
  'PART_PREFERENCE_RESTORE_UNVERIFIED', 'PART_ROLLBACK_AND_PREFERENCE_UNVERIFIED',
  'PAD_UNSUPPORTED', 'PAD_PLAN_STALE', 'PAD_PARAMETER_VERIFY_FAILED', 'PAD_GEOMETRY_VERIFY_FAILED',
  'PAD_RECOMPUTE_UNVERIFIED', 'PAD_NATIVE_CONFIRMATION_REQUIRED',
  'DRAWING_UNSUPPORTED', 'DRAWING_PLAN_STALE', 'DRAWING_TEMPLATE_UNAVAILABLE', 'DRAWING_CREATE_FAILED',
  'DRAWING_RECOMPUTE_UNVERIFIED', 'DRAWING_PROJECTION_UNAVAILABLE', 'DRAWING_NATIVE_CONFIRMATION_REQUIRED', 'DRAWING_RESULT_INVALID', 'DIMENSION_UNSUPPORTED', 'DIMENSION_PLAN_STALE', 'DIMENSION_CREATE_FAILED',
  'DIMENSION_RECOMPUTE_UNVERIFIED', 'DIMENSION_VALUE_UNVERIFIED', 'DIMENSION_NATIVE_CONFIRMATION_REQUIRED', 'DIMENSION_RESULT_INVALID'] as const;
const positiveFinite = z.number().finite().positive();
const padGeometry = z.object({ volume_mm3: positiveFinite,
  dimensions_mm: z.tuple([positiveFinite, positiveFinite, positiveFinite]), solids: z.literal(1),
}).strict();
export const PftPadLengthDesignResultSchema = z.object({ kind: z.literal('pad_length'), body: PftPadNativeNameSchema, pad: PftPadNativeNameSchema,
  length_mm: z.number().finite().min(0.01).max(10000), recompute_count: z.number().int().min(0).max(Number.MAX_SAFE_INTEGER),
  checked_objects: z.number().int().min(1).max(64), geometry: z.object({ pad: padGeometry, body: padGeometry }).strict(),
  validation: z.literal('native_valid_one_solid_and_length_scaled_volume'),
}).strict();
/** Drawing nodes are internal Names, never template file paths or display Labels. */
export const PftDrawingNativeNameSchema = PftPadNativeNameSchema;
export const PftDrawingDesignResultSchema = z.object({ kind: z.literal('drawing_draft'),
  page: PftDrawingNativeNameSchema, template: PftDrawingNativeNameSchema, source: PftDrawingNativeNameSchema,
  views: z.tuple([
    z.object({ name: PftDrawingNativeNameSchema, kind: z.literal('front') }).strict(),
    z.object({ name: PftDrawingNativeNameSchema, kind: z.literal('top') }).strict(),
    z.object({ name: PftDrawingNativeNameSchema, kind: z.literal('axonometric') }).strict(),
  ]), scale: z.number().finite().positive(), projection: z.literal('first_angle'), paper: z.literal('A4_landscape'),
  projection_verification: z.literal('not_checked'), manual_review_required: z.literal(true),
}).strict().superRefine((value, context) => {
  const created = [value.page, value.template, ...value.views.map(view => view.name)];
  if (new Set(created).size !== 5 || created.includes(value.source)) {
    context.addIssue({ code: 'custom', message: '图纸草稿必须包含五个不同的新节点，且不能与源对象重名' });
  }
});
export const PftDrawingDimensionKindSchema = z.enum(['Distance', 'DistanceX', 'DistanceY', 'Diameter', 'Radius']);
export const PftDrawingDimensionEdgeSchema = z.string().regex(/^Edge(?:0|[1-9][0-9]{0,3})$(?![\s\S])/u)
  .refine(value => Number(value.slice(4)) <= 2047, '投影边编号范围为0至2047');
const dimensionCoordinate = z.number().finite().min(-1000).max(1000);
export const PftDrawingDimensionRequestItemSchema = z.object({ edge: PftDrawingDimensionEdgeSchema,
  kind: PftDrawingDimensionKindSchema, x_mm: dimensionCoordinate, y_mm: dimensionCoordinate }).strict();
export const PftDrawingDimensionsRequestSchema = z.object({ page: PftDrawingNativeNameSchema,
  view: PftDrawingNativeNameSchema, dimensions: z.array(PftDrawingDimensionRequestItemSchema).min(1).max(8),
}).strict().superRefine((value, context) => {
  if (value.page === value.view || new Set(value.dimensions.map(item => item.edge + ':' + item.kind)).size !== value.dimensions.length)
    context.addIssue({ code: 'custom', message: '页和视图须不同，且本批不能重复同一边与尺寸类型' });
});
export const PftDrawingDimensionResultItemSchema = PftDrawingDimensionRequestItemSchema.extend({
  name: PftDrawingNativeNameSchema, value_mm: z.number().finite().positive().max(1e9),
}).strict();
export const PftDrawingDimensionsDesignResultSchema = z.object({ kind: z.literal('drawing_dimensions'),
  page: PftDrawingNativeNameSchema, view: PftDrawingNativeNameSchema,
  dimensions: z.array(PftDrawingDimensionResultItemSchema).min(1).max(8),
  measurement_verification: z.literal('native_reference_and_value_checked'),
  projection_verification: z.literal('not_checked'), manual_review_required: z.literal(true),
}).strict().superRefine((value, context) => {
  if (!PftDrawingDimensionsRequestSchema.safeParse({ page: value.page, view: value.view,
    dimensions: value.dimensions.map(({ name: _name, value_mm: _value, ...item }) => item) }).success
    || new Set(value.dimensions.map(item => item.name)).size !== value.dimensions.length
    || value.dimensions.some(item => [value.page, value.view].includes(item.name)))
    context.addIssue({ code: 'custom', message: '尺寸结果必须含1至8个不同新节点及不重复的明确关联引用' });
});
const legacyResult = z.object({ changed_count: changedCount, saves_document: z.literal(false) }).strict();
const padResult = z.object({ changed_count: z.literal(1), saves_document: z.literal(false),
  design_result: PftPadLengthDesignResultSchema,
}).strict();
const dimensionResult = z.object({ changed_count: z.number().int().min(1).max(8), saves_document: z.literal(false),
  design_result: PftDrawingDimensionsDesignResultSchema,
}).strict();
const drawingResult = z.object({ changed_count: z.literal(5), saves_document: z.literal(false),
  design_result: PftDrawingDesignResultSchema,
}).strict();
export const PftPartApprovalDataSchema = z.object({ document: documentName,
  operation: z.enum(['normalize_names', 'sync_link_labels', 'initialize_properties', 'recommend_properties', 'set_properties', 'set_pad_length', 'create_drawing', 'add_drawing_dimensions']),
  token, state: z.enum(['prepared', 'awaiting_confirmation', 'applying', 'applied', 'cancelled', 'failed', 'expired']),
  changed_count: changedCount, requires_confirmation: z.literal(true), saves_document: z.literal(false),
  storage: z.literal('memory_only'), native_confirmation: z.boolean(),
  result: z.union([legacyResult, padResult, drawingResult, dimensionResult]).nullable(),
  error_code: z.enum(fixedErrorCodes).nullable(),
}).strict().superRefine((value, context) => {
  const isPad = value.operation === 'set_pad_length';
  if (isPad && (value.changed_count > 1
    || ['awaiting_confirmation', 'applying', 'applied'].includes(value.state) && value.changed_count !== 1
    || ['applying', 'applied'].includes(value.state) && !value.native_confirmation)) {
    context.addIssue({ code: 'custom', message: 'Pad应用只允许一个明确Length变更，并必须经过原生人工确认' });
  }
  const isDrawing = value.operation === 'create_drawing';
  if (isDrawing && (![0, 5].includes(value.changed_count)
    || ['awaiting_confirmation', 'applying', 'applied'].includes(value.state) && value.changed_count !== 5
    || ['applying', 'applied'].includes(value.state) && !value.native_confirmation)) {
    context.addIssue({ code: 'custom', message: '图纸草稿只创建五个节点，并必须经过原生人工确认' });
  }
  const isDimension = value.operation === 'add_drawing_dimensions';
  if (isDimension && (value.changed_count > 8
    || ['awaiting_confirmation', 'applying', 'applied'].includes(value.state) && value.changed_count < 1
    || ['applying', 'applied'].includes(value.state) && !value.native_confirmation))
    context.addIssue({ code: 'custom', message: '尺寸应用必须含1至8项并经过原生人工确认' });
  if (value.state === 'applied' && value.result !== null) {
    const expectedKind = isPad ? 'pad_length' : isDrawing ? 'drawing_draft' : isDimension ? 'drawing_dimensions' : null;
    const actualKind = 'design_result' in value.result ? value.result.design_result.kind : null;
    if (isDimension && 'design_result' in value.result && value.result.design_result.kind === 'drawing_dimensions'
      && value.result.design_result.dimensions.length !== value.changed_count)
      context.addIssue({ code: 'custom', message: '尺寸实际节点数与结果数量不一致' });
    if (actualKind !== expectedKind) {
      context.addIssue({ code: 'custom', message: '应用结果必须与操作类型一致，不能混用Pad、图纸或属性结果' });
    }
  }
  if (['prepared', 'awaiting_confirmation', 'cancelled', 'expired'].includes(value.state) && value.native_confirmation) {
    context.addIssue({ code: 'custom', message: '尚未确认或已取消/过期的状态不能声称原生确认' });
  }
  if (value.state === 'applied') {
    if (value.result === null || value.error_code !== null || value.result.changed_count !== value.changed_count) {
      context.addIssue({ code: 'custom', message: '已应用状态必须有一致的实际结果且没有错误' });
    }
  } else if (value.state === 'failed') {
    if (value.result !== null || value.error_code === null) {
      context.addIssue({ code: 'custom', message: '失败状态必须只有固定错误码' });
    }
  } else if (value.result !== null || value.error_code !== null) {
    context.addIssue({ code: 'custom', message: '未完成状态不能携带应用结果或错误' });
  }
});
export type PftPartApprovalData = z.output<typeof PftPartApprovalDataSchema>;
export const PftPartApprovalSuccessSchema = z.object({ ok: z.literal(true), source_kind: z.literal('live_freecad_gui'),
  observed_at: z.string(), data: PftPartApprovalDataSchema }).strict();
export type PftPartApprovalMethod = 'request_part_change_approval' | 'get_part_change_status';
type PftPartApprovalTransport = { status: () => Promise<Record<string, unknown>>;
  call: (method: PftPartApprovalMethod, params: Record<string, unknown>) => Promise<unknown> };
type PftPartApprovalErrorConstructor = new (code: string, message: string) => Error;
const transportErrors = new Set(['HTTP_ERROR', 'INVALID_RESPONSE', 'RESPONSE_TOO_LARGE', 'CONNECTION_FAILED', 'BRIDGE_TIMEOUT']);
const unverifiedState = '原生确认状态无法验证；请求可能已送达，请保留原 token 查询，不要自动重试或推定已确认、已写入。';

/** A request only opens/reuses a native window; only its human GUI callback can approve a write. */
export async function pftPartApproval(method: PftPartApprovalMethod, input: { token: string },
  transport: PftPartApprovalTransport, ErrorClass: PftPartApprovalErrorConstructor): Promise<PftPartApprovalData> {
  if (method !== 'request_part_change_approval' && method !== 'get_part_change_status') {
    throw new ErrorClass('INVALID_ARGUMENT', '原生确认链仅支持请求确认或读取状态。');
  }
  const parsedInput = PftPartApprovalInputSchema.safeParse(input);
  if (!parsedInput.success) throw new ErrorClass('INVALID_ARGUMENT', '原生确认请求仅接受有效的预览 token，不接受外部确认标志。');
  const status = await transport.status();
  if (!Array.isArray(status.supported_methods)
    || !status.supported_methods.includes('request_part_change_approval')
    || !status.supported_methods.includes('get_part_change_status') || status.part_approvals === undefined) {
    throw new ErrorClass('UNSUPPORTED_CAPABILITY', '当前 FreeCAD 插件未声明原生确认与状态查询能力；未派发请求，请更新并重新加载插件。');
  }
  if (!PftPartApprovalCapabilitySchema.safeParse(status.part_approvals).success) {
    throw new ErrorClass('INVALID_RESPONSE', 'FreeCAD 插件原生确认能力声明格式无效；未派发请求。');
  }
  let value: unknown;
  try { value = await transport.call(method, { token: parsedInput.data.token }); }
  catch (error) {
    if (error instanceof ErrorClass) {
      const code = Reflect.get(error, 'code');
      if (typeof code === 'string' && transportErrors.has(code)) throw new ErrorClass(code, unverifiedState);
      throw error;
    }
    throw new ErrorClass('INVALID_RESPONSE', unverifiedState);
  }
  let encoded: string | undefined;
  try { encoded = JSON.stringify(value); } catch { /* Reject non-JSON status without forwarding it. */ }
  const parsed = encoded !== undefined && Buffer.byteLength(encoded, 'utf8') <= PFT_PART_APPROVAL_CAPABILITY.max_status_bytes
    ? PftPartApprovalDataSchema.safeParse(value) : null;
  if (!parsed?.success || parsed.data.token !== parsedInput.data.token) {
    throw new ErrorClass('INVALID_RESPONSE', unverifiedState);
  }
  return parsed.data;
}
