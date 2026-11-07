// SPDX-License-Identifier: Apache-2.0
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { PftDrawingDimensionsIntentSchema, PftDrawingDimensionsResolutionSchema } from '../src/pft-drawing-layout-contract.js';
import { PftDrawingDimensionsRequestSchema } from '../src/pft-part-approval.js';

const intent = { page: 'NeutralPage', view: 'NeutralView', dimensions: [
  { edge: 'Edge0', kind: 'Distance', placement: 'auto' },
  { edge: 'Edge17', kind: 'Diameter', x_mm: 30, y_mm: -25 },
] };
const resolved = { page: intent.page, view: intent.view, dimensions: [
  { edge: 'Edge0', kind: 'Distance', x_mm: 45.25, y_mm: -18.75 },
  { edge: 'Edge17', kind: 'Diameter', x_mm: 30, y_mm: -25 },
] };

test('布局意图须明确选择auto或提供完整坐标，原manual合同保持', () => {
  assert.deepEqual(PftDrawingDimensionsIntentSchema.parse(intent), intent);
  assert.deepEqual(PftDrawingDimensionsIntentSchema.parse(resolved), resolved);
  assert.equal(PftDrawingDimensionsRequestSchema.safeParse(intent).success, false);
  for (const row of [
    { edge: 'Edge0', kind: 'Distance' }, { edge: 'Edge0', kind: 'Distance', x_mm: 0 },
    { edge: 'Edge0', kind: 'Distance', placement: 'explicit' },
    { edge: 'Edge0', kind: 'Distance', placement: true },
    { edge: 'Edge0', kind: 'Distance', placement: 'auto', x_mm: 0, y_mm: 1 },
    { edge: 'Edge0', kind: 'Distance', placement: 'auto', extra: true },
  ]) assert.equal(PftDrawingDimensionsIntentSchema.safeParse({ ...intent, dimensions: [row] }).success, false);
});

test('布局意图限制种类、规范边号、唯一引用、批量和安全内部名称', () => {
  const row = intent.dimensions[0]!;
  for (const dimensions of [[], Array(9).fill(row), [row, row],
    [row, { edge: 'Edge0', kind: 'Distance', x_mm: 1, y_mm: 2 }],
    ...['Edge00', 'Edge2048', 'Edge0\n'].map(edge => [{ ...row, edge }]),
    ...['DistanceZ', true].map(kind => [{ ...row, kind }]),
  ]) assert.equal(PftDrawingDimensionsIntentSchema.safeParse({ ...intent, dimensions }).success, false);
  for (const page of ['../Page', 'Page\n', intent.view])
    assert.equal(PftDrawingDimensionsIntentSchema.safeParse({ ...intent, page }).success, false);
  assert.equal(PftDrawingDimensionsIntentSchema.safeParse({ ...intent, page: '图页', view: '视图',
    dimensions: Array.from({ length: 8 }, (_, i) => ({ ...row, edge: 'Edge' + i })) }).success, true);
});

test('解出的坐标完整绑定顺序、页、视图、引用和显式位置，不能夹带未解项', () => {
  const value = PftDrawingDimensionsResolutionSchema.parse({ intent, resolved });
  assert.deepEqual(value, { intent, resolved });
  value.resolved.dimensions[0]!.x_mm = 0;
  assert.equal(resolved.dimensions[0]!.x_mm, 45.25);
  for (const mode of ['page', 'view', 'count', 'order', 'edge', 'kind', 'explicitX', 'explicitY', 'unresolved', 'extra']) {
    const changed: any = structuredClone(resolved);
    if (mode === 'page' || mode === 'view') changed[mode] = 'DifferentOwner';
    else if (mode === 'count') changed.dimensions.pop();
    else if (mode === 'order') changed.dimensions.reverse();
    else if (mode === 'edge') changed.dimensions[0].edge = 'Edge1';
    else if (mode === 'kind') changed.dimensions[0].kind = 'DistanceX';
    else if (mode === 'explicitX') changed.dimensions[1].x_mm += 1e-10;
    else if (mode === 'explicitY') changed.dimensions[1].y_mm += 1e-10;
    else if (mode === 'unresolved') changed.dimensions[0] = structuredClone(intent.dimensions[0]);
    else changed.dimensions[0].placement = 'auto';
    assert.equal(PftDrawingDimensionsResolutionSchema.safeParse({ intent, resolved: changed }).success, false, mode);
  }
});

