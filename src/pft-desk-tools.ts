// SPDX-License-Identifier: Apache-2.0
import type { McpServer, CallToolResult } from '@modelcontextprotocol/server';
import * as z from 'zod/v4';
import { isAbsolute, resolve } from 'node:path';
import { PftDeskClient, PftDeskError, type PftDeskConfig } from './adapters/pft-desk-client.js';

export const PFT_DESK_TOOL_NAMES = [
  'desk_get_connection_status', 'desk_search_index', 'desk_query_references',
  'desk_query_cached_metadata', 'desk_get_cached_thumbnail',
] as const;

const MAX_RESULT_BYTES = 128 * 1024;
const text = z.string().min(1).max(4096).refine(value => value === value.trim()
  && !/[\p{Cc}\p{Cf}\p{Cs}]/u.test(value), '须为无控制字符的明确文本');
const workspace = text.refine(value => isAbsolute(value) && resolve(value) === value,
  '工作空间须为明确的规范绝对目录路径').describe('本次查询的工作空间绝对路径；不读取或切换 Desk 当前或最近空间');
const rel = text.refine(value => !value.includes('\\') && !value.includes(':') && !value.startsWith('/')
  && value.split('/').every(part => part && part !== '.' && part !== '..') && /\.fcstd$/iu.test(value),
  '须为工作空间内明确的 POSIX 相对 FCStd 路径，不接受 ./、穿越或外部目标');
const read = { readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: false };

/** Separate provenance from live FreeCAD memory. Never log or persist query data. */
async function run(operation: () => Promise<unknown>, sourceKind: 'desk_service' | 'desk_workspace_index' | 'desk_saved_cache'): Promise<CallToolResult> {
  try {
    const data = await operation();
    const value = { ok: true, source_kind: sourceKind, observed_at: new Date().toISOString(), data };
    const serialized = JSON.stringify(value);
    const response: CallToolResult = { content: [{ type: 'text', text: serialized }], structuredContent: value };
    // Count both backwards-compatible JSON text and structuredContent copies.
    if (Buffer.byteLength(JSON.stringify(response), 'utf8') > MAX_RESULT_BYTES) {
      throw new PftDeskError('DESK_RESULT_TOO_LARGE', 'Desk MCP 结果超过 128 KiB，请缩小查询范围。');
    }
    return response;
  } catch (error) {
    const value = { ok: false, error: { code: error instanceof PftDeskError ? error.code : 'DESK_INTERNAL_ERROR',
      message: error instanceof PftDeskError ? error.message : 'Desk 查询失败，请检查连接和索引状态。' } };
    return { isError: true, content: [{ type: 'text', text: JSON.stringify(value) }], structuredContent: value };
  }
}

async function runImage(operation: () => ReturnType<PftDeskClient['getCachedThumbnail']>): Promise<CallToolResult> {
  try {
    const { data, ...metadata } = await operation();
    const value = { ok: true, source_kind: 'desk_saved_cache', observed_at: new Date().toISOString(),
      data: { ...metadata, image_in_content: true } };
    const response: CallToolResult = { content: [{ type: 'image', data, mimeType: 'image/png' },
      { type: 'text', text: JSON.stringify(value) }], structuredContent: value };
    if (Buffer.byteLength(JSON.stringify(response), 'utf8') > 1024 * 1024) {
      throw new PftDeskError('DESK_RESULT_TOO_LARGE', 'Desk 缓存图像 MCP 结果超过 1 MiB；不会缩放或重新生成。');
    }
    return response;
  } catch (error) { return run(() => Promise.reject(error), 'desk_saved_cache'); }
}

