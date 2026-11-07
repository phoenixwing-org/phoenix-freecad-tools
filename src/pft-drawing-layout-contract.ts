// SPDX-License-Identifier: Apache-2.0
/** Bounded detached layout data; validation never searches or invokes native code. */
import * as z from 'zod/v4';
import { PftDrawingDimensionEdgeSchema, PftDrawingDimensionKindSchema, PftDrawingDimensionRequestItemSchema,
  PftDrawingDimensionsRequestSchema, PftDrawingNativeNameSchema } from './pft-part-approval.js';

export const PFT_DRAWING_DIMENSION_LAYOUT_CAPABILITY = Object.freeze({ version: 1, operation: 'add_drawing_dimensions',
  algorithm: 'paper_lanes_v1', profile: 'freecad_1_1_4_a4_mm_v1', confirmation: 'native_gui_only', max_dimensions: 8,
  max_input_bytes: 65536, max_result_bytes: 32768, max_plan_bytes: 114688, max_candidates_per_dimension: 64,
  max_search_attempts: 8192, max_geometry_checks: 131072, layout_verification: 'suggested_only', saves_document: false } as const);
export const PftDrawingDimensionLayoutCapabilitySchema = z.object({ version: z.literal(1), operation: z.literal('add_drawing_dimensions'),
  algorithm: z.literal('paper_lanes_v1'), profile: z.literal('freecad_1_1_4_a4_mm_v1'), confirmation: z.literal('native_gui_only'),
  max_dimensions: z.literal(8), max_input_bytes: z.literal(65536), max_result_bytes: z.literal(32768), max_plan_bytes: z.literal(114688),
  max_candidates_per_dimension: z.literal(64), max_search_attempts: z.literal(8192), max_geometry_checks: z.literal(131072),
  layout_verification: z.literal('suggested_only'), saves_document: z.literal(false) }).strict();

export const PftDrawingDimensionAutoIntentSchema = z.object({ edge: PftDrawingDimensionEdgeSchema,
  kind: PftDrawingDimensionKindSchema, placement: z.literal('auto') }).strict();
export const PftDrawingDimensionsIntentSchema = z.object({ page: PftDrawingNativeNameSchema,
  view: PftDrawingNativeNameSchema, dimensions: z.array(z.union([
    PftDrawingDimensionRequestItemSchema, PftDrawingDimensionAutoIntentSchema,
  ])).min(1).max(8),
}).strict().superRefine((value, context) => {
  if (value.page === value.view || new Set(value.dimensions.map(item => item.edge + ':' + item.kind)).size !== value.dimensions.length)
    context.addIssue({ code: 'custom', message: '页和视图须不同，且本批不能重复同一边与尺寸类型' });
});
export type PftDrawingDimensionsIntent = z.output<typeof PftDrawingDimensionsIntentSchema>;
/** Bind a complete resolution without allowing the solver to move explicit rows. */
export const PftDrawingDimensionsResolutionSchema = z.object({ intent: PftDrawingDimensionsIntentSchema,
  resolved: PftDrawingDimensionsRequestSchema,
}).strict().superRefine(({ intent, resolved }, context) => {
  if (intent.page !== resolved.page || intent.view !== resolved.view || intent.dimensions.length !== resolved.dimensions.length
    || intent.dimensions.some((before, index) => {
      const after = resolved.dimensions[index];
      return !after || before.edge !== after.edge || before.kind !== after.kind
        || !('placement' in before) && (before.x_mm !== after.x_mm || before.y_mm !== after.y_mm);
    })) context.addIssue({ code: 'custom', message: '建议须逐项绑定原请求，并保留显式坐标' });
});

const EPS = 1e-8;
const boundedNumber = z.number().finite().min(-1e6).max(1e6);
const coordinate = z.number().finite().min(-1000).max(1000);
const point = z.tuple([boundedNumber, boundedNumber]);
const rect = z.tuple([boundedNumber, boundedNumber, boundedNumber, boundedNumber])
  .refine(value => value[0] < value[2] && value[1] < value[3], '矩形必须有正面积');
