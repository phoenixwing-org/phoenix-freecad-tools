import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { PftFreeCadClient } from '../src/adapters/pft-freecad-client.js';

test('拒绝未知协议、非 JSON 响应及超时，不自动重试相机操作', async () => {
  let mode = 'protocol'; let requests = 0;
  const server = createServer((req, res) => {
    requests++;
    if (mode === 'timeout') return;
    if (mode === 'html') { res.end('<html>not a bridge</html>'); return; }
    res.setHeader('Content-Type', 'application/json');
    res.end(JSON.stringify({ ok: true, data: { protocol_version: 99 } }));
  });
  await new Promise<void>(resolve => server.listen(0, '127.0.0.1', resolve));
  const client = new PftFreeCadClient({ url: `http://127.0.0.1:${(server.address() as { port: number }).port}/`, timeoutMs: 100 });
  try {
    await assert.rejects(client.status(), { code: 'UNSUPPORTED_PROTOCOL' });
    mode = 'html'; await assert.rejects(client.status(), { code: 'INVALID_RESPONSE' });
    mode = 'timeout'; await assert.rejects(client.status(), { code: 'BRIDGE_TIMEOUT' });
    assert.equal(requests, 3);
  } finally { server.closeAllConnections(); await new Promise<void>(resolve => server.close(() => resolve())); }
});


test('Pft客户端保留旧string文档调用，同时在HTTP前拒绝无token修订', async () => {
  const calls: Array<{ method: string; params: unknown }> = [];
  const server = createServer(async (req, res) => {
    let text = ''; for await (const chunk of req) text += chunk;
    const body = JSON.parse(text); calls.push(body);
    res.setHeader('Content-Type', 'application/json');
    res.end(JSON.stringify({ ok: true, data: body.method === 'get_status'
      ? { protocol_version: 1, supported_methods: ['get_document_info'] } : { document: 'PublicSample' } }));
  });
  await new Promise<void>(resolve => server.listen(0, '127.0.0.1', resolve));
  const client = new PftFreeCadClient({ url: `http://127.0.0.1:${(server.address() as { port: number }).port}/`, timeoutMs: 1000 });
  try {
    await assert.rejects(client.getDocumentInfo({ expectedRevision: 0 }), { code: 'INVALID_ARGUMENT' });
    assert.equal(calls.length, 0);
    const result = await client.getDocumentInfo('PublicSample') as Record<string, unknown>;
    assert.deepEqual(calls.at(-1), { method: 'get_document_info', params: { docName: 'PublicSample' } });
    assert.equal(result.revision_status, 'legacy_plugin');
  } finally { server.closeAllConnections(); await new Promise<void>(resolve => server.close(() => resolve())); }
});