export function pftRegisterDeskTools(server: McpServer, config: PftDeskConfig, timeoutMs: number): void {
  const desk = new PftDeskClient(config, timeoutMs);
  server.registerTool('desk_get_connection_status', {
    title: '检查 Desk 连接',
    description: '通过明确配置的可信 Desk CLI 检查已运行后台和兼容性。可显式设 includeRuntimeFeatures=true，额外读取同实例的版本化能力观察，区分既有缓存支持与 Native 门禁；idle 仅表示仍需请求检查，不授予执行权限。默认不读取能力接口；404仅报告未声明，畸形或身份变化报错。不启动后台、浏览器或任务，不读取最近空间或 CAD。',
    inputSchema: z.object({ includeRuntimeFeatures: z.boolean().optional().describe('显式读取运行态能力观察；默认关闭，不执行Native预检或生成') }).strict(), annotations: read,
  }, args => run(() => desk.status(args), 'desk_service'));
  server.registerTool('desk_search_index', {
    title: '搜索 Desk 文件索引',
    description: '从明确工作空间的已有 SQLite 索引搜索文件元数据，不扫描、打开 CAD 或同步业务记录。query 为非空 SQL LIKE 包含模式，% 和 _ 是通配符；结果最多200项，无分页，达到上限不能视为完整集合。返回索引诊断，不证明磁盘或依赖全部新鲜。只读租约会临时创建/释放协调锁；已有锁或读取失败明确报错，不清锁。',
    inputSchema: z.object({ workspace, query: text.pipe(z.string().max(256)) }).strict(), annotations: read,
  }, args => run(() => desk.searchIndex(args), 'desk_workspace_index'));
  server.registerTool('desk_query_references', {
    title: '批量读取 Desk 引用索引',
    description: '按明确工作空间和1–10个相对FCStd路径读取已有引用计数或出向引用。计数分别为索引引用行、不同文件和BOM行，不能当实物数量；不递归重算。逐项读取不是跨文件原子快照，返回来源/新鲜度；出向引用最多limit项，没有可靠hasMore，完整性未知。只读租约可能短暂协调锁，不刷新索引、读取模型、启动CAD或修改业务数据。',
    inputSchema: z.object({ workspace,
      rels: z.array(rel).min(1).max(10).refine(values => new Set(values).size === values.length, '路径不能重复'),
      view: z.enum(['counts', 'outgoing']).default('counts'), limit: z.number().int().min(1).max(100).default(50),
    }).strict(), annotations: read,
  }, args => run(() => desk.queryReferences(args), 'desk_workspace_index'));
  const cacheRel = rel.pipe(z.string().max(1024)).refine(value => value === value.normalize('NFC')
    && value.split('/').every(part => !part.startsWith('.') && !['node_modules', '__pycache__'].includes(part.toLowerCase())),
    '缓存路径须为规范NFC且不经过隐藏或缓存目录');
  server.registerTool('desk_query_cached_metadata', {
    title: '读取 Desk 既有缩略图与尺寸元数据',
    description: '从明确工作空间读取1–10个FCStd已有缓存，不生成、刷新或打开CAD。需运行态cad.cache.read v1能力；缺失/过期/不支持分别返回，不把缺尺寸补零。尺寸为缓存box_mm，非实时测量；来源只stat已记录依赖，content_hash_verified=false，不证明依赖全集。数据库行同一读事务，文件stat非原子；读租约临时协调锁，DB前后全hash成本不只与rels数量相关。',
    inputSchema: z.object({ workspace, rels: z.array(cacheRel).min(1).max(10)
      .refine(values => new Set(values.map(value => value.toLowerCase())).size === values.length, '缓存路径不能大小写重复') }).strict(),
    annotations: read,
  }, args => run(() => desk.queryCachedMetadata(args), 'desk_saved_cache'));
  server.registerTool('desk_get_cached_thumbnail', {
    title: '读取 Desk 既有缩略图',
    description: '仅在明确请求查看图像时使用。需要对应thumbnail元数据的精确revision；内部重新核对，不自动改用新版本。只读固定PNG缓存，不读取FCStd正文、不回退Native或生成。图片字节/hash/尺寸/完整PNG结构校验并剥附带元数据；源文件内容hash仍未本次验证，图片可能含模型内容。最多4MiB输入、600KiB净化PNG及1MiB完整MCP结果，超限拒绝不缩放。',
    inputSchema: z.object({ workspace, rel: cacheRel, revision: z.string().regex(/^[a-f0-9]{64}$/u) }).strict(), annotations: read,
  }, args => runImage(() => desk.getCachedThumbnail(args)));

}
