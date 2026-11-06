// SPDX-License-Identifier: Apache-2.0
/** Exact native cached projection references; no completeness or HLR-ready claim. */
import * as z from 'zod/v4';
import { PftNativeReadDataSchema, PftReadCapabilitySchema, PFT_MAX_READ_BYTES } from './pft-read-provenance.js';

export const PFT_DRAWING_REFERENCES_CAPABILITY = Object.freeze({ version: 1, method: 'inspect_drawing_page',
  index_base: 0, max_index: 2047, max_items: 50, source: 'native_cached_projection',
  projection_verification: 'not_checked' } as const);
export const PftDrawingReferencesCapabilitySchema = z.object({ version: z.literal(1), method: z.literal('inspect_drawing_page'),
  index_base: z.literal(0), max_index: z.literal(2047), max_items: z.literal(50),
  source: z.literal('native_cached_projection'), projection_verification: z.literal('not_checked') }).strict();
const text = (maximum: number) => z.string().max(maximum * 2)
  .refine(value => Array.from(value).length <= maximum, '文本超过Unicode字符数量上限').regex(/^[^\p{Cs}]*$/u);
const name = text(256).min(1).regex(/^[^\p{Cc}\p{Cf}\p{Cs}]+$(?![\s\S])/u);
const count = z.number().int().min(0).max(2147483647);
const index = z.number().int().min(0).max(2047);
const limit = z.number().int().min(1).max(50);
const point = z.tuple([z.number().finite(), z.number().finite(), z.number().finite()]);
const edgeName = z.string().regex(/^Edge(?:0|[1-9][0-9]{0,3})$(?![\s\S])/u)
  .refine(value => Number(value.slice(4)) <= 2047, 'TechDraw边编号超限');

export const PftDrawingReferencesInputSchema = z.object({ document: name.optional(), page: name,
  view: name.optional(), offset: index.optional(), limit: limit.optional() }).strict().superRefine((value, context) => {
    if (value.view === undefined && (value.offset !== undefined || value.limit !== undefined)) {
      context.addIssue({ code: 'custom', message: '只有明确view才可以查询投影边索引窗口' });
    }
    if (value.view !== undefined && (value.offset ?? 0) + (value.limit ?? 20) - 1 > 2047) {
      context.addIssue({ code: 'custom', message: '投影边窗口越过最大编号2047' });
    }
  });
export type PftDrawingReferencesInput = z.input<typeof PftDrawingReferencesInputSchema>;
export const PftDrawingEdgeSchema = z.object({ edge: edgeName,
  status: z.enum(['available', 'missing', 'read_failed', 'unsupported']),
  curve: z.enum(['line', 'circle', 'unsupported']).nullable(), start: point.nullable(), end: point.nullable(),
  length_mm: z.number().finite().positive().nullable(), radius_mm: z.number().finite().positive().nullable(),
  center: point.nullable(), closed: z.boolean().nullable(),
  supported_kinds: z.array(z.enum(['Distance', 'DistanceX', 'DistanceY', 'Radius', 'Diameter'])).max(3),
}).strict().superRefine((value, context) => {
  const fail = () => context.addIssue({ code: 'custom', message: '投影边状态、类型及量值不一致' });
  if (value.status === 'available') {
    if (value.length_mm === null) fail();
    if (value.curve === 'line') {
      if (value.start === null || value.end === null || value.start.every((number, i) => number === value.end![i])
        || value.radius_mm !== null || value.center !== null || value.closed !== false
        || JSON.stringify(value.supported_kinds) !== '["Distance","DistanceX","DistanceY"]') fail();
    } else if (value.curve === 'circle') {
      if (value.start !== null || value.end !== null || value.radius_mm === null || value.center === null
        || value.closed !== true || JSON.stringify(value.supported_kinds) !== '["Radius","Diameter"]') fail();
    } else fail();
  } else if ([value.start, value.end, value.length_mm, value.radius_mm, value.center, value.closed].some(item => item !== null)
    || value.supported_kinds.length !== 0 || value.curve !== (value.status === 'unsupported' ? 'unsupported' : null)) fail();
  if (Buffer.byteLength(JSON.stringify(value), 'utf8') > 2048) fail();
});
const geometry = z.object({ view: name, status: z.enum(['available', 'unsupported', 'read_failed']),
  offset: index, limit, index_base: z.literal(0), max_index: z.literal(2047),
  items: z.array(PftDrawingEdgeSchema).max(50), total_count: z.null(), enumeration_complete: z.literal(false),
  length_unit: z.literal('mm'), coordinate_system: z.literal('view_local_unscaled_qt_y_down'),
  source: z.literal('native_cached_projection'), projection_verification: z.literal('not_checked'),
  geometry_freshness: z.literal('not_verified'),
}).strict().superRefine((value, context) => {
  if (value.offset + value.limit - 1 > 2047
    || (value.status === 'available' ? value.items.length !== value.limit
      || value.items.some((item, i) => item.edge !== 'Edge' + String(value.offset + i)) : value.items.length !== 0)) {
    context.addIssue({ code: 'custom', message: '投影边索引窗口必须逐项返回，missing不能冒充完整枚举' });
  }
});
const json = z.json();
const reference = z.object({ document: name, object: name, external: z.boolean(),
  subelements: z.array(text(512)).max(100).optional(), subelements_limited: z.literal(true).optional() }).strict();
const summary = z.object({ Name: name, Label: json, TypeId: z.literal('TechDraw::DrawPage'), scale: json,
  template: reference.nullable(), object_count: count, state: json }).strict();
