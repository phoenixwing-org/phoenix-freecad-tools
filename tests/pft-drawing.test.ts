// SPDX-License-Identifier: Apache-2.0
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';
import { PftBridgeError } from '../src/adapters/pft-freecad-client.js';
import { PFT_DRAWING_CAPABILITY, PftDrawingCapabilitySchema, PftDrawingInputSchema,
  PftDrawingPreviewSchema, PftDrawingDesignResultSchema, pftPrepareDrawing } from '../src/pft-drawing.js';
import { PFT_PART_APPROVAL_CAPABILITY, PftPartApprovalDataSchema, pftPartApproval } from '../src/pft-part-approval.js';

const input = { document: 'NeutralDocument', objects: ['NeutralPart'], operation: 'create_drawing' as const };
const token = 'neutral_drawing_confirmation01';
const methods = ['prepare_part_changes', 'request_part_change_approval', 'get_part_change_status'];
const status = { supported_methods: methods, part_approvals: PFT_PART_APPROVAL_CAPABILITY,
  drawing_create: PFT_DRAWING_CAPABILITY };
const nodes = ['NeutralPage', 'NeutralTemplate', 'NeutralFront', 'NeutralTop', 'NeutralIso'];
function preview(blocked = false) {
  return { document: input.document, operation: input.operation, token, state: 'prepared', can_apply: !blocked,
    expires_at: '2030-01-01T00:00:00Z', ttl_seconds: 300, changed_count: blocked ? 0 : 5,
    rows: blocked ? [{ object: input.objects[0], status: 'blocked', reason: 'Neutral unsupported source' }]
      : nodes.map(object => ({ object, field: 'TechDraw draft node', old: null, new: 'Neutral draft node',
        status: 'change', reason: '', source: 'cached_native_typed_snapshot', note: 'Projection not checked' })),
    requires_confirmation: true, saves_document: false, storage: 'memory_only',
    design_request: { source: input.objects[0], page: blocked ? null : nodes[0] } };
}
function designResult() {
  return { kind: 'drawing_draft', page: nodes[0], template: nodes[1], source: input.objects[0],
    views: [{ name: nodes[2], kind: 'front' }, { name: nodes[3], kind: 'top' }, { name: nodes[4], kind: 'axonometric' }],
    scale: 1, projection: 'first_angle', paper: 'A4_landscape', projection_verification: 'not_checked',
    manual_review_required: true };
}
function approval(state = 'applied') {
  return { document: input.document, operation: input.operation, token, state, changed_count: 5,
    requires_confirmation: true, saves_document: false, storage: 'memory_only',
    native_confirmation: ['applying', 'applied'].includes(state),
    result: state === 'applied' ? { changed_count: 5, saves_document: false, design_result: designResult() } : null,
    error_code: state === 'failed' ? 'DRAWING_CREATE_FAILED' : null };
}

test('出图能力精确声明草稿边界、三视图顺序和仅原生确认', () => {
  assert.deepEqual(PftDrawingCapabilitySchema.parse(PFT_DRAWING_CAPABILITY), PFT_DRAWING_CAPABILITY);
  for (const value of [null, {}, { ...PFT_DRAWING_CAPABILITY, extra: true },
    ...Object.keys(PFT_DRAWING_CAPABILITY).map(key => Object.fromEntries(Object.entries(PFT_DRAWING_CAPABILITY)
      .filter(([name]) => name !== key))),
    ...Object.entries(PFT_DRAWING_CAPABILITY).map(([key, value]) => ({ ...PFT_DRAWING_CAPABILITY,
      [key]: typeof value === 'number' ? value + 1 : typeof value === 'boolean' ? !value : 'unsupported' })),
    { ...PFT_DRAWING_CAPABILITY, views: ['top', 'front', 'axonometric'] },
    { ...PFT_DRAWING_CAPABILITY, views: ['front', 'top'] }]) {
    assert.equal(PftDrawingCapabilitySchema.safeParse(value).success, false, JSON.stringify(value));
  }
});

test('出图仅接受明确文档和一个源，Unicode安全名不回退ASCII或路径', async () => {
  for (const document of ['中立文档', '😀'.repeat(256)]) {
    for (const source of ['中立零件', '😀'.repeat(64)]) {
      assert.equal(PftDrawingInputSchema.safeParse({ ...input, document, objects: [source] }).success, true);
    }
  }
  const invalid = [undefined, null, {}, { ...input, document: '' }, { ...input, document: '😀'.repeat(257) },
    { ...input, objects: [] }, { ...input, objects: ['NeutralPart', 'OtherPart'] },
    ...['', '.', '..', ' source', 'source ', 'a/b', 'a\\b', 'a:b', 'a\u200bb', 'a\ud800b', 'A'.repeat(65)]
      .map(source => ({ ...input, objects: [source] })),
    ...['values', 'body', 'lengthMm', 'length', 'template', 'page', 'confirmed', 'native_confirmation', 'approved', 'extra']
      .map(key => ({ ...input, [key]: true }))];
  for (const value of invalid) {
    const calls: unknown[] = [];
    assert.equal(PftDrawingInputSchema.safeParse(value).success, false, JSON.stringify(value));
    await assert.rejects(pftPrepareDrawing(value as never, {
      status: async () => { calls.push('status'); return status; }, prepare: async params => { calls.push(params); return preview(); },
    }, PftBridgeError), { code: 'INVALID_ARGUMENT' });
    assert.deepEqual(calls, []);
  }
});

