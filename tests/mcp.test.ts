// SPDX-License-Identifier: Apache-2.0
import { test, type TestContext } from 'node:test';
import assert from 'node:assert/strict';
import { createServer as httpServer } from 'node:http';
import { fileURLToPath } from 'node:url';
import { deflateSync } from 'node:zlib';
import { spawnSync } from 'node:child_process';
import { pftAppearanceScript } from '../src/pft-appearance.js';
import { PFT_RECOMPUTE_CAPABILITY } from '../src/pft-recompute.js';
import { PFT_PART_APPROVAL_CAPABILITY } from '../src/pft-part-approval.js';
import { PFT_PAD_LENGTH_CAPABILITY } from '../src/pft-pad-length.js';
import { Client } from '@modelcontextprotocol/client';
import { StdioClientTransport } from '@modelcontextprotocol/client/stdio';

const TOOL_NAMES = ['get_capabilities', 'get_connection_status', 'list_documents', 'fit_view', 'set_axonometric_view',
  'get_document_info', 'list_objects', 'get_object_info', 'get_selection', 'measure_geometry', 'inspect_design', 'list_drawing_pages',
  'inspect_drawing_page', 'set_view', 'set_appearance', 'capture_view', 'execute_script', 'get_script_status', 'set_selection', 'set_visibility', 'recompute_document', 'prepare_part_changes', 'request_part_change_approval', 'get_part_change_status', 'apply_part_changes'];
const METHODS = ['get_status', 'list_documents', 'view_fit', 'view_axonometric', 'get_document_info', 'list_objects',
  'get_object_info', 'get_selection', 'measure_geometry', 'inspect_design', 'list_drawing_pages', 'inspect_drawing_page',
  'view_standard', 'capture_view', 'execute_script', 'get_script_status', 'set_selection', 'set_visibility', 'recompute_document', 'prepare_part_changes', 'request_part_change_approval', 'get_part_change_status', 'apply_part_changes'];
const READ_METHODS = ['get_document_info', 'list_objects', 'get_object_info'];
const INSTANCE_TOKEN = 'sample_document_instance_token01';
const READ_CAPABILITY = { version: 1, methods: [...READ_METHODS, 'inspect_design'], revision_coverage: 'partial', write_guard: false };
const READ_PROVENANCE = { source: 'live_native_document', geometry_freshness: 'not_verified', recomputed: false, write_guard: false };
function nativeRead(extra: Record<string, unknown> = {}): Record<string, unknown> {
  return { document: 'PublicSample', source_kind: 'live_freecad_gui', document_instance_token: INSTANCE_TOKEN, instance_token: INSTANCE_TOKEN, revision: 7,
    revision_scope: 'observed_document_events_since_registration', revision_coverage: 'partial', revision_status: 'observing',
    provenance: READ_PROVENANCE, ...extra };
}
function designRead(params: Record<string, unknown> = {}): Record<string, unknown> {
  const offset = Number(params.constraintOffset ?? 0), limit = Number(params.constraintLimit ?? 50);
  const cache = (value: unknown, source: string) => ({ value, source, status: 'available' });
  return nativeRead({ document: params.docName ?? 'PublicSample', object: { Name: params.objectName ?? 'Sketch', Label: 'Public sketch', TypeId: 'Sketcher::SketchObject',
    kind: 'sketch', status: 'available', reason: null, state: cache([], 'DocumentObject.State'), has_error_marker: false },
    body: null, sketch: { geometry_count: cache(1, 'Sketch.GeometryCount'), degrees_of_freedom: cache(2, 'Sketch.DoF'),
      fully_constrained: cache(false, 'Sketch.FullyConstrained'), solver_invoked: false,
      constraints: { status: 'available', total_count: 0, offset, limit, items: [], has_more: false, next_offset: null } },
    parameters: [], parameters_status: 'available', parameters_source: 'Object.PropertiesList',
    expressions: { status: 'available', total_count: 0, items: [], truncated: false, evaluated: false },
    warnings: [], recomputed: false, solver_invoked: false, geometry_validity: 'not_verified' });
}
const readArgs = (name: string) => name === 'get_object_info' ? { document: 'PublicSample', object: 'Body' } : { document: 'PublicSample' };
type Call = { method: string; params: Record<string, unknown> };

/** A complete synthetic PNG, with independent fixture CRCs and compressed rows. */
function png(width: number, height: number): Buffer {
  function chunk(type: string, data: Buffer): Buffer {
    const name = Buffer.from(type), result = Buffer.alloc(12 + data.length);
    result.writeUInt32BE(data.length, 0); name.copy(result, 4); data.copy(result, 8);
    let crc = 0xffffffff;
    for (const value of Buffer.concat([name, data])) {
      crc ^= value;
      for (let bit = 0; bit < 8; bit++) crc = crc & 1 ? (crc >>> 1) ^ 0xedb88320 : crc >>> 1;
    }
    result.writeUInt32BE((crc ^ 0xffffffff) >>> 0, result.length - 4);
    return result;
  }
  const header = Buffer.alloc(13); header.writeUInt32BE(width, 0); header.writeUInt32BE(height, 4); header[8] = 8; header[9] = 6;
  return Buffer.concat([Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]), chunk('IHDR', header),
    chunk('IDAT', deflateSync(Buffer.alloc((width * 4 + 1) * height))), chunk('IEND', Buffer.alloc(0))]);
}

async function session(t: TestContext, toolScope: 'full' | 'chat' = 'full', deskEnv: Record<string, string> = {}) {
  const calls: Call[] = [];
  const state: { methods: unknown; capture: ((value: Record<string, unknown>) => unknown) | undefined; readProvenance: unknown;
    script: ((params: Record<string, unknown>) => unknown) | undefined;
    recomputeCapability: unknown; recompute: ((params: Record<string, unknown>) => unknown) | undefined;
    recomputeError: { code: string; message: string } | undefined;
    padLengthCapability: unknown; partApprovalCapability: unknown; partApproval: ((method: string, params: Record<string, unknown>) => unknown) | undefined;
    partApprovalError: { code: string; message: string } | undefined; partApprovalMode: string;
    read: ((method: string, params: Record<string, unknown>) => unknown) | undefined; readError: { code: string; message: string } | undefined }
    = { methods: METHODS, capture: undefined, readProvenance: undefined, script: undefined, read: undefined, readError: undefined,
      recomputeCapability: PFT_RECOMPUTE_CAPABILITY, recompute: undefined, recomputeError: undefined,
      padLengthCapability: undefined, partApprovalCapability: PFT_PART_APPROVAL_CAPABILITY, partApproval: undefined, partApprovalError: undefined, partApprovalMode: 'normal' };
  const bridge = httpServer(async (req, res) => {
    let text = ''; for await (const chunk of req) text += chunk;
    const body = JSON.parse(text) as Call; calls.push(body);
    res.setHeader('Content-Type', 'application/json');
    if (body.params.docName === 'missing' || body.params.requestId === 'fail') {
      res.end(JSON.stringify({ ok: false, error: { code: body.params.requestId === 'fail' ? 'SCRIPT_FAILED' : 'DOCUMENT_NOT_FOUND', message: '测试业务错误' } })); return;
    }
    let data: unknown;
    if ((READ_METHODS.includes(body.method) || body.method === 'inspect_design') && state.readError) { res.end(JSON.stringify({ ok: false, error: state.readError })); return; }
    if (body.method === 'recompute_document' && state.recomputeError) { res.end(JSON.stringify({ ok: false, error: state.recomputeError })); return; }
    const approvalMethod = ['request_part_change_approval', 'get_part_change_status'].includes(body.method);
    if (approvalMethod && state.partApprovalError) { res.end(JSON.stringify({ ok: false, error: state.partApprovalError })); return; }
    if (approvalMethod && state.partApprovalMode === 'timeout') return;
    if (approvalMethod && state.partApprovalMode === 'invalid_json') { res.end('untrusted-provider-body'); return; }
    if (body.method === 'get_status') data = { protocol_version: 1, running: true, addon_version: '0.3.1', ...(state.methods === undefined ? {} : { supported_methods: state.methods }),
      ...(state.readProvenance === undefined ? {} : { read_provenance: state.readProvenance }),
      ...(state.recomputeCapability === undefined ? {} : { recompute_result: state.recomputeCapability }),
      ...(state.partApprovalCapability === undefined ? {} : { part_approvals: state.partApprovalCapability }),
      ...(state.padLengthCapability === undefined ? {} : { pad_length_write: state.padLengthCapability }) };
    else if ((READ_METHODS.includes(body.method) || body.method === 'inspect_design') && state.read) data = state.read(body.method, body.params);
    else if (body.method === 'inspect_design') data = designRead(body.params);
    else if (body.method === 'list_documents') data = ['PublicSample'];
    else if (approvalMethod) data = state.partApproval ? state.partApproval(body.method, body.params) : approvalStatus(String(body.params.token));
    else if (body.method === 'recompute_document') data = state.recompute ? state.recompute(body.params) : { document: body.params.docName,
      saved: false, recompute_invoked: true, native_object_count: 0, checked_object_count: 0, object_status: 'no_invalid_or_pending',
      native_completion: 'not_verified', geometry_validity: 'not_verified', geometry_freshness: 'not_verified' };
    else if (body.method === 'execute_script' && state.script) data = state.script(body.params);
    else if (body.method === 'capture_view') {
      const width = Number(body.params.width), height = Number(body.params.height), image = png(width, height).toString('base64');
      const capture = { document: body.params.docName ?? 'PublicSample', mime_type: 'image/png', width, height, data: image,
        extra_base64: image, local_path: 'not-forwarded-local-path' };
      data = state.capture ? state.capture(capture) : capture;
    } else data = { document: body.params.docName ?? 'PublicSample', received_method: body.method, params: body.params };
    res.end(JSON.stringify({ ok: true, data }));
  });
  await new Promise<void>((resolve, reject) => { bridge.once('error', reject); bridge.listen(0, '127.0.0.1', resolve); });
  t.after(async () => { bridge.closeAllConnections(); await new Promise<void>(resolve => bridge.close(() => resolve())); });
  const port = (bridge.address() as { port: number }).port;
  const transport = new StdioClientTransport({ command: process.execPath,
    args: [fileURLToPath(new URL('../dist/cli.js', import.meta.url))], env: { PHOENIX_FREECAD_URL: `http://127.0.0.1:${port}/`, PHOENIX_FREECAD_TOOL_SCOPE: toolScope, ...deskEnv }, stderr: 'pipe' });
  const client = new Client({ name: 'phoenix-integration-test', version: '0.3.2' });
  await client.connect(transport); t.after(() => client.close());
  return { client, calls, state };
}