const properties = z.object(Object.fromEntries(['X', 'Y', 'Scale', 'Direction', 'Rotation', 'Type', 'FormatSpec', 'MeasureType']
  .map(field => [field, json.optional()]))).strict();
export const PftDrawingNativeMeasurementSchema = z.object({
  status: z.enum(['available', 'unsupported', 'unknown', 'read_failed']),
  kind: z.enum(['Distance', 'DistanceX', 'DistanceY', 'Radius', 'Diameter']).nullable(),
  value_mm: z.number().finite().positive().max(1e9).nullable(), unit: z.literal('mm'),
  source: z.literal('native_cached_dimension'), geometry_freshness: z.literal('not_verified'),
}).strict().superRefine((value, context) => {
  if (value.status === 'available' ? value.kind === null || value.value_mm === null : value.value_mm !== null) {
    context.addIssue({ code: 'custom', message: '原生缓存尺寸状态与毫米量值不一致' });
  }
});
const content = z.object({ Name: name.nullable(), Label: json, TypeId: text(256).nullable(), state: json,
  sources: z.array(reference.extend({ field: z.enum(['Source', 'Source2', 'References2D', 'References3D', 'BaseView']) }).strict()).max(100),
  sources_truncated: z.boolean(), properties,
  native_measurement: PftDrawingNativeMeasurementSchema.optional(),
}).strict().superRefine((value, context) => {
  const measurement = value.native_measurement;
  if (measurement !== undefined && (value.TypeId !== 'TechDraw::DrawViewDimension'
    || measurement.kind !== null && value.properties['Type'] !== measurement.kind
    || measurement.status === 'available' && value.properties['MeasureType'] !== 'Projected')) {
    context.addIssue({ code: 'custom', message: '仅对应类型的原生投影尺寸可以返回缓存尺寸量值' });
  }
});
export const PftDrawingReferencesDataSchema = PftNativeReadDataSchema.safeExtend({ document: name,
  page: summary, template: z.object({ reference: reference.nullable(), editable_fields: json,
    width: json, height: json, has_file_reference: z.boolean() }).strict().nullable(),
  objects: z.array(content).max(100), total: count, truncated: z.boolean(), recomputed: z.literal(false),
  references_validated: z.literal(false), warnings: z.array(text(512)).max(30), projected_geometry: geometry,
}).strict().superRefine((value, context) => {
  if (value.objects.length !== Math.min(value.total, 100) || value.truncated !== (value.total > 100)
    || value.page.object_count !== value.total) {
    context.addIssue({ code: 'custom', message: '图页摘要数量与截断状态不一致' });
  }
});
export type PftDrawingReferencesData = z.output<typeof PftDrawingReferencesDataSchema>;
type Transport = { status: () => Promise<Record<string, unknown>>; read: (params: Record<string, unknown>) => Promise<unknown> };
type ErrorConstructor = new (code: string, message: string) => Error;

/** This helper requires view; callers preserve the legacy summary route without it. */
export async function pftInspectDrawingReferences(input: PftDrawingReferencesInput, transport: Transport,
  ErrorClass: ErrorConstructor): Promise<PftDrawingReferencesData> {
  const parsed = PftDrawingReferencesInputSchema.safeParse(input);
  if (!parsed.success || parsed.data.view === undefined) {
    throw new ErrorClass('INVALID_ARGUMENT', '投影边读取需要明确page、view及有效索引窗口。');
  }
  const status = await transport.status();
  if (!Array.isArray(status.supported_methods) || !status.supported_methods.includes('inspect_drawing_page')
    || status.drawing_references === undefined || status.read_provenance === undefined) {
    throw new ErrorClass('UNSUPPORTED_CAPABILITY', '当前插件未声明投影引用及原生只读来源能力；未读取。');
  }
  const capability = PftDrawingReferencesCapabilitySchema.safeParse(status.drawing_references);
  const provenance = PftReadCapabilitySchema.safeParse(status.read_provenance);
  if (!capability.success || !provenance.success) {
    throw new ErrorClass('INVALID_RESPONSE', '投影引用或原生读取能力声明格式无效；未读取。');
  }
  if (!provenance.data.methods.includes('inspect_drawing_page')) {
    throw new ErrorClass('UNSUPPORTED_CAPABILITY', '当前插件没有图页原生读取来源能力；未读取。');
  }
  const { document, page, view, offset = 0, limit = 20 } = parsed.data;
  const value = await transport.read({ ...(document === undefined ? {} : { docName: document }),
    pageName: page, viewName: view, offset, limit });
  let encoded: string | undefined;
  try { encoded = JSON.stringify(value); } catch { /* A non-JSON DTO is invalid, never forwarded. */ }
  if (encoded !== undefined && Buffer.byteLength(encoded, 'utf8') > PFT_MAX_READ_BYTES) {
    throw new ErrorClass('READ_RESPONSE_TOO_LARGE', '图页投影引用读取超过128 KiB预算，请缩小范围。');
  }
  const result = encoded === undefined ? null : PftDrawingReferencesDataSchema.safeParse(value);
  if (!result?.success || (document !== undefined && result.data.document !== document)
    || result.data.page.Name !== page || result.data.projected_geometry.view !== view
    || result.data.projected_geometry.offset !== offset || result.data.projected_geometry.limit !== limit) {
    throw new ErrorClass('INVALID_RESPONSE', '图页投影引用返回无效、不完整或与明确目标不一致。');
  }
  return result.data;
}
