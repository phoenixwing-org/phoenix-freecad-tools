// SPDX-License-Identifier: Apache-2.0
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { PftBridgeError } from '../src/adapters/pft-freecad-client.js';
import { PFT_DRAWING_DIMENSIONS_CAPABILITY, PftDrawingDimensionsCapabilitySchema, PftDrawingDimensionsInputSchema,
  PftDrawingDimensionsPreviewSchema, PftDrawingDimensionsDesignResultSchema, pftPrepareDrawingDimensions } from '../src/pft-drawing-dimensions.js';
import { PFT_PART_APPROVAL_CAPABILITY, PftPartApprovalDataSchema, PftDrawingDimensionEdgeSchema, pftPartApproval } from '../src/pft-part-approval.js';

const input = { document: 'NeutralDocument', operation: 'add_drawing_dimensions' as const, page: 'NeutralPage', view: 'NeutralFront',
  dimensions: [{ edge: 'Edge0', kind: 'Distance' as const, xMm: 0, yMm: 10 }] };
const request = { page: input.page, view: input.view, dimensions: [{ edge: 'Edge0', kind: 'Distance', x_mm: 0, y_mm: 10 }] };
const token = 'neutral_dimension_confirmation01';
const status = { supported_methods: ['prepare_part_changes', 'request_part_change_approval', 'get_part_change_status'],
  part_approvals: PFT_PART_APPROVAL_CAPABILITY, drawing_dimensions: PFT_DRAWING_DIMENSIONS_CAPABILITY };
function preview(blocked = false) {
  return { document: input.document, operation: input.operation, token, state: 'prepared', can_apply: !blocked,
    expires_at: '2030-01-01T00:00:00Z', ttl_seconds: 300, changed_count: blocked ? 0 : 1,
    rows: blocked ? [{ object: input.page, field: 'TechDraw dimension', old: null, new: null, status: 'blocked', reason: 'Neutral unsupported reference' }]
      : [{ object: 'NeutralDimension', field: 'TechDraw dimension', old: null, new: 600., status: 'change',
        ...request.dimensions[0], value_mm: 600., source: 'native_cached_projected_geometry', note: 'Native cached mm; paper mm offsets; manual review required.' }],
    requires_confirmation: true, saves_document: false, storage: 'memory_only', design_request: structuredClone(request) };
}
function designResult() {
  return { kind: 'drawing_dimensions', page: input.page, view: input.view, dimensions: [{ name: 'NeutralDimension',
    ...request.dimensions[0], value_mm: 600. }], measurement_verification: 'native_reference_and_value_checked',
    projection_verification: 'not_checked', manual_review_required: true };
}
function approval(state = 'applied') {
  return { document: input.document, operation: input.operation, token, state, changed_count: 1,
    requires_confirmation: true, saves_document: false, storage: 'memory_only', native_confirmation: ['applying', 'applied'].includes(state),
    result: state === 'applied' ? { changed_count: 1, saves_document: false, design_result: designResult() } : null,
    error_code: state === 'failed' ? 'DIMENSION_VALUE_UNVERIFIED' : null };
}

test('尺寸cap精确冻结五类型、0基编号、8项、纸面相对偏移和原生确认', () => {
  assert.deepEqual(PftDrawingDimensionsCapabilitySchema.parse(PFT_DRAWING_DIMENSIONS_CAPABILITY), PFT_DRAWING_DIMENSIONS_CAPABILITY);
  for (const key of Object.keys(PFT_DRAWING_DIMENSIONS_CAPABILITY)) {
    const missing = { ...PFT_DRAWING_DIMENSIONS_CAPABILITY } as Record<string, unknown>; delete missing[key];
    assert.equal(PftDrawingDimensionsCapabilitySchema.safeParse(missing).success, false);
  }
  for (const value of [{ ...PFT_DRAWING_DIMENSIONS_CAPABILITY, extra: true },
    { ...PFT_DRAWING_DIMENSIONS_CAPABILITY, edge_index_base: 1 }, { ...PFT_DRAWING_DIMENSIONS_CAPABILITY, max_dimensions: 9 },
    { ...PFT_DRAWING_DIMENSIONS_CAPABILITY, kinds: [...PFT_DRAWING_DIMENSIONS_CAPABILITY.kinds].reverse() }])
    assert.equal(PftDrawingDimensionsCapabilitySchema.safeParse(value).success, false);
});