test('真实 stdio 默认发现25工具；离线清单区分插件运行时能力和工具作用', async (t) => {
  const { client, calls } = await session(t), list = await client.listTools();
  assert.deepEqual(list.tools.map(tool => tool.name).sort(), [...TOOL_NAMES].sort());
  assert.equal(calls.length, 0);
  const capabilities = await client.callTool({ name: 'get_capabilities', arguments: {} });
  assert.equal(capabilities.structuredContent?.version, '0.3.2');
  assert.equal(capabilities.structuredContent?.runtime_supported_checked, false);
  assert.deepEqual(capabilities.structuredContent?.script_history, { scope: 'bridge_service_session', session_id_field: 'script_session_id', full_results: 20, remembered_request_ids: 4096, expired_requests_reexecute: false });
  assert.equal(calls.length, 0);
  for (const name of ['get_document_info', 'list_objects', 'get_object_info', 'get_selection', 'measure_geometry', 'inspect_design', 'list_drawing_pages', 'inspect_drawing_page', 'capture_view', 'get_script_status', 'prepare_part_changes']) {
    assert.equal(list.tools.find(tool => tool.name === name)?.annotations?.readOnlyHint, true, name);
  }
  assert.equal(list.tools.find(tool => tool.name === 'set_view')?.annotations?.readOnlyHint, false);
  assert.equal(list.tools.find(tool => tool.name === 'prepare_part_changes')?.annotations?.idempotentHint, false);
  assert.equal(list.tools.find(tool => tool.name === 'apply_part_changes')?.annotations?.readOnlyHint, false);
  assert.equal(list.tools.find(tool => tool.name === 'apply_part_changes')?.annotations?.idempotentHint, true);
  assert.deepEqual(list.tools.find(tool => tool.name === 'execute_script')?.annotations, {
    readOnlyHint: false, destructiveHint: true, idempotentHint: true, openWorldHint: true,
  });
});

test('MCP 调用保留原视图工具，并准确映射所有新增参数与默认值', async (t) => {
  const { client, calls } = await session(t);
  const docs = await client.callTool({ name: 'list_documents', arguments: {} });
  assert.deepEqual(docs.structuredContent?.data, ['PublicSample']);
  const cases: Array<{ name: string; args: Record<string, unknown>; method: string; params: Record<string, unknown> }> = [
    { name: 'set_axonometric_view', args: { document: 'PublicSample', fit: true }, method: 'view_axonometric', params: { docName: 'PublicSample', fit: true } },
    { name: 'fit_view', args: { document: 'PublicSample' }, method: 'view_fit', params: { docName: 'PublicSample' } },
    { name: 'get_document_info', args: { document: 'PublicSample' }, method: 'get_document_info', params: { docName: 'PublicSample' } },
    { name: 'list_objects', args: { document: 'PublicSample', offset: 2, limit: 3, typeId: 'Part::Feature' }, method: 'list_objects', params: { docName: 'PublicSample', offset: 2, limit: 3, typeId: 'Part::Feature' } },
    { name: 'list_objects', args: {}, method: 'list_objects', params: { offset: 0, limit: 50 } },
    { name: 'get_object_info', args: { document: 'PublicSample', object: 'Body', properties: ['Label', 'Length'] }, method: 'get_object_info', params: { docName: 'PublicSample', objectName: 'Body', properties: ['Label', 'Length'] } },
    { name: 'get_object_info', args: { object: 'Body', properties: ['长度'] }, method: 'get_object_info', params: { objectName: 'Body', properties: ['长度'] } },
    { name: 'get_selection', args: {}, method: 'get_selection', params: {} },
    { name: 'measure_geometry', args: { document: 'PublicSample', object: 'Body', subelement: 'Face1' }, method: 'measure_geometry', params: { docName: 'PublicSample', objectName: 'Body', subelement: 'Face1' } },
    { name: 'list_drawing_pages', args: { document: 'PublicSample' }, method: 'list_drawing_pages', params: { docName: 'PublicSample' } },
    { name: 'inspect_drawing_page', args: { document: 'PublicSample', page: 'Page' }, method: 'inspect_drawing_page', params: { docName: 'PublicSample', pageName: 'Page' } },
    { name: 'set_view', args: { document: 'PublicSample', view: 'top', fit: true }, method: 'view_standard', params: { docName: 'PublicSample', view: 'top', fit: true } },
    { name: 'set_view', args: { view: 'front' }, method: 'view_standard', params: { view: 'front', fit: false } },
    { name: 'execute_script', args: { code: 'print("sample")', requestId: 'script_1' }, method: 'execute_script', params: { code: 'print("sample")', requestId: 'script_1' } },
    { name: 'get_script_status', args: { requestId: 'script_1' }, method: 'get_script_status', params: { requestId: 'script_1' } },
    { name: 'set_selection', args: { document: 'PublicSample', objects: [] }, method: 'set_selection', params: { docName: 'PublicSample', objectNames: [] } },
    { name: 'set_visibility', args: { document: 'PublicSample', objects: ['Body'], visible: false }, method: 'set_visibility', params: { docName: 'PublicSample', objectNames: ['Body'], visible: false } },
    { name: 'recompute_document', args: { document: 'PublicSample' }, method: 'recompute_document', params: { docName: 'PublicSample' } },
    { name: 'prepare_part_changes', args: { document: 'PublicSample', objects: ['Body'], operation: 'initialize_properties' }, method: 'prepare_part_changes', params: { docName: 'PublicSample', objectNames: ['Body'], operation: 'initialize_properties' } },
    { name: 'prepare_part_changes', args: { document: 'PublicSample', objects: ['Body'], operation: 'set_properties', values: { Body: { PartName: 'Demo' } } }, method: 'prepare_part_changes', params: { docName: 'PublicSample', objectNames: ['Body'], operation: 'set_properties', values: { Body: { PartName: 'Demo' } } } },
    { name: 'apply_part_changes', args: { token: 'a'.repeat(32) }, method: 'apply_part_changes', params: { token: 'a'.repeat(32) } },
  ];
  for (const item of cases) {
    const result = await client.callTool({ name: item.name, arguments: item.args });
    assert.equal(result.isError, undefined, item.name);
    assert.deepEqual(calls.at(-1), { method: item.method, params: item.params });
  }
  for (const [name, args, code] of [
    ['fit_view', { document: 'missing' }, 'DOCUMENT_NOT_FOUND'],
    ['get_object_info', { document: 'missing', object: 'Body' }, 'DOCUMENT_NOT_FOUND'],
    ['execute_script', { code: 'raise RuntimeError()', requestId: 'fail' }, 'SCRIPT_FAILED'],
  ] as const) {
    const result = await client.callTool({ name, arguments: args });
    assert.equal(result.isError, true);
    assert.equal((result.structuredContent?.error as { code: string }).code, code);
  }
});

test('严格 schema 拒绝未知字段、越界值和错误类型，且不派发任何桥请求', async (t) => {
  const { client, calls } = await session(t);
  const invalid: Array<[string, Record<string, unknown>]> = [

    ['set_selection', { objects: ['Body'] }], ['set_selection', { document: 'Doc', objects: ['Body', 'Body'] }],
    ['set_visibility', { document: 'Doc', objects: [], visible: false }], ['set_visibility', { document: 'Doc', objects: ['Body'], visible: 1 }],
    ['recompute_document', { document: 'Doc', save: true }],
    ['prepare_part_changes', { document: 'Doc', objects: ['Body'], operation: 'run_code' }],
    ['prepare_part_changes', { document: 'Doc', objects: ['Body'], operation: 'set_properties' }],
    ['prepare_part_changes', { document: 'Doc', objects: ['Body'], operation: 'initialize_properties', values: { Body: { PartName: 'x' } } }],
    ['prepare_part_changes', { document: 'Doc', objects: ['Body'], operation: 'set_properties', values: { Other: { PartName: 'x' } } }],
    ['prepare_part_changes', { document: 'Doc', objects: ['Body'], operation: 'set_properties', values: { Body: { Label: 'x' } } }],
    ['prepare_part_changes', { document: 'Doc', objects: ['Body'], operation: 'set_properties', values: { Body: { PartName: 'x'.repeat(2049) } } }],
    ['prepare_part_changes', { document: 'Doc', objects: ['Body','Link'], operation: 'set_properties', values: { Body: { PartName: 'x' }, Link: { PartNumber: 'y' } } }],
    ['apply_part_changes', { token: 'short' }], ['apply_part_changes', { token: 'a'.repeat(32), code: 'pass' }],
    ['get_document_info', { document: 'PublicSample', code: 'print(1)' }], ['list_objects', { limit: 101 }],
    ['list_objects', { offset: -1 }], ['list_objects', { offset: 0.5 }], ['list_objects', { limit: '50' }],
    ['list_objects', { offset: 2147483648 }], ['list_objects', { typeId: 'Part Design' }], ['list_objects', { typeId: '1Part' }],
    ['get_object_info', { object: 'Body', properties: Array(21).fill('Label') }], ['get_object_info', { object: '' }],
    ['get_object_info', { object: 'Body', properties: ['Label', 'Label'] }], ['get_object_info', { object: 'Body', properties: ['_private'] }],
    ['get_object_info', { object: 'Body', properties: ['Shape.Volume'] }], ['get_object_info', { object: 'Body', properties: ['1Value'] }],
    ['get_selection', { document: 'bad\u0000name' }], ['measure_geometry', { object: 'Body', subelement: 'Face0' }],
    ['get_selection', { document: 'bad\u0085name' }], ['get_selection', { document: 'bad\uD800name' }],
    ['measure_geometry', { object: 'Body', subelement: 'Edge10000000' }],
    ['measure_geometry', { object: 'Body', subelement: 'run()' }], ['inspect_drawing_page', {}],
    ['set_view', { view: 'perspective' }], ['set_view', { view: 'top', fit: 'yes' }],
    ['capture_view', { width: 127 }], ['capture_view', { height: 1025 }], ['capture_view', { path: 'image.png' }],
    ['execute_script', { code: '', requestId: 'one' }], ['execute_script', { code: 'x'.repeat(32769), requestId: 'one' }],
    ['execute_script', { code: 'print(1)', requestId: '-bad' }], ['get_script_status', { requestId: 'bad/id' }],
    ['execute_script', { code: 'x\u0000', requestId: 'nul' }], ['execute_script', { code: 'x\uD800', requestId: 'surrogate' }],
  ];
  for (const [name, args] of invalid) {
    const before = calls.length;
    const outcome = await client.callTool({ name, arguments: args }).then(value => ({ value }), error => ({ error }));
    assert.ok('error' in outcome || outcome.value.isError === true, name);
    assert.equal(calls.length, before, name);
  }
});

