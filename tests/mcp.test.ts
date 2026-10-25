// SPDX-License-Identifier: Apache-2.0
import { test, type TestContext } from 'node:test';
import assert from 'node:assert/strict';
import { createServer as httpServer } from 'node:http';
import { fileURLToPath } from 'node:url';
import { deflateSync } from 'node:zlib';
import { Client } from '@modelcontextprotocol/client';
import { StdioClientTransport } from '@modelcontextprotocol/client/stdio';

const TOOL_NAMES = ['get_capabilities', 'get_connection_status', 'list_documents', 'fit_view', 'set_axonometric_view',
  'get_document_info', 'list_objects', 'get_object_info', 'get_selection', 'measure_geometry', 'list_drawing_pages',
  'inspect_drawing_page', 'set_view', 'capture_view', 'execute_script', 'get_script_status'];
const METHODS = ['get_status', 'list_documents', 'view_fit', 'view_axonometric', 'get_document_info', 'list_objects',
  'get_object_info', 'get_selection', 'measure_geometry', 'list_drawing_pages', 'inspect_drawing_page',
  'view_standard', 'capture_view', 'execute_script', 'get_script_status'];
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

async function session(t: TestContext) {
  const calls: Call[] = [];
  const state: { methods: unknown; capture: ((value: Record<string, unknown>) => unknown) | undefined } = { methods: METHODS, capture: undefined };
  const bridge = httpServer(async (req, res) => {
    let text = ''; for await (const chunk of req) text += chunk;
    const body = JSON.parse(text) as Call; calls.push(body);
    res.setHeader('Content-Type', 'application/json');
    if (body.params.docName === 'missing' || body.params.requestId === 'fail') {
      res.end(JSON.stringify({ ok: false, error: { code: body.params.requestId === 'fail' ? 'SCRIPT_FAILED' : 'DOCUMENT_NOT_FOUND', message: '测试业务错误' } })); return;
    }
    let data: unknown;
    if (body.method === 'get_status') data = { protocol_version: 1, running: true, addon_version: '0.2.1', ...(state.methods === undefined ? {} : { supported_methods: state.methods }) };
    else if (body.method === 'list_documents') data = ['PublicSample'];
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
    args: [fileURLToPath(new URL('../dist/cli.js', import.meta.url))], env: { PHOENIX_FREECAD_URL: `http://127.0.0.1:${port}/` }, stderr: 'pipe' });
  const client = new Client({ name: 'phoenix-integration-test', version: '0.2.1' });
  await client.connect(transport); t.after(() => client.close());
  return { client, calls, state };
}

test('真实 stdio 发现 16 工具；离线清单区分插件运行时能力和工具作用', async (t) => {
  const { client, calls } = await session(t), list = await client.listTools();
  assert.deepEqual(list.tools.map(tool => tool.name).sort(), [...TOOL_NAMES].sort());
  assert.equal(calls.length, 0);
  const capabilities = await client.callTool({ name: 'get_capabilities', arguments: {} });
  assert.equal(capabilities.structuredContent?.version, '0.2.1');
  assert.equal(capabilities.structuredContent?.runtime_supported_checked, false);
  assert.deepEqual(capabilities.structuredContent?.script_history, { scope: 'bridge_service_session', session_id_field: 'script_session_id', full_results: 20, remembered_request_ids: 4096, expired_requests_reexecute: false });
  assert.equal(calls.length, 0);
  for (const name of ['get_document_info', 'list_objects', 'get_object_info', 'get_selection', 'measure_geometry', 'list_drawing_pages', 'inspect_drawing_page', 'capture_view', 'get_script_status']) {
    assert.equal(list.tools.find(tool => tool.name === name)?.annotations?.readOnlyHint, true, name);
  }
  assert.equal(list.tools.find(tool => tool.name === 'set_view')?.annotations?.readOnlyHint, false);
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
