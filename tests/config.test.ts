import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readConfig } from '../src/config.js';

test('本机配置只允许受支持的 IPv4 根地址，拒绝远端与 URL 凭据', () => {
  assert.equal(readConfig({}).timeoutMs, 15000);
  assert.equal(readConfig({ PHOENIX_FREECAD_URL: 'http://localhost:9876/' }).freecadUrl, 'http://localhost:9876/');
  for (const PHOENIX_FREECAD_URL of ['http://[::1]:9876/', 'http://localhost/ignored', 'https://localhost/', 'http://example.com/', 'http://secret@localhost/', 'http://localhost/?token=secret']) {
    assert.throws(() => readConfig({ PHOENIX_FREECAD_URL }));
  }
  assert.throws(() => readConfig({ PHOENIX_TIMEOUT_MS: 'NaN' }));
});