test('无三方法及双exact能力时零派发，无脚本、旧版本预览或直接apply回退', async () => {
  for (const value of [{}, { supported_methods: methods }, { ...status, drawing_create: undefined },
    { ...status, part_approvals: undefined },
    ...methods.map(method => ({ ...status, supported_methods: methods.filter(name => name !== method) }))]) {
    let dispatched = 0;
    await assert.rejects(pftPrepareDrawing(input, { status: async () => value,
      prepare: async () => { dispatched++; return preview(); } }, PftBridgeError), { code: 'UNSUPPORTED_CAPABILITY' });
    assert.equal(dispatched, 0);
  }
  for (const value of [
    ...[null, {}, { ...PFT_DRAWING_CAPABILITY, version: 2 }, { ...PFT_DRAWING_CAPABILITY, extra: true }]
      .map(drawing_create => ({ ...status, drawing_create })),
    { ...status, part_approvals: { ...PFT_PART_APPROVAL_CAPABILITY, confirmation: 'external_flag' } }]) {
    let dispatched = 0;
    await assert.rejects(pftPrepareDrawing(input, { status: async () => value,
      prepare: async () => { dispatched++; return preview(); } }, PftBridgeError), { code: 'INVALID_RESPONSE' });
    assert.equal(dispatched, 0);
  }
});

test('单次预览精确映射并保留完整五节点差异或blocked，不提前执行写入', async () => {
  for (const blocked of [false, true]) {
    const calls: unknown[] = [];
    const value = await pftPrepareDrawing(input, { status: async () => { calls.push('status'); return status; },
      prepare: async params => { calls.push(params); return preview(blocked); } }, PftBridgeError);
    assert.deepEqual(value, preview(blocked));
    assert.deepEqual(calls, ['status', { docName: input.document, objectNames: input.objects, operation: input.operation }]);
    assert.equal(value.can_apply, !blocked);
    assert.equal(value.design_request.page, blocked ? null : nodes[0]);
  }
});

test('预览必须绑定明确source/document及五节点，畸形超限仅拒绝且不重发', async () => {
  const valid = preview();
  const invalid = [null, {}, { ...valid, document: 'AnotherDocument' },
    { ...valid, design_request: { ...valid.design_request, source: 'AnotherPart' } },
    { ...valid, design_request: { ...valid.design_request, page: null } },
    { ...valid, rows: valid.rows.slice(1) }, { ...valid, rows: [...valid.rows.slice(0, 4), valid.rows[0]] },
    { ...valid, changed_count: 0 }, { ...valid, native_confirmation: true },
    { ...preview(true), changed_count: 5 }, { ...preview(true), design_request: valid.design_request },
    { ...valid, rows: valid.rows.map(row => ({ ...row, note: 'x'.repeat(128 * 1024) })) }];
  for (const value of invalid) {
    let dispatched = 0;
    await assert.rejects(pftPrepareDrawing(input, { status: async () => status,
      prepare: async () => { dispatched++; return value; } }, PftBridgeError), { code: 'INVALID_RESPONSE' });
    assert.equal(dispatched, 1);
  }
  const error = new PftBridgeError('DRAWING_TEMPLATE_UNAVAILABLE', 'Neutral missing template');
  let dispatched = 0;
  await assert.rejects(pftPrepareDrawing(input, { status: async () => status,
    prepare: async () => { dispatched++; throw error; } }, PftBridgeError), value => value === error);
  assert.equal(dispatched, 1);
});

test('出图结果只承认五个不同新节点及未核验投影，不承诺生产尺寸完成', () => {
  const result = designResult();
  assert.deepEqual(PftDrawingDesignResultSchema.parse(result), result);
  for (const value of [{ ...result, kind: 'pad_length' }, { ...result, template: '/neutral/template.svg' },
    { ...result, page: result.source }, { ...result, template: result.page },
    { ...result, views: [result.views[1], result.views[0], result.views[2]] },
    { ...result, views: result.views.slice(1) }, { ...result, views: result.views.map(row => ({ ...row, extra: true })) },
    ...[0, -1, NaN, Infinity, '1'].map(scale => ({ ...result, scale })),
    { ...result, projection: 'third_angle' }, { ...result, paper: 'A3' },
    { ...result, projection_verification: 'verified' }, { ...result, manual_review_required: false },
    { ...result, dimensions_complete: true }]) {
    assert.equal(PftDrawingDesignResultSchema.safeParse(value).success, false, JSON.stringify(value));
  }
});

