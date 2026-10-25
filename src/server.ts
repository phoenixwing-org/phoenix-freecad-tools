// SPDX-License-Identifier: Apache-2.0
import { McpServer, type CallToolResult } from '@modelcontextprotocol/server';
import * as z from 'zod/v4';
import { readFileSync } from 'node:fs';
import { FreeCadClient, BridgeError, type CapturedView } from './adapters/freecad.js';
import type { Config } from './config.js';

export const VERSION = '0.2.1';
export const TOOL_NAMES = [
  'get_capabilities', 'get_connection_status', 'list_documents', 'fit_view', 'set_axonometric_view',
  'get_document_info', 'list_objects', 'get_object_info', 'get_selection', 'measure_geometry',
  'list_drawing_pages', 'inspect_drawing_page', 'set_view', 'capture_view', 'execute_script', 'get_script_status',
] as const;

function result(data: unknown): CallToolResult {
  const value = { ok: true, source_kind: 'live_freecad_gui', observed_at: new Date().toISOString(), data };
  return { content: [{ type: 'text', text: JSON.stringify(value) }], structuredContent: value };
}
function imageResult(image: CapturedView): CallToolResult {
  const { data, ...metadata } = image;
  const value = { ok: true, source_kind: 'live_freecad_gui', observed_at: new Date().toISOString(),
    data: { ...metadata, capture_scope: 'freecad_3d_view', image_in_content: true } };
  return { content: [{ type: 'image', data, mimeType: 'image/png' }, { type: 'text', text: JSON.stringify(value) }], structuredContent: value };
}
async function run<T>(operation: () => Promise<T>, serialize: (data: T) => CallToolResult = result): Promise<CallToolResult> {
  try { return serialize(await operation()); }
  catch (error) {
    const value = { ok: false, error: { code: error instanceof BridgeError ? error.code : 'INTERNAL_ERROR', message: error instanceof BridgeError ? error.message : '操作失败，请检查服务状态' } };
    return { isError: true, content: [{ type: 'text', text: JSON.stringify(value) }], structuredContent: value };
  }
}

