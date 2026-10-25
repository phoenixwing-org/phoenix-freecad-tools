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


test('工具范围默认保持full，chat必须明确选择，拒绝未知或空范围', () => {
  assert.equal(readConfig({}).toolScope, 'full');
  assert.equal(readConfig({ PHOENIX_FREECAD_TOOL_SCOPE: 'full' }).toolScope, 'full');
  assert.equal(readConfig({ PHOENIX_FREECAD_TOOL_SCOPE: 'chat' }).toolScope, 'chat');
  for (const PHOENIX_FREECAD_TOOL_SCOPE of ['', 'CHAT', ' chat', 'unsafe']) {
    assert.throws(() => readConfig({ PHOENIX_FREECAD_TOOL_SCOPE }));
  }
});


test('Desk 三个可选路径必须一起明确配置；解析不访问路径或启动后台', () => {
  assert.equal(readConfig({}).desk, undefined);
  const configured = { PHOENIX_DESK_NODE: '/opt/example/node', PHOENIX_DESK_CLI: '/opt/example/desk/scripts/desk-web-service.mjs',
    PHOENIX_DESK_STATE_DIR: '/var/example/private-desk' };
  assert.deepEqual(readConfig(configured).desk, { nodeExecutable: configured.PHOENIX_DESK_NODE,
    cliEntry: configured.PHOENIX_DESK_CLI, stateDir: configured.PHOENIX_DESK_STATE_DIR });
  for (const invalid of [ { PHOENIX_DESK_NODE: configured.PHOENIX_DESK_NODE },
    { ...configured, PHOENIX_DESK_CLI: '' }, { ...configured, PHOENIX_DESK_STATE_DIR: 'relative' },
    { ...configured, PHOENIX_DESK_NODE: '/opt/../example/node' },
    { ...configured, PHOENIX_DESK_STATE_DIR: '/var/example/\u200bhidden' },
    { ...configured, PHOENIX_DESK_CLI: ' /opt/example/desk/scripts/desk-web-service.mjs' } ]) {
    assert.throws(() => readConfig(invalid));
  }
});


test('聊天 Desk 数据工具须精确 opt-in 且路径完整，错误标志不得降级启用', () => {
  const paths = { PHOENIX_DESK_NODE: '/opt/example/node', PHOENIX_DESK_CLI: '/opt/example/desk/scripts/desk-web-service.mjs',
    PHOENIX_DESK_STATE_DIR: '/var/example/private-desk' };
  assert.equal(readConfig({ ...paths, PHOENIX_FREECAD_TOOL_SCOPE: 'chat' }).deskChatReads, undefined);
  assert.equal(readConfig({ ...paths, PHOENIX_DESK_CHAT_READS: '0' }).deskChatReads, false);
  assert.equal(readConfig({ ...paths, PHOENIX_DESK_CHAT_READS: '1' }).deskChatReads, true);
  assert.throws(() => readConfig({ PHOENIX_DESK_CHAT_READS: '1' }));
  for (const PHOENIX_DESK_CHAT_READS of ['', 'true', 'yes', ' 1', '2', 'CHAT']) {
    assert.throws(() => readConfig({ ...paths, PHOENIX_DESK_CHAT_READS }));
  }
});
