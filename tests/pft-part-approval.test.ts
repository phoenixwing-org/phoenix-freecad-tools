// SPDX-License-Identifier: Apache-2.0
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';
import { PftBridgeError } from '../src/adapters/pft-freecad-client.js';
import { PFT_PART_APPROVAL_CAPABILITY, PftPartApprovalCapabilitySchema, PftPartApprovalDataSchema,
  PftPartApprovalInputSchema, pftPartApproval, type PftPartApprovalMethod } from '../src/pft-part-approval.js';

const token = 'pft_native_confirmation_token01';
const methods: PftPartApprovalMethod[] = ['request_part_change_approval', 'get_part_change_status'];
const status = { supported_methods: methods, part_approvals: PFT_PART_APPROVAL_CAPABILITY };
const unverifiedState = '原生确认状态无法验证；请求可能已送达，请保留原 token 查询，不要自动重试或推定已确认、已写入。';
function fixture(state = 'awaiting_confirmation'): Record<string, unknown> {
  return { document: 'PublicSample', operation: 'normalize_names', token, state, changed_count: 1,
    requires_confirmation: true, saves_document: false, storage: 'memory_only', native_confirmation: false,
    result: state === 'applied' ? { changed_count: 1, saves_document: false } : null,
    error_code: state === 'failed' ? 'PART_CHANGE_FAILED' : null };
}

test('原生确认capability精确约束版本/方法/人工确认/单窗口/内存/TTL/预算', () => {
  assert.deepEqual(PftPartApprovalCapabilitySchema.parse(PFT_PART_APPROVAL_CAPABILITY), PFT_PART_APPROVAL_CAPABILITY);
  for (const value of [null, {}, true, { ...PFT_PART_APPROVAL_CAPABILITY, version: 2 },
    { ...PFT_PART_APPROVAL_CAPABILITY, request_method: 'apply_part_changes' },
    { ...PFT_PART_APPROVAL_CAPABILITY, status_method: 'get_script_status' },
    { ...PFT_PART_APPROVAL_CAPABILITY, confirmation: 'external_flag' },
    { ...PFT_PART_APPROVAL_CAPABILITY, max_pending: 2 }, { ...PFT_PART_APPROVAL_CAPABILITY, storage: 'disk' },
    { ...PFT_PART_APPROVAL_CAPABILITY, ttl_seconds: 301 }, { ...PFT_PART_APPROVAL_CAPABILITY, max_status_bytes: 8192 },
    { ...PFT_PART_APPROVAL_CAPABILITY, unknown: true }]) {
    assert.equal(PftPartApprovalCapabilitySchema.safeParse(value).success, false);
  }
});

test('输入仅接受有界token，不接受确认标志、grant或其它字段', () => {
  for (const value of [16, 32, 128]) assert.equal(PftPartApprovalInputSchema.safeParse({ token: 'a'.repeat(value) }).success, true);
  for (const value of [undefined, null, {}, { token: true }, { token: 'a'.repeat(15) }, { token: 'a'.repeat(129) },
    { token: '../another' }, { token, confirmed: true }, { token, approved: true }, { token, grant: {} },
    { token, native_confirmation: true }, { token, state: 'applied' }]) {
    assert.equal(PftPartApprovalInputSchema.safeParse(value).success, false);
  }
});

test('原生状态保留prepared/pending/applying而非完成，advanced applied不伪造GUI确认', () => {
  for (const state of ['prepared', 'awaiting_confirmation', 'applying', 'applied', 'cancelled', 'failed', 'expired']) {
    const data = PftPartApprovalDataSchema.parse(fixture(state));
    assert.equal(data.state, state); assert.equal(data.requires_confirmation, true); assert.equal(data.saves_document, false);
    assert.equal(data.native_confirmation, false);
    if (state !== 'applied') assert.equal(data.result, null);
  }
  for (const count of [0, 500]) {
    const data = PftPartApprovalDataSchema.parse({ ...fixture('applied'), changed_count: count,
      native_confirmation: true, result: { changed_count: count, saves_document: false } });
    assert.equal(data.changed_count, count); assert.equal(data.result?.changed_count, count);
  }
  for (const operation of ['normalize_names', 'sync_link_labels', 'initialize_properties', 'recommend_properties', 'set_properties']) {
    assert.equal(PftPartApprovalDataSchema.safeParse({ ...fixture(), operation }).success, true);
  }
});

