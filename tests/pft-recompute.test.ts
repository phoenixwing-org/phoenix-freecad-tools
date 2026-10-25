// SPDX-License-Identifier: Apache-2.0
import { test, type TestContext } from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';
import { PftBridgeError, PftFreeCadClient } from '../src/adapters/pft-freecad-client.js';
import { PFT_RECOMPUTE_CAPABILITY, PFT_MAX_RECOMPUTE_RESULT_BYTES, PftRecomputeCapabilitySchema,
  PftRecomputeDataSchema, pftRecomputeDocument } from '../src/pft-recompute.js';

const status = { protocol_version: 1, supported_methods: ['recompute_document'], recompute_result: PFT_RECOMPUTE_CAPABILITY };
const uncertainMessage = 'FreeCAD 重算请求可能已执行，结果未能验证；请检查当前文档，不要自动重试。';
const fixture = (count = 0): Record<string, unknown> => ({ document: 'PublicSample', saved: false, recompute_invoked: true,
  native_object_count: count, checked_object_count: 7, object_status: 'no_invalid_or_pending',
  native_completion: 'not_verified', geometry_validity: 'not_verified', geometry_freshness: 'not_verified' });

test('重算原生整数0和正数仅为计数，协议不冒充完成或几何验证', () => {
  for (const count of [0, 1, 7, Number.MAX_SAFE_INTEGER]) {
    const parsed = PftRecomputeDataSchema.parse(fixture(count));
    assert.equal(parsed.native_object_count, count); assert.equal(parsed.native_completion, 'not_verified');
    assert.equal(parsed.geometry_validity, 'not_verified'); assert.equal(parsed.geometry_freshness, 'not_verified');
    assert.equal(Object.hasOwn(parsed, 'native_success'), false); assert.equal(Object.hasOwn(parsed, 'recomputed'), false);
  }
  for (const value of [false, true, -1, 0.1, Number.MAX_SAFE_INTEGER + 1, Infinity, NaN, '0', null]) {
    assert.equal(PftRecomputeDataSchema.safeParse({ ...fixture(), native_object_count: value }).success, false);
  }
  for (const value of [false, true, -1, 0.1, 10001, Number.MAX_SAFE_INTEGER, Infinity, '0', null]) {
    assert.equal(PftRecomputeDataSchema.safeParse({ ...fixture(), checked_object_count: value }).success, false);
  }
  for (const extra of [{ saved: true }, { recompute_invoked: false }, { native_completion: 'completed' },
    { object_status: 'unknown' }, { geometry_validity: 'valid' }, { geometry_freshness: 'fresh' },
    { native_success: true }, { recomputed: true }, { unknown: 'untrusted-extra-field' }, { document: '' },
    { document: 'x'.repeat(257) }, { document: 'bad\u200bname' }, { document: 'bad\u0000name' }, { document: 'bad\ud800name' }]) {
    assert.equal(PftRecomputeDataSchema.safeParse({ ...fixture(), ...extra }).success, false);
  }
  assert.equal(PftRecomputeDataSchema.safeParse({ ...fixture(), document: '😀'.repeat(256) }).success, true);
});

test('重算能力声明必须精确匹配，不接受旧布尔成功、额外字段或错误预算', () => {
  assert.equal(PftRecomputeCapabilitySchema.safeParse(PFT_RECOMPUTE_CAPABILITY).success, true);
  for (const value of [undefined, null, {}, true, { ...PFT_RECOMPUTE_CAPABILITY, version: 2 },
    { ...PFT_RECOMPUTE_CAPABILITY, native_result_kind: 'boolean' }, { ...PFT_RECOMPUTE_CAPABILITY, max_objects: 9999 },
    { ...PFT_RECOMPUTE_CAPABILITY, max_objects: 10001 }, { ...PFT_RECOMPUTE_CAPABILITY, object_status_check: 'best_effort' },
    { ...PFT_RECOMPUTE_CAPABILITY, native_completion: 'verified' }, { ...PFT_RECOMPUTE_CAPABILITY, geometry_freshness: 'fresh' },
    { ...PFT_RECOMPUTE_CAPABILITY, geometry_validity: 'verified' }, { ...PFT_RECOMPUTE_CAPABILITY, extra: true }]) {
    assert.equal(PftRecomputeCapabilitySchema.safeParse(value).success, false);
  }
});

