import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { FreeCadClient } from '../src/adapters/freecad.js';

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
  const client = new FreeCadClient({ url: `http://127.0.0.1:${(server.address() as { port: number }).port}/`, timeoutMs: 100 });
  try {
    await assert.rejects(client.status(), { code: 'UNSUPPORTED_PROTOCOL' });
    mode = 'html'; await assert.rejects(client.status(), { code: 'INVALID_RESPONSE' });
    mode = 'timeout'; await assert.rejects(client.status(), { code: 'BRIDGE_TIMEOUT' });
    assert.equal(requests, 3);
  } finally { server.closeAllConnections(); await new Promise<void>(resolve => server.close(() => resolve())); }
});
