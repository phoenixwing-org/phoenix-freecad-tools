// SPDX-License-Identifier: Apache-2.0
import { McpServer, type CallToolResult } from '@modelcontextprotocol/server';
import * as z from 'zod/v4';
import { readFileSync } from 'node:fs';
import type { Config } from './config.js';
import { PftBridgeError, PftFreeCadClient } from './adapters/pft-freecad-client.js';
import { PftThumbnailJobInputSchema, PftThumbnailRenderInputSchema, type PftThumbnailJob } from './pft-thumbnail-worker.js';

function result(data: unknown): CallToolResult {
  const value = { ok: true, source_kind: 'isolated_thumbnail_worker', data };
  // Keep the bounded PNG only once: duplicating it in text and structuredContent
  // can exceed the SDK's message limit for a valid 4 MiB image.
  const job = data as PftThumbnailJob;
  const summary = job?.result?.png_base64 ? { ...job, result: { ...job.result, png_base64: '[available in structuredContent]' } } : data;
  return { structuredContent: value, content: [{ type: 'text', text: JSON.stringify({ ...value, data: summary }) }] };
}
async function run(operation: () => Promise<unknown>): Promise<CallToolResult> {
  try { return result(await operation()); }
  catch (error) {
    const value = { ok: false, error: { code: error instanceof PftBridgeError ? error.code : 'INTERNAL_ERROR',
      message: error instanceof PftBridgeError ? error.message : '缩略图 worker 请求失败。' } };
    return { isError: true, structuredContent: value, content: [{ type: 'text', text: JSON.stringify(value) }] };
  }
}
/** Separate registration surface: never offers ordinary interactive CAD tools. */
export function pftCreateThumbnailWorkerServer(config: Config, version: string): McpServer {
  const icon = readFileSync(new URL('../addon/PhoenixFreeCADTools/Resources/icons/phoenix-wing-mark.svg', import.meta.url));
  const server = new McpServer({ name: 'phoenix-freecad-mcp', title: 'Phoenix Tools Thumbnail Worker', version,
    icons: [{ src: `data:image/svg+xml;base64,${icon.toString('base64')}`, mimeType: 'image/svg+xml', sizes: ['128x128'] }] });
  const client = new PftFreeCadClient({ url: config.freecadUrl, timeoutMs: config.timeoutMs });
  const read = { readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: false };
  const action = { readOnlyHint: false, destructiveHint: false, idempotentHint: true, openWorldHint: false };
  server.registerTool('get_thumbnail_worker_status', { title: '检查缩略图 worker', description: '核验已由本次受控启动器提供的独立 worker 实际状态。身份字符串仅用于关联，不证明所有权；本工具不启动、发现或接管实例。', inputSchema: z.object({}).strict(), annotations: read }, () => run(() => client.getThumbnailWorkerStatus()));
  server.registerTool('render_document_thumbnail', { title: '提交独立缩略图任务', description: '仅处理本次明确授权的自包含原生文件和预期SHA-256；立即返回任务状态，同session/jobId幂等，失联后查询原任务，不自动重试或保存CAD。', inputSchema: PftThumbnailRenderInputSchema, annotations: action }, input => run(() => client.renderDocumentThumbnail(input)));
  server.registerTool('get_thumbnail_job', { title: '查询缩略图任务', description: '查询相同worker/session/jobId；过期结果明确报错，不重做。完成结果包含已验证PNG、摘要及关闭/恢复声明；不打开普通CAD文档。', inputSchema: PftThumbnailJobInputSchema, annotations: read }, input => run(() => client.getThumbnailJob(input)));
  server.registerTool('cancel_thumbnail_job', { title: '取消缩略图任务', description: '请求取消明确的现有任务；取消请求不表示原生阶段已经中断或完成清理，须继续查询原任务。', inputSchema: PftThumbnailJobInputSchema, annotations: action }, input => run(() => client.cancelThumbnailJob(input)));
  return server;
}
