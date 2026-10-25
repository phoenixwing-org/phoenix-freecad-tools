// SPDX-License-Identifier: Apache-2.0
import { McpServer, type CallToolResult } from '@modelcontextprotocol/server';
import * as z from 'zod/v4';
import { readFileSync } from 'node:fs';
import { FreeCadClient, BridgeError } from './adapters/freecad.js';
import type { Config } from './config.js';

export const VERSION = '0.1.0';
export const TOOL_NAMES = ['get_capabilities', 'get_connection_status', 'list_documents', 'fit_view', 'set_axonometric_view'] as const;

function result(data: unknown): CallToolResult {
  const value = { ok: true, source_kind: 'live_freecad_gui', observed_at: new Date().toISOString(), data };
  return { content: [{ type: 'text', text: JSON.stringify(value) }], structuredContent: value };
}
async function run(operation: () => Promise<unknown>): Promise<CallToolResult> {
  try { return result(await operation()); }
  catch (error) {
    const value = { ok: false, error: { code: error instanceof BridgeError ? error.code : 'INTERNAL_ERROR', message: error instanceof BridgeError ? error.message : '操作失败，请检查服务状态' } };
    return { isError: true, content: [{ type: 'text', text: JSON.stringify(value) }], structuredContent: value };
  }
}

export function createServer(config: Config): McpServer {
  const icon = readFileSync(new URL('../addon/PhoenixFreeCADMCP/Resources/icons/phoenix-wing-mark.svg', import.meta.url));
  const server = new McpServer({ name: 'phoenix-freecad-mcp', title: 'Phoenix FreeCAD', version: VERSION, icons: [{ src: `data:image/svg+xml;base64,${icon.toString('base64')}`, mimeType: 'image/svg+xml', sizes: ['128x128'] }] });
  const freecad = new FreeCadClient({ url: config.freecadUrl, timeoutMs: config.timeoutMs });
  const empty = z.object({}).strict();
  const doc = z.string().min(1).max(256).describe('FreeCAD 文档内部 Name；省略时使用当前活动文档').optional();
  const read = { readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: false };
  const camera = { readOnlyHint: false, destructiveHint: false, idempotentHint: true, openWorldHint: false };
  server.registerTool('get_capabilities', { title: '查看 Phoenix 能力', description: '查看此最小示例支持的工具；无需 FreeCAD 在线，不代表连接已建立。', inputSchema: empty, annotations: read }, async () => {
    const value = { ok: true, source_kind: 'server_manifest', version: VERSION, bridge_protocol: 1, tools: TOOL_NAMES, effects: '读取状态、文档名和改变相机视图', connection_checked: false };
    return { content: [{ type: 'text', text: JSON.stringify(value) }], structuredContent: value };
  });
  server.registerTool('get_connection_status', { title: '检查 FreeCAD 连接', description: '检查 Phoenix 插件是否已启动，返回版本、协议及 GUI 调度状态。', inputSchema: empty, annotations: read }, () => run(() => freecad.status()));
  server.registerTool('list_documents', { title: '列出打开的文档', description: '读取 FreeCAD 当前打开的文档内部 Name；包含尚未保存的会话文档，不读取磁盘工程文件。', inputSchema: empty, annotations: read }, () => run(() => freecad.listDocuments()));
  server.registerTool('fit_view', { title: '适合窗口', description: '执行 FreeCAD fitAll，将目标文档当前视图缩放到适合窗口；会改变相机，不保存或修改模型。', inputSchema: z.object({ document: doc }).strict(), annotations: camera }, args => run(() => freecad.fitView(args.document)));
  server.registerTool('set_axonometric_view', { title: '切换轴测图', description: '把目标文档相机切换为轴测图；fit=true 时同时适合窗口，不保存或修改模型。', inputSchema: z.object({ document: doc, fit: z.boolean().default(false).describe('是否同时执行适合窗口') }).strict(), annotations: camera }, args => run(() => freecad.setAxonometricView(args.document, args.fit)));
  return server;
}