const segment = z.tuple([point, point]).refine(value => !samePoint(value[0], value[1]), '线段不能退化');
const labelText = z.string().min(1).max(512).refine(value => Array.from(value).length <= 256
  && (value.match(/\n/gu)?.length ?? 0) <= 3
  && !/[\p{Cc}\p{Cf}\p{Cs}]/u.test(value.replaceAll('\n', '')), '标签文字含不支持字符或过长');
const line = z.object({ curve: z.literal('line'), start_mm: point, end_mm: point }).strict()
  .refine(value => !samePoint(value.start_mm, value.end_mm), '引用线段不能退化');
const circle = z.object({ curve: z.literal('circle'), center_mm: point, radius_mm: z.number().finite().positive().max(1e6) }).strict();
const common = { edge: PftDrawingDimensionEdgeSchema, kind: PftDrawingDimensionKindSchema,
  geometry: z.union([line, circle]), text: labelText,
  label_size_mm: z.tuple([z.number().finite().positive().max(1000), z.number().finite().positive().max(1000)]) };
const inputRow = z.union([
  z.object({ ...common, placement: z.literal('auto') }).strict(),
  z.object({ ...common, placement: z.literal('explicit'), x_mm: coordinate, y_mm: coordinate }).strict(),
]);
function jsonFits(value: unknown, maximum: number): boolean {
  try { const encoded = JSON.stringify(value); return encoded !== undefined && Buffer.byteLength(encoded, 'utf8') <= maximum; }
  catch { return false; }
}
export const PftDrawingLayoutInputSchema = z.object({ version: z.literal(1),
  paper: z.object({ width_mm: z.number().finite().positive().max(10000), height_mm: z.number().finite().positive().max(10000),
    margin_mm: z.number().finite().min(0).max(5000), reserved_rects: z.array(rect).max(32) }).strict(),
  view: z.object({ name: PftDrawingNativeNameSchema, origin_mm: point, bounds_mm: rect }).strict(),
  obstacles: z.object({ rects: z.array(z.object({ name: PftDrawingNativeNameSchema, kind: z.enum(['view', 'dimension']), bounds_mm: rect }).strict()).max(128),
    segments: z.array(segment).max(512) }).strict(),
  spacing: z.object({ clearance_mm: z.number().finite().min(0).max(20), offset_mm: z.number().finite().positive().max(100),
    lane_mm: z.number().finite().positive().max(100) }).strict(),
  dimensions: z.array(inputRow).min(1).max(8),
}).strict().superRefine((value, context) => {
  const names = [value.view.name, ...value.obstacles.rects.map(item => item.name)];
  if (value.paper.margin_mm * 2 >= Math.min(value.paper.width_mm, value.paper.height_mm)
    || new Set(names).size !== names.length
    || new Set(value.dimensions.map(row => row.edge + ':' + row.kind)).size !== value.dimensions.length
    || !jsonFits(value, 65536)) context.addIssue({ code: 'custom', message: '布局输入范围、唯一名称或字节预算无效' });
  for (const row of value.dimensions) {
    const geometry = row.geometry;
    const circular = row.kind === 'Radius' || row.kind === 'Diameter';
    if (circular !== (geometry.curve === 'circle')) {
      context.addIssue({ code: 'custom', message: '尺寸类型与引用几何不一致' }); continue;
    }
    if (geometry.curve === 'line' && (row.kind === 'DistanceX' && geometry.start_mm[0] === geometry.end_mm[0]
      || row.kind === 'DistanceY' && geometry.start_mm[1] === geometry.end_mm[1]))
      context.addIssue({ code: 'custom', message: '尺寸投影量值不能为零' });
    const bounds: Rect = geometry.curve === 'circle'
      ? [geometry.center_mm[0] - geometry.radius_mm, geometry.center_mm[1] - geometry.radius_mm,
        geometry.center_mm[0] + geometry.radius_mm, geometry.center_mm[1] + geometry.radius_mm]
      : [Math.min(geometry.start_mm[0], geometry.end_mm[0]), Math.min(geometry.start_mm[1], geometry.end_mm[1]),
        Math.max(geometry.start_mm[0], geometry.end_mm[0]), Math.max(geometry.start_mm[1], geometry.end_mm[1])];
    if (!inside(value.view.bounds_mm, bounds)) context.addIssue({ code: 'custom', message: '引用几何须位于冻结视图范围' });
  }
});
export type PftDrawingLayoutInput = z.output<typeof PftDrawingLayoutInputSchema>;
const resultRow = z.object({ edge: PftDrawingDimensionEdgeSchema, kind: PftDrawingDimensionKindSchema,
  placement: z.enum(['auto', 'explicit']), x_mm: coordinate, y_mm: coordinate, text: labelText,
  label_box_mm: rect, segments_mm: z.array(segment).max(12) }).strict();
