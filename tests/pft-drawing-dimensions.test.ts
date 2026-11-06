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
