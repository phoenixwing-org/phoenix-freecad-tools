// SPDX-License-Identifier: Apache-2.0
import { McpServer, type CallToolResult } from '@modelcontextprotocol/server';
import * as z from 'zod/v4';
import { readFileSync } from 'node:fs';
import { PftFreeCadClient, PftBridgeError, type CapturedView } from './adapters/pft-freecad-client.js';
import type { Config } from './config.js';
import { PFT_DESK_TOOL_NAMES, pftRegisterDeskTools } from './pft-desk-tools.js';
import { PftReadGuardFields, pftRefineReadGuards, PftReadSuccessSchema } from './pft-read-provenance.js';
import { PftAppearanceInputSchema, pftAppearanceScript, pftAppearanceJob } from './pft-appearance.js';
import { PftDesignInspectionInputSchema, PftDesignInspectionSuccessSchema } from './pft-design-inspection.js';
import { PFT_RECOMPUTE_CAPABILITY, PftRecomputeSuccessSchema } from './pft-recompute.js';
import { PFT_PART_APPROVAL_CAPABILITY, PftPartApprovalInputSchema, PftPartApprovalSuccessSchema } from './pft-part-approval.js';
import { PFT_PAD_LENGTH_CAPABILITY, PftPadLengthInputSchema } from './pft-pad-length.js';

export const VERSION = '0.3.2';
export const TOOL_NAMES = [
  'get_capabilities', 'get_connection_status', 'list_documents', 'fit_view', 'set_axonometric_view',
  'get_document_info', 'list_objects', 'get_object_info', 'get_selection', 'measure_geometry', 'inspect_design',
  'list_drawing_pages', 'inspect_drawing_page', 'set_view', 'set_appearance', 'capture_view', 'execute_script', 'get_script_status',
  'set_selection', 'set_visibility', 'recompute_document', 'prepare_part_changes', 'request_part_change_approval', 'get_part_change_status', 'apply_part_changes',
] as const;
export const CHAT_TOOL_NAMES = [
  'get_capabilities', 'get_connection_status', 'list_documents', 'get_document_info', 'list_objects',
  'get_object_info', 'get_selection', 'measure_geometry', 'inspect_design', 'list_drawing_pages', 'inspect_drawing_page',
  'fit_view', 'set_axonometric_view', 'set_view', 'set_appearance', 'get_script_status',
  'prepare_part_changes', 'request_part_change_approval', 'get_part_change_status',
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
    const value = { ok: false, error: { code: error instanceof PftBridgeError ? error.code : 'INTERNAL_ERROR', message: error instanceof PftBridgeError ? error.message : '操作失败，请检查服务状态' } };
    return { isError: true, content: [{ type: 'text', text: JSON.stringify(value) }], structuredContent: value };
  }
}

