// SPDX-License-Identifier: Apache-2.0
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { PftBridgeError } from '../src/adapters/pft-freecad-client.js';
import { PFT_PAD_LENGTH_CAPABILITY, PftPadLengthCapabilitySchema, PftPadLengthInputSchema,
  pftPreparePadLength } from '../src/pft-pad-length.js';
import { PFT_PART_APPROVAL_CAPABILITY, PftPartApprovalCapabilitySchema, PftPartApprovalDataSchema,
  pftPartApproval, type PftPartApprovalMethod } from '../src/pft-part-approval.js';

const input = { document: 'PublicSample', objects: ['Pad'], operation: 'set_pad_length' as const,
  body: 'Body', lengthMm: 12 };
const token = 'pft_pad_length_confirmation01';
const methods = ['prepare_part_changes', 'request_part_change_approval', 'get_part_change_status'];
const approvalMethods: PftPartApprovalMethod[] = ['request_part_change_approval', 'get_part_change_status'];
const status = { supported_methods: methods, part_approvals: PFT_PART_APPROVAL_CAPABILITY,
  pad_length_write: PFT_PAD_LENGTH_CAPABILITY };
const expectedCapability = { version: 1, operation: 'set_pad_length', confirmation: 'native_gui_only',
  preview_method: 'prepare_part_changes', request_method: 'request_part_change_approval',
  status_method: 'get_part_change_status', length_unit: 'mm', minimum_mm: 0.01, maximum_mm: 10000,
  model_scope: 'single_document_first_pad', max_open_documents: 1, saves_document: false };
const geometry = () => ({ volume_mm3: 2400, dimensions_mm: [20, 10, 12], solids: 1 });
const designResult = () => ({ kind: 'pad_length', body: 'Body', pad: 'Pad', length_mm: 12,
  recompute_count: 2, checked_objects: 3, geometry: { pad: geometry(), body: geometry() },
  validation: 'native_valid_one_solid_and_length_scaled_volume' });
const appliedResult = (design: unknown = designResult()) => ({ changed_count: 1, saves_document: false,
  design_result: design });
function padStatus(state = 'applied') {
  return { document: input.document, operation: input.operation, token, state, changed_count: 1,
    requires_confirmation: true, saves_document: false, storage: 'memory_only',
    native_confirmation: state === 'applying' || state === 'applied',
    result: state === 'applied' ? appliedResult() : null,
    error_code: state === 'failed' ? 'PAD_GEOMETRY_VERIFY_FAILED' : null };
}

// All transports below are in-memory fakes; no FreeCAD process or service is used.
test('Pad能力独立且exact，旧part_approvals v1保持原合同', () => {
  assert.deepEqual(PFT_PAD_LENGTH_CAPABILITY, expectedCapability);
  assert.deepEqual(PftPadLengthCapabilitySchema.parse(expectedCapability), expectedCapability);
  assert.deepEqual(PFT_PART_APPROVAL_CAPABILITY, { version: 1,
    request_method: 'request_part_change_approval', status_method: 'get_part_change_status',
    confirmation: 'native_gui_only', max_pending: 1, storage: 'memory_only', ttl_seconds: 300,
    max_status_bytes: 4096 });
  assert.equal(PftPartApprovalCapabilitySchema.safeParse({ ...PFT_PART_APPROVAL_CAPABILITY,
    pad_length_write: expectedCapability }).success, false);
  for (const value of [null, {}, true, { ...expectedCapability, extra: true },
    ...Object.keys(expectedCapability).map(key => Object.fromEntries(Object.entries(expectedCapability)
      .filter(([name]) => name !== key))),
    ...Object.entries(expectedCapability).map(([key, value]) => ({ ...expectedCapability,
      [key]: typeof value === 'number' ? value + 1 : typeof value === 'boolean' ? !value : 'unsupported' }))]) {
    assert.equal(PftPadLengthCapabilitySchema.safeParse(value).success, false, JSON.stringify(value));
  }
});

