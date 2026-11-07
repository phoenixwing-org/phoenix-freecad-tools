// SPDX-License-Identifier: Apache-2.0
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { PFT_DRAWING_DRAFT_LAYOUT_CAPABILITY, PftDrawingDraftLayoutCapabilitySchema,
  PftDrawingDraftLayoutSchema, PftDrawingDraftLayoutPairSchema, PftDrawingDraftLayoutPreviewBindingSchema } from '../src/pft-draft-layout-contract.js';
import { neutralDraftLayouts } from './helpers/pft-draft-layout-fixture.js';

function changed(value: unknown, path: readonly (string | number)[], replacement: unknown): unknown {
  const result: unknown = structuredClone(value); let current = result;
  for (const part of path.slice(0, -1)) {
    assert(current !== null && typeof current === 'object'); current = Reflect.get(current, String(part));
  }
  assert(current !== null && typeof current === 'object'); Reflect.set(current, String(path.at(-1)), replacement); return result;
}
function binding() {
  const layout = neutralDraftLayouts().plans[0]!;
  const names = [layout.page, 'NeutralTemplate', ...layout.views.map(view => view.name)];
  return { pair: { draft_layout: layout, draft_layout_reason: null }, request: { source: layout.source, page: layout.page },
    rows: names.map(object => ({ object, field: 'TechDraw draft node', old: null, new: 'Neutral draft node', status: 'change',
      reason: '', source: 'cached_native_typed_snapshot', note: 'Source global AABB estimate, native projection remains unchecked.' })), can_apply: true };
}

test('新草稿cap精确独立且与Python相同，不承诺尺寸或已核验投影', () => {
  assert.deepEqual(PftDrawingDraftLayoutCapabilitySchema.parse(PFT_DRAWING_DRAFT_LAYOUT_CAPABILITY), PFT_DRAWING_DRAFT_LAYOUT_CAPABILITY);
  assert.deepEqual(neutralDraftLayouts().capability, PFT_DRAWING_DRAFT_LAYOUT_CAPABILITY);
  for (const key of Object.keys(PFT_DRAWING_DRAFT_LAYOUT_CAPABILITY)) {
    const value: Record<string, unknown> = { ...PFT_DRAWING_DRAFT_LAYOUT_CAPABILITY }; delete value[key];
    assert.equal(PftDrawingDraftLayoutCapabilitySchema.safeParse(value).success, false);
  }
  for (const patch of [{ algorithm: 'other' }, { profile: 'any_A4' }, { max_input_bytes: 8192 }, { max_result_bytes: 32768 },
    { confirmation: 'external' }, { saves_document: true }, { extra: true }, { layout_verification: 'native_rendered' }])
    assert.equal(PftDrawingDraftLayoutCapabilitySchema.safeParse({ ...PFT_DRAWING_DRAFT_LAYOUT_CAPABILITY, ...patch }).success, false);
});

test('Python实际纯计划8组跨语言：普通/小/大/细长/高柱及最大比例边界', () => {
  const { plans } = neutralDraftLayouts(); assert.equal(plans.length, 8);
  for (const value of plans) assert.deepEqual(PftDrawingDraftLayoutSchema.parse(value), value);
  const ordinary = plans[0]!;
  assert.equal(ordinary.scale, .5);
  assert.deepEqual(ordinary.views.map(view => view.center_mm), [[106.82233047033631, 174.5], [106.82233047033631, 114.5], [187.5, 140.5]]);
  assert.equal(plans[6]!.scale, 1); assert.equal(plans[7]!.scale, .5);
  assert.equal(ordinary.manual_review_required, true); assert.equal(ordinary.projection_verification, 'not_checked');
});

