// SPDX-License-Identifier: Apache-2.0
/** Strict detached annotation-slot estimates; no native operation or rendering. */
import * as z from 'zod/v4';
import { PftDrawingNativeNameSchema } from './pft-part-approval.js';

export const PFT_DRAWING_DRAFT_LAYOUT_CAPABILITY = Object.freeze({ version: 1, operation: 'create_drawing',
  algorithm: 'annotation_slots_v1', profile: 'freecad_1_1_4_a4_mm_v1', confirmation: 'native_gui_only',
  preview_field: 'draft_layout', max_input_bytes: 4096, max_result_bytes: 16384, saves_document: false,
  layout_verification: 'estimated_geometry_only' } as const);
export const PftDrawingDraftLayoutCapabilitySchema = z.object({ version: z.literal(1), operation: z.literal('create_drawing'),
  algorithm: z.literal('annotation_slots_v1'), profile: z.literal('freecad_1_1_4_a4_mm_v1'), confirmation: z.literal('native_gui_only'),
  preview_field: z.literal('draft_layout'), max_input_bytes: z.literal(4096), max_result_bytes: z.literal(16384),
  saves_document: z.literal(false), layout_verification: z.literal('estimated_geometry_only') }).strict();
const EPS = 1e-8;
const SCALES = [1, .5, .2, .1, .05, .02, .01, .005, .002, .001] as const;
const KINDS = ['front', 'top', 'axonometric'] as const;
const DIRECTIONS = [[0, -1, 0], [0, 0, 1], [1, -1, 1]] as const;
const X_DIRECTIONS = [[1, 0, 0], [1, 0, 0], [1, 1, 0]] as const;
const number = z.number().finite().min(-1e12).max(1e12);
const positive = z.number().finite().positive().max(1e12);
const point = z.tuple([number, number]);
const vector = z.tuple([number, number, number]);
const rect = z.tuple([number, number, number, number]).refine(box => box[0] < box[2] && box[1] < box[3], '估算矩形必须有正面积');
const spans = z.tuple([positive, positive, positive]);
const view = z.object({ name: PftDrawingNativeNameSchema, kind: z.enum(KINDS), direction: vector, x_direction: vector,
  rotation_degrees: z.literal(0), center_mm: point, projected_span_mm: z.tuple([positive, positive]),
  estimated_geometry_bounds_mm: rect, annotation_envelope_mm: rect }).strict();