test('旧插件保留原工具兼容；新增能力必须由 supported_methods 明确声明', async (t) => {
  const { client, calls, state } = await session(t);
  state.methods = undefined;
  assert.equal((await client.callTool({ name: 'fit_view', arguments: {} })).isError, undefined);
  for (const [name, args] of [['get_document_info', {}], ['execute_script', { code: 'print(1)', requestId: 'one' }], ['get_script_status', { requestId: 'one' }]] as const) {
    const before = calls.length, result = await client.callTool({ name, arguments: args });
    assert.equal(result.isError, true);
    assert.equal((result.structuredContent?.error as { code: string }).code, 'UNSUPPORTED_CAPABILITY');
    assert.deepEqual(calls.slice(before).map(call => call.method), ['get_status']);
  }
  state.methods = ['get_document_info'];
  assert.equal((await client.callTool({ name: 'get_document_info', arguments: {} })).isError, undefined);
  const unsupported = await client.callTool({ name: 'capture_view', arguments: {} });
  assert.equal((unsupported.structuredContent?.error as { code: string }).code, 'UNSUPPORTED_CAPABILITY');
  state.methods = ['get_document_info', 123];
  const malformed = await client.callTool({ name: 'get_connection_status', arguments: {} });
  assert.equal((malformed.structuredContent?.error as { code: string }).code, 'INVALID_RESPONSE');
});

test('PNG 以 MCP image 内容返回一次，元数据不回显 Base64、多余字段或本机路径', async (t) => {
  const { client, calls } = await session(t);
  for (const args of [{ document: 'PublicSample', width: 128, height: 256 }, {}]) {
    const result = await client.callTool({ name: 'capture_view', arguments: args });
    assert.equal(result.isError, undefined);
    const width = 'width' in args ? args.width : 800, height = 'height' in args ? args.height : 600;
    assert.deepEqual(calls.at(-1), { method: 'capture_view', params: { ...('document' in args ? { docName: args.document } : {}), width, height } });
    const image = result.content.find(block => block.type === 'image');
    assert.ok(image && image.type === 'image');
    const expected = png(width!, height!).toString('base64');
    assert.equal(image.data, expected); assert.equal(image.mimeType, 'image/png');
    assert.equal(JSON.stringify(result).split(expected).length - 1, 1);
    assert.equal(JSON.stringify(result.structuredContent).includes(expected), false);
    assert.equal(JSON.stringify(result).includes('not-forwarded-local-path'), false);
    assert.equal(JSON.stringify(result).includes('extra_base64'), false);
    const metadata = result.structuredContent?.data as Record<string, unknown>;
    assert.equal(metadata.width, width); assert.equal(metadata.height, height); assert.equal(metadata.image_in_content, true);
  }
});

test('拒绝非规范 Base64、伪 PNG、宽高不符及超过 600 KiB 的图像', async (t) => {
  const { client, state } = await session(t);
  const invalid = [
    (value: Record<string, unknown>) => ({ ...value, data: String(value.data) + '\n' }),
    (value: Record<string, unknown>) => ({ ...value, data: 'AB==' }),
    (value: Record<string, unknown>) => ({ ...value, mime_type: 'image/jpeg' }),
    (value: Record<string, unknown>) => ({ ...value, width: 129 }),
    (value: Record<string, unknown>) => ({ ...value, data: Buffer.alloc(100).toString('base64') }),
    (value: Record<string, unknown>) => ({ ...value, data: Buffer.alloc(600 * 1024 + 1).toString('base64') }),
    (value: Record<string, unknown>) => { const image = Buffer.from(String(value.data), 'base64'); image.writeUInt32BE(129, 16); return { ...value, data: image.toString('base64') }; },
  ];
  for (const transform of invalid) {
    state.capture = transform;
    const result = await client.callTool({ name: 'capture_view', arguments: { width: 128, height: 128 } });
    assert.equal(result.isError, true);
    assert.equal((result.structuredContent?.error as { code: string }).code, 'INVALID_RESPONSE');
    assert.equal(result.content.some(block => block.type === 'image'), false);
  }
});


test('3个原生读取声明成功outputSchema并映射实例与修订条件，保留原生上下文扩展', async (t) => {
  const { client, calls, state } = await session(t);
  state.readProvenance = READ_CAPABILITY;
  state.read = () => nativeRead({ context: { active_body: { value: null, source: null, status: 'unavailable' } },
    bodies: [{ Name: 'Body', Tip: { Name: 'Pad' } }], modified: null, modified_status: 'unavailable' });
  const list = await client.listTools();
  for (const name of READ_METHODS) {
    const schema = list.tools.find(tool => tool.name === name)?.outputSchema;
    assert.equal(schema?.type, 'object', name);
    assert.ok(schema?.properties?.data, name);
    const args = { ...readArgs(name), expectedDocumentInstanceToken: INSTANCE_TOKEN, expectedRevision: 7 };
    const result = await client.callTool({ name, arguments: args });
    assert.equal(result.isError, undefined, name);
    const expected = { docName: 'PublicSample', expectedDocumentInstanceToken: INSTANCE_TOKEN, expectedRevision: 7,
      ...(name === 'list_objects' ? { offset: 0, limit: 50 } : {}), ...(name === 'get_object_info' ? { objectName: 'Body' } : {}) };
    assert.deepEqual(calls.at(-1), { method: name, params: expected });
    assert.deepEqual(result.structuredContent?.data, nativeRead({ context: { active_body: { value: null, source: null, status: 'unavailable' } },
      bodies: [{ Name: 'Body', Tip: { Name: 'Pad' } }], modified: null, modified_status: 'unavailable' }));
  }
  const tokenOnly = await client.callTool({ name: 'get_document_info', arguments: { expectedDocumentInstanceToken: INSTANCE_TOKEN } });
  assert.equal(tokenOnly.isError, undefined);
  assert.deepEqual(calls.at(-1)?.params, { expectedDocumentInstanceToken: INSTANCE_TOKEN });
});

test('旧桥接三个读取归一化为明确legacy未知状态，不沿用其伪修订字段', async (t) => {
  const { client, state } = await session(t);
  state.read = () => ({ document: 'PublicSample', revision: 900, instance_token: 'untrusted',
    document_instance_token: 'untrusted', revision_status: 'observing', existing_field: true });
  for (const name of READ_METHODS) {
    const result = await client.callTool({ name, arguments: readArgs(name) });
    assert.equal(result.isError, undefined, name);
    const data = result.structuredContent?.data as Record<string, unknown>;
    assert.equal(data.document_instance_token, null); assert.equal(data.instance_token, null); assert.equal(data.revision, null);
    assert.equal(data.revision_scope, 'unavailable'); assert.equal(data.revision_coverage, 'unknown');
    assert.equal(data.revision_status, 'legacy_plugin'); assert.equal(data.existing_field, true);
    assert.deepEqual(data.provenance, READ_PROVENANCE);
  }
});

test('旧桥接或未声明此方法的来源能力拒绝expected条件且只派发状态查询', async (t) => {
  const { client, calls, state } = await session(t);
  for (const flag of [undefined, { ...READ_CAPABILITY, version: 2 }, { ...READ_CAPABILITY, methods: [] }]) {
    state.readProvenance = flag;
    for (const name of READ_METHODS) {
      for (const guard of [{ expectedDocumentInstanceToken: INSTANCE_TOKEN }, { expectedDocumentInstanceToken: INSTANCE_TOKEN, expectedRevision: 7 }]) {
        const before = calls.length;
        const result = await client.callTool({ name, arguments: { ...readArgs(name), ...guard } });
        assert.equal(result.isError, true, name);
        assert.equal((result.structuredContent?.error as { code: string }).code, 'UNSUPPORTED_CAPABILITY');
        assert.deepEqual(calls.slice(before).map(call => call.method), ['get_status']);
      }
    }
  }
});

test('来源修订unknown保持null，桥接的实例及修订拒绝错误完整返回MCP isError', async (t) => {
  const { client, state } = await session(t);
  state.readProvenance = READ_CAPABILITY;
  for (const revision_status of ['observer_unavailable', 'registration_failed']) {
    state.read = () => nativeRead({ revision: null, revision_coverage: 'unknown', revision_status });
    for (const name of READ_METHODS) {
      const result = await client.callTool({ name, arguments: readArgs(name) });
      assert.equal(result.isError, undefined, name);
      const data = result.structuredContent?.data as Record<string, unknown>;
      assert.equal(data.revision, null); assert.equal(data.revision_coverage, 'unknown'); assert.equal(data.revision_status, revision_status);
    }
  }
  for (const code of ['DOCUMENT_INSTANCE_MISMATCH', 'DOCUMENT_REVISION_MISMATCH', 'DOCUMENT_REVISION_UNAVAILABLE', 'READ_RESPONSE_TOO_LARGE']) {
    state.readError = { code, message: 'synthetic read failure' };
    for (const name of READ_METHODS) {
      const result = await client.callTool({ name, arguments: { ...readArgs(name), expectedDocumentInstanceToken: INSTANCE_TOKEN, expectedRevision: 7 } });
      assert.equal(result.isError, true, name);
      assert.deepEqual(result.structuredContent?.error, state.readError);
    }
  }
});

test('expected参数严格校验，包括无token修订与不安全整数，拒绝时零桥接派发', async (t) => {
  const { client, calls } = await session(t);
  for (const name of READ_METHODS) {
    for (const guard of [{ expectedRevision: 0 }, { expectedDocumentInstanceToken: 'short' },
      { expectedDocumentInstanceToken: 'a'.repeat(129) }, { expectedDocumentInstanceToken: 'a/b'.repeat(10) },
      { expectedDocumentInstanceToken: INSTANCE_TOKEN, expectedRevision: -1 }, { expectedDocumentInstanceToken: INSTANCE_TOKEN, expectedRevision: 0.5 },
      { expectedDocumentInstanceToken: INSTANCE_TOKEN, expectedRevision: Number.MAX_SAFE_INTEGER + 1 },
      { expectedDocumentInstanceToken: INSTANCE_TOKEN, expectedRevision: null }, { expectedDocumentInstanceToken: null },
      { expectedDocumentInstanceToken: INSTANCE_TOKEN, expectedRevision: '7' }]) {
      const before = calls.length;
      const outcome = await client.callTool({ name, arguments: { ...readArgs(name), ...guard } }).then(value => ({ value }), error => ({ error }));
      assert.ok('error' in outcome || outcome.value.isError === true, name);
      assert.equal(calls.length, before, name);
    }
  }
});