test('未确认状态拒绝确认声明，已进入应用的状态区分人工与advanced来源', async () => {
  for (const state of ['prepared', 'awaiting_confirmation', 'cancelled', 'expired']) {
    const value = { ...fixture(state), native_confirmation: true };
    assert.equal(PftPartApprovalDataSchema.safeParse(value).success, false);
    for (const method of methods) {
      let dispatched = 0;
      await assert.rejects(pftPartApproval(method, { token }, { status: async () => status,
        call: async () => { dispatched++; return value; } }, PftBridgeError), { code: 'INVALID_RESPONSE' });
      assert.equal(dispatched, 1);
    }
  }
  for (const state of ['applying', 'applied', 'failed']) {
    for (const native_confirmation of [false, true]) {
      assert.equal(PftPartApprovalDataSchema.safeParse({ ...fixture(state), native_confirmation }).success, true);
    }
  }
  for (const error_code of ['UNRECOGNIZED_CODE', 'SYNTHETIC_SENSITIVE_TEXT', 'PART_APPROVAL_PENDING']) {
    assert.equal(PftPartApprovalDataSchema.safeParse({ ...fixture('failed'), error_code }).success, false);
  }
});

test('DTO状态/结果/错误码关系严格，拒绝任意额外数据与矛盾完成', () => {
  const malformed = [
    { ...fixture('applied'), result: null }, { ...fixture('applied'), error_code: 'PART_CHANGE_FAILED' },
    { ...fixture('applied'), result: { changed_count: 0, saves_document: false } },
    { ...fixture('applied'), result: { changed_count: 1, saves_document: true } },
    { ...fixture('applied'), result: { changed_count: 1, saves_document: false, detail: 'untrusted-extra' } },
    { ...fixture('failed'), error_code: null }, { ...fixture('failed'), error_code: 'raw backend message' },
    { ...fixture('failed'), error_code: 'X'.repeat(97) }, { ...fixture('failed'), result: { changed_count: 1, saves_document: false } },
    ...['prepared', 'awaiting_confirmation', 'applying', 'cancelled', 'expired'].flatMap(state => [
      { ...fixture(state), result: { changed_count: 1, saves_document: false } }, { ...fixture(state), error_code: 'PART_CHANGE_FAILED' }]),
    ...[true, -1, 0.5, 501, Number.MAX_SAFE_INTEGER, Infinity, '1'].map(changed_count => ({ ...fixture(), changed_count })),
    { ...fixture(), requires_confirmation: false }, { ...fixture(), saves_document: true }, { ...fixture(), storage: 'disk' },
    { ...fixture(), native_confirmation: 'yes' }, { ...fixture(), state: 'confirmed' }, { ...fixture(), operation: 'execute_script' },
    { ...fixture(), document: '' }, { ...fixture(), document: '😀'.repeat(257) }, { ...fixture(), document: 'bad\u200bname' },
    { ...fixture(), document: 'bad\u0000name' }, { ...fixture(), document: 'bad\ud800name' },
    { ...fixture(), message: 'untrusted-extra' }, { ...fixture(), grant: true }, { ...fixture(), confirmed: true },
  ];
  for (const value of malformed) assert.equal(PftPartApprovalDataSchema.safeParse(value).success, false, JSON.stringify(value));
  assert.equal(PftPartApprovalDataSchema.safeParse({ ...fixture(), document: '😀'.repeat(256) }).success, true);
});

