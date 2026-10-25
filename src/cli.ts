#!/usr/bin/env node
// SPDX-License-Identifier: Apache-2.0
import { StdioServerTransport } from '@modelcontextprotocol/server/stdio';
import { readConfig } from './config.js';
import { PftFreeCadClient, PftBridgeError } from './adapters/pft-freecad-client.js';
import { createServer } from './server.js';

async function main(): Promise<void> {
  const args = process.argv.slice(2);
  if (args.includes('--help')) {
    process.stdout.write('phoenix-freecad-mcp\n默认：MCP stdio 服务\n--check：检测 FreeCAD 插件\n--demo：轴测图并适合窗口\n环境：PHOENIX_FREECAD_URL（默认 http://127.0.0.1:9876/），PHOENIX_TIMEOUT_MS\n可选 Desk 只读连接：同时设置 PHOENIX_DESK_NODE、PHOENIX_DESK_CLI、PHOENIX_DESK_STATE_DIR（chat 模式另须 PHOENIX_DESK_CHAT_READS=1 明确启用）\n'); return;
  }
  if (args.length > 1 || args.some(arg => !['--check', '--demo'].includes(arg))) throw new Error('不支持的参数；使用 --help 查看用法');
  const config = readConfig();
  if (args.length) {
    const client = new PftFreeCadClient({ url: config.freecadUrl, timeoutMs: config.timeoutMs });
    const status = await client.status();
    const data = args[0] === '--demo' ? await client.setAxonometricView(undefined, true) : status;
    process.stdout.write(JSON.stringify({ ok: true, data }, null, 2) + '\n'); return;
  }
  const server = createServer(config);
  await server.connect(new StdioServerTransport());
  const stop = () => { void server.close().finally(() => process.exit(0)); };
  process.once('SIGINT', stop); process.once('SIGTERM', stop);
}
main().catch(error => {
  process.stderr.write(JSON.stringify({ ok: false, error: { code: error instanceof PftBridgeError ? error.code : 'STARTUP_ERROR', message: error instanceof Error ? error.message : '启动失败' } }) + '\n');
  process.exitCode = 1;
});