test('声明v1来源的响应拒绝错误类型、虚假freshness和不一致修订，且不回显无效数据', async (t) => {
  const { client, state } = await session(t);
  state.readProvenance = READ_CAPABILITY;
  const invalid: unknown[] = [null, [], 'not an object', nativeRead({ document_instance_token: null }), nativeRead({ document_instance_token: 'short' }),
    nativeRead({ instance_token: 'b'.repeat(32) }), nativeRead({ revision: -1 }), nativeRead({ revision: Number.MAX_SAFE_INTEGER + 1 }),
    nativeRead({ revision: '7' }), nativeRead({ revision: null }), nativeRead({ revision_scope: 'complete_revision' }),
    nativeRead({ revision_coverage: 'complete' }), nativeRead({ revision_coverage: 'unknown' }), nativeRead({ revision_status: 'legacy_plugin' }),
    nativeRead({ revision_status: 'future_unknown_status' }), nativeRead({ revision_status: 'observer_unavailable' }),
    nativeRead({ provenance: { ...READ_PROVENANCE, write_guard: true } }), nativeRead({ provenance: { ...READ_PROVENANCE, recomputed: true } }),
    nativeRead({ provenance: { ...READ_PROVENANCE, geometry_freshness: 'verified' } }), nativeRead({ provenance: { ...READ_PROVENANCE, source: 'disk' } }),
    nativeRead({ provenance: { ...READ_PROVENANCE, extra: 'should-not-echo' } }),
    nativeRead({ source_kind: 'disk' }), nativeRead({ document: 12 })];
  for (const value of invalid) {
    state.read = () => value;
    const result = await client.callTool({ name: 'get_document_info', arguments: {} });
    assert.equal(result.isError, true);
    assert.equal((result.structuredContent?.error as { code: string }).code, 'INVALID_RESPONSE');
    assert.equal(JSON.stringify(result).includes('should-not-echo'), false);
  }
  for (const value of [{ ...READ_CAPABILITY, write_guard: true }, { ...READ_CAPABILITY, revision_coverage: 'complete' }, { ...READ_CAPABILITY, methods: [123] }]) {
    state.readProvenance = value;
    const result = await client.callTool({ name: 'get_document_info', arguments: {} });
    assert.equal((result.structuredContent?.error as { code: string }).code, 'INVALID_RESPONSE');
  }
});

test('原生三个读取总JSON预算为128KiB，按UTF8字节拒绝且不回显超大payload', async (t) => {
  const { client, state } = await session(t);
  state.readProvenance = READ_CAPABILITY;
  for (const payload of ['x'.repeat(128 * 1024), '界'.repeat(45 * 1024)]) {
    state.read = () => nativeRead({ payload });
    for (const name of READ_METHODS) {
      const result = await client.callTool({ name, arguments: readArgs(name) });
      assert.equal(result.isError, true, name);
      assert.equal((result.structuredContent?.error as { code: string }).code, 'READ_RESPONSE_TOO_LARGE');
      assert.ok(JSON.stringify(result).length < 1024);
    }
  }
});


test('兼容旧读取的归一化结果同样遵守128KiB预算', async (t) => {
  const { client, state } = await session(t);
  state.read = () => ({ document: 'PublicSample', payload: 'x'.repeat(128 * 1024) });
  for (const name of READ_METHODS) {
    const result = await client.callTool({ name, arguments: readArgs(name) });
    assert.equal(result.isError, true, name);
    assert.equal((result.structuredContent?.error as { code: string }).code, 'READ_RESPONSE_TOO_LARGE');
  }
});

const CHAT_TOOLS = ['get_capabilities', 'get_connection_status', 'list_documents', 'get_document_info', 'list_objects',
  'get_object_info', 'get_selection', 'measure_geometry', 'inspect_design', 'list_drawing_pages', 'inspect_drawing_page',
  'fit_view', 'set_axonometric_view', 'set_view', 'set_appearance', 'get_script_status',
  'prepare_part_changes', 'request_part_change_approval', 'get_part_change_status'];
const CHAT_EXCLUDED = ['capture_view', 'execute_script', 'set_selection', 'set_visibility', 'recompute_document', 'apply_part_changes'];

test('inspect_design真实stdio在full/chat均为只读严格工具，raw tools/call映射分页与原生来源', async (t) => {
  for (const scope of ['full', 'chat'] as const) {
    const { client, calls, state } = await session(t, scope);
    state.readProvenance = READ_CAPABILITY;
    const tool = (await client.listTools()).tools.find(item => item.name === 'inspect_design');
    assert.deepEqual(tool?.annotations, { readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: false });
    assert.equal(tool?.inputSchema.additionalProperties, false);
    assert.equal(tool?.outputSchema?.type, 'object');
    const result = await client.request({ method: 'tools/call', params: { name: 'inspect_design', arguments: {
      document: '公开设计', object: '草图', constraintOffset: 2, constraintLimit: 1,
      expectedDocumentInstanceToken: INSTANCE_TOKEN, expectedRevision: 7 } } });
    assert.equal(result.isError, undefined);
    assert.deepEqual(calls.at(-1), { method: 'inspect_design', params: { docName: '公开设计', objectName: '草图',
      constraintOffset: 2, constraintLimit: 1, expectedDocumentInstanceToken: INSTANCE_TOKEN, expectedRevision: 7 } });
    const data = result.structuredContent?.data as Record<string, unknown>;
    assert.equal(data.document, '公开设计');
    assert.equal((data.object as Record<string, unknown>).Name, '草图');
    const defaults = await client.callTool({ name: 'inspect_design', arguments: { object: 'Sketch' } });
    assert.equal(defaults.isError, undefined);
    assert.deepEqual(calls.at(-1)?.params, { objectName: 'Sketch', constraintOffset: 0, constraintLimit: 50 });
  }
});

test('inspect_design严格输入及修订守卫错误均零桥派发', async (t) => {
  const { client, calls } = await session(t);
  for (const args of [{}, { object: 'Sketch', code: 'pass' }, { object: 'bad\x00name' },
    { object: 'Sketch', constraintOffset: -1 }, { object: 'Sketch', constraintOffset: 2147483648 },
    { object: 'Sketch', constraintLimit: 101 }, { object: 'Sketch', constraintLimit: 0 },
    { object: 'Sketch', constraintLimit: '50' }, { object: 'Sketch', expectedRevision: 0 },
    { object: 'Sketch', expectedDocumentInstanceToken: 'short' },
    { object: 'Sketch', expectedDocumentInstanceToken: INSTANCE_TOKEN, expectedRevision: Number.MAX_SAFE_INTEGER + 1 }]) {
    const before = calls.length;
    const result = await client.request({ method: 'tools/call', params: { name: 'inspect_design', arguments: args } })
      .then(value => ({ value }), error => ({ error }));
    assert.ok('error' in result || result.value.isError === true);
    assert.equal(calls.length, before);
  }
});

test('inspect_design约束分页完整传回并保持跨页修订检查', async (t) => {
  const { client, calls, state } = await session(t);
  state.readProvenance = READ_CAPABILITY;
  state.read = (_method, params) => {
    const value = designRead(params), offset = Number(params.constraintOffset), limit = Number(params.constraintLimit);
    const items = Array.from({ length: Math.min(limit, Math.max(0, 3 - offset)) }, (_, i) => ({ index: offset + i,
      status: 'available', Type: 'Coincident', Name: null, First: 0, FirstPos: 1, Second: 1, SecondPos: 2,
      Third: null, ThirdPos: null, is_driving: { value: true, source: 'Sketch.getDriving', status: 'available' },
      is_active: { value: true, source: 'Sketch.getActive', status: 'available' },
      datum: { value: null, source: null, status: 'unsupported' } }));
    const has_more = offset + items.length < 3;
    value.sketch = { ...(value.sketch as object), constraints: { status: 'available', total_count: 3, offset,
      limit, items, has_more, next_offset: has_more ? offset + items.length : null } };
    return value;
  };
  const first = await client.callTool({ name: 'inspect_design', arguments: { object: 'Sketch', constraintLimit: 2 } });
  assert.equal(first.isError, undefined);
  const page = ((first.structuredContent?.data as Record<string, unknown>).sketch as Record<string, unknown>).constraints as Record<string, unknown>;
  assert.equal(page.next_offset, 2); assert.equal((page.items as unknown[]).length, 2);
  const next = await client.callTool({ name: 'inspect_design', arguments: { object: 'Sketch', constraintOffset: 2, constraintLimit: 2,
    expectedDocumentInstanceToken: INSTANCE_TOKEN, expectedRevision: 7 } });
  assert.equal(next.isError, undefined);
  const last = ((next.structuredContent?.data as Record<string, unknown>).sketch as Record<string, unknown>).constraints as Record<string, unknown>;
  assert.equal(last.has_more, false); assert.equal(last.next_offset, null);
  assert.deepEqual((last.items as Array<Record<string, unknown>>).map(item => item.index), [2]);
  assert.deepEqual(calls.at(-1)?.params, { objectName: 'Sketch', constraintOffset: 2, constraintLimit: 2,
    expectedDocumentInstanceToken: INSTANCE_TOKEN, expectedRevision: 7 });
});

test('inspect_design旧插件和未声明来源均拒绝，绝无legacy降级或诊断派发', async (t) => {
  const { client, calls, state } = await session(t);
  for (const [methods, capability] of [[METHODS.filter(name => name !== 'inspect_design'), READ_CAPABILITY],
    [METHODS, undefined], [METHODS, { ...READ_CAPABILITY, version: 2 }],
    [METHODS, { ...READ_CAPABILITY, methods: READ_METHODS }]] as const) {
    state.methods = methods; state.readProvenance = capability;
    const before = calls.length;
    const result = await client.callTool({ name: 'inspect_design', arguments: { object: 'Sketch' } });
    assert.equal(result.isError, true);
    assert.equal((result.structuredContent?.error as { code: string }).code, 'UNSUPPORTED_CAPABILITY');
    assert.deepEqual(calls.slice(before).map(call => call.method), ['get_status']);
  }
});