test('审批状态区分drawing/pad/legacy，图纸应用必须5节点且原生确认', async () => {
  for (const state of ['prepared', 'awaiting_confirmation', 'applying', 'applied', 'cancelled', 'failed', 'expired']) {
    assert.equal(PftPartApprovalDataSchema.safeParse(approval(state)).success, true, state);
  }
  assert.equal(PftPartApprovalDataSchema.safeParse({ ...approval('prepared'), changed_count: 0 }).success, true);
  for (const value of [{ ...approval(), native_confirmation: false }, { ...approval('applying'), native_confirmation: false },
    { ...approval(), changed_count: 0 }, { ...approval(), changed_count: 1 },
    { ...approval(), result: { changed_count: 5, saves_document: false } },
    { ...approval(), result: { ...approval().result!, changed_count: 1 } },
    { ...approval(), operation: 'normalize_names' }, { ...approval(), operation: 'set_pad_length' }]) {
    assert.equal(PftPartApprovalDataSchema.safeParse(value).success, false, JSON.stringify(value));
  }
  for (const error_code of ['DRAWING_UNSUPPORTED', 'DRAWING_PLAN_STALE', 'DRAWING_TEMPLATE_UNAVAILABLE',
    'DRAWING_CREATE_FAILED', 'DRAWING_RECOMPUTE_UNVERIFIED', 'DRAWING_PROJECTION_UNAVAILABLE', 'DRAWING_NATIVE_CONFIRMATION_REQUIRED', 'DRAWING_RESULT_INVALID']) {
    assert.equal(PftPartApprovalDataSchema.safeParse({ ...approval('failed'), error_code }).success, true);
  }
  for (const method of ['request_part_change_approval', 'get_part_change_status'] as const) {
    let dispatched = 0;
    const result = await pftPartApproval(method, { token }, { status: async () => status,
      call: async () => { dispatched++; return approval(); } }, PftBridgeError);
    assert.deepEqual(result, approval());
    assert.equal(dispatched, 1);
  }
  assert.equal(PftDrawingPreviewSchema.safeParse(preview()).success, true);
});

test('实际Python预览和原生确认生命周期与TS绘图合同贯通，合成确认前零写入', async t => {
  const directory = mkdtempSync(join(tmpdir(), 'pft-drawing-contract-'));
  t.after(() => rmSync(directory, { recursive: true, force: true }));
  const fixturePath = fileURLToPath(new URL('./python', import.meta.url));
  const python = `import json
import sys
sys.path.insert(0, sys.argv[1])
import test_pft_drawing_approval as fixtures
case = fixtures.DrawingApprovalTests()
case.setUp()
try:
    preview = case.prepare()
    assert case.doc.events == []
    pending = case.request(preview["token"])
    assert case.doc.events == []
    assert pending["native_confirmation"] is False
    applied = case.views[0].confirm()
    assert case.doc.events == ["synthetic_drawing_write"]
    assert case.views[0].confirm() == applied
    assert case.drawing.apply_plan.call_count == 1
    assert not any(name.startswith(("FreeCAD", "PySide")) for name in sys.modules)
    print(json.dumps({"capability": fixtures.business.DRAWING_CREATE_CAPABILITIES,
        "preview": preview, "pending": pending, "applied": applied}, ensure_ascii=True))
finally:
    case.doCleanups()
`;
  const child = spawnSync('python3', ['-I', '-B', '-X', 'utf8', '-c', python, fixturePath], {
    cwd: directory, env: { PATH: '/usr/bin:/bin:/usr/sbin:/sbin' }, encoding: 'utf8', timeout: 5000, maxBuffer: 32 * 1024,
  });
  assert.equal(child.error, undefined); assert.equal(child.status, 0); assert.equal(child.signal, null); assert.equal(child.stderr, '');
  const output = JSON.parse(child.stdout) as { capability: unknown; preview: unknown; pending: unknown; applied: unknown };
  assert.deepEqual(PftDrawingCapabilitySchema.parse(output.capability), PFT_DRAWING_CAPABILITY);
  const prepared = PftDrawingPreviewSchema.parse(output.preview);
  const pending = PftPartApprovalDataSchema.parse(output.pending);
  const applied = PftPartApprovalDataSchema.parse(output.applied);
  assert.equal(prepared.changed_count, 5); assert.equal(pending.native_confirmation, false);
  assert.equal(applied.native_confirmation, true); assert.equal(applied.state, 'applied');
  assert.equal(pending.token, prepared.token); assert.equal(applied.token, prepared.token);
  const result = applied.result!;
  assert.ok('design_result' in result);
  assert.equal(result.design_result.kind, 'drawing_draft');
  assert.equal(result.design_result.source, prepared.design_request.source);
  assert.equal(result.design_result.page, prepared.design_request.page);
  const returned = await pftPrepareDrawing({ document: prepared.document, objects: [prepared.design_request.source],
    operation: 'create_drawing' }, { status: async () => status, prepare: async () => output.preview }, PftBridgeError);
  assert.deepEqual(returned, prepared);
});
