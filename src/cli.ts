#!/usr/bin/env node
// SPDX-License-Identifier: Apache-2.0
import { StdioServerTransport } from '@modelcontextprotocol/server/stdio';
import { loadConfig } from './config.js';
import { PftMcpSettingsError } from './pft-mcp-settings.js';
import { PftFreeCadClient, PftBridgeError } from './adapters/pft-freecad-client.js';
import { PftInstanceRouter } from './pft-instance-router.js';
import { PftInstanceError } from './pft-instance-registry.js';
import { createServer } from './server.js';

async function main(): Promise<void> {
  const args = process.argv.slice(2);
  if (args.includes('--help')) {
    process.stdout.write('phoenix-freecad-mcp\n默认：MCP stdio 服务\n--check：检测 FreeCAD 插件\n--demo：轴测图并适合窗口\n独立缩略图 worker：PHOENIX_FREECAD_SCOPE=worker（仅四个 worker 工具）\n环境：PHOENIX_FREECAD_URL（显式直连优先），PHOENIX_TIMEOUT_MS\n未指定 URL 时读取用户级 PhoenixTools/mcp/config.json；默认端口9876，显式启用discovery时仅连接登记实例，多实例须先选择\n可选 Desk 只读连接：同时设置 PHOENIX_DESK_NODE、PHOENIX_DESK_CLI、PHOENIX_DESK_STATE_DIR（chat 模式另须 PHOENIX_DESK_CHAT_READS=1 明确启用）\n'); return;
  }
  if (args.length > 1 || args.some(arg => !['--check', '--demo'].includes(arg))) throw new Error('不支持的参数；使用 --help 查看用法');
  const config = await loadConfig();
  if (config.instanceDiscovery && config.toolScope !== 'full') throw new PftBridgeError('INSTANCE_SCOPE_UNSUPPORTED', 'Chat and worker scopes require an explicit FreeCAD URL.');
  if (args.length) {
    if (config.toolScope === 'worker' && args[0] === '--demo') throw new Error('worker 作用域不支持 --demo');
    const lease = config.instanceDiscovery ? await new PftInstanceRouter({ runtimeDirectory: config.instanceDiscovery.runtimeDirectory, timeoutMs: config.timeoutMs }).acquire() : undefined;
    try {
    const client = lease?.client ?? new PftFreeCadClient({ url: config.freecadUrl, timeoutMs: config.timeoutMs });
    const status = config.toolScope === 'worker' ? await client.getThumbnailWorkerStatus() : await client.status();
    const data = args[0] === '--demo' ? await client.setAxonometricView(undefined, true)
      : { ...status, mcp_connection_configuration: config.connectionConfiguration };
    process.stdout.write(JSON.stringify({ ok: true, data }, null, 2) + '\n'); return;
    } finally { lease?.release(); }
  }
  const server = createServer(config);
  await server.connect(new StdioServerTransport());
  const stop = () => { void server.close().finally(() => process.exit(0)); };
  process.once('SIGINT', stop); process.once('SIGTERM', stop);
}
main().catch(error => {
  process.stderr.write(JSON.stringify({ ok: false, error: { code: error instanceof PftBridgeError || error instanceof PftMcpSettingsError || error instanceof PftInstanceError ? error.code : 'STARTUP_ERROR', message: error instanceof Error ? error.message : '启动失败' } }) + '\n');
  process.exitCode = 1;
});