test('inspect_design拒绝畸形/跨目标/分页不符/过大数据，不回显未知字段', async (t) => {
  const { client, state } = await session(t);
  state.readProvenance = READ_CAPABILITY;
  for (const change of [null, { ...designRead(), FileName: '/private/never-forward' },
    { ...designRead(), object: { ...(designRead().object as object), Name: 'OtherSketch' } },
    { ...designRead(), revision: 8 },
    { ...designRead(), parameters_status: 'read_failed' },
    { ...designRead(), sketch: { ...(designRead().sketch as object), solver_invoked: true } },
    { ...designRead(), sketch: { ...(designRead().sketch as object), constraints: {
      status: 'available', total_count: 1, offset: 0, limit: 50, items: [], has_more: false, next_offset: null } } }]) {
    state.read = () => change;
    const result = await client.callTool({ name: 'inspect_design', arguments: { object: 'Sketch',
      expectedDocumentInstanceToken: INSTANCE_TOKEN, expectedRevision: 7 } });
    assert.equal(result.isError, true);
    assert.equal((result.structuredContent?.error as { code: string }).code, 'INVALID_RESPONSE');
    assert.equal(JSON.stringify(result).includes('/private/never-forward'), false);
  }
  state.read = () => ({ ...designRead(), unknown_payload: 'x'.repeat(128 * 1024) });
  const oversized = await client.callTool({ name: 'inspect_design', arguments: { object: 'Sketch' } });
  assert.equal((oversized.structuredContent?.error as { code: string }).code, 'READ_RESPONSE_TOO_LARGE');
  state.readError = { code: 'DOCUMENT_REVISION_MISMATCH', message: 'synthetic revision changed' };
  const changed = await client.callTool({ name: 'inspect_design', arguments: { object: 'Sketch',
    expectedDocumentInstanceToken: INSTANCE_TOKEN, expectedRevision: 7 } });
  assert.deepEqual(changed.structuredContent?.error, state.readError);
});
const appearanceArgs = { document: 'PublicSample', objects: ['Body'], operation: 'half_transparent' as const, requestId: 'appearance_1' };

function appearancePayload(code: string): { document: string; objects: string[]; operation: string; transparency?: number } {
  const encoded = /b64decode\("([A-Za-z0-9+/=]+)", validate=True\)/u.exec(code)?.[1];
  assert.ok(encoded, 'one fixed Base64 payload');
  return JSON.parse(Buffer.from(encoded, 'base64').toString('utf8'));
}

function appearanceJob(params: Record<string, unknown>, state = 'done'): Record<string, unknown> {
  const payload = appearancePayload(String(params.code));
  return { request_id: params.requestId, state, created_at: 1, started_at: state === 'queued' ? null : 2,
    finished_at: state === 'done' ? 3 : null, ...(state === 'done' ? { result: { document: payload.document, action: payload.operation,
      target_count: payload.objects.length, changed_count: payload.objects.length, saved: false, recomputed: false, replayed: false } } : {}) };
}

test('chat真实stdio仅注册精确19工具，manifest清单分组和effects均只描述启用能力', async (t) => {
  const { client, calls } = await session(t, 'chat');
  const tools = (await client.listTools()).tools;
  assert.deepEqual(tools.map(tool => tool.name).sort(), [...CHAT_TOOLS].sort());
  assert.equal(tools.find(tool => tool.name === 'set_appearance')?.annotations?.readOnlyHint, false);
  assert.equal(tools.find(tool => tool.name === 'set_appearance')?.annotations?.destructiveHint, false);
  assert.equal(tools.find(tool => tool.name === 'set_appearance')?.annotations?.idempotentHint, true);
  assert.equal(tools.find(tool => tool.name === 'set_appearance')?.annotations?.openWorldHint, false);
  const manifest = (await client.callTool({ name: 'get_capabilities', arguments: {} })).structuredContent!;
  assert.equal(manifest.tool_scope, 'chat');
  assert.deepEqual([...(manifest.tools as string[])].sort(), [...CHAT_TOOLS].sort());
  assert.deepEqual(Object.values(manifest.groups as Record<string, string[]>).flat().sort(), [...CHAT_TOOLS].sort());
  for (const key of ['general_actions', 'image', 'script']) {
    assert.equal(key in (manifest.effects as object), false, key);
  }
  assert.equal('phoenix_operations' in manifest, true);
  assert.equal('part_previews' in manifest, true);
  assert.deepEqual(manifest.part_approvals, PFT_PART_APPROVAL_CAPABILITY);
  assert.equal(calls.length, 0);
  for (const name of CHAT_EXCLUDED) {
    const result = await client.callTool({ name, arguments: {} }).then(value => ({ value }), error => ({ error }));
    assert.ok('error' in result || result.value.isError === true, name);
    assert.equal(calls.length, 0, name);
  }
});

test('typed外观参数严格拒绝且零桥派发，不允许提供任意code或未知字段', async (t) => {
  const { client, calls } = await session(t, 'chat');
  const invalid: Record<string, unknown>[] = [
    { objects: ['Body'], operation: 'opaque', requestId: 'one' },
    { ...appearanceArgs, document: '' }, { ...appearanceArgs, document: 'bad\u202ename' },
    { ...appearanceArgs, objects: [] }, { ...appearanceArgs, objects: ['Body', 'Body'] },
    { ...appearanceArgs, objects: Array.from({ length: 101 }, (_, index) => `Object${index}`) },
    { ...appearanceArgs, objects: [1] }, { ...appearanceArgs, objects: ['bad\u0000name'] },
    { ...appearanceArgs, operation: 'run_python' }, { ...appearanceArgs, operation: 1 },
    { ...appearanceArgs, requestId: '-invalid' }, { ...appearanceArgs, requestId: 'a'.repeat(65) },
    { ...appearanceArgs, code: 'pass' }, { ...appearanceArgs, save: true },
    { ...appearanceArgs, operation: 'set_transparency' },
    ...[null, true, false, -1, 101, 37.5, '37'].map(transparency => ({ ...appearanceArgs, operation: 'set_transparency', transparency })),
    ...['random_color', 'half_transparent', 'opaque'].map(operation => ({ ...appearanceArgs, operation, transparency: 37 })),
    { ...appearanceArgs, objects: Array.from({ length: 100 }, (_, index) => `界${index}`.padEnd(256, '界')) },
  ];
  for (const args of invalid) {
    const result = await client.callTool({ name: 'set_appearance', arguments: args }).then(value => ({ value }), error => ({ error }));
    assert.ok('error' in result || result.value.isError === true);
    assert.equal(calls.length, 0);
  }
});

test('typed外观只派发固定Python及Base64 JSON，原ID重放保留并可查询，兼容操作及透明度百分比均准确传递', async (t) => {
  const { client, calls, state } = await session(t, 'chat');
  const jobs = new Map<string, { code: string; value: Record<string, unknown> }>();
  let executions = 0;
  state.script = params => {
    const id = String(params.requestId), code = String(params.code), old = jobs.get(id);
    if (old) { assert.equal(code, old.code); return old.value; }
    executions += 1;
    const value = appearanceJob(params);
    jobs.set(id, { code, value });
    return value;
  };
  const unusual = { document: '文档"; __import__("os").system("false") #', objects: ['Object\'quoted', 'Body/with | symbols'] };
  const operations = [{ operation: 'random_color' }, { operation: 'half_transparent' }, { operation: 'opaque' },
    { operation: 'set_transparency', transparency: 0 }, { operation: 'set_transparency', transparency: 37 },
    { operation: 'set_transparency', transparency: 100 }] as const;
  for (const parameters of operations) {
    const suffix = parameters.operation === 'set_transparency' ? `_${parameters.transparency}` : '';
    const args = { ...unusual, ...parameters, requestId: `appearance_${parameters.operation}${suffix}` };
    for (let count = 0; count < 2; count++) {
      const result = await client.callTool({ name: 'set_appearance', arguments: args });
      assert.equal(result.isError, undefined);
      assert.equal(result.structuredContent?.data && (result.structuredContent.data as Record<string, unknown>).state, 'done');
      const call = calls.at(-1)!;
      assert.equal(call.method, 'execute_script');
      assert.equal(call.params.requestId, args.requestId);
      const code = String(call.params.code);
      assert.deepEqual(appearancePayload(code), { ...unusual, ...parameters });
      assert.equal(code.includes(unusual.document), false);
      for (const object of unusual.objects) assert.equal(code.includes(object), false);
      assert.ok(code.includes('prepare_appearance') && code.includes('apply_appearance'));
      assert.ok(code.includes('pft_bridge') && code.includes('Unrecognized cached Phoenix visual addon module'));
    }
  }
  assert.equal(executions, 6);
  await client.callTool({ name: 'get_script_status', arguments: { requestId: 'appearance_opaque' } });
  assert.deepEqual(calls.at(-1), { method: 'get_script_status', params: { requestId: 'appearance_opaque' } });
});

test('外观queued/starting/running保留真实状态，未知状态、失败、错ID或伪完成不能当成功', async (t) => {
  const { client, state } = await session(t, 'chat');
  for (const phase of ['queued', 'starting', 'running', 'done']) {
    state.script = params => appearanceJob(params, phase);
    const response = await client.callTool({ name: 'set_appearance', arguments: appearanceArgs });
    assert.equal(response.isError, undefined, phase);
    assert.equal((response.structuredContent?.data as Record<string, unknown>).state, phase);
  }
  for (const [transform, code] of [
    [(value: Record<string, unknown>) => ({ ...value, state: 'success', extra: 'should-not-echo' }), 'INVALID_RESPONSE'],
    [(value: Record<string, unknown>) => ({ ...value, request_id: 'another_request' }), 'INVALID_RESPONSE'],
    [(value: Record<string, unknown>) => ({ ...value, result: null }), 'INVALID_RESPONSE'],
    [(value: Record<string, unknown>) => ({ ...value, result: { ...(value.result as object), saved: true } }), 'INVALID_RESPONSE'],
    [(value: Record<string, unknown>) => ({ ...value, state: 'failed' }), 'SCRIPT_FAILED'],
    [(value: Record<string, unknown>) => ({ ...value, state: 'cancelled' }), 'SCRIPT_CANCELLED'],
  ] as const) {
    state.script = params => transform(appearanceJob(params));
    const result = await client.callTool({ name: 'set_appearance', arguments: appearanceArgs });
    assert.equal(result.isError, true);
    assert.equal((result.structuredContent?.error as Record<string, unknown>).code, code);
    assert.equal(JSON.stringify(result).includes('should-not-echo'), false);
  }
});