export const PftDrawingLayoutBlockedReasonSchema = z.enum(['OUTSIDE_PAPER', 'RESERVED_REGION', 'LABEL_COLLISION',
  'LINE_COLLISION', 'NO_SPACE', 'SEARCH_BUDGET_EXHAUSTED']);
export const PftDrawingLayoutResultSchema = z.object({ version: z.literal(1), algorithm: z.literal('paper_lanes_v1'), can_apply: z.boolean(),
  dimensions: z.array(resultRow).max(8), blocked: z.array(z.object({ index: z.number().int().min(0).max(7),
    reason: PftDrawingLayoutBlockedReasonSchema }).strict()).max(8), candidates_tested: z.number().int().min(0).max(8192),
  layout_verification: z.literal('suggested_only') }).strict().superRefine((value, context) => {
  if (!jsonFits(value, 32768) || value.can_apply && (value.dimensions.length < 1 || value.blocked.length !== 0)
    || !value.can_apply && (value.dimensions.length !== 0 || value.blocked.length < 1)
    || new Set(value.dimensions.map(row => row.edge + ':' + row.kind)).size !== value.dimensions.length
    || new Set(value.blocked.map(row => row.index)).size !== value.blocked.length)
    context.addIssue({ code: 'custom', message: '布局结果须完整成功或整批阻断且有界' });
});
export type PftDrawingLayoutResult = z.output<typeof PftDrawingLayoutResultSchema>;
type Point = [number, number];
type Rect = [number, number, number, number];
type Segment = [Point, Point];
type InputRow = PftDrawingLayoutInput['dimensions'][number];
type ResultRow = PftDrawingLayoutResult['dimensions'][number];
type RecordSegment = { role: 'extension' | 'dimension' | 'leader'; segment: Segment; anchor: Point | null };
type Candidate = { result: ResultRow; records: RecordSegment[]; anchors: Point[]; circle: { edge: string; center: Point; radius: number } | null };
class Budget {
  private checks = 0;
  tick(): void { if (this.checks++ >= 131072) throw new Error('bounded geometry validation'); }
}
function samePoint(a: Point, b: Point): boolean { return a[0] === b[0] && a[1] === b[1]; }
function distance(a: Point, b: Point): number { return Math.hypot(b[0] - a[0], b[1] - a[1]); }
function inside(a: Rect, b: Rect): boolean { return b[0] >= a[0] - EPS && b[1] >= a[1] - EPS && b[2] <= a[2] + EPS && b[3] <= a[3] + EPS; }
function inflate(a: Rect, gap: number): Rect { return [a[0] - gap, a[1] - gap, a[2] + gap, a[3] + gap]; }
function overlap(a: Rect, b: Rect, gap: number, budget: Budget): boolean {
  budget.tick(); return !(a[2] + gap < b[0] - EPS || b[2] + gap < a[0] - EPS || a[3] + gap < b[1] - EPS || b[3] + gap < a[1] - EPS);
}
function clip(s: Segment, box: Rect, budget?: Budget): Point | null {
  budget?.tick(); let low = 0; let high = 1;
  for (const i of [0, 1] as const) {
    const delta = s[1][i] - s[0][i];
    if (delta === 0) { if (s[0][i] < box[i] || s[0][i] > box[i + 2]!) return null; }
    else { const first = (box[i] - s[0][i]) / delta; const last = (box[i + 2]! - s[0][i]) / delta;
      low = Math.max(low, Math.min(first, last)); high = Math.min(high, Math.max(first, last)); if (low > high) return null; }
  }
  return [low, high];
}
function at(s: Segment, t: number): Point { return [s[0][0] + (s[1][0] - s[0][0]) * t, s[0][1] + (s[1][1] - s[0][1]) * t]; }
function trim(s: Segment, box: Rect): Segment[] {
  const interval = clip(s, box); if (interval === null) return [s]; const result: Segment[] = [];
  if (interval[0] > 0) result.push([s[0], at(s, interval[0])]); if (interval[1] < 1) result.push([at(s, interval[1]), s[1]]); return result;
}
function pointDistance(point: Point, s: Segment): number {
  const length = distance(...s); if (length === 0) return distance(point, s[0]);
  const unit: Point = [(s[1][0] - s[0][0]) / length, (s[1][1] - s[0][1]) / length];
  const extent = Math.max(0, Math.min(length, (point[0] - s[0][0]) * unit[0] + (point[1] - s[0][1]) * unit[1]));
  return Math.hypot(point[0] - s[0][0] - extent * unit[0], point[1] - s[0][1] - extent * unit[1]);
}
function cross(a: Point, b: Point, c: Point): number { return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]); }
function near(a: Segment, b: Segment, gap: number, budget: Budget): boolean {
  budget.tick(); const [c1, c2, c3, c4] = [cross(...a, b[0]), cross(...a, b[1]), cross(...b, a[0]), cross(...b, a[1])];
  if ((c1! > 0 && c2! < 0 || c2! > 0 && c1! < 0) && (c3! > 0 && c4! < 0 || c4! > 0 && c3! < 0)) return true;
  return Math.min(pointDistance(a[0], b), pointDistance(a[1], b), pointDistance(b[0], a), pointDistance(b[1], a)) <= gap + EPS;
}
function candidate(row: InputRow, x: number, y: number, origin: Point, clearance: number): Candidate {
  const center: Point = [origin[0] + x, origin[1] + y]; const [w, h] = row.label_size_mm;
  const box: Rect = [center[0] - w / 2, center[1] - h / 2, center[0] + w / 2, center[1] + h / 2];
  const raw: RecordSegment[] = []; const geometry = row.geometry; let anchors: Point[] = [];
  if (geometry.curve === 'line') {
    const p = geometry.start_mm; const q = geometry.end_mm; const length = distance(p, q);
    const direction: Point = row.kind === 'DistanceX' ? [1, 0] : row.kind === 'DistanceY' ? [0, 1] : [(q[0] - p[0]) / length, (q[1] - p[1]) / length];
    const feet = [p, q].map(point => { const t = (point[0] - center[0]) * direction[0] + (point[1] - center[1]) * direction[1];
      const foot: Point = [center[0] + direction[0] * t, center[1] + direction[1] * t];
      raw.push({ role: 'extension', segment: [point, foot], anchor: point }); return foot; });
    const projections = feet.map(point => (point[0] - center[0]) * direction[0] + (point[1] - center[1]) * direction[1]);
    const low = Math.min(0, ...projections); const high = Math.max(0, ...projections);
    raw.push({ role: 'dimension', segment: [[center[0] + direction[0] * low, center[1] + direction[1] * low],
      [center[0] + direction[0] * high, center[1] + direction[1] * high]], anchor: null }); anchors = [p, q];
  } else {
    const p = geometry.center_mm; const length = distance(p, center); const direction: Point = length ? [(center[0] - p[0]) / length, (center[1] - p[1]) / length] : [1, 0];
    const start: Point = row.kind === 'Diameter' ? [p[0] - direction[0] * geometry.radius_mm, p[1] - direction[1] * geometry.radius_mm] : [...p];
    raw.push({ role: 'leader', segment: [start, center], anchor: null });
  }
  const segments: Segment[] = []; const records: RecordSegment[] = [];
  for (const entry of raw) if (distance(...entry.segment) > EPS) for (const piece of trim(entry.segment, inflate(box, clearance / 2)))
    if (distance(...piece) > EPS) { segments.push(piece); records.push({ ...entry, segment: piece }); }
  return { result: { edge: row.edge, kind: row.kind, placement: row.placement, x_mm: x, y_mm: y, text: row.text, label_box_mm: box, segments_mm: segments },
    records, anchors, circle: geometry.curve === 'circle' ? { edge: row.edge, center: geometry.center_mm, radius: geometry.radius_mm } : null };
}
function staticConflict(c: Candidate, input: PftDrawingLayoutInput, budget: Budget): boolean {
  const { paper, spacing, obstacles, view } = input; const gap = spacing.clearance_mm; const r = c.result;
  const usable: Rect = [paper.margin_mm, paper.margin_mm, paper.width_mm - paper.margin_mm, paper.height_mm - paper.margin_mm];
  budget.tick(); if (!inside(usable, r.label_box_mm)) return true;
  if (r.segments_mm.some(s => s.some(p => p[0] < usable[0] - EPS || p[0] > usable[2] + EPS || p[1] < usable[1] - EPS || p[1] > usable[3] + EPS))) return true;
  for (const rect of paper.reserved_rects) if (overlap(r.label_box_mm, rect, gap, budget) || r.segments_mm.some(s => clip(s, inflate(rect, gap), budget) !== null)) return true;
  if (overlap(r.label_box_mm, view.bounds_mm, gap, budget)) return true;
  for (const { bounds_mm: rect } of obstacles.rects) if (overlap(r.label_box_mm, rect, gap, budget) || r.segments_mm.some(s => clip(s, inflate(rect, gap), budget) !== null)) return true;
  for (const s of obstacles.segments) if (clip(s, inflate(r.label_box_mm, gap), budget) !== null || r.segments_mm.some(other => near(s, other, gap, budget))) return true;
  return false;
}
function shared(a: RecordSegment, b: RecordSegment, left: Candidate, right: Candidate): boolean {
  if (a.role === 'extension' && b.role === 'extension' && a.anchor !== null && b.anchor !== null && samePoint(a.anchor, b.anchor)) return true;
  if (a.role === 'leader' && b.role === 'leader' && left.circle !== null && right.circle !== null
    && left.circle.edge === right.circle.edge && left.circle.radius === right.circle.radius && samePoint(left.circle.center, right.circle.center)) {
    const da: Point = [a.segment[1][0] - a.segment[0][0], a.segment[1][1] - a.segment[0][1]];
    const db: Point = [b.segment[1][0] - b.segment[0][0], b.segment[1][1] - b.segment[0][1]];
    return Math.abs(da[0] * db[1] - da[1] * db[0]) > EPS * distance(...a.segment) * distance(...b.segment)
      && pointDistance(left.circle.center, a.segment) <= EPS && pointDistance(left.circle.center, b.segment) <= EPS;
  }
  if (b.role === 'extension' && a.role === 'dimension') return shared(b, a, right, left);
  if (a.role !== 'extension' || b.role !== 'dimension' || a.anchor === null || !right.anchors.some(p => samePoint(p, a.anchor!))) return false;
  const la = distance(...a.segment); const lb = distance(...b.segment);
  const da: Point = [a.segment[1][0] - a.segment[0][0], a.segment[1][1] - a.segment[0][1]];
  const db: Point = [b.segment[1][0] - b.segment[0][0], b.segment[1][1] - b.segment[0][1]];
  if (la <= EPS || lb <= EPS || Math.abs(da[0] * db[0] + da[1] * db[1]) > EPS * la * lb) return false;
  const unit: Point = [db[0] / lb, db[1] / lb]; const t = (a.anchor[0] - b.segment[0][0]) * unit[0] + (a.anchor[1] - b.segment[0][1]) * unit[1];
  const projection: Point = [b.segment[0][0] + unit[0] * t, b.segment[0][1] + unit[1] * t];
  return pointDistance(projection, a.segment) <= EPS && pointDistance(projection, b.segment) <= EPS;
}
function pairConflict(left: Candidate, right: Candidate, gap: number, budget: Budget): boolean {
  const a = left.result; const b = right.result;
  if (overlap(a.label_box_mm, b.label_box_mm, gap, budget)
    || b.segments_mm.some(s => clip(s, inflate(a.label_box_mm, gap), budget) !== null)
    || a.segments_mm.some(s => clip(s, inflate(b.label_box_mm, gap), budget) !== null)) return true;
  return left.records.some(sa => right.records.some(sb => near(sa.segment, sb.segment, gap, budget) && !shared(sa, sb, left, right)));
}
function closeArray(a: number[], b: number[]): boolean { return a.length === b.length && a.every((n, i) => Math.abs(n - b[i]!) <= EPS); }
/** Validate the derived estimates, fixed obstacles and complete batch, without reflow. */
export function pftDrawingLayoutMatches(input: PftDrawingLayoutInput, result: PftDrawingLayoutResult): boolean {
  if (result.blocked.some(row => row.index >= input.dimensions.length)) return false;
  if (!result.can_apply) return result.dimensions.length === 0;
  if (result.dimensions.length !== input.dimensions.length) return false;
  const budget = new Budget(); const candidates: Candidate[] = [];
  try {
    for (const [i, row] of result.dimensions.entries()) {
      const before = input.dimensions[i]!;
      if (row.edge !== before.edge || row.kind !== before.kind || row.placement !== before.placement || row.text !== before.text
        || before.placement === 'explicit' && (before.x_mm !== row.x_mm || before.y_mm !== row.y_mm)) return false;
      const derived = candidate(before, row.x_mm, row.y_mm, input.view.origin_mm, input.spacing.clearance_mm);
      if (!closeArray(derived.result.label_box_mm, row.label_box_mm) || derived.result.segments_mm.length !== row.segments_mm.length
        || derived.result.segments_mm.some((s, j) => !closeArray(s.flat(), row.segments_mm[j]!.flat()))
        || staticConflict(derived, input, budget) || candidates.some(other => pairConflict(derived, other, input.spacing.clearance_mm, budget))) return false;
      candidates.push(derived);
    }
    return true;
  } catch { return false; }
}
export const PftDrawingLayoutGraphicsSchema = z.object({ version: z.literal(1), input: PftDrawingLayoutInputSchema,
  result: PftDrawingLayoutResultSchema }).strict().superRefine((value, context) => {
  if (!pftDrawingLayoutMatches(value.input, value.result)) context.addIssue({ code: 'custom', message: '冻结计划图与输入、派生框线或障碍不一致' });
});
export const PftDrawingLayoutReasonCodeSchema = z.enum(['DRAWING_LAYOUT_PROFILE_UNSUPPORTED', 'DRAWING_LAYOUT_PAGE_OPEN_REQUIRED',
  'DRAWING_LAYOUT_TEMPLATE_UNSUPPORTED', 'DRAWING_LAYOUT_STYLE_UNSUPPORTED', 'DRAWING_LAYOUT_OBSTACLE_UNAVAILABLE',
  'DRAWING_LAYOUT_PLAN_STALE', 'DRAWING_LAYOUT_INVALID_ARGUMENT', 'DRAWING_LAYOUT_INVALID_RESULT', 'DRAWING_LAYOUT_TOO_LARGE']);