test('尺寸输入严格1至8项，拒绝重复、越界、覆盖和其它操作参数，支持安全Unicode名字', () => {
  const item = input.dimensions[0]!;
  const invalid = [{ ...input, dimensions: [] }, { ...input, dimensions: Array(9).fill(item) }, { ...input, dimensions: [item, item] },
    { ...input, page: input.view }, ...['objects', 'values', 'body', 'confirmed', 'FormatSpec', 'value_mm', 'unknown'].map(key => ({ ...input, [key]: true })),
    ...['Edge-1', 'Edge00', 'Edge2048', 'Edge1;print(1)'].map(edge => ({ ...input, dimensions: [{ ...item, edge }] })),
    ...[true, Infinity, NaN, -1000.001, 1000.001, '0'].map(xMm => ({ ...input, dimensions: [{ ...item, xMm }] })),
    { ...input, dimensions: [{ ...item, kind: 'distance' }] }, { ...input, dimensions: [{ ...item, value_mm: 7 }] },
    { ...input, view: '../View' }];
  for (const value of invalid) assert.equal(PftDrawingDimensionsInputSchema.safeParse(value).success, false);
  assert.equal(PftDrawingDimensionsInputSchema.safeParse({ ...input, document: '示例文档', page: '图页', view: '视图',
    dimensions: Array.from({ length: 8 }, (_, i) => ({ ...item, edge: 'Edge' + i, xMm: -1000, yMm: 1000 })) }).success, true);
  assert.equal(PftDrawingDimensionsInputSchema.safeParse({ ...input, dimensions: [{ ...item, edge: 'Edge2047' }] }).success, true);
});

test('投影边全串匹配拒绝终尾换行及前导零，input/preview/result均限精确0至2047', () => {
  const invalid = ['Edge00', 'Edge01', 'Edge02047', 'Edge2048',
    ...['\n', '\r', '\r\n', '\u2028', '\u2029'].flatMap(tail => ['Edge0' + tail, 'Edge2047' + tail])];
  for (const edge of invalid) {
    assert.equal(PftDrawingDimensionEdgeSchema.safeParse(edge).success, false, JSON.stringify(edge));
    assert.equal(PftDrawingDimensionsInputSchema.safeParse({ ...input, dimensions: [{ ...input.dimensions[0], edge }] }).success, false);
    const draft = preview(); draft.rows[0]!.edge = edge; draft.design_request.dimensions[0]!.edge = edge;
    assert.equal(PftDrawingDimensionsPreviewSchema.safeParse(draft).success, false);
    const result = designResult(); result.dimensions[0]!.edge = edge;
    assert.equal(PftDrawingDimensionsDesignResultSchema.safeParse(result).success, false);
    const applied = approval(); applied.result!.design_result.dimensions[0]!.edge = edge;
    assert.equal(PftPartApprovalDataSchema.safeParse(applied).success, false);
  }
  for (const edge of ['Edge0', 'Edge1', 'Edge2047']) {
    assert.equal(PftDrawingDimensionEdgeSchema.safeParse(edge).success, true);
    assert.equal(PftDrawingDimensionsInputSchema.safeParse({ ...input, dimensions: [{ ...input.dimensions[0], edge }] }).success, true);
    const draft = preview(); draft.rows[0]!.edge = edge; draft.design_request.dimensions[0]!.edge = edge;
    assert.equal(PftDrawingDimensionsPreviewSchema.safeParse(draft).success, true);
    const result = designResult(); result.dimensions[0]!.edge = edge;
    assert.equal(PftDrawingDimensionsDesignResultSchema.safeParse(result).success, true);
  }
});

test('尺寸文档和审批文档拒绝终尾Cc，保留Python合同允许的Unicode分隔字符名称', () => {
  for (const suffix of ['\n', '\r', '\r\n']) {
    const document = input.document + suffix;
    assert.equal(PftDrawingDimensionsInputSchema.safeParse({ ...input, document }).success, false);
    assert.equal(PftDrawingDimensionsPreviewSchema.safeParse({ ...preview(), document }).success, false);
    assert.equal(PftPartApprovalDataSchema.safeParse({ ...approval(), document }).success, false);
  }
  // U+2028 is Zl, not Cc/Cf/Cs; document names keep their established grammar.
  const document = input.document + '\u2028';
  assert.equal(PftDrawingDimensionsInputSchema.safeParse({ ...input, document }).success, true);
  assert.equal(PftDrawingDimensionsPreviewSchema.safeParse({ ...preview(), document }).success, true);
  assert.equal(PftPartApprovalDataSchema.safeParse({ ...approval(), document }).success, true);
});