test('固定Python仅加载bridge同目录模块，未知cached模块离线拒绝且不执行其函数', () => {
  const args = { ...appearanceArgs, objects: ['Part\'quoted; no execution'] };
  const harness = String.raw`import importlib.util,json,pathlib,sys,tempfile,types
payload=json.loads(sys.stdin.read())
with tempfile.TemporaryDirectory(prefix="pft-appearance-fixed-script-") as temporary:
    root=pathlib.Path(temporary)/"PhoenixFreeCADTools";root.mkdir()
    bridge_file=root/"pft_bridge.py";bridge_file.write_text("# synthetic bridge\n")
    visual=root/"pft_visual_tools.py"
    visual.write_text("""def prepare_appearance(app,gui,document_name,object_names,action,transparency=None):
    result={"document":document_name,"objects":object_names,"operation":action}
    if transparency is not None: result["transparency"]=transparency
    return result
def apply_appearance(app,gui,plan):
    return plan
""")
    bridge=types.ModuleType("pft_bridge");bridge.__file__=str(bridge_file);sys.modules["pft_bridge"]=bridge
    sys.modules["FreeCAD"]=types.ModuleType("FreeCAD");sys.modules["FreeCADGui"]=types.ModuleType("FreeCADGui")
    if payload["cached"]=="unknown":
        bad=types.ModuleType("pft_visual_tools");bad.__file__=str(bridge_file)
        bad.prepare_appearance=lambda *args: (_ for _ in ()).throw(AssertionError("untrusted called"))
        sys.modules["pft_visual_tools"]=bad
    elif payload["cached"]=="trusted":
        spec=importlib.util.spec_from_file_location("pft_visual_tools",visual)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);sys.modules["pft_visual_tools"]=module
    namespace={}
    try:
        exec(payload["code"],namespace,namespace)
        print(json.dumps({"succeeded":True,"result":namespace["result"]}))
    except Exception as error:
        print(json.dumps({"succeeded":False,"error":str(error)}))
`;
  for (const parameters of [args, { ...args, operation: 'set_transparency' as const, transparency: 37 }]) {
    const code = pftAppearanceScript(parameters);
    for (const cached of ['absent', 'trusted', 'unknown']) {
      const child = spawnSync('python3', ['-B', '-c', harness], { input: JSON.stringify({ code, cached }), encoding: 'utf8' });
      assert.equal(child.status, 0, child.stderr);
      const value = JSON.parse(child.stdout) as { succeeded: boolean; result?: unknown; error?: string };
      assert.equal(value.succeeded, cached !== 'unknown');
      if (cached === 'unknown') assert.equal(value.error, 'Unrecognized cached Phoenix visual addon module');
      else assert.deepEqual(value.result, { document: parameters.document, objects: parameters.objects, operation: parameters.operation,
        ...(parameters.operation === 'set_transparency' ? { transparency: parameters.transparency } : {}) });
    }
  }
});


const DESK_ENV = { PHOENIX_DESK_NODE: '/opt/public-example/node',
  PHOENIX_DESK_CLI: '/opt/public-example/desk/scripts/desk-web-service.mjs', PHOENIX_DESK_STATE_DIR: '/var/public-example/private-state' };
const DESK_TOOLS = ['desk_get_connection_status', 'desk_search_index', 'desk_query_references', 'desk_query_cached_metadata', 'desk_get_cached_thumbnail'];

test('Desk 可选 full 范围精确发现30工具；离线清单不探测配置或服务', async t => {
  const { client, calls } = await session(t, 'full', DESK_ENV);
  const list = await client.listTools();
  assert.deepEqual(list.tools.map(tool => tool.name).sort(), [...TOOL_NAMES, ...DESK_TOOLS].sort());
  const reply = await client.callTool({ name: 'get_capabilities', arguments: {} });
  const caps = reply.structuredContent!;
  assert.deepEqual((caps.groups as Record<string, unknown>).desk, DESK_TOOLS);
  assert.deepEqual(caps.desk, { configured: true, connection_checked: false, query_endpoints_checked: false,
    scope: 'explicit_workspace_per_call', auto_start: false, cad_access: 'none', batch_snapshot: 'not_atomic' });
  assert.equal(JSON.stringify(reply).includes('/opt/public-example'), false);
  for (const name of DESK_TOOLS) assert.deepEqual(list.tools.find(tool => tool.name === name)?.annotations,
    { readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: false });
  assert.equal(calls.length, 0);
});

test('chat 即使有 Desk 候选配置仍精确19工具；不新增权限或披露配置', async t => {
  const { client, calls } = await session(t, 'chat', DESK_ENV);
  const list = await client.listTools();
  assert.equal(list.tools.length, 19);
  assert.equal(list.tools.some(tool => DESK_TOOLS.includes(tool.name)), false);
  const reply = await client.callTool({ name: 'get_capabilities', arguments: {} });
  assert.equal(reply.structuredContent?.desk, undefined);
  assert.equal((reply.structuredContent?.groups as Record<string, unknown>).desk, undefined);
  assert.equal(calls.length, 0);
});

test('Desk 查询在派发前拒绝缺范围、穿越、重复、超限和额外指令字段', async t => {
  const { client, calls } = await session(t, 'full', DESK_ENV);
  const search = { workspace: '/opt/public-example/workspace', query: 'example' };
  const refs = { workspace: '/opt/public-example/workspace', rels: ['parts/Example.FCStd'] };
  for (const [name, args] of [
    ['desk_search_index', { query: 'example' }],
    ['desk_search_index', { ...search, workspace: 'relative' }],
    ['desk_search_index', { ...search, query: '' }],
    ['desk_search_index', { ...search, query: 'x'.repeat(257) }],
    ['desk_search_index', { ...search, url: 'http://127.0.0.1/' }],
    ['desk_query_references', { ...refs, rels: ['../External.FCStd'] }],
    ['desk_query_references', { ...refs, rels: ['./parts/Example.FCStd'] }],
    ['desk_query_references', { ...refs, rels: ['parts/Example.FCStd', 'parts/Example.FCStd'] }],
    ['desk_query_references', { ...refs, rels: [] }],
    ['desk_query_references', { ...refs, limit: 101 }],
    ['desk_query_references', { ...refs, view: 'incoming' }],
    ['desk_get_connection_status', { action: 'ensure' }],
  ] as Array<[string, Record<string, unknown>]>) {
    const result = await client.callTool({ name, arguments: args });
    assert.equal(result.isError, true, name);
  }
  assert.equal(calls.length, 0);
});


test('chat 显式 Desk opt-in 只增加五个业务只读工具，仍拒绝六个完整权限入口', async t => {
  const { client, calls } = await session(t, 'chat', { ...DESK_ENV, PHOENIX_DESK_CHAT_READS: '1' });
  const tools = (await client.listTools()).tools;
  assert.deepEqual(tools.map(tool => tool.name).sort(), [...CHAT_TOOLS, ...DESK_TOOLS].sort());
  const manifest = (await client.callTool({ name: 'get_capabilities', arguments: {} })).structuredContent!;
  assert.equal(manifest.tool_scope, 'chat');
  assert.equal((manifest.desk as Record<string, unknown>).explicit_chat_opt_in, true);
  assert.deepEqual((manifest.groups as Record<string, unknown>).desk, DESK_TOOLS);
  assert.equal('phoenix_operations' in manifest, true);
  assert.equal('part_previews' in manifest, true);
  assert.deepEqual(manifest.part_approvals, PFT_PART_APPROVAL_CAPABILITY);
  for (const name of DESK_TOOLS) assert.equal(tools.find(tool => tool.name === name)?.annotations?.readOnlyHint, true);
  for (const name of CHAT_EXCLUDED) {
    const result = await client.callTool({ name, arguments: {} }).then(value => ({ value }), error => ({ error }));
    assert.ok('error' in result || result.value.isError === true, name);
    assert.equal(calls.length, 0, name);
  }
});


test('重算严格成功outputSchema保留0/正计数但不声称完成；结构化错误不被成功schema吞掉', async t => {
  const { client, calls, state } = await session(t);
  const tools = await client.listTools(), tool = tools.tools.find(item => item.name === 'recompute_document');
  assert.equal(tools.tools.length, 25);
  assert.equal(tool?.outputSchema?.type, 'object');
  assert.match(tool!.description!, /不承诺原生回调或跨文档隔离/u);
  const manifest = await client.callTool({ name: 'get_capabilities', arguments: {} });
  assert.deepEqual(manifest.structuredContent?.recompute_result, PFT_RECOMPUTE_CAPABILITY);
  for (const count of [0, 4]) {
    state.recompute = params => ({ document: params.docName, saved: false, recompute_invoked: true,
      native_object_count: count, checked_object_count: 4, object_status: 'no_invalid_or_pending',
      native_completion: 'not_verified', geometry_validity: 'not_verified', geometry_freshness: 'not_verified' });
    const before = calls.length;
    const result = await client.callTool({ name: 'recompute_document', arguments: { document: 'PublicSample' } });
    assert.equal(result.isError, undefined);
    assert.deepEqual(calls.slice(before).map(call => call.method), ['get_status', 'recompute_document']);
    const data = result.structuredContent?.data as Record<string, unknown>;
    assert.equal(data.native_object_count, count);
    assert.equal(data.native_completion, 'not_verified'); assert.equal(data.geometry_validity, 'not_verified');
    assert.equal(data.geometry_freshness, 'not_verified');
    assert.equal(Object.hasOwn(data, 'native_success'), false); assert.equal(Object.hasOwn(data, 'recomputed'), false);
  }
  for (const code of ['RECOMPUTE_FROZEN', 'RECOMPUTE_PRECHECK_FAILED', 'RECOMPUTE_UNVERIFIED', 'RECOMPUTE_FAILED', 'RECOMPUTE_INCOMPLETE', 'DOCUMENT_NOT_FOUND']) {
    state.recomputeError = { code, message: '测试固定重算错误' };
    const before = calls.length;
    const result = await client.callTool({ name: 'recompute_document', arguments: { document: 'PublicSample' } });
    assert.equal(result.isError, true); assert.deepEqual(result.structuredContent?.error, state.recomputeError);
    assert.deepEqual(calls.slice(before).map(call => call.method), ['get_status', 'recompute_document']);
  }
});