export function createServer(config: Config): McpServer {
  const icon = readFileSync(new URL('../addon/PhoenixFreeCADMCP/Resources/icons/phoenix-wing-mark.svg', import.meta.url));
  const server = new McpServer({ name: 'phoenix-freecad-mcp', title: 'Phoenix Tools', version: VERSION, icons: [{ src: `data:image/svg+xml;base64,${icon.toString('base64')}`, mimeType: 'image/svg+xml', sizes: ['128x128'] }] });
  const freecad = new FreeCadClient({ url: config.freecadUrl, timeoutMs: config.timeoutMs });
  const empty = z.object({}).strict();
  const name = z.string().min(1).max(256).regex(/^[^\p{Cc}\p{Cs}]+$/u);
  const propertyName = z.string().min(1).max(128).regex(/^\p{XID_Start}\p{XID_Continue}*$/u);
  const doc = name.describe('FreeCAD 文档内部 Name；省略时使用当前活动文档').optional();
  const object = name.describe('对象内部 Name，不是显示 Label；可从 list_objects 获取');
  const requestId = z.string().regex(/^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/u).describe('同一次脚本请求的唯一 ID；超时后用原 ID 查询或重放，避免重复执行');
  const read = { readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: false };
  const camera = { readOnlyHint: false, destructiveHint: false, idempotentHint: true, openWorldHint: false };
  server.registerTool('get_capabilities', { title: '查看 Phoenix 能力', description: '离线查看 Node 服务注册的工具与作用范围；实际插件能力须调用 get_connection_status 检查 supported_methods。', inputSchema: empty, annotations: read }, async () => {
    const value = { ok: true, source_kind: 'server_manifest', version: VERSION, bridge_protocol: 1, tools: TOOL_NAMES,
      groups: { connection: ['get_capabilities', 'get_connection_status', 'list_documents'],
        inspect: ['get_document_info', 'list_objects', 'get_object_info', 'get_selection', 'measure_geometry', 'list_drawing_pages', 'inspect_drawing_page'],
        camera: ['fit_view', 'set_axonometric_view', 'set_view'], image: ['capture_view'], advanced: ['execute_script', 'get_script_status'] },
      effects: { inspect: '读取当前会话，不重算、不保存或修改模型', camera: '改变目标文档三维视图相机',
        image: '仅在明确请求时导出 FreeCAD 三维视图，图像可能包含当前模型信息；不截图桌面、不改变视角',
        script: '高级脚本与 FreeCAD Python 控制台同权限，在 GUI 主线程执行，可修改模型、访问文件及保存；无自动回滚；请求记录仅限当前桥接服务会话，重载或重启会重置，以 script_session_id 识别' },
      script_history: { scope: 'bridge_service_session', session_id_field: 'script_session_id', full_results: 20, remembered_request_ids: 4096, expired_requests_reexecute: false },
      connection_checked: false, runtime_supported_checked: false, runtime_supported: '请检查 get_connection_status 返回的 supported_methods；清单不代表旧插件已支持新增工具' };
    return { content: [{ type: 'text', text: JSON.stringify(value) }], structuredContent: value };
  });
  server.registerTool('get_connection_status', { title: '检查 FreeCAD 连接', description: '检查 Phoenix 插件是否已启动，返回版本、协议、GUI 调度状态及插件实际声明的 supported_methods。', inputSchema: empty, annotations: read }, () => run(() => freecad.status()));
  server.registerTool('list_documents', { title: '列出打开的文档', description: '读取 FreeCAD 当前打开的文档内部 Name；包含尚未保存的会话文档，不读取磁盘工程文件。', inputSchema: empty, annotations: read }, () => run(() => freecad.listDocuments()));
  server.registerTool('fit_view', { title: '适合窗口', description: '执行 FreeCAD fitAll，将目标文档当前视图缩放到适合窗口；会改变相机，不保存或修改模型。', inputSchema: z.object({ document: doc }).strict(), annotations: camera }, args => run(() => freecad.fitView(args.document)));
  server.registerTool('set_axonometric_view', { title: '切换轴测图', description: '把目标文档相机切换为轴测图；fit=true 时同时适合窗口，不保存或修改模型。', inputSchema: z.object({ document: doc, fit: z.boolean().default(false).describe('是否同时执行适合窗口') }).strict(), annotations: camera }, args => run(() => freecad.setAxonometricView(args.document, args.fit)));
  server.registerTool('get_document_info', { title: '查看文档信息', description: '只读查看已打开文档的会话状态与对象统计，不读取或返回本机文件路径。', inputSchema: z.object({ document: doc }).strict(), annotations: read }, args => run(() => freecad.getDocumentInfo(args.document)));
  server.registerTool('list_objects', { title: '分页查看对象', description: '按内部 Name 分页读取文档对象，可按准确 TypeId 筛选；不展开全部属性或返回完整模型。', inputSchema: z.object({ document: doc,
    offset: z.number().int().min(0).max(2147483647).default(0), limit: z.number().int().min(1).max(100).default(50),
    typeId: z.string().regex(/^[A-Za-z_][A-Za-z0-9_:]{0,127}$/u).describe('准确的 FreeCAD TypeId，例如 PartDesign::Body').optional() }).strict(), annotations: read }, args => run(() => freecad.listObjects(args)));
  server.registerTool('get_object_info', { title: '查看对象属性', description: '只读查看指定对象的类型、状态及选定属性；最多请求 20 个属性，不执行表达式或任意代码。', inputSchema: z.object({ document: doc, object,
    properties: z.array(propertyName).max(20).refine(values => new Set(values).size === values.length, '属性名称不能重复').optional() }).strict(), annotations: read }, args => run(() => freecad.getObjectInfo(args)));
  server.registerTool('get_selection', { title: '读取当前选择', description: '只读读取当前 FreeCAD 选择及子元素名称；可限定已打开文档，不改变选择。', inputSchema: z.object({ document: doc }).strict(), annotations: read }, args => run(() => freecad.getSelection(args.document)));
  server.registerTool('measure_geometry', { title: '测量现有几何', description: '只读测量指定对象或 FaceN、EdgeN、VertexN 子元素的现有几何；不重算、修复或修改模型。', inputSchema: z.object({ document: doc, object,
    subelement: z.string().regex(/^(?:Face|Edge|Vertex)[1-9][0-9]{0,6}$/u).optional() }).strict(), annotations: read }, args => run(() => freecad.measureGeometry(args)));
  server.registerTool('list_drawing_pages', { title: '列出图纸页', description: '只读列出当前文档中的 TechDraw 图纸页，不生成或更新图纸。', inputSchema: z.object({ document: doc }).strict(), annotations: read }, args => run(() => freecad.listDrawingPages(args.document)));
  server.registerTool('inspect_drawing_page', { title: '检查图纸页', description: '只读查看指定 TechDraw 页的视图、模板与标注摘要；不保证生产尺寸完整，不修复引用或更新页面。', inputSchema: z.object({ document: doc,
    page: name.describe('TechDraw 页的内部 Name，从 list_drawing_pages 获取') }).strict(), annotations: read }, args => run(() => freecad.inspectDrawingPage(args)));
  server.registerTool('set_view', { title: '切换标准视图', description: '将指定文档三维相机切为前、后、左、右、上、下或轴测；fit=true 同时适合窗口，不保存模型。', inputSchema: z.object({ document: doc,
    view: z.enum(['front', 'rear', 'left', 'right', 'top', 'bottom', 'axonometric']), fit: z.boolean().default(false) }).strict(), annotations: camera }, args => run(() => freecad.setView(args)));
  server.registerTool('capture_view', { title: '获取三维视图图像', description: '仅在用户明确请求查看图像时使用：导出 FreeCAD 当前三维视图 PNG，可能透露模型信息。不截图桌面、不改变视角、不保存模型；图像大小上限 600 KiB。', inputSchema: z.object({ document: doc,
    width: z.number().int().min(128).max(1024).default(800), height: z.number().int().min(128).max(1024).default(600) }).strict(), annotations: read }, args => run(() => freecad.captureView(args), imageResult));
  server.registerTool('execute_script', { title: '执行高级 FreeCAD 脚本', description: '高级能力：与 FreeCAD Python 控制台同权限，在 GUI 主线程执行，可修改模型、读写文件或自行保存。框架不额外保存，也不承诺异常自动回滚。超时后先用 get_script_status 查询，重放须使用相同 requestId 和代码。去重仅限同一桥接服务会话；重载或重启会重置，通过 get_connection_status 的 script_session_id 识别。完整结果保留最近 20 份，过期 ID 在该会话中仍拒绝重执行。', inputSchema: z.object({
    code: z.string().min(1).max(32768).regex(/^[^\u0000\p{Cs}]*$/u), requestId }).strict(), annotations: { readOnlyHint: false, destructiveHint: true, idempotentHint: true, openWorldHint: true } }, args => run(() => freecad.executeScript(args.code, args.requestId)));
  server.registerTool('get_script_status', { title: '查询脚本请求状态', description: '只读查询同一桥接服务会话内指定 requestId 的状态；不重新执行代码、不进入 GUI 队列。重载或重启会重置，以 script_session_id 识别；完整结果保留最近 20 份，过期 ID 在会话中仍拒绝重执行。', inputSchema: z.object({ requestId }).strict(), annotations: read }, args => run(() => freecad.getScriptStatus(args.requestId)));
  return server;
}