test('无完整精确运行态cap时零派发，单次映射不授予确认或写入', async () => {
  for (const cap of [undefined, null, {}, { ...PFT_DRAWING_DIMENSIONS_CAPABILITY, projection_verification: 'checked' }]) {
    let calls = 0;
    await assert.rejects(pftPrepareDrawingDimensions(input, { status: async () => ({ ...status, drawing_dimensions: cap }),
      prepare: async () => { calls++; return preview(); } }, PftBridgeError));
    assert.equal(calls, 0);
  }
  const calls: unknown[] = [];
  const value = await pftPrepareDrawingDimensions(input, { status: async () => status,
    prepare: async params => { calls.push(params); return preview(); } }, PftBridgeError);
  assert.deepEqual(calls, [{ docName: input.document, objectNames: [input.page], viewName: input.view,
    operation: input.operation, dimensions: request.dimensions }]);
  assert.deepEqual(value, preview());
});

test('预览保留完整原生缓存量值并逐项绑定请求；畸形或超限不重发', async () => {
  assert.deepEqual(PftDrawingDimensionsPreviewSchema.parse(preview(true)), preview(true));
  for (const mode of ['page', 'view', 'edge', 'offset', 'value', 'partial', 'extra', 'duplicate', 'large']) {
    const value = preview() as any;
    if (mode === 'page') value.design_request.page = 'OtherPage';
    if (mode === 'view') value.design_request.view = 'OtherView';
    if (mode === 'edge') value.rows[0].edge = 'Edge1';
    if (mode === 'offset') value.rows[0].x_mm = 2;
    if (mode === 'value') value.rows[0].value_mm = 1e9 + 1;
    if (mode === 'partial') value.changed_count = 0;
    if (mode === 'extra') value.rows[0].Arbitrary = true;
    if (mode === 'duplicate') value.rows[0].object = input.page;
    if (mode === 'large') value.rows[0].note = 'a'.repeat(128 * 1024);
    let calls = 0;
    await assert.rejects(pftPrepareDrawingDimensions(input, { status: async () => status,
      prepare: async () => { calls++; return value; } }, PftBridgeError), { code: 'INVALID_RESPONSE' });
    assert.equal(calls, 1);
  }
});

test('尺寸结果及确认状态严格绑定kind、节点数量、引用与真实值验证，不伪造投影完成', async () => {
  assert.deepEqual(PftDrawingDimensionsDesignResultSchema.parse(designResult()), designResult());
  assert.deepEqual(PftPartApprovalDataSchema.parse(approval()), approval());
  for (const mode of ['native', 'count', 'kind', 'value', 'projection', 'manual', 'extra']) {
    const value = approval() as any;
    if (mode === 'native') value.native_confirmation = false;
    if (mode === 'count') value.changed_count = value.result.changed_count = 2;
    if (mode === 'kind') value.operation = 'create_drawing';
    if (mode === 'value') value.result.design_result.dimensions[0].value_mm = true;
    if (mode === 'projection') value.result.design_result.projection_verification = 'checked';
    if (mode === 'manual') value.result.design_result.manual_review_required = false;
    if (mode === 'extra') value.result.design_result.dimensions[0].override = 6;
    assert.equal(PftPartApprovalDataSchema.safeParse(value).success, false);
  }
  for (const state of ['prepared', 'awaiting_confirmation', 'applying', 'applied', 'cancelled', 'failed', 'expired'])
    assert.equal(PftPartApprovalDataSchema.safeParse(approval(state)).success, true);
  let calls = 0;
  const value = await pftPartApproval('get_part_change_status', { token }, { status: async () => status,
    call: async () => { calls++; return approval(); } }, PftBridgeError);
  assert.equal(value.state, 'applied'); assert.equal(calls, 1);
});