test('旧重算能力拒绝派发；畸形已派发结果整笔拒绝且仅一次原生调用', async t => {
  const { client, calls, state } = await session(t);
  for (const capability of [undefined, null, {}, { ...PFT_RECOMPUTE_CAPABILITY, native_result_kind: 'boolean' }]) {
    state.recomputeCapability = capability;
    const before = calls.length;
    const result = await client.callTool({ name: 'recompute_document', arguments: { document: 'PublicSample' } });
    assert.equal(result.isError, true);
    assert.equal((result.structuredContent?.error as Record<string, unknown>).code, capability === undefined ? 'UNSUPPORTED_CAPABILITY' : 'INVALID_RESPONSE');
    assert.deepEqual(calls.slice(before).map(call => call.method), ['get_status']);
  }
  state.recomputeCapability = PFT_RECOMPUTE_CAPABILITY;
  for (const invalid of [{ document: 'PublicSample', native_success: true, recomputed: true },
    { document: 'OtherSample', saved: false, recompute_invoked: true, native_object_count: 0, checked_object_count: 0,
      object_status: 'no_invalid_or_pending', native_completion: 'not_verified', geometry_validity: 'not_verified', geometry_freshness: 'not_verified' }]) {
    state.recompute = () => invalid;
    const before = calls.length;
    const result = await client.callTool({ name: 'recompute_document', arguments: { document: 'PublicSample' } });
    assert.equal(result.isError, true);
    assert.deepEqual(result.structuredContent?.error, { code: 'INVALID_RESPONSE',
      message: 'FreeCAD 重算请求可能已执行，结果未能验证；请检查当前文档，不要自动重试。' });
    assert.deepEqual(calls.slice(before).map(call => call.method), ['get_status', 'recompute_document']);
  }
});

test('chat仍19工具且离线元数据不宣称排除的重算能力', async t => {
  const { client, calls } = await session(t, 'chat');
  assert.equal((await client.listTools()).tools.length, 19);
  const manifest = await client.callTool({ name: 'get_capabilities', arguments: {} });
  assert.equal(Object.hasOwn(manifest.structuredContent!, 'recompute_result'), false);
  assert.equal(calls.length, 0);
});


function approvalStatus(token: string, state = 'awaiting_confirmation'): Record<string, unknown> {
  return { document: 'PublicSample', operation: 'normalize_names', token, state, changed_count: 1,
    requires_confirmation: true, saves_document: false, storage: 'memory_only', native_confirmation: false,
    result: null, error_code: null };
}

test('chat原生零件链精确映射预览/请求/查询，pending不是写入且仅GUI能确认', async t => {
  const { client, calls, state } = await session(t, 'chat');
  const tools = (await client.listTools()).tools;
  assert.equal(tools.length, 19);
  assert.equal(tools.some(tool => tool.name === 'apply_part_changes'), false);
  const request = tools.find(tool => tool.name === 'request_part_change_approval')!;
  assert.deepEqual(request.annotations, { readOnlyHint: false, destructiveHint: false, idempotentHint: true, openWorldHint: false });
  assert.equal(request.inputSchema.additionalProperties, false); assert.equal(request.outputSchema?.type, 'object');
  assert.match(request.description!, /完整预览/u); assert.match(request.description!, /等待用户亲自点击应用/u);
  assert.equal(tools.find(tool => tool.name === 'get_part_change_status')?.annotations?.readOnlyHint, true);
  const token = 'pft_native_preview_token01';
  const prepared = await client.callTool({ name: 'prepare_part_changes', arguments: {
    document: 'PublicSample', objects: ['Body'], operation: 'set_properties', values: { Body: { PartName: 'Neutral bracket' } } } });
  assert.equal(prepared.isError, undefined);
  assert.deepEqual(calls.at(-1), { method: 'prepare_part_changes', params: { docName: 'PublicSample', objectNames: ['Body'],
    operation: 'set_properties', values: { Body: { PartName: 'Neutral bracket' } } } });
  for (const [method, pendingState] of [['request_part_change_approval', 'awaiting_confirmation'], ['get_part_change_status', 'applying']]) {
    state.partApproval = () => ({ ...approvalStatus(token, pendingState), native_confirmation: pendingState === 'applying' });
    const before = calls.length, result = await client.callTool({ name: method!, arguments: { token } });
    assert.equal(result.isError, undefined);
    assert.deepEqual(calls.slice(before), [{ method: 'get_status', params: {} }, { method, params: { token } }]);
    const data = result.structuredContent?.data as Record<string, unknown>;
    assert.equal(data.state, pendingState); assert.equal(data.result, null); assert.equal(data.requires_confirmation, true);
    assert.equal(data.saves_document, false);
  }
  state.partApproval = () => ({ ...approvalStatus(token, 'applied'), native_confirmation: true,
    result: { changed_count: 1, saves_document: false } });
  const applied = await client.callTool({ name: 'get_part_change_status', arguments: { token } });
  assert.equal(applied.isError, undefined);
  assert.deepEqual((applied.structuredContent?.data as Record<string, unknown>).result, { changed_count: 1, saves_document: false });
  assert.equal(calls.some(call => call.method === 'apply_part_changes' || call.method === 'execute_script'), false);
});

test('原生确认只接受token，不接受AI确认标志；严格输入拒绝时零桥派发', async t => {
  for (const scope of ['full', 'chat'] as const) {
    const { client, calls } = await session(t, scope);
    for (const name of ['request_part_change_approval', 'get_part_change_status']) {
      for (const args of [{}, { token: 'short' }, { token: 'x'.repeat(129) }, { token: true },
        { token: 'a'.repeat(32), confirmed: true }, { token: 'a'.repeat(32), approved: true },
        { token: 'a'.repeat(32), grant: 'human' }, { token: 'a'.repeat(32), native_confirmation: true },
        { token: 'a'.repeat(32), document: 'PublicSample' }, { token: 'a'.repeat(32), code: 'pass' }]) {
        const before = calls.length, result = await client.callTool({ name, arguments: args });
        assert.equal(result.isError, true, name); assert.equal(calls.length, before);
      }
    }
  }
});

test('新确认链旧/畸形能力或缺任一方法均拒绝，零窗口与确认派发', async t => {
  const { client, calls, state } = await session(t, 'chat');
  for (const methods of [undefined, ['prepare_part_changes'], ['request_part_change_approval'], ['get_part_change_status']]) {
    state.methods = methods;
    for (const name of ['request_part_change_approval', 'get_part_change_status']) {
      const before = calls.length, result = await client.callTool({ name, arguments: { token: 'a'.repeat(32) } });
      assert.equal(result.isError, true); assert.equal((result.structuredContent?.error as Record<string, unknown>).code, 'UNSUPPORTED_CAPABILITY');
      assert.deepEqual(calls.slice(before).map(call => call.method), ['get_status']);
    }
  }
  state.methods = METHODS;
  for (const capability of [undefined, null, {}, { ...PFT_PART_APPROVAL_CAPABILITY, confirmation: 'external_flag' },
    { ...PFT_PART_APPROVAL_CAPABILITY, max_pending: 2 }, { ...PFT_PART_APPROVAL_CAPABILITY, extra: true }]) {
    state.partApprovalCapability = capability;
    for (const name of ['request_part_change_approval', 'get_part_change_status']) {
      const before = calls.length, result = await client.callTool({ name, arguments: { token: 'a'.repeat(32) } });
      assert.equal(result.isError, true);
      assert.equal((result.structuredContent?.error as Record<string, unknown>).code, capability === undefined ? 'UNSUPPORTED_CAPABILITY' : 'INVALID_RESPONSE');
      assert.deepEqual(calls.slice(before).map(call => call.method), ['get_status']);
    }
  }
});

test('原生状态矛盾/错token/多余数据不能成为完成，不回显未知响应', async t => {
  const { client, calls, state } = await session(t, 'chat'), token = 'a'.repeat(32);
  for (const invalid of [{ ...approvalStatus(token), confirmed: true }, { ...approvalStatus('b'.repeat(32)) },
    { ...approvalStatus(token, 'applied') }, { ...approvalStatus(token, 'failed') },
    { ...approvalStatus(token), result: { changed_count: 1, saves_document: false } },
    { ...approvalStatus(token), error_code: 'PART_CHANGE_FAILED' },
    { ...approvalStatus(token), detail: 'untrusted-provider-body'.repeat(300) }]) {
    state.partApproval = () => invalid;
    for (const name of ['request_part_change_approval', 'get_part_change_status']) {
      const before = calls.length, result = await client.callTool({ name, arguments: { token } });
      assert.equal(result.isError, true); assert.equal((result.structuredContent?.error as Record<string, unknown>).code, 'INVALID_RESPONSE');
      assert.equal(JSON.stringify(result).includes('untrusted-provider-body'), false);
      assert.deepEqual(calls.slice(before).map(call => call.method), ['get_status', name]);
    }
  }
  state.partApproval = () => ({ ...approvalStatus(token, 'failed'), error_code: 'PART_APPROVAL_UI_UNAVAILABLE' });
  const failed = await client.callTool({ name: 'get_part_change_status', arguments: { token } });
  assert.equal(failed.isError, undefined); assert.equal((failed.structuredContent?.data as Record<string, unknown>).state, 'failed');
});

test('原生确认超时/坏JSON最多一次请求，原token可显式查询或重请求，不自动重试', async t => {
  const { client, calls, state } = await session(t, 'chat', { PHOENIX_TIMEOUT_MS: '100' }), token = 'a'.repeat(32);
  for (const [mode, code] of [['timeout', 'BRIDGE_TIMEOUT'], ['invalid_json', 'INVALID_RESPONSE']]) {
    state.partApprovalMode = mode!;
    const before = calls.length, result = await client.callTool({ name: 'request_part_change_approval', arguments: { token } });
    assert.equal(result.isError, true); assert.equal((result.structuredContent?.error as Record<string, unknown>).code, code);
    assert.match(String((result.structuredContent?.error as Record<string, unknown>).message), /原 token 查询/u);
    assert.equal(JSON.stringify(result).includes('untrusted-provider-body'), false);
    assert.deepEqual(calls.slice(before).map(call => call.method), ['get_status', 'request_part_change_approval']);
  }
  state.partApprovalMode = 'normal';
  for (const name of ['get_part_change_status', 'request_part_change_approval']) {
    const before = calls.length, result = await client.callTool({ name, arguments: { token } });
    assert.equal(result.isError, undefined);
    assert.deepEqual(calls.slice(before), [{ method: 'get_status', params: {} }, { method: name, params: { token } }]);
  }
});

test('旧full外部apply仍仅full保留，原生确认业务错误结构化传回', async t => {
  const { client, calls, state } = await session(t);
  state.partApprovalCapability = undefined;
  state.methods = ['prepare_part_changes', 'apply_part_changes'];
  const token = 'a'.repeat(32);
  const applied = await client.callTool({ name: 'apply_part_changes', arguments: { token } });
  assert.equal(applied.isError, undefined); assert.deepEqual(calls.at(-1), { method: 'apply_part_changes', params: { token } });
  state.partApprovalCapability = PFT_PART_APPROVAL_CAPABILITY; state.methods = METHODS;
  state.partApprovalError = { code: 'PART_APPROVAL_BUSY', message: '测试固定原生确认错误' };
  const busy = await client.callTool({ name: 'request_part_change_approval', arguments: { token } });
  assert.equal(busy.isError, true); assert.deepEqual(busy.structuredContent?.error, state.partApprovalError);
});