import { PFT_DRAWING_DIMENSION_LAYOUT_CAPABILITY, PftDrawingDimensionLayoutCapabilitySchema,
  PftDrawingLayoutInputSchema, PftDrawingLayoutResultSchema, PftDrawingLayoutGraphicsSchema,
  PftDrawingDimensionLayoutPreviewSchema } from '../src/pft-drawing-layout-contract.js';
import { neutralLayoutCases } from './helpers/pft-drawing-layout-fixture.js';

test('布局cap精确固定profile/算法/资源预算，不改五类型的原尺寸cap', () => {
  assert.deepEqual(PftDrawingDimensionLayoutCapabilitySchema.parse(PFT_DRAWING_DIMENSION_LAYOUT_CAPABILITY), PFT_DRAWING_DIMENSION_LAYOUT_CAPABILITY);
  for (const key of Object.keys(PFT_DRAWING_DIMENSION_LAYOUT_CAPABILITY)) {
    const changed: Record<string, unknown> = { ...PFT_DRAWING_DIMENSION_LAYOUT_CAPABILITY }; delete changed[key];
    assert.equal(PftDrawingDimensionLayoutCapabilitySchema.safeParse(changed).success, false);
  }
  for (const extra of [{ extra: true }, { max_search_attempts: 8193 }, { profile: 'unbounded' }, { confirmation: 'external' }])
    assert.equal(PftDrawingDimensionLayoutCapabilitySchema.safeParse({ ...PFT_DRAWING_DIMENSION_LAYOUT_CAPABILITY, ...extra }).success, false);
});

test('真实纯Python求解的五类、四比例、同圆与短边共享投影接点跨语言通过', () => {
  const cases = neutralLayoutCases();
  assert.equal(cases.length, 11);
  for (const row of cases) {
    const parsed = PftDrawingLayoutGraphicsSchema.parse(row.graphics);
    assert.deepEqual(parsed, row.graphics, row.name);
    assert.equal(parsed.result.can_apply, row.name !== 'blocked', row.name);
    assert.equal(parsed.result.layout_verification, 'suggested_only');
  }
});

test('计划图严格绑定完整字段、派生框线、文字、顺序及显式位置', () => {
  const base = neutralLayoutCases().find(row => row.name === 'mixed')!.graphics;
  const mutations: Array<(value: typeof base) => void> = [
    value => { value.input.dimensions[0]!.edge = 'Edge99'; },
    value => { value.input.dimensions.reverse(); },
    value => { value.result.dimensions[0]!.text = 'Other text'; },
    value => { value.result.dimensions[0]!.label_box_mm[0] += .25; },
    value => { value.result.dimensions[0]!.segments_mm[0]![0][0] += .25; },
    value => { value.result.dimensions[0]!.x_mm += .25; },
    value => { const item = value.input.dimensions.at(-1)!; if (item.placement === 'explicit') item.x_mm += 1e-10; },
    value => { value.result.dimensions.pop(); },
    value => { value.result.blocked = [{ index: 0, reason: 'NO_SPACE' }]; },
    value => { value.input.paper.reserved_rects = [[0, 0, 297, 210]]; },
    value => { value.input.obstacles.rects.push({ name: 'ForeignView', kind: 'view', bounds_mm: [...value.result.dimensions[0]!.label_box_mm] }); },
    value => { value.input.obstacles.segments.push([...value.result.dimensions[0]!.segments_mm[0]!] as [[number, number], [number, number]]); },
  ];
  for (const [index, mutate] of mutations.entries()) {
    const value = structuredClone(base); mutate(value);
    assert.equal(PftDrawingLayoutGraphicsSchema.safeParse(value).success, false, String(index));
  }
  for (const extra of [{ confirmed: true }, { version: true }, { raw_native: {} }])
    assert.equal(PftDrawingLayoutGraphicsSchema.safeParse({ ...base, ...extra }).success, false);
  const falseResult = structuredClone(base); falseResult.result.can_apply = false;
  assert.equal(PftDrawingLayoutGraphicsSchema.safeParse(falseResult).success, false);
});