export const PftDrawingDimensionLayoutPreviewSchema = z.object({ version: z.literal(1), layout_verification: z.literal('suggested_only'),
  intent: PftDrawingDimensionsIntentSchema, graphics: PftDrawingLayoutGraphicsSchema.nullable(),
  reason_code: PftDrawingLayoutReasonCodeSchema.nullable() }).strict().superRefine((value, context) => {
  const { intent, graphics } = value;
  if (!jsonFits(value, 114688)) context.addIssue({ code: 'custom', message: '布局预览超过有限字节预算' });
  if (!intent.dimensions.some(row => 'placement' in row)) context.addIssue({ code: 'custom', message: '布局预览必须明确选择自动建议' });
  if (graphics === null) {
    if (value.reason_code === null) context.addIssue({ code: 'custom', message: '缺少计划图必须说明固定原因' }); return;
  }
  if (value.reason_code !== null || graphics.input.view.name !== intent.view || graphics.input.dimensions.length !== intent.dimensions.length
    || intent.dimensions.some((before, i) => { const after = graphics.input.dimensions[i];
      return !after || before.edge !== after.edge || before.kind !== after.kind || after.placement !== ('placement' in before ? 'auto' : 'explicit')
        || !('placement' in before) && (after.placement !== 'explicit' || before.x_mm !== after.x_mm || before.y_mm !== after.y_mm); }))
    context.addIssue({ code: 'custom', message: '布局输入须逐项完整绑定原意图' });
});
export type PftDrawingDimensionLayoutPreview = z.output<typeof PftDrawingDimensionLayoutPreviewSchema>;
/** The same payload budget as the Python private plan, excluding its native context. */
export function pftDrawingLayoutPlanFits(layout: PftDrawingDimensionLayoutPreview, request: unknown): boolean {
  return jsonFits({ intent: layout.intent, request, graphics: layout.graphics }, 114688);
}