const padPrepareArgs = { document: 'PublicSample', objects: ['Pad'], operation: 'set_pad_length', body: 'Body', lengthMm: 12 };
function padAppliedStatus(token: string): Record<string, unknown> {
  const geometry = () => ({ volume_mm3: 2400, dimensions_mm: [20, 10, 12], solids: 1 });
  return { ...approvalStatus(token, 'applied'), operation: 'set_pad_length', native_confirmation: true,
    result: { changed_count: 1, saves_document: false, design_result: { kind: 'pad_length', body: 'Body', pad: 'Pad',
      length_mm: 12, recompute_count: 2, checked_objects: 3, geometry: { pad: geometry(), body: geometry() },
      validation: 'native_valid_one_solid_and_length_scaled_volume' } } };
}

test('Pad离线能力和operation声明不新增工具，不把注册清单当作运行能力', async t => {
  for (const [scope, desk, expected] of [['full', false, 25], ['chat', false, 19], ['full', true, 30], ['chat', true, 24]] as const) {
    const { client, calls } = await session(t, scope, desk ? { ...DESK_ENV, ...(scope === 'chat' ? { PHOENIX_DESK_CHAT_READS: '1' } : {}) } : {});
    const tools = (await client.listTools()).tools;
    assert.equal(tools.length, expected);
    const prepare = tools.find(tool => tool.name === 'prepare_part_changes')!;
    assert.match(prepare.description!, /原生窗口确认/u); assert.match(prepare.description!, /不虚构新几何/u);
    const value = (await client.callTool({ name: 'get_capabilities', arguments: {} })).structuredContent!;
    assert.deepEqual(value.pad_length_write, PFT_PAD_LENGTH_CAPABILITY);
    assert.deepEqual(value.part_approvals, PFT_PART_APPROVAL_CAPABILITY);
    assert.ok((value.phoenix_operations as string[]).includes('set_pad_length'));
    assert.equal(value.connection_checked, false); assert.equal(value.runtime_supported_checked, false);
    assert.equal(calls.length, 0);
  }
});

test('full/chat Pad预览准确映射Body与mm，原token原生状态保留完整几何回执', async t => {
  for (const scope of ['full', 'chat'] as const) {
    const { client, calls, state } = await session(t, scope);
    state.padLengthCapability = PFT_PAD_LENGTH_CAPABILITY;
    const prepared = await client.callTool({ name: 'prepare_part_changes', arguments: padPrepareArgs });
    assert.equal(prepared.isError, undefined);
    assert.deepEqual(calls, [{ method: 'get_status', params: {} }, { method: 'prepare_part_changes', params: {
      docName: 'PublicSample', objectNames: ['Pad'], operation: 'set_pad_length', bodyName: 'Body', lengthMm: 12 } }]);
    const token = 'pft_pad_mcp_confirmation01';
    state.partApproval = () => ({ ...approvalStatus(token), operation: 'set_pad_length' });
    const pending = await client.callTool({ name: 'request_part_change_approval', arguments: { token } });
    assert.equal(pending.isError, undefined);
    assert.equal((pending.structuredContent?.data as Record<string, unknown>).state, 'awaiting_confirmation');
    assert.equal((pending.structuredContent?.data as Record<string, unknown>).native_confirmation, false);
    state.partApproval = () => padAppliedStatus(token);
    const result = await client.callTool({ name: 'get_part_change_status', arguments: { token } });
    assert.equal(result.isError, undefined);
    assert.deepEqual((result.structuredContent?.data as Record<string, unknown>).result, padAppliedStatus(token).result);
    assert.equal(calls.some(call => ['apply_part_changes', 'execute_script', 'recompute_document'].includes(call.method)), false);
  }
});

test('Pad与旧操作参数互斥，禁止外部确认标志，schema拒绝时零RPC', async t => {
  const invalid = [
    { ...padPrepareArgs, body: undefined }, { ...padPrepareArgs, lengthMm: undefined },
    { ...padPrepareArgs, objects: [] }, { ...padPrepareArgs, objects: ['Pad', 'Other'] },
    { ...padPrepareArgs, body: '' }, { ...padPrepareArgs, body: 'A'.repeat(65) },
    { ...padPrepareArgs, body: ' Body' }, { ...padPrepareArgs, body: 'body/part' },
    { ...padPrepareArgs, objects: ['..'] }, { ...padPrepareArgs, objects: ['A'.repeat(65)] },
    { ...padPrepareArgs, objects: ['prefix:Pad'] }, { ...padPrepareArgs, lengthMm: 0 }, { ...padPrepareArgs, lengthMm: 0.009 },
    { ...padPrepareArgs, lengthMm: 10000.001 }, { ...padPrepareArgs, lengthMm: true }, { ...padPrepareArgs, lengthMm: '12' },
    { ...padPrepareArgs, values: { Pad: { PartName: 'Neutral plate' } } }, { ...padPrepareArgs, confirmed: true },
    { ...padPrepareArgs, approved: true }, { ...padPrepareArgs, grant: 'synthetic-flag' },
    ...['normalize_names', 'sync_link_labels', 'initialize_properties', 'recommend_properties'].flatMap(operation => [
      { document: 'PublicSample', objects: ['Pad'], operation, body: 'Body' },
      { document: 'PublicSample', objects: ['Pad'], operation, lengthMm: 12 },
    ]),
    { document: 'PublicSample', objects: ['Pad'], operation: 'set_properties', values: { Pad: { PartName: 'Neutral plate' } }, body: 'Body' },
  ];
  for (const scope of ['full', 'chat'] as const) {
    const { client, calls } = await session(t, scope);
    for (const args of invalid) {
      const result = await client.callTool({ name: 'prepare_part_changes', arguments: args });
      assert.equal(result.isError, true, JSON.stringify(args));
    }
    assert.equal(calls.length, 0);
  }
});

test('Pad需三方法和双exact能力，旧插件仍可处理原五操作但不能降级Pad预览', async t => {
  const { client, calls, state } = await session(t);
  const invalid: Array<[unknown, unknown, unknown, string]> = [
    [METHODS, undefined, PFT_PART_APPROVAL_CAPABILITY, 'UNSUPPORTED_CAPABILITY'],
    [METHODS, null, PFT_PART_APPROVAL_CAPABILITY, 'INVALID_RESPONSE'],
    [METHODS, { ...PFT_PAD_LENGTH_CAPABILITY, extra: true }, PFT_PART_APPROVAL_CAPABILITY, 'INVALID_RESPONSE'],
    [METHODS, { ...PFT_PAD_LENGTH_CAPABILITY, confirmation: 'external_flag' }, PFT_PART_APPROVAL_CAPABILITY, 'INVALID_RESPONSE'],
    [METHODS, PFT_PAD_LENGTH_CAPABILITY, undefined, 'UNSUPPORTED_CAPABILITY'],
    [METHODS, PFT_PAD_LENGTH_CAPABILITY, { ...PFT_PART_APPROVAL_CAPABILITY, extra: true }, 'INVALID_RESPONSE'],
    ...['prepare_part_changes', 'request_part_change_approval', 'get_part_change_status'].map(method => [
      METHODS.filter(value => value !== method), PFT_PAD_LENGTH_CAPABILITY, PFT_PART_APPROVAL_CAPABILITY, 'UNSUPPORTED_CAPABILITY',
    ] as [unknown, unknown, unknown, string]),
  ];
  for (const [methods, capability, approvals, code] of invalid) {
    state.methods = methods; state.padLengthCapability = capability; state.partApprovalCapability = approvals;
    const before = calls.length, result = await client.callTool({ name: 'prepare_part_changes', arguments: padPrepareArgs });
    assert.equal(result.isError, true); assert.equal((result.structuredContent?.error as Record<string, unknown>).code, code);
    assert.deepEqual(calls.slice(before), [{ method: 'get_status', params: {} }]);
  }
  state.methods = METHODS; state.padLengthCapability = undefined; state.partApprovalCapability = undefined;
  for (const operation of ['normalize_names', 'sync_link_labels', 'initialize_properties', 'recommend_properties', 'set_properties']) {
    const before = calls.length;
    const args = { document: 'PublicSample', objects: ['Pad'], operation,
      ...(operation === 'set_properties' ? { values: { Pad: { PartName: 'Neutral plate' } } } : {}) };
    assert.equal((await client.callTool({ name: 'prepare_part_changes', arguments: args })).isError, undefined);
    assert.deepEqual(calls.slice(before).map(call => call.method), ['get_status', 'prepare_part_changes']);
  }
});

test('Pad虚假完成与几何坏结果被整笔拒绝，固定失败状态保留且不回显原文', async t => {
  const { client, calls, state } = await session(t, 'chat'), token = 'pft_pad_mcp_confirmation01';
  state.padLengthCapability = PFT_PAD_LENGTH_CAPABILITY;
  const applied = padAppliedStatus(token);
  const malformed = [
    { ...applied, native_confirmation: false }, { ...applied, changed_count: 0 },
    { ...applied, result: { changed_count: 1, saves_document: false } },
    { ...applied, result: { ...(applied.result as Record<string, unknown>), design_result: {
      ...((applied.result as Record<string, unknown>).design_result as Record<string, unknown>), validation: 'synthetic-untrusted-validation' } } },
    { ...applied, detail: 'synthetic-untrusted-validation' },
  ];
  for (const value of malformed) {
    state.partApproval = () => value;
    const before = calls.length, result = await client.callTool({ name: 'get_part_change_status', arguments: { token } });
    assert.equal(result.isError, true); assert.equal((result.structuredContent?.error as Record<string, unknown>).code, 'INVALID_RESPONSE');
    assert.equal(JSON.stringify(result).includes('synthetic-untrusted-validation'), false);
    assert.deepEqual(calls.slice(before).map(call => call.method), ['get_status', 'get_part_change_status']);
  }
  state.partApproval = () => ({ ...approvalStatus(token, 'failed'), operation: 'set_pad_length', error_code: 'PAD_RECOMPUTE_UNVERIFIED' });
  const failed = await client.callTool({ name: 'get_part_change_status', arguments: { token } });
  assert.equal(failed.isError, undefined);
  assert.equal((failed.structuredContent?.data as Record<string, unknown>).error_code, 'PAD_RECOMPUTE_UNVERIFIED');
});