test('同心不同边和同圆外部重合不借引用接点规则通过，非零标注几何始终要求', () => {
  const value = neutralLayoutCases().find(row => row.name === 'same_circle')!.graphics;
  value.input.dimensions[1]!.edge = value.result.dimensions[1]!.edge = 'Edge3';
  assert.equal(PftDrawingLayoutGraphicsSchema.safeParse(value).success, false);
  const input = neutralLayoutCases()[0]!.graphics.input;
  const line = input.dimensions[0]!.geometry;
  if (line.curve === 'line') line.end_mm = [...line.start_mm];
  assert.equal(PftDrawingLayoutInputSchema.safeParse(input).success, false);
});

test('布局输入/结果预算有界、整批失败不夹带部分坐标和越界原因行', () => {
  const base = neutralLayoutCases()[0]!.graphics;
  const oversized = structuredClone(base.input);
  oversized.obstacles.segments = Array.from({ length: 512 }, (_, i) => [
    [10000.12345678901 + i, 11000.12345678901 + i], [12000.12345678901 + i, 13000.12345678901 + i],
  ]);
  oversized.obstacles.rects = Array.from({ length: 128 }, (_, i) => ({ name: '障'.repeat(60) + i, kind: 'view' as const,
    bounds_mm: [10000.12345678901 + i, 11000.12345678901 + i, 12000.12345678901 + i, 13000.12345678901 + i] }));
  assert.ok(Buffer.byteLength(JSON.stringify(oversized)) > 65536);
  assert.equal(PftDrawingLayoutInputSchema.safeParse(oversized).success, false);
  for (const extra of [{ candidates_tested: 8193 }, { candidates_tested: true }, { layout_verification: 'native_ready' },
    { dimensions: Array(9).fill(base.result.dimensions[0]) }, { blocked: [{ index: 8, reason: 'NO_SPACE' }] }])
    assert.equal(PftDrawingLayoutResultSchema.safeParse({ ...base.result, ...extra }).success, false);
  const blocked = neutralLayoutCases().find(row => row.name === 'blocked')!.graphics;
  blocked.result.blocked[0]!.index = 7;
  assert.equal(PftDrawingLayoutGraphicsSchema.safeParse(blocked).success, false);
});

test('layout缺图形必须固定原因，有图形绑定原意图且不接受原生私有字段', () => {
  const graphics = neutralLayoutCases()[0]!.graphics;
  const intent = { page: 'NeutralPage', view: graphics.input.view.name, dimensions: graphics.input.dimensions.map(row =>
    row.placement === 'auto' ? { edge: row.edge, kind: row.kind, placement: 'auto' as const }
      : { edge: row.edge, kind: row.kind, x_mm: row.x_mm, y_mm: row.y_mm }) };
  const layout = { version: 1, layout_verification: 'suggested_only', intent, graphics, reason_code: null };
  assert.equal(PftDrawingDimensionLayoutPreviewSchema.safeParse(layout).success, true);
  assert.equal(PftDrawingDimensionLayoutPreviewSchema.safeParse({ ...layout, graphics: null, reason_code: 'DRAWING_LAYOUT_PAGE_OPEN_REQUIRED' }).success, true);
  for (const changed of [{ ...layout, graphics: null }, { ...layout, reason_code: 'private exception text' },
    { ...layout, reason_code: 'DRAWING_LAYOUT_PROFILE_UNSUPPORTED' }, { ...layout, native_context: {} },
    { ...layout, intent: { ...intent, view: 'OtherView' } }, { ...layout, layout_verification: 'native_ready' }])
    assert.equal(PftDrawingDimensionLayoutPreviewSchema.safeParse(changed).success, false);
});