import { PFT_DRAWING_DIMENSION_LAYOUT_CAPABILITY } from '../src/pft-drawing-layout-contract.js';
import { neutralAutoInput, neutralAutoPreview } from './helpers/pft-drawing-layout-fixture.js';
const layoutStatus = { ...status, drawing_dimension_layout: PFT_DRAWING_DIMENSION_LAYOUT_CAPABILITY };

test('auto须明确opt-in且严格二选一；旧插件不派发，新插件完整映射混合意图', async () => {
  const value = neutralAutoInput();
  const row = value.dimensions[0]!;
  assert.equal(PftDrawingDimensionsInputSchema.safeParse(value).success, true);
  for (const dimensions of [[{ ...row, xMm: 0, yMm: 0 }], [{ ...row, placement: 'explicit' }], [{ edge: row.edge, kind: row.kind }],
    [{ ...row, placement: true }], [{ ...row, x_mm: 0 }]])
    assert.equal(PftDrawingDimensionsInputSchema.safeParse({ ...value, dimensions }).success, false);
  for (const cap of [undefined, null, {}, { ...PFT_DRAWING_DIMENSION_LAYOUT_CAPABILITY, max_search_attempts: 8193 }]) {
    let calls = 0;
    await assert.rejects(pftPrepareDrawingDimensions(value, { status: async () => ({ ...status, drawing_dimension_layout: cap }),
      prepare: async () => { calls++; return neutralAutoPreview(); } }, PftBridgeError),
    { code: cap === undefined ? 'UNSUPPORTED_CAPABILITY' : 'INVALID_RESPONSE' });
    assert.equal(calls, 0);
  }
  const calls: unknown[] = [];
  const response = await pftPrepareDrawingDimensions(value, { status: async () => layoutStatus,
    prepare: async params => { calls.push(params); return neutralAutoPreview(); } }, PftBridgeError);
  assert.deepEqual(calls, [{ docName: value.document, objectNames: [value.page], viewName: value.view,
    operation: value.operation, dimensions: response.layout!.intent.dimensions }]);
  assert.deepEqual(response, neutralAutoPreview());
  assert.equal(response.design_request!.dimensions.at(-1)!.x_mm, 'xMm' in value.dimensions.at(-1)! ? value.dimensions.at(-1)!.xMm : undefined);
});

test('auto成功/无解/原生profile不足保留完整意图，blocked没有伪坐标和部分成功', async () => {
  const success = neutralAutoPreview();
  const blocked = neutralAutoPreview('blocked');
  const unsupported = { ...blocked, layout: { ...blocked.layout!, graphics: null, reason_code: 'DRAWING_LAYOUT_PAGE_OPEN_REQUIRED' as const } };
  for (const [value, args] of [[success, neutralAutoInput()], [blocked, neutralAutoInput('blocked')], [unsupported, neutralAutoInput('blocked')]] as const) {
    assert.deepEqual(PftDrawingDimensionsPreviewSchema.parse(value), value);
    assert.deepEqual(await pftPrepareDrawingDimensions(args, { status: async () => layoutStatus, prepare: async () => value }, PftBridgeError), value);
  }
  assert.equal(blocked.design_request, null); assert.deepEqual(blocked.layout!.graphics!.result.dimensions, []);
  for (const value of [{ ...blocked, design_request: success.design_request }, { ...blocked, changed_count: 1 },
    { ...blocked, can_apply: true }, { ...blocked, rows: success.rows }, { ...blocked, layout: undefined },
    { ...unsupported, layout: { ...unsupported.layout, reason_code: null } },
    { ...success, design_request: null }, { ...success, layout: { ...success.layout!, graphics: null, reason_code: 'DRAWING_LAYOUT_STYLE_UNSUPPORTED' } }])
    assert.equal(PftDrawingDimensionsPreviewSchema.safeParse(value).success, false);
});