test('每次重算前重新读取双能力，缺失或畸形能力与无效输入均在调用前拒绝', async () => {
  const calls: unknown[] = [];
  const transport = { status: async () => { calls.push('get_status'); return status; },
    recompute: async (params: unknown) => { calls.push(params); return fixture(); } };
  await assert.rejects(pftRecomputeDocument('', transport, PftBridgeError), { code: 'INVALID_ARGUMENT' });
  assert.equal(calls.length, 0);
  await pftRecomputeDocument('PublicSample', transport, PftBridgeError);
  assert.deepEqual(calls, ['get_status', { docName: 'PublicSample' }]);
  for (const unsupported of [{}, { ...status, supported_methods: [] }, { ...status, supported_methods: 'recompute_document' },
    { protocol_version: 1, supported_methods: ['recompute_document'] }]) {
    let writes = 0;
    await assert.rejects(pftRecomputeDocument('PublicSample', { status: async () => unsupported,
      recompute: async () => { writes++; return fixture(); } }, PftBridgeError), { code: 'UNSUPPORTED_CAPABILITY' });
    assert.equal(writes, 0);
  }
  for (const malformed of [null, {}, { ...PFT_RECOMPUTE_CAPABILITY, extra: true }]) {
    let writes = 0;
    await assert.rejects(pftRecomputeDocument('PublicSample', { status: async () => ({ ...status, recompute_result: malformed }),
      recompute: async () => { writes++; return fixture(); } }, PftBridgeError), { code: 'INVALID_RESPONSE' });
    assert.equal(writes, 0);
  }
  await pftRecomputeDocument('PublicSample', transport, PftBridgeError);
  assert.deepEqual(calls, ['get_status', { docName: 'PublicSample' }, 'get_status', { docName: 'PublicSample' }]);
});

test('已派发结果错文档、类型或多余数据均整笔拒绝且仅一次重算', async () => {
  const circular: Record<string, unknown> = {}; circular.self = circular;
  const invalid: unknown[] = [null, false, 0, fixture(false as unknown as number), { ...fixture(), document: 'OtherSample' },
    { ...fixture(), native_success: true }, { ...fixture(), recomputed: true }, { ...fixture(), saved: true },
    { ...fixture(), recompute_invoked: false }, { ...fixture(), native_completion: 'verified' },
    { ...fixture(), native_object_count: -1 }, { ...fixture(), native_object_count: 0.5 },
    { ...fixture(), native_object_count: Number.MAX_SAFE_INTEGER + 1 }, { ...fixture(), checked_object_count: 10001 },
    { ...fixture(), extra: 'x'.repeat(PFT_MAX_RECOMPUTE_RESULT_BYTES) }, circular, 1n];
  for (const value of invalid) {
    let writes = 0;
    await assert.rejects(pftRecomputeDocument('PublicSample', { status: async () => status,
      recompute: async () => { writes++; return value; } }, PftBridgeError), { code: 'INVALID_RESPONSE', message: uncertainMessage });
    assert.equal(writes, 1);
  }
});

test('派发后的网络与格式错误保留类型但固定未知结果提示，后端业务错误保持结构化', async () => {
  for (const code of ['HTTP_ERROR', 'INVALID_RESPONSE', 'RESPONSE_TOO_LARGE', 'CONNECTION_FAILED', 'BRIDGE_TIMEOUT']) {
    let writes = 0;
    await assert.rejects(pftRecomputeDocument('PublicSample', { status: async () => status,
      recompute: async () => { writes++; throw new PftBridgeError(code, 'untrusted-provider-body'); } }, PftBridgeError),
    { code, message: uncertainMessage });
    assert.equal(writes, 1);
  }
  const backend = new PftBridgeError('RECOMPUTE_INCOMPLETE', '测试固定后端错误');
  await assert.rejects(pftRecomputeDocument('PublicSample', { status: async () => status,
    recompute: async () => { throw backend; } }, PftBridgeError), error => error === backend);
  await assert.rejects(pftRecomputeDocument('PublicSample', { status: async () => status,
    recompute: async () => { throw new Error('untrusted-provider-body'); } }, PftBridgeError),
  { code: 'INVALID_RESPONSE', message: uncertainMessage });
});

async function httpFixture(t: TestContext) {
  const calls: Array<{ method: string; params: unknown }> = [];
  const state: { capability: unknown; result: unknown; mode: string } = { capability: PFT_RECOMPUTE_CAPABILITY, result: fixture(), mode: 'valid' };
  const server = createServer(async (req, res) => {
    let body = ''; for await (const chunk of req) body += chunk;
    const parsed = JSON.parse(body); calls.push(parsed);
    res.setHeader('Content-Type', 'application/json');
    if (parsed.method === 'get_status') {
      res.end(JSON.stringify({ ok: true, data: { protocol_version: 1, supported_methods: ['recompute_document'],
        ...(state.capability === undefined ? {} : { recompute_result: state.capability }) } })); return;
    }
    if (state.mode === 'timeout') return;
    if (state.mode === 'invalid_json') { res.end('untrusted-provider-body'); return; }
    if (state.mode === 'oversize') { res.end('x'.repeat(1024 * 1024 + 1)); return; }
    if (state.mode === 'non_json') { res.setHeader('Content-Type', 'text/html'); res.end('untrusted-provider-body'); return; }
    if (state.mode === 'closed') { req.socket.destroy(); return; }
    res.end(JSON.stringify({ ok: true, data: state.result }));
  });
  await new Promise<void>(resolve => server.listen(0, '127.0.0.1', resolve));
  t.after(async () => { server.closeAllConnections(); await new Promise<void>(resolve => server.close(() => resolve())); });
  const client = new PftFreeCadClient({ url: `http://127.0.0.1:${(server.address() as { port: number }).port}/`, timeoutMs: 75 });
  return { client, state, calls };
}