test('Pad输入只接受一个明确对象和正有界mm，不接受属性或外部确认参数', () => {
  for (const lengthMm of [0.01, 12, 10000]) {
    assert.deepEqual(PftPadLengthInputSchema.parse({ ...input, lengthMm }), { ...input, lengthMm });
  }
  for (const value of [undefined, null, {}, { ...input, objects: [] },
    { ...input, objects: ['Pad', 'OtherPad'] }, { ...input, objects: ['Pad', 'Pad'] },
    { ...input, objects: [''] }, { ...input, document: '' }, { ...input, body: '' },
    { ...input, operation: 'set_properties' }, { ...input, values: { Pad: { PartName: 'Example' } } },
    { ...input, confirmed: true }, { ...input, native_confirmation: true },
    { ...input, approved: true }, { ...input, extra: 'unsupported' },
    ...[0, -1, 0.009, 10000.001, Infinity, NaN, '12', true].map(lengthMm => ({ ...input, lengthMm }))]) {
    assert.equal(PftPadLengthInputSchema.safeParse(value).success, false, JSON.stringify(value));
  }
});

test('无效Pad输入在读取能力和派发前拒绝', async () => {
  for (const value of [{ ...input, objects: [] }, { ...input, lengthMm: 0 },
    { ...input, confirmed: true }, { ...input, values: {} }]) {
    const calls: unknown[] = [];
    await assert.rejects(pftPreparePadLength(value as never, {
      status: async () => { calls.push('status'); return status; },
      prepare: async params => { calls.push(params); return {}; },
    }, PftBridgeError), { code: 'INVALID_ARGUMENT' });
    assert.deepEqual(calls, []);
  }
});

test('每次先检查完整运行能力，再精确映射Pad预览RPC且原样返回完整预览', async () => {
  const calls: unknown[] = [];
  const preview = { document: input.document, operation: input.operation, token,
    changed_count: 1, can_apply: true, rows: [{ object: 'Pad', field: 'Length', old: 8, new: 12,
      status: 'change', unit: 'mm', body: 'Body', recompute_scope: ['Sketch', 'Pad', 'Body'] }] };
  const transport = { status: async () => { calls.push('status'); return status; },
    prepare: async (params: Record<string, unknown>) => { calls.push(params); return preview; } };
  for (let attempt = 0; attempt < 2; attempt++) {
    assert.equal(await pftPreparePadLength(input, transport, PftBridgeError), preview);
  }
  const params = { docName: input.document, objectNames: ['Pad'], operation: 'set_pad_length',
    bodyName: 'Body', lengthMm: 12 };
  assert.deepEqual(calls, ['status', params, 'status', params]);
});

test('缺少Pad能力、原生确认能力或任一必要方法时零派发，不降级为旧预览', async () => {
  const missing = [{}, { supported_methods: methods },
    { ...status, pad_length_write: undefined }, { ...status, part_approvals: undefined },
    { ...status, supported_methods: [] },
    ...methods.map(method => ({ ...status, supported_methods: methods.filter(name => name !== method) }))];
  for (const value of missing) {
    let dispatched = 0;
    await assert.rejects(pftPreparePadLength(input, { status: async () => value,
      prepare: async () => { dispatched++; return {}; } }, PftBridgeError), { code: 'UNSUPPORTED_CAPABILITY' });
    assert.equal(dispatched, 0, JSON.stringify(value));
  }
});

test('畸形或不兼容能力在派发前拒绝，不能忽略原part_approvals的额外字段', async () => {
  for (const value of [
    ...[null, {}, { ...PFT_PAD_LENGTH_CAPABILITY, version: 2 },
      { ...PFT_PAD_LENGTH_CAPABILITY, confirmation: 'external_flag' },
      { ...PFT_PAD_LENGTH_CAPABILITY, saves_document: true },
      { ...PFT_PAD_LENGTH_CAPABILITY, extra: true }].map(pad_length_write => ({ ...status, pad_length_write })),
    ...[null, {}, { ...PFT_PART_APPROVAL_CAPABILITY, version: 2 },
      { ...PFT_PART_APPROVAL_CAPABILITY, operations: ['set_pad_length'] }]
      .map(part_approvals => ({ ...status, part_approvals })),
  ]) {
    let dispatched = 0;
    await assert.rejects(pftPreparePadLength(input, { status: async () => value,
      prepare: async () => { dispatched++; return {}; } }, PftBridgeError), { code: 'INVALID_RESPONSE' });
    assert.equal(dispatched, 0, JSON.stringify(value));
  }
});

test('预览错误不自动重试，保留单次派发的固定业务错误', async () => {
  const error = new PftBridgeError('PAD_UNSUPPORTED', '该明确对象不在允许的Pad模型范围内。');
  let dispatched = 0;
  await assert.rejects(pftPreparePadLength(input, { status: async () => status,
    prepare: async () => { dispatched++; throw error; } }, PftBridgeError), value => value === error);
  assert.equal(dispatched, 1);
});