export function createServer(config: Config): McpServer {
  const toolScope = config.toolScope ?? 'full';
  if (toolScope !== 'full' && toolScope !== 'chat') throw new Error('工具范围仅允许 full 或 chat');
  if (config.deskChatReads !== undefined && typeof config.deskChatReads !== 'boolean') throw new Error('Desk 聊天开关必须为布尔值');
  if (config.deskChatReads === true && config.desk === undefined) throw new Error('Desk 聊天共享需要完整路径配置');
  const deskEnabled = config.desk !== undefined && (toolScope === 'full' || config.deskChatReads === true);
  const baseTools = toolScope === 'chat' ? CHAT_TOOL_NAMES : TOOL_NAMES;
  const activeTools: readonly string[] = deskEnabled ? [...baseTools, ...PFT_DESK_TOOL_NAMES] : baseTools;
  const icon = readFileSync(new URL('../addon/PhoenixFreeCADTools/Resources/icons/phoenix-wing-mark.svg', import.meta.url));
  const server = new McpServer({ name: 'phoenix-freecad-mcp', title: 'Phoenix Tools', version: VERSION, icons: [{ src: `data:image/svg+xml;base64,${icon.toString('base64')}`, mimeType: 'image/svg+xml', sizes: ['128x128'] }] });
  const freecad = new PftFreeCadClient({ url: config.freecadUrl, timeoutMs: config.timeoutMs });
  const empty = z.object({}).strict();
  const name = z.string().min(1).max(256).regex(/^[^\p{Cc}\p{Cs}]+$/u);
  const propertyName = z.string().min(1).max(128).regex(/^\p{XID_Start}\p{XID_Continue}*$/u);
  const doc = name.describe('FreeCAD 文档内部 Name；省略时使用当前活动文档').optional();
  const object = name.describe('对象内部 Name，不是显示 Label；可从 list_objects 获取');
  const requestId = z.string().regex(/^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/u).describe('同一次脚本请求的唯一 ID；超时后用原 ID 查询或重放，避免重复执行');
  const objectList = z.array(name).max(100).refine(values => new Set(values).size === values.length, '对象名称不能重复');
  const nonemptyObjects = z.array(name).min(1).max(100).refine(values => new Set(values).size === values.length, '对象名称不能重复');
  const fieldText = z.string().max(2048).regex(/^[^\p{Cc}\p{Cs}]*$/u);
  const partValues = z.record(name, z.object({ PartNumber: fieldText.optional(), PartVersion: fieldText.optional(), TypeCode: fieldText.optional(), ModelSeries: fieldText.optional(), PartName: fieldText.optional() }).strict().refine(value => Object.keys(value).length > 0, '每个对象至少指定一个正式字段')).refine(value => Object.keys(value).length >= 1 && Object.keys(value).length <= 100, 'values对象数量必须为1到100');
  const read = { readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: false };
  const camera = { readOnlyHint: false, destructiveHint: false, idempotentHint: true, openWorldHint: false };
  server.registerTool('get_capabilities', { title: '查看 Phoenix 能力', description: '离线查看 Node 服务注册的工具与作用范围；FreeCAD实际能力须调用get_connection_status检查supported_methods，可选Desk连接另用desk_get_connection_status检查，不以注册清单推定运行可用。', inputSchema: empty, annotations: read }, async () => {
    const allGroups: Record<string, readonly string[]> = {
      connection: ['get_capabilities', 'get_connection_status', 'list_documents'],
      inspect: ['get_document_info', 'list_objects', 'get_object_info', 'get_selection', 'measure_geometry', 'inspect_design', 'list_drawing_pages', 'inspect_drawing_page'],
      camera: ['fit_view', 'set_axonometric_view', 'set_view'], appearance: ['set_appearance'],
      general_actions: ['set_selection', 'set_visibility', 'recompute_document'], phoenix: ['prepare_part_changes', 'request_part_change_approval', 'get_part_change_status', 'apply_part_changes'],
      image: ['capture_view'], advanced: ['execute_script', 'get_script_status'],
      desk: PFT_DESK_TOOL_NAMES,
    };
    const allowed = new Set<string>(activeTools);
    const groups = Object.fromEntries(Object.entries(allGroups).map(([group, names]): [string, string[]] => [group, names.filter(name => allowed.has(name))])
      .filter(([, names]) => names.length > 0));
    const allEffects: Record<string, string> = {
      connection: '检查当前桥接连接及打开文档，不读取磁盘工程',
      inspect: '读取当前会话，不重算、不保存或修改模型', camera: '改变目标文档三维视图相机',
      appearance: '仅更改明确目标的原生颜色或透明度；单事务、可撤销，不重算、不保存；任务未完成时用同一 requestId 查询',
      general_actions: '对明确活动文档操作选择、可撤销显隐或调用原生重算；重算不承诺原生回调或跨文档隔离，不自动保存',
      phoenix: toolScope === 'chat'
        ? '只预览并请求FreeCAD原生人工确认；AI不能授予确认或调用apply；查询原token状态，等待确认不表示已写入，可撤销且不自动保存'
        : '先完整预览；Pad Length始终须FreeCAD原生人工确认，原五项也可在明确授权下应用尚未进入确认链的token；写后校验、可撤销、不保存',
      image: '仅在明确请求时导出 FreeCAD 三维视图，图像可能包含当前模型信息；不截图桌面、不改变视角',
      desk: '可选 Desk 适配：正式 CLI 检查状态，可显式读取运行态能力观察但不授予Native权限；显式工作空间查询已有索引/缩略图与尺寸缓存；缓存另需版本化运行态能力。不启动后台、扫描、生成或修改业务记录及CAD；读租约临时协调锁，缓存和索引均与FreeCAD当前内存分开。',
      advanced: toolScope === 'chat' ? '只读查询当前桥接会话已存在请求的状态，不提供任意脚本执行入口'
        : '高级脚本与 FreeCAD Python 控制台同权限，可修改模型、访问文件及保存；另提供只读任务状态查询',
    };
    const value = { ok: true, source_kind: 'server_manifest', version: VERSION, bridge_protocol: 1, tool_scope: toolScope, tools: activeTools,
      groups, effects: Object.fromEntries(Object.keys(groups).map(group => [group, allEffects[group]])),
      phoenix_operations: ['normalize_names', 'sync_link_labels', 'initialize_properties', 'recommend_properties', 'set_properties', 'set_pad_length'],
      part_previews: { ttl_seconds: 300, max_plans: 32, max_preview_bytes: 128 * 1024, storage: 'memory_only', application_requires_preview: true },
      part_approvals: PFT_PART_APPROVAL_CAPABILITY,
      pad_length_write: PFT_PAD_LENGTH_CAPABILITY,
      ...(toolScope === 'full' ? { recompute_result: PFT_RECOMPUTE_CAPABILITY } : {}),
      script_history: { scope: 'bridge_service_session', session_id_field: 'script_session_id', full_results: 20, remembered_request_ids: 4096, expired_requests_reexecute: false },
      ...(deskEnabled ? { desk: { configured: true, connection_checked: false, query_endpoints_checked: false, scope: 'explicit_workspace_per_call',
        auto_start: false, cad_access: 'none', batch_snapshot: 'not_atomic',
        ...(toolScope === 'chat' ? { explicit_chat_opt_in: true } : {}) } } : {}),
      connection_checked: false, runtime_supported_checked: false, runtime_supported: '请检查 get_connection_status 返回的 supported_methods；清单不代表旧插件已支持新增工具' };
    return { content: [{ type: 'text', text: JSON.stringify(value) }], structuredContent: value };
  });
  server.registerTool('get_connection_status', { title: '检查 FreeCAD 连接', description: '检查 Phoenix 插件是否已启动，返回版本、协议、GUI 调度状态及插件实际声明的 supported_methods。', inputSchema: empty, annotations: read }, () => run(() => freecad.status()));
  server.registerTool('list_documents', { title: '列出打开的文档', description: '读取 FreeCAD 当前打开的文档内部 Name；包含尚未保存的会话文档，不读取磁盘工程文件。', inputSchema: empty, annotations: read }, () => run(() => freecad.listDocuments()));
  server.registerTool('fit_view', { title: '适合窗口', description: '执行 FreeCAD fitAll，将目标文档当前视图缩放到适合窗口；会改变相机，不保存或修改模型。', inputSchema: z.object({ document: doc }).strict(), annotations: camera }, args => run(() => freecad.fitView(args.document)));
  server.registerTool('set_axonometric_view', { title: '切换轴测图', description: '把目标文档相机切换为轴测图；fit=true 时同时适合窗口，不保存或修改模型。', inputSchema: z.object({ document: doc, fit: z.boolean().default(false).describe('是否同时执行适合窗口') }).strict(), annotations: camera }, args => run(() => freecad.setAxonometricView(args.document, args.fit)));
  server.registerTool('get_document_info', { title: '查看文档信息', description: '只读查看会话状态、原生活动上下文和 Body Tip。实例 token 可检测同名重开；修订仅覆盖注册后观察到的部分事件，不能证明几何新鲜或作为完整写入保护。不重算或返回本机路径。', inputSchema: z.object({ document: doc, ...PftReadGuardFields }).strict().superRefine(pftRefineReadGuards), outputSchema: PftReadSuccessSchema, annotations: read }, args => run(() => freecad.getDocumentInfo(args)));
  server.registerTool('list_objects', { title: '分页查看对象', description: '按内部 Name 分页读取对象，可按准确 TypeId 筛选。跨页可传前页实例 token 与部分事件修订，变化则拒绝；不是冻结快照或完整写入保护。不重算，读取结果最多128 KiB。', inputSchema: z.object({ document: doc, ...PftReadGuardFields,
    offset: z.number().int().min(0).max(2147483647).default(0), limit: z.number().int().min(1).max(100).default(50),
    typeId: z.string().regex(/^[A-Za-z_][A-Za-z0-9_:]{0,127}$/u).describe('准确的 FreeCAD TypeId，例如 PartDesign::Body').optional() }).strict().superRefine(pftRefineReadGuards), outputSchema: PftReadSuccessSchema, annotations: read }, args => run(() => freecad.listObjects(args)));
  server.registerTool('get_object_info', { title: '查看对象属性', description: '只读查看类型、缓存状态及最多20个选定属性，返回文档实例与部分事件修订；不证明几何新鲜，不执行表达式或任意代码。不重算，读取结果最多128 KiB。', inputSchema: z.object({ document: doc, object, ...PftReadGuardFields,
    properties: z.array(propertyName).max(20).refine(values => new Set(values).size === values.length, '属性名称不能重复').optional() }).strict().superRefine(pftRefineReadGuards), outputSchema: PftReadSuccessSchema, annotations: read }, args => run(() => freecad.getObjectInfo(args)));
  server.registerTool('get_selection', { title: '读取当前选择', description: '只读读取当前 FreeCAD 选择及子元素名称；可限定已打开文档，不改变选择。', inputSchema: z.object({ document: doc }).strict(), annotations: read }, args => run(() => freecad.getSelection(args.document)));
  server.registerTool('inspect_design', { title: '检查设计状态', description: '只读检查明确对象的 Body Tip、成员、缓存特征参数、表达式及草图自由度和约束分页。只报告可观察的原生缓存，不求解、不重算、不保存、不激活文档或改变选择，不展开 Link 来源或读取文件路径。约束 datum 使用 FreeCAD 内部单位（角度 deg），未知状态明确为 null；修订仅覆盖部分观察事件，不证明几何有效或构成完整写入保护。结果最多128 KiB；必须由支持此新能力的插件执行。',
    inputSchema: PftDesignInspectionInputSchema, outputSchema: PftDesignInspectionSuccessSchema, annotations: read }, args => run(() => freecad.inspectDesign(args)));
  server.registerTool('measure_geometry', { title: '测量现有几何', description: '只读测量指定对象或 FaceN、EdgeN、VertexN 子元素的现有几何；不重算、修复或修改模型。', inputSchema: z.object({ document: doc, object,
    subelement: z.string().regex(/^(?:Face|Edge|Vertex)[1-9][0-9]{0,6}$/u).optional() }).strict(), annotations: read }, args => run(() => freecad.measureGeometry(args)));
  server.registerTool('list_drawing_pages', { title: '列出图纸页', description: '只读列出当前文档中的 TechDraw 图纸页，不生成或更新图纸。', inputSchema: z.object({ document: doc }).strict(), annotations: read }, args => run(() => freecad.listDrawingPages(args.document)));
  server.registerTool('inspect_drawing_page', { title: '检查图纸页', description: '只读查看指定 TechDraw 页的视图、模板与标注摘要；不保证生产尺寸完整，不修复引用或更新页面。', inputSchema: z.object({ document: doc,
    page: name.describe('TechDraw 页的内部 Name，从 list_drawing_pages 获取') }).strict(), annotations: read }, args => run(() => freecad.inspectDrawingPage(args)));
  server.registerTool('set_view', { title: '切换标准视图', description: '将指定文档三维相机切为前、后、左、右、上、下或轴测；fit=true 同时适合窗口，不保存模型。', inputSchema: z.object({ document: doc,
    view: z.enum(['front', 'rear', 'left', 'right', 'top', 'bottom', 'axonometric']), fit: z.boolean().default(false) }).strict(), annotations: camera }, args => run(() => freecad.setView(args)));
  server.registerTool('set_appearance', { title: '设置对象颜色或透明度',
    description: '仅更改明确活动文档及完整目标对象的原生颜色或透明度。random_color 固定本次随机色；set_transparency 必须指定 transparency 整数0–100（0不透明、100完全透明）；兼容 half_transparent 为50%，opaque为0%。单事务、可撤销，不重算、不保存、不改选择或Link来源；复杂Link拒绝。返回真实脚本任务状态，queued/starting/running不表示已应用；用同一requestId调用get_script_status查询，重放必须保留相同参数与ID。',
    inputSchema: PftAppearanceInputSchema, annotations: { ...camera, idempotentHint: true } },
    args => run(async () => pftAppearanceJob(await freecad.executeScript(pftAppearanceScript(args), args.requestId), args)));
  server.registerTool('prepare_part_changes', { title: '预览 Phoenix 零件或Pad长度修改', description: '只预览命名、Link名称、五字段属性或有限Pad Length，不改模型。set_pad_length仅接受一个明确Pad内部Name、body及lengthMm（0.01至10000毫米）；限单打开文档的Body首个且最终单侧固定Length Pad，未知依赖拒绝。预览只显示原值、请求值及将执行的重算，不虚构新几何；该操作始终须用户在FreeCAD原生窗口确认，不能直接apply，不自动保存，也不承诺原生回调隔离。原五操作仍按既有规则；仅set_properties接受values且所有对象字段集一致。返回5分钟token和完整差异/冲突，AI必须完整展示，再调用request_part_change_approval等待人工操作，不能把等待当写入。', inputSchema: z.object({ document: name, objects: nonemptyObjects,
    operation: z.enum(['normalize_names', 'sync_link_labels', 'initialize_properties', 'recommend_properties', 'set_properties', 'set_pad_length']),
    values: partValues.optional(), body: name.optional(), lengthMm: z.number().finite().min(0.01).max(10000).optional() }).strict().superRefine((value, context) => {
      if (value.operation === 'set_pad_length') {
        if (!PftPadLengthInputSchema.safeParse(value).success) context.addIssue({ code: 'custom', message: 'Pad预览须只指定一个Pad、明确body和有限lengthMm，不接受values或确认标志' });
        return;
      }
      if (Object.hasOwn(value, 'body') || Object.hasOwn(value, 'lengthMm')) context.addIssue({ code: 'custom', message: '仅set_pad_length允许body和lengthMm' });
      if ((value.operation === 'set_properties') !== (value.values !== undefined)) context.addIssue({ code: 'custom', message: '仅set_properties需要且允许values', path: ['values'] });
      if (value.values && (Object.keys(value.values).length !== value.objects.length || value.objects.some(object => !Object.hasOwn(value.values!, object)))) context.addIssue({ code: 'custom', message: 'values必须与所有目标对象精确对应', path: ['values'] });
      if (value.values && new Set(Object.values(value.values).map(fields => Object.keys(fields).sort().join(','))).size > 1) context.addIssue({ code: 'custom', message: '所有对象必须使用相同字段集', path: ['values'] });
    }), annotations: { ...read, idempotentHint: false } }, args => run(() => args.operation === 'set_pad_length'
      ? freecad.preparePartChanges(PftPadLengthInputSchema.parse(args))
      : freecad.preparePartChanges({ document: args.document, objects: args.objects, operation: args.operation,
          ...(args.values === undefined ? {} : { values: args.values }) })));
  server.registerTool('request_part_change_approval', { title: '请求原生确认零件修改',
    description: '先将prepare_part_changes的完整预览展示给用户，再用原token打开或复用FreeCAD原生确认窗口。请求本身不授予确认；等待用户亲自点击应用，只有GUI回调能执行此确认链写入。awaiting_confirmation或applying不表示已写入；用get_part_change_status查询原token。超时不自动循环或重试；调用者可显式查询或用同一token再次请求复用窗口，不创建新token规避状态。不自动保存。',
    inputSchema: PftPartApprovalInputSchema, outputSchema: PftPartApprovalSuccessSchema,
    annotations: { ...camera, destructiveHint: false, idempotentHint: true } }, args => run(() => freecad.requestPartChangeApproval(args.token)));
  server.registerTool('get_part_change_status', { title: '查询零件修改确认状态',
    description: '仅查询原预览token的内存状态，不打开窗口、不授予确认、不应用或保存。prepared/awaiting_confirmation/applying均不是完成；applied的result才报告实际修改数量；Pad另含独立几何反验摘要且须原生确认，不把重算计数当成功。native_confirmation单独表明是否经过原生GUI确认。失败只返回固定错误码。超时或无效结果不自动重试，不把未知状态当成已确认或已写入。',
    inputSchema: PftPartApprovalInputSchema, outputSchema: PftPartApprovalSuccessSchema, annotations: read },
    args => run(() => freecad.getPartChangeStatus(args.token)));
  if (toolScope === 'full') {
  server.registerTool('capture_view', { title: '获取三维视图图像', description: '仅在用户明确请求查看图像时使用：导出 FreeCAD 当前三维视图 PNG，可能透露模型信息。不截图桌面、不改变视角、不保存模型；图像大小上限 600 KiB。', inputSchema: z.object({ document: doc,
    width: z.number().int().min(128).max(1024).default(800), height: z.number().int().min(128).max(1024).default(600) }).strict(), annotations: read }, args => run(() => freecad.captureView(args), imageResult));
  server.registerTool('set_selection', { title: '选择对象', description: '用明确的内部 Name 替换目标活动文档的对象选择；空列表清除此文档选择。保留其他文档选择，不修改模型或保存。', inputSchema: z.object({ document: name, objects: objectList }).strict(), annotations: camera }, args => run(() => freecad.setSelection(args)));
  server.registerTool('set_visibility', { title: '设置对象显隐', description: '对明确活动文档的所选目标统一设置可见性。先校验全部目标，事务可撤销，不自动保存；不会改 Label 或其他属性。', inputSchema: z.object({ document: name, objects: nonemptyObjects, visible: z.boolean() }).strict(), annotations: camera }, args => run(() => freecad.setVisibility(args)));
  server.registerTool('recompute_document', { title: '重算文档', description: '显式调用明确活动文档的原生重算，要求未处于编辑或事务中，会执行特征求解并可能改变缓存几何。先检查兼容结果能力，原生返回值仅为非负对象数量；对象检查仅限入口文档当前对象，不承诺原生回调或跨文档隔离；不将数量或对象状态视为完成、几何有效或几何新鲜的证明。不保存、不自动重试；超时或结果无效时请求可能已经执行。', inputSchema: z.object({ document: name }).strict(), outputSchema: PftRecomputeSuccessSchema, annotations: { ...camera, destructiveHint: true, idempotentHint: false } }, args => run(() => freecad.recomputeDocument(args.document)));
  server.registerTool('apply_part_changes', { title: '应用 Phoenix 零件工具预览', description: '仅在用户授权的范围内，应用prepare_part_changes返回的5分钟token。复核文档实例、对象快照和来源，冲突则拒绝。写入经工作台Controller事务，可撤销但不保存；相同token不会再次执行，过期或失败须重新预览；已进入原生确认链的token不能通过此入口确认或应用；set_pad_length始终拒绝直接apply，只能由原生GUI人工确认。', inputSchema: z.object({ token: z.string().regex(/^[A-Za-z0-9_-]{16,128}$/u) }).strict(), annotations: { ...camera, destructiveHint: true } }, args => run(() => freecad.applyPartChanges(args.token)));
  server.registerTool('execute_script', { title: '执行高级 FreeCAD 脚本', description: '高级能力：与 FreeCAD Python 控制台同权限，在 GUI 主线程执行，可修改模型、读写文件或自行保存。框架不额外保存，也不承诺异常自动回滚。超时后先用 get_script_status 查询，重放须使用相同 requestId 和代码。去重仅限同一桥接服务会话；重载或重启会重置，通过 get_connection_status 的 script_session_id 识别。完整结果保留最近 20 份，过期 ID 在该会话中仍拒绝重执行。', inputSchema: z.object({
    code: z.string().min(1).max(32768).regex(/^[^\u0000\p{Cs}]*$/u), requestId }).strict(), annotations: { readOnlyHint: false, destructiveHint: true, idempotentHint: true, openWorldHint: true } }, args => run(() => freecad.executeScript(args.code, args.requestId)));
  }
  server.registerTool('get_script_status', { title: '查询脚本请求状态', description: '只读查询同一桥接服务会话内指定 requestId 的状态；不重新执行代码、不进入 GUI 队列。重载或重启会重置，以 script_session_id 识别；完整结果保留最近 20 份，过期 ID 在会话中仍拒绝重执行。', inputSchema: z.object({ requestId }).strict(), annotations: read }, args => run(() => freecad.getScriptStatus(args.requestId)));
  if (deskEnabled) pftRegisterDeskTools(server, config.desk!, config.timeoutMs);
  return server;
}