test('policy常量/单位/方向/名称/顺序/比例和派生框严格核对，不把旧view位置当留白', () => {
  const original = neutralDraftLayouts().plans[0]!;
  const changes: Array<[Array<string | number>, unknown]> = [
    [['version'], true], [['algorithm'], 'geometry_only'], [['source'], original.page], [['page'], original.views[0]!.name],
    [['source_spans_mm', 0], 61], [['source_spans_mm', 0], true], [['source_spans_mm', 1], 0],
    [['source_spans_mm', 2], Infinity], [['source_spans_mm', 2], 1e12 + 1],
    [['paper', 'reserved_rects_mm'], [[10, 10, 287, 80]]], [['paper', 'usable_bounds_mm', 1], 80],
    [['paper', 'width_mm'], 297 + 1e-10], [['annotation', 'margin_mm'], 10], [['annotation', 'gutter_mm'], 4],
    [['annotation', 'unit'], 'model_mm'], [['annotation', 'verification'], 'native_ready'],
    [['scale'], .2], [['views', 0, 'kind'], 'top'], [['views', 0, 'direction'], [0, 1, 0]],
    [['views', 2, 'x_direction'], [1 / Math.sqrt(2), 1 / Math.sqrt(2), 0]],
    [['views', 1, 'rotation_degrees'], 1e-10], [['views', 0, 'center_mm'], [85, 160]],
    [['views', 0, 'estimated_geometry_bounds_mm', 0], 1], [['views', 1, 'annotation_envelope_mm', 1], 80],
    [['views', 2, 'projected_span_mm', 0], 3], [['views', 1, 'name'], original.views[0]!.name],
    [['views'], original.views.slice(0, 2)], [['views', 0, 'file_path'], '/neutral/unsupported.svg'],
    [['manual_review_required'], false], [['projection_verification'], 'verified'], [['extra'], {}],
  ];
  for (const [path, replacement] of changes)
    assert.equal(PftDrawingDraftLayoutSchema.safeParse(changed(original, path, replacement)).success, false, path.join('.'));
});

test('派生绝对容差只限估算几何，保留原DTO且返回detached；原政策仍精确', () => {
  const value = neutralDraftLayouts().plans[0]!; value.views[0]!.center_mm[0] += 5e-9;
  const returned = PftDrawingDraftLayoutSchema.parse(value);
  assert.equal(returned.views[0]!.center_mm[0], value.views[0]!.center_mm[0]);
  returned.views[0]!.center_mm[0] += 1; assert.notEqual(returned.views[0]!.center_mm[0], value.views[0]!.center_mm[0]);
  for (const path of [['scale'], ['annotation', 'margin_mm'], ['views', 0, 'rotation_degrees']] as const)
    assert.equal(PftDrawingDraftLayoutSchema.safeParse(changed(value, path, 1e-10)).success, false);
});

test('paired字段必须完整成功或单个blocked；source/page/原顺序5新名与8键row完整绑定', () => {
  const value = binding(); assert.equal(PftDrawingDraftLayoutPreviewBindingSchema.safeParse(value).success, true);
  const changes: Array<[Array<string | number>, unknown]> = [
    [['request', 'source'], 'OtherSource'], [['request', 'page'], 'OtherPage'], [['rows', 0, 'object'], 'OtherPage'],
    [['rows', 1, 'object'], value.rows[2]!.object], [['rows', 2, 'object'], value.rows[3]!.object],
    [['rows', 0, 'field'], 'untrusted'], [['rows', 0, 'old'], 1], [['rows', 1, 'new'], null],
    [['rows', 3, 'source'], 'arbitrary'], [['rows', 1, 'reason'], 'foreign'], [['rows', 4, 'extra'], true],
    [['rows', 2, 'note'], 'x'.repeat(2049)], [['rows'], value.rows.slice(1)],
    [['pair', 'draft_layout_reason'], 'DRAWING_DRAFT_LAYOUT_NO_SPACE'], [['can_apply'], false],
  ];
  for (const [path, replacement] of changes)
    assert.equal(PftDrawingDraftLayoutPreviewBindingSchema.safeParse(changed(value, path, replacement)).success, false, path.join('.'));
  for (const reason of ['DRAWING_DRAFT_LAYOUT_UNAVAILABLE', 'DRAWING_DRAFT_LAYOUT_NO_SPACE', 'DRAWING_DRAFT_LAYOUT_STALE']) {
    const blocked = { pair: { draft_layout: null, draft_layout_reason: reason }, request: { source: 'NeutralPart', page: null },
      rows: [{ object: 'NeutralPart', field: 'TechDraw draft node', old: null, new: null, status: 'blocked', reason: 'Neutral complete refusal.' }], can_apply: false };
    assert.equal(PftDrawingDraftLayoutPreviewBindingSchema.safeParse(blocked).success, true);
    assert.equal(PftDrawingDraftLayoutPreviewBindingSchema.safeParse(changed(blocked, ['rows', 0, 'source'], 'cached_native_typed_snapshot')).success, false);
    assert.equal(PftDrawingDraftLayoutPreviewBindingSchema.safeParse(changed(blocked, ['rows', 0, 'object'], 'OtherSource')).success, false);
  }
  for (const pair of [{}, { draft_layout: null }, { draft_layout: null, draft_layout_reason: null },
    { draft_layout: null, draft_layout_reason: 'private exception text' },
    { draft_layout: value.pair.draft_layout, draft_layout_reason: 'DRAWING_DRAFT_LAYOUT_UNAVAILABLE' }])
    assert.equal(PftDrawingDraftLayoutPairSchema.safeParse(pair).success, false);
});
