import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createServer as httpServer } from 'node:http';
import { fileURLToPath } from 'node:url';
import { Client } from '@modelcontextprotocol/client';
import { StdioClientTransport } from '@modelcontextprotocol/client/stdio';

test('真实 stdio MCP 握手、发现工具、调用视图操作和失败回传', async () => {
  const calls: { method: string; params: Record<string, unknown> }[] = [];
  const bridge = httpServer(async (req, res) => {
    let text = ''; for await (const chunk of req) text += chunk;
    const body = JSON.parse(text); calls.push(body);
    res.setHeader('Content-Type', 'application/json');
    if (body.params.docName === 'missing') {
      res.end(JSON.stringify({ ok: false, error: { code: 'DOCUMENT_NOT_FOUND', message: '文档不存在' } })); return;
    }
    const data = body.method === 'get_status' ? { protocol_version: 1, running: true, addon_version: '0.1.0' }
      : body.method === 'list_documents' ? ['PublicSample']
      : { document: body.params.docName ?? 'PublicSample', view: body.method, fit: body.params.fit ?? true };
    res.end(JSON.stringify({ ok: true, data }));
  });
  await new Promise<void>(resolve => bridge.listen(0, '127.0.0.1', resolve));
  const port = (bridge.address() as { port: number }).port;
  const transport = new StdioClientTransport({ command: process.execPath, args: [fileURLToPath(new URL('../dist/cli.js', import.meta.url))], env: { PHOENIX_FREECAD_URL: `http://127.0.0.1:${port}/` }, stderr: 'pipe' });
  const client = new Client({ name: 'phoenix-integration-test', version: '1.0.0' });
  try {
    await client.connect(transport);
    const list = await client.listTools();
    assert.deepEqual(list.tools.map(tool => tool.name).sort(), ['get_capabilities', 'get_connection_status', 'list_documents', 'fit_view', 'set_axonometric_view'].sort());
    assert.equal(list.tools.find(tool => tool.name === 'fit_view')?.annotations?.readOnlyHint, false);
    assert.equal(calls.length, 0, '工具发现不应依赖在线 FreeCAD');
    const docs = await client.callTool({ name: 'list_documents', arguments: {} });
    assert.equal(docs.isError, undefined);
    assert.deepEqual((docs.structuredContent as { data: unknown }).data, ['PublicSample']);
    const axo = await client.callTool({ name: 'set_axonometric_view', arguments: { document: 'PublicSample', fit: true } });
    assert.equal(axo.isError, undefined);
    assert.deepEqual(calls.at(-1), { method: 'view_axonometric', params: { docName: 'PublicSample', fit: true } });
    await client.callTool({ name: 'fit_view', arguments: { document: 'PublicSample' } });
    assert.equal(calls.at(-1)?.method, 'view_fit');
    const missing = await client.callTool({ name: 'fit_view', arguments: { document: 'missing' } });
    assert.equal(missing.isError, true);
    assert.equal((missing.structuredContent as { error: { code: string } }).error.code, 'DOCUMENT_NOT_FOUND');
  } finally { await client.close(); await new Promise<void>(resolve => bridge.close(() => resolve())); }
});