function fits(value: unknown, bytes: number): boolean {
  try { const encoded = JSON.stringify(value); return encoded !== undefined && Buffer.byteLength(encoded, 'utf8') <= bytes; }
  catch { return false; }
}
function close(a: readonly number[], b: readonly number[]): boolean {
  return a.length === b.length && a.every((n, i) => Math.abs(n - b[i]!) <= EPS);
}
type Rect = [number, number, number, number];
function bounds(center: readonly number[], size: readonly number[]): Rect {
  return [center[0]! - size[0]! / 2, center[1]! - size[1]! / 2, center[0]! + size[0]! / 2, center[1]! + size[1]! / 2];
}
function inflate(box: Rect): Rect { return [box[0] - 20, box[1] - 20, box[2] + 20, box[3] + 20]; }
function inside(box: Rect): boolean { return box[0] >= 10 - EPS && box[1] >= 81 - EPS && box[2] <= 287 + EPS && box[3] <= 200 + EPS; }
function overlap(a: Rect, b: Rect): boolean { return !(a[2] <= b[0] + EPS || b[2] <= a[0] + EPS || a[3] <= b[1] + EPS || b[3] <= a[1] + EPS); }
export const PftDrawingDraftLayoutSchema = z.object({ version: z.literal(1), algorithm: z.literal('annotation_slots_v1'),
  source: PftDrawingNativeNameSchema, page: PftDrawingNativeNameSchema, source_spans_mm: spans,
  paper: z.object({ width_mm: z.literal(297), height_mm: z.literal(210), margin_mm: z.literal(10),
    reserved_rects_mm: z.tuple([z.tuple([z.literal(0), z.literal(0), z.literal(297), z.literal(80)])]),
    usable_bounds_mm: z.tuple([z.literal(10), z.literal(81), z.literal(287), z.literal(200)]) }).strict(),
  annotation: z.object({ margin_mm: z.literal(20), gutter_mm: z.literal(8), title_clearance_mm: z.literal(1),
    unit: z.literal('paper_mm'), verification: z.literal('reserved_space_only') }).strict(),
  scale: z.number().finite().refine(n => SCALES.some(scale => n === scale), '必须使用原有有限比例'),
  views: z.tuple([view, view, view]), geometry_source: z.literal('source_global_aabb'),
  layout_verification: z.literal('estimated_geometry_only'), projection_verification: z.literal('not_checked'),
  manual_review_required: z.literal(true) }).strict().superRefine((value, context) => {
  const names = [value.page, ...value.views.map(item => item.name)];
  if (new Set(names).size !== names.length || names.includes(value.source)
    || !fits({ source: value.source, page: value.page, view_names: value.views.map(item => item.name), source_spans_mm: value.source_spans_mm }, 4096)
    || !fits(value, 16384)) context.addIssue({ code: 'custom', message: '新节点绑定或草稿布局字节预算无效' });
  const [x, y, z] = value.source_spans_mm;
  const raw = [[x, z], [x, y], [(x + y) / Math.sqrt(2), (x + y + 2 * z) / Math.sqrt(6)]] as const;
  function dimensions(scale: number) {
    const projected = raw.map(size => [size[0] * scale, size[1] * scale] as [number, number]);
    const leftWidth = Math.max(projected[0]![0], projected[1]![0]) + 40;
    const leftHeight = projected[0]![1] + projected[1]![1] + 88;
    const rightWidth = projected[2]![0] + 40; const rightHeight = projected[2]![1] + 40;
    return { projected, leftWidth, leftHeight, rightWidth, rightHeight, totalWidth: leftWidth + 8 + rightWidth };
  }
  const largest = SCALES.find(scale => { const d = dimensions(scale); return d.totalWidth <= 277 && Math.max(d.leftHeight, d.rightHeight) <= 119; });
  if (largest === undefined || value.scale !== largest) { context.addIssue({ code: 'custom', message: '未采用可用的最大既定比例或无法完整留白' }); return; }
  const d = dimensions(value.scale); const left = 10 + (277 - d.totalWidth) / 2; const bottom = 81 + (119 - d.leftHeight) / 2;
  const centerX = left + d.leftWidth / 2;
  const centers = [[centerX, bottom + d.projected[1]![1] + 40 + 8 + (d.projected[0]![1] + 40) / 2],
    [centerX, bottom + (d.projected[1]![1] + 40) / 2], [left + d.leftWidth + 8 + d.rightWidth / 2, 140.5]];
  for (const [index, row] of value.views.entries()) {
    const estimated = bounds(centers[index]!, d.projected[index]!); const envelope = inflate(estimated);
    if (row.kind !== KINDS[index] || !row.direction.every((n, i) => n === DIRECTIONS[index]![i])
      || !row.x_direction.every((n, i) => n === X_DIRECTIONS[index]![i]) || !close(row.center_mm, centers[index]!)
      || !close(row.projected_span_mm, d.projected[index]!) || !close(row.estimated_geometry_bounds_mm, estimated)
      || !close(row.annotation_envelope_mm, envelope) || !inside(row.annotation_envelope_mm)
      || estimated[0] >= estimated[2] || estimated[1] >= estimated[3])
      context.addIssue({ code: 'custom', message: '视图顺序、方向、比例、中心或派生留白框不一致' });
    if (value.views.slice(0, index).some(previous => overlap(row.annotation_envelope_mm, previous.annotation_envelope_mm)))
      context.addIssue({ code: 'custom', message: '三视图注释区域不能互相重叠' });
  }
});
export type PftDrawingDraftLayout = z.output<typeof PftDrawingDraftLayoutSchema>;
export const PftDrawingDraftLayoutReasonSchema = z.enum(['DRAWING_DRAFT_LAYOUT_UNAVAILABLE', 'DRAWING_DRAFT_LAYOUT_NO_SPACE', 'DRAWING_DRAFT_LAYOUT_STALE']);
export const PftDrawingDraftLayoutPairSchema = z.object({ draft_layout: PftDrawingDraftLayoutSchema.nullable(),
  draft_layout_reason: PftDrawingDraftLayoutReasonSchema.nullable() }).strict().superRefine((value, context) => {
  if ((value.draft_layout === null) !== (value.draft_layout_reason !== null))
    context.addIssue({ code: 'custom', message: '完整布局与整批阻断原因必须成对返回' });
});
const text = z.string().max(4096).refine(value => Array.from(value).length <= 2048, '草稿描述超出有界长度');
const successRow = z.object({ object: PftDrawingNativeNameSchema, field: z.literal('TechDraw draft node'), old: z.null(), new: text.min(1),
  status: z.literal('change'), reason: z.literal(''), source: z.literal('cached_native_typed_snapshot'), note: text.min(1) }).strict();
const blockedRow = z.object({ object: PftDrawingNativeNameSchema, field: z.literal('TechDraw draft node'), old: z.null(), new: z.null(),
  status: z.literal('blocked'), reason: text.min(1) }).strict();
export const PftDrawingDraftLayoutPreviewBindingSchema = z.object({ pair: PftDrawingDraftLayoutPairSchema,
  request: z.object({ source: PftDrawingNativeNameSchema, page: PftDrawingNativeNameSchema.nullable() }).strict(),
  rows: z.array(z.union([successRow, blockedRow])).min(1).max(5), can_apply: z.boolean() }).strict().superRefine((value, context) => {
  const layout = value.pair.draft_layout;
  if (!value.can_apply) {
    if (layout !== null || value.pair.draft_layout_reason === null || value.request.page !== null || value.rows.length !== 1
      || value.rows[0]!.status !== 'blocked' || value.rows[0]!.object !== value.request.source)
      context.addIssue({ code: 'custom', message: '草稿布局阻断不能包含部分新节点或坐标' });
    return;
  }
  const names = value.rows.map(row => row.object);
  if (layout === null || value.pair.draft_layout_reason !== null || value.request.page === null || value.rows.length !== 5
    || value.rows.some(row => row.status !== 'change') || new Set(names).size !== 5 || names.includes(value.request.source)
    || layout.source !== value.request.source || layout.page !== value.request.page || names[0] !== value.request.page
    || layout.views.some((view, index) => view.name !== names[index + 2]))
    context.addIssue({ code: 'custom', message: '草稿布局必须绑定source/page及原顺序五个新节点' });
});