test('真实HTTP客户端预检能力，兼容0/正整数并精确映射一次调用', async t => {
  const { client, state, calls } = await httpFixture(t);
  for (const count of [0, 3]) {
    state.result = fixture(count);
    const before = calls.length, result = await client.recomputeDocument('PublicSample');
    assert.equal(result.native_object_count, count); assert.equal(result.native_completion, 'not_verified');
    assert.deepEqual(calls.slice(before), [{ method: 'get_status', params: {} }, { method: 'recompute_document', params: { docName: 'PublicSample' } }]);
  }
  for (const capability of [undefined, null, {}, { ...PFT_RECOMPUTE_CAPABILITY, extra: true }]) {
    state.capability = capability;
    const before = calls.length;
    await assert.rejects(client.recomputeDocument('PublicSample'), { code: capability === undefined ? 'UNSUPPORTED_CAPABILITY' : 'INVALID_RESPONSE' });
    assert.deepEqual(calls.slice(before), [{ method: 'get_status', params: {} }]);
  }
});

test('真实HTTP超时/断连/格式/预算失败重算最多一次，结果明确未知且不泄露响应', async t => {
  const { client, state, calls } = await httpFixture(t);
  for (const [mode, code] of [['timeout', 'BRIDGE_TIMEOUT'], ['invalid_json', 'INVALID_RESPONSE'],
    ['oversize', 'RESPONSE_TOO_LARGE'], ['non_json', 'INVALID_RESPONSE'], ['closed', 'CONNECTION_FAILED']]) {
    state.mode = mode!;
    const before = calls.length;
    await assert.rejects(client.recomputeDocument('PublicSample'), { code, message: uncertainMessage });
    assert.deepEqual(calls.slice(before), [{ method: 'get_status', params: {} }, { method: 'recompute_document', params: { docName: 'PublicSample' } }]);
  }
});


test('独立Python桥接与操作模块真实源合同匹配TS schemas，仅使用独立fake文档', t => {
  const directory = mkdtempSync(join(tmpdir(), 'pft-recompute-contract-'));
  t.after(() => rmSync(directory, { recursive: true, force: true }));
  const bridgePath = fileURLToPath(new URL('../addon/PhoenixFreeCADTools/pft_bridge.py', import.meta.url));
  const fixturePath = fileURLToPath(new URL('./python/test_pft_actions.py', import.meta.url));
  const python = `import importlib.util
import json
import sys

def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

bridge = load("pft_contract_bridge", sys.argv[1])
fixtures = load("pft_contract_fixtures", sys.argv[2])
fixture = fixtures.ActionTests()
fixture.setUp()
capability = bridge.get_status()["recompute_result"]
data = bridge._operations.perform("recompute_document", {"docName": "Doc"},
                                  fixture.app, fixture.gui, bridge.PftBridgeError)
assert fixture.doc.events == ["recompute"]
assert "FreeCAD" not in sys.modules and "FreeCADGui" not in sys.modules
print(json.dumps({"capability": capability, "data": data}, ensure_ascii=True))
`;
  const child = spawnSync('python3', ['-I', '-B', '-X', 'utf8', '-c', python, bridgePath, fixturePath], {
    cwd: directory, env: { PATH: process.env.PATH ?? '/usr/bin:/bin' }, encoding: 'utf8', timeout: 5000,
    maxBuffer: 16 * 1024,
  });
  assert.equal(child.error, undefined); assert.equal(child.status, 0); assert.equal(child.signal, null);
  assert.equal(child.stderr, '');
  const output = JSON.parse(child.stdout) as Record<string, unknown>;
  assert.deepEqual(Object.keys(output).sort(), ['capability', 'data']);
  assert.deepEqual(PftRecomputeCapabilitySchema.parse(output.capability), PFT_RECOMPUTE_CAPABILITY);
  const data = PftRecomputeDataSchema.parse(output.data);
  assert.equal(data.document, 'Doc'); assert.equal(data.native_object_count, 2); assert.equal(data.checked_object_count, 2);
  assert.equal(data.native_completion, 'not_verified'); assert.equal(data.geometry_validity, 'not_verified');
  assert.equal(data.geometry_freshness, 'not_verified');
});