test('每次先读当前双方法与exact capability，旧插件不派发请求且无fallback', async () => {
  const calls: unknown[] = [];
  const transport = { status: async () => { calls.push('status'); return status; },
    call: async (method: PftPartApprovalMethod, params: unknown) => { calls.push({ method, params }); return fixture(); } };
  for (const method of methods) {
    assert.equal((await pftPartApproval(method, { token }, transport, PftBridgeError)).state, 'awaiting_confirmation');
  }
  assert.deepEqual(calls, ['status', { method: methods[0], params: { token } }, 'status', { method: methods[1], params: { token } }]);
  for (const old of [{}, { supported_methods: methods }, { ...status, supported_methods: [] },
    { ...status, supported_methods: methods.slice(0, 1) }, { ...status, supported_methods: methods.slice(1) }]) {
    let dispatched = 0;
    for (const method of methods) {
      await assert.rejects(pftPartApproval(method, { token }, { status: async () => old,
        call: async () => { dispatched++; return fixture(); } }, PftBridgeError), { code: 'UNSUPPORTED_CAPABILITY' });
    }
    assert.equal(dispatched, 0);
  }
  for (const capability of [null, {}, { ...PFT_PART_APPROVAL_CAPABILITY, extra: true }]) {
    let dispatched = 0;
    for (const method of methods) {
      await assert.rejects(pftPartApproval(method, { token }, { status: async () => ({ ...status, part_approvals: capability }),
        call: async () => { dispatched++; return fixture(); } }, PftBridgeError), { code: 'INVALID_RESPONSE' });
    }
    assert.equal(dispatched, 0);
  }
  const before = calls.length;
  await assert.rejects(pftPartApproval(methods[0]!, { token, confirmed: true } as { token: string }, transport, PftBridgeError), { code: 'INVALID_ARGUMENT' });
  await assert.rejects(pftPartApproval('apply_part_changes' as PftPartApprovalMethod, { token }, transport, PftBridgeError), { code: 'INVALID_ARGUMENT' });
  assert.equal(calls.length, before);
});

test('响应绑定原token并整笔校验4KiB，不回显畸形或过大结果且不重发', async () => {
  const circular: Record<string, unknown> = {}; circular.self = circular;
  for (const method of methods) {
    for (const value of [null, false, fixture('applied'), { ...fixture(), token: 'b'.repeat(32) },
      { ...fixture(), extra: 'x'.repeat(4096) }, circular, 1n, { ...fixture('failed'), error_code: null }]) {
      // Applied with a matching actual result is deliberately accepted independently of native_confirmation.
      const valid = value !== null && typeof value === 'object' && !Array.isArray(value) && Reflect.get(value, 'state') === 'applied';
      let dispatched = 0;
      const run = pftPartApproval(method, { token }, { status: async () => status,
        call: async () => { dispatched++; return value; } }, PftBridgeError);
      if (valid) assert.equal((await run).state, 'applied');
      else await assert.rejects(run, { code: 'INVALID_RESPONSE', message: unverifiedState });
      assert.equal(dispatched, 1);
    }
  }
});

test('派发后timeout/格式/网络错误保持固定未知状态提示，业务错误保留结构化且不自动重试', async () => {
  for (const method of methods) {
    for (const code of ['HTTP_ERROR', 'INVALID_RESPONSE', 'RESPONSE_TOO_LARGE', 'CONNECTION_FAILED', 'BRIDGE_TIMEOUT']) {
      let dispatched = 0;
      await assert.rejects(pftPartApproval(method, { token }, { status: async () => status,
        call: async () => { dispatched++; throw new PftBridgeError(code, 'untrusted-provider-body'); } }, PftBridgeError),
      { code, message: unverifiedState });
      assert.equal(dispatched, 1);
    }
    const error = new PftBridgeError('PART_APPROVAL_BUSY', '测试固定后台错误');
    await assert.rejects(pftPartApproval(method, { token }, { status: async () => status,
      call: async () => { throw error; } }, PftBridgeError), value => value === error);
    await assert.rejects(pftPartApproval(method, { token }, { status: async () => status,
      call: async () => { throw new Error('untrusted-provider-body'); } }, PftBridgeError),
    { code: 'INVALID_RESPONSE', message: unverifiedState });
  }
});