test('auto响应逐项绑定原意图与解，拒绝文字/几何/路径/顺序/显式坐标篡改且不重发', async () => {
  const changes: Array<(value: ReturnType<typeof neutralAutoPreview>) => void> = [
    value => { value.layout!.intent.page = 'OtherPage'; },
    value => { value.layout!.intent.dimensions.reverse(); },
    value => { value.design_request!.dimensions[0]!.x_mm += 1e-10; },
    value => { const row = value.layout!.intent.dimensions.at(-1)!; if (!('placement' in row)) row.x_mm += 1e-10; },
    value => { value.layout!.graphics!.input.dimensions[0]!.text = 'Other label'; },
    value => { value.layout!.graphics!.result.dimensions[0]!.label_box_mm[0] += 1; },
    value => { value.rows[0]!.object = value.layout!.intent.page; },
    value => { delete value.layout; },
    value => { value.layout!.graphics!.result.dimensions[0]!.segments_mm[0]![0][0] += .5; },
  ];
  for (const [index, change] of changes.entries()) {
    const value = neutralAutoPreview(); change(value); let calls = 0;
    await assert.rejects(pftPrepareDrawingDimensions(neutralAutoInput(), { status: async () => layoutStatus,
      prepare: async () => { calls++; return value; } }, PftBridgeError), { code: 'INVALID_RESPONSE' }, String(index));
    assert.equal(calls, 1);
  }
  // A whole alternate, internally valid plan cannot replace the submitted request.
  const alternate = neutralAutoPreview('five');
  await assert.rejects(pftPrepareDrawingDimensions(neutralAutoInput(), { status: async () => layoutStatus,
    prepare: async () => alternate }, PftBridgeError), { code: 'INVALID_RESPONSE' });
  // Existing explicit-only calls still neither require nor return a layout field.
  assert.deepEqual(await pftPrepareDrawingDimensions(input, { status: async () => status, prepare: async () => preview() }, PftBridgeError), preview());
  await assert.rejects(pftPrepareDrawingDimensions(input, { status: async () => layoutStatus,
    prepare: async () => ({ ...preview(), layout: neutralAutoPreview().layout }) }, PftBridgeError), { code: 'INVALID_RESPONSE' });
});

test('布局新增固定状态错误不扩测量结果，拒绝任意原生原因/清理正文', async () => {
  for (const error_code of ['DIMENSION_LAYOUT_UNAVAILABLE', 'DIMENSION_LAYOUT_STALE', 'DIMENSION_LAYOUT_STYLE_UNVERIFIED', 'DIMENSION_LAYOUT_CLEANUP_UNVERIFIED']) {
    const value = { ...approval('failed'), error_code };
    assert.equal(PftPartApprovalDataSchema.safeParse(value).success, true);
    assert.deepEqual(await pftPartApproval('get_part_change_status', { token }, { status: async () => status, call: async () => value }, PftBridgeError), value);
  }
  assert.equal(PftPartApprovalDataSchema.safeParse({ ...approval('failed'), error_code: 'DRAWING_LAYOUT_PRIVATE_EXCEPTION' }).success, false);
  assert.equal(PftPartApprovalDataSchema.safeParse({ ...approval(), layout: neutralAutoPreview().layout }).success, false);
});

test('完整auto预览精确128KiB边界，合法内部64KiB/32KiB数据不靠字段长度提前拒绝', () => {
  const value = neutralAutoPreview('bounded_eight');
  value.rows.forEach((row, index) => { if (row.status === 'change') { row.note = '界'.repeat(2048); row.object = '尺'.repeat(60) + index; } });
  value.document = '文'.repeat(256); value.expires_at = '期'.repeat(128);
  value.layout!.intent.page = value.design_request!.page = '页'.repeat(60);
  value.layout!.intent.view = value.design_request!.view = value.layout!.graphics!.input.view.name = '视'.repeat(60);
  const size = () => Buffer.byteLength(JSON.stringify(value));
  const overflow = size() - 128 * 1024;
  assert.ok(overflow > 0 && overflow < 1000);
  const last = value.rows.at(-1)!; assert.equal(last.status, 'change');
  if (last.status !== 'change') throw new Error('neutral fixture expected a successful row');
  const remove = Math.ceil(overflow / 3); last.note = '界'.repeat(2048 - remove) + 'a'.repeat(remove * 3 - overflow);
  assert.equal(size(), 128 * 1024);
  assert.equal(PftDrawingDimensionsPreviewSchema.safeParse(value).success, true);
  last.note += 'a'; assert.equal(size(), 128 * 1024 + 1);
  assert.equal(PftDrawingDimensionsPreviewSchema.safeParse(value).success, false);
});