test('Pad应用必须经过原生确认并带完整设计结果，非完成状态不携带result', () => {
  for (const state of ['prepared', 'awaiting_confirmation', 'applying', 'applied', 'cancelled', 'failed', 'expired']) {
    const value = padStatus(state);
    assert.equal(PftPartApprovalDataSchema.safeParse(value).success, true, state);
    if (state !== 'applied') {
      assert.equal(PftPartApprovalDataSchema.safeParse({ ...value, result: appliedResult() }).success, false, state);
    }
  }
  for (const state of ['applying', 'applied']) {
    assert.equal(PftPartApprovalDataSchema.safeParse({ ...padStatus(state), native_confirmation: false }).success, false);
  }
  for (const native_confirmation of [false, true]) {
    assert.equal(PftPartApprovalDataSchema.safeParse({ ...padStatus('failed'), native_confirmation }).success, true);
  }
  for (const value of [{ ...padStatus(), result: null },
    { ...padStatus(), result: { changed_count: 1, saves_document: false } },
    { ...padStatus(), result: { ...appliedResult(), design_result: undefined } },
    { ...padStatus(), changed_count: 0 }, { ...padStatus(), changed_count: 2 },
    { ...padStatus(), result: { ...appliedResult(), changed_count: 0 } },
    { ...padStatus(), result: { ...appliedResult(), saves_document: true } }]) {
    assert.equal(PftPartApprovalDataSchema.safeParse(value).success, false, JSON.stringify(value));
  }
});

test('Pad设计结果严格校验mm数值、有限正体积与三维尺寸、单实体和检查数量', () => {
  const baseline = designResult();
  const malformed: unknown[] = [null, {}, { ...baseline, extra: true },
    { ...baseline, kind: 'generic_property' }, { ...baseline, body: '' }, { ...baseline, pad: '' },
    { ...baseline, body: 1 }, { ...baseline, pad: null },
    { ...baseline, length_unit: 'mm' }, { ...baseline, validation: 'recompute_returned_true' },
    { ...baseline, geometry: { ...baseline.geometry, extra: geometry() } },
    { ...baseline, geometry: { pad: geometry() } },
    ...[0, 0.009, 10000.001, NaN, Infinity, '12'].map(length_mm => ({ ...baseline, length_mm })),
    ...[-1, 0.5, NaN, Infinity, Number.MAX_SAFE_INTEGER + 1].map(recompute_count => ({ ...baseline, recompute_count })),
    ...[0, 65, 1.5, true].map(checked_objects => ({ ...baseline, checked_objects }))];
  for (const target of ['pad', 'body'] as const) {
    for (const shape of [null, {}, { ...geometry(), extra: true }, { ...geometry(), unit: 'mm' },
      ...[0, -1, Infinity, NaN, '2400'].map(volume_mm3 => ({ ...geometry(), volume_mm3 })),
      ...[0, 2, 1.5, true].map(solids => ({ ...geometry(), solids })),
      ...[[], [1, 2], [1, 2, 3, 4], [1, 0, 3], [1, -1, 3], [1, Infinity, 3], [1, NaN, 3], [1, '2', 3]]
        .map(dimensions_mm => ({ ...geometry(), dimensions_mm }))]) {
      malformed.push({ ...baseline, geometry: { ...baseline.geometry, [target]: shape } });
    }
  }
  for (const design of malformed) {
    const value = { ...padStatus(), result: appliedResult(design) };
    assert.equal(PftPartApprovalDataSchema.safeParse(value).success, false, JSON.stringify(value));
  }
  for (const checked_objects of [1, 64]) {
    for (const recompute_count of [0, Number.MAX_SAFE_INTEGER]) {
      const value = { ...padStatus(), result: appliedResult({ ...baseline, checked_objects, recompute_count }) };
      assert.equal(PftPartApprovalDataSchema.safeParse(value).success, true);
    }
  }
});

test('原五类操作保持两字段结果，不能混入Pad几何报告；Pad固定错误码可通过状态合同', () => {
  for (const operation of ['normalize_names', 'sync_link_labels', 'initialize_properties', 'recommend_properties', 'set_properties']) {
    const legacy = { ...padStatus(), operation, native_confirmation: false,
      result: { changed_count: 1, saves_document: false } };
    assert.equal(PftPartApprovalDataSchema.safeParse(legacy).success, true, operation);
    assert.equal(PftPartApprovalDataSchema.safeParse({ ...legacy, result: appliedResult() }).success, false, operation);
  }
  for (const error_code of ['PAD_UNSUPPORTED', 'PAD_PLAN_STALE', 'PAD_PARAMETER_VERIFY_FAILED',
    'PAD_GEOMETRY_VERIFY_FAILED', 'PAD_RECOMPUTE_UNVERIFIED', 'PAD_NATIVE_CONFIRMATION_REQUIRED']) {
    assert.equal(PftPartApprovalDataSchema.safeParse({ ...padStatus('failed'), error_code }).success, true, error_code);
  }
});