test('Python实际bridge/business与TS合同贯通，纯fake原生窗口回调前外部apply不能写入', t => {
  const directory = mkdtempSync(join(tmpdir(), 'pft-approval-contract-'));
  t.after(() => rmSync(directory, { recursive: true, force: true }));
  const bridgePath = fileURLToPath(new URL('../addon/PhoenixFreeCADTools/pft_bridge.py', import.meta.url));
  const fixturePath = fileURLToPath(new URL('./python/test_pft_business.py', import.meta.url));
  const python = `import importlib.util
import json
import sys
import types

def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

bridge = load("pft_approval_contract_bridge", sys.argv[1])
fixtures = load("pft_approval_contract_fixtures", sys.argv[2])
case = fixtures.BusinessTests()
case.setUp()
business = bridge._business
business.begin_approval_lifecycle()  # Independent in-memory fixture; no service is launched.
preview = business.perform("prepare_part_changes", {"docName": "Demo", "objectNames": ["Part"],
    "operation": "normalize_names"}, case.app, case.gui, bridge.PftBridgeError)
token = preview["token"]
prepared = business.perform("get_part_change_status", {"token": token}, None, None, bridge.PftBridgeError)
callbacks = {}
presented = []
def open_fake(gui, preview, on_confirm, on_cancel, read_status):
    callbacks.update(confirm=on_confirm, cancel=on_cancel, status=read_status)
    return types.SimpleNamespace(present=lambda: presented.append(True), close=lambda: None)
business._open_approval = open_fake
pending = business.perform("request_part_change_approval", {"token": token}, case.app, case.gui, bridge.PftBridgeError)
pending_again = business.perform("request_part_change_approval", {"token": token}, case.app, case.gui, bridge.PftBridgeError)
assert pending == pending_again and presented == [True]
assert case.doc.events == [] and case.prefs.events == []
try:
    business.perform("apply_part_changes", {"token": token}, case.app, case.gui, bridge.PftBridgeError)
except bridge.PftBridgeError as error:
    assert error.code == "PART_APPROVAL_PENDING"
else:
    raise AssertionError("pending plan was externally applied")
assert case.doc.events == []
applied = callbacks["confirm"]()
assert case.doc.events == ["open", "commit"]
assert case.part.Label == "7305.004-Example bracket"
assert callbacks["confirm"]() == applied and case.doc.events == ["open", "commit"]
queried = business.perform("get_part_change_status", {"token": token}, None, None, bridge.PftBridgeError)
assert queried == applied
assert not any(name.startswith(("FreeCAD", "PySide")) for name in sys.modules)
print(json.dumps({"capability": bridge.get_status()["part_approvals"], "states": [prepared, pending, applied]}, ensure_ascii=True))
`;
  const child = spawnSync('python3', ['-I', '-B', '-X', 'utf8', '-c', python, bridgePath, fixturePath], {
    cwd: directory, env: { PATH: '/usr/bin:/bin:/usr/sbin:/sbin' }, encoding: 'utf8', timeout: 5000, maxBuffer: 16 * 1024,
  });
  assert.equal(child.error, undefined); assert.equal(child.status, 0); assert.equal(child.signal, null); assert.equal(child.stderr, '');
  const output = JSON.parse(child.stdout) as { capability: unknown; states: unknown[] };
  assert.deepEqual(Object.keys(output).sort(), ['capability', 'states']);
  assert.deepEqual(PftPartApprovalCapabilitySchema.parse(output.capability), PFT_PART_APPROVAL_CAPABILITY);
  const states = output.states.map(value => PftPartApprovalDataSchema.parse(value));
  assert.deepEqual(states.map(value => value.state), ['prepared', 'awaiting_confirmation', 'applied']);
  assert.deepEqual(states.map(value => value.native_confirmation), [false, false, true]);
  assert.equal(states.length, 3);
  for (const value of states) { assert.equal(value.document, 'Demo'); assert.equal(value.token, states[0]!.token); assert.equal(value.operation, 'normalize_names'); }
  assert.equal(states[0]!.result, null); assert.equal(states[1]!.result, null);
  assert.deepEqual(states[2]!.result, { changed_count: 1, saves_document: false });
});