test('原生审批包装保留Pad几何结果与token绑定，整笔4KiB预算和错误响应不重发', async () => {
  for (const method of approvalMethods) {
    const value = padStatus();
    let dispatched = 0;
    const data = await pftPartApproval(method, { token }, { status: async () => status,
      call: async () => { dispatched++; return value; } }, PftBridgeError);
    assert.deepEqual(data, value);
    assert.equal(dispatched, 1);
    const oversized = { ...value, extra: 'x'.repeat(PFT_PART_APPROVAL_CAPABILITY.max_status_bytes) };
    assert.ok(Buffer.byteLength(JSON.stringify(oversized), 'utf8') > PFT_PART_APPROVAL_CAPABILITY.max_status_bytes);
    for (const malformed of [oversized, { ...value, token: 'another_pad_length_token01' },
      { ...value, result: { changed_count: 1, saves_document: false } },
      { ...value, result: appliedResult({ ...designResult(), length_mm: '12 mm' }) }]) {
      let failedDispatches = 0;
      await assert.rejects(pftPartApproval(method, { token }, { status: async () => status,
        call: async () => { failedDispatches++; return malformed; } }, PftBridgeError), { code: 'INVALID_RESPONSE' });
      assert.equal(failedDispatches, 1);
    }
  }
});


test('Pad输入Body与对象名采用64 Unicode字符的独立nativeName合同，文档名保留原界限', async () => {
  for (const value of ['Body_1', '示例对象', '😀'.repeat(64), 'A'.repeat(64)]) {
    assert.equal(PftPadLengthInputSchema.safeParse({ ...input, body: value }).success, true);
    assert.equal(PftPadLengthInputSchema.safeParse({ ...input, objects: [value] }).success, true);
  }
  const forbidden = ['', '.', '..', 'A'.repeat(65), '😀'.repeat(65), ' Body', 'Body ', '\tBody',
    'Body\n', 'Body\u200b', 'Body\u0000', 'Body\ud800', 'assembly/Pad', 'assembly\\Pad', 'prefix:Pad'];
  for (const value of forbidden) {
    for (const candidate of [{ ...input, body: value }, { ...input, objects: [value] }]) {
      assert.equal(PftPadLengthInputSchema.safeParse(candidate).success, false);
      let dispatched = 0;
      await assert.rejects(pftPreparePadLength(candidate, { status: async () => { dispatched++; return status; },
        prepare: async () => { dispatched++; return {}; } }, PftBridgeError), { code: 'INVALID_ARGUMENT' });
      assert.equal(dispatched, 0);
    }
  }
  assert.equal(PftPadLengthInputSchema.safeParse({ ...input, document: 'D'.repeat(256) }).success, true);
  assert.equal(PftPadLengthInputSchema.safeParse({ ...input, document: 'D'.repeat(257) }).success, false);
});

test('Pad结果Body与对象名采用相同nativeName合同，不改变全局审批文档名schema', () => {
  for (const key of ['body', 'pad'] as const) {
    for (const value of ['Body_1', '示例对象', '😀'.repeat(64), 'A'.repeat(64)]) {
      assert.equal(PftPartApprovalDataSchema.safeParse({ ...padStatus(),
        result: appliedResult({ ...designResult(), [key]: value }) }).success, true);
    }
    for (const value of ['', '.', '..', 'A'.repeat(65), '😀'.repeat(65), ' Body', 'Body ', '\tBody',
      'Body\n', 'Body\u200b', 'Body\u0000', 'Body\ud800', 'assembly/Pad', 'assembly\\Pad', 'prefix:Pad']) {
      assert.equal(PftPartApprovalDataSchema.safeParse({ ...padStatus(),
        result: appliedResult({ ...designResult(), [key]: value }) }).success, false);
    }
  }
  assert.equal(PftPartApprovalDataSchema.safeParse({ ...padStatus(), document: '😀'.repeat(256) }).success, true);
  assert.equal(PftPartApprovalDataSchema.safeParse({ ...padStatus(), document: '😀'.repeat(257) }).success, false);
});
