// SPDX-License-Identifier: Apache-2.0
import { request } from 'node:http';
import { PftNativeReadDataSchema, PftReadCapabilitySchema, PftReadGuardSchema, PFT_LEGACY_READ_STATE, PFT_MAX_READ_BYTES, type PftReadGuards } from '../pft-read-provenance.js';
import { pftInspectDesign, type PftDesignInspectionInput, type PftDesignInspectionData } from '../pft-design-inspection.js';
import { pftRecomputeDocument, PftRecomputeCapabilitySchema, type PftRecomputeData } from '../pft-recompute.js';
import { pftPartApproval, PftPartApprovalCapabilitySchema, type PftPartApprovalData } from '../pft-part-approval.js';
import { pftPreparePadLength, PftPadLengthCapabilitySchema, type PftPadLengthInput } from '../pft-pad-length.js';

export class PftBridgeError extends Error {
  constructor(public readonly code: string, message: string) { super(message); this.name = 'PftBridgeError'; }
}

export type StandardView = 'front' | 'rear' | 'left' | 'right' | 'top' | 'bottom' | 'axonometric';
type DocumentInput = { document?: string | undefined };
export type PftDocumentInfoInput = DocumentInput & PftReadGuards;
export type ListObjectsInput = PftDocumentInfoInput & { offset?: number | undefined; limit?: number | undefined; typeId?: string | undefined };
export type ObjectInfoInput = PftDocumentInfoInput & { object: string; properties?: string[] | undefined };
export type GeometryInput = DocumentInput & { object: string; subelement?: string | undefined };
export type DrawingPageInput = DocumentInput & { page: string };
export type ViewInput = DocumentInput & { view: StandardView; fit?: boolean | undefined };
export type CaptureInput = DocumentInput & { width?: number | undefined; height?: number | undefined };
export type ObjectsInput = { document: string; objects: string[] };
export type VisibilityInput = ObjectsInput & { visible: boolean };
export type PartOperation = 'normalize_names' | 'sync_link_labels' | 'initialize_properties' | 'recommend_properties' | 'set_properties' | 'set_pad_length';
export type BomField = 'PartNumber' | 'PartVersion' | 'TypeCode' | 'ModelSeries' | 'PartName';
export type PartChangesInput = PftPadLengthInput | (ObjectsInput & { operation: Exclude<PartOperation, 'set_pad_length'>;
  values?: Record<string, Partial<Record<BomField, string | undefined>>> | undefined; body?: never; lengthMm?: never });
export type CapturedView = { document: string; mime_type: 'image/png'; data: string; width: number; height: number };

const isRecord = (value: unknown): value is Record<string, unknown> => value !== null && typeof value === 'object' && !Array.isArray(value);
const documentParams = (document?: string): Record<string, unknown> => document === undefined ? {} : { docName: document };
const MAX_IMAGE_BYTES = 600 * 1024;
const PNG_SIGNATURE = Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]);

function captureResult(value: unknown, width: number, height: number): CapturedView {
  if (!isRecord(value) || typeof value.document !== 'string' || !value.document || value.document.length > 256
    || /[\p{Cc}\p{Cs}]/u.test(value.document) || value.mime_type !== 'image/png'
    || value.width !== width || value.height !== height || typeof value.data !== 'string'
    || !value.data || value.data.length > Math.ceil(MAX_IMAGE_BYTES / 3) * 4 || value.data.length % 4 !== 0
    || !/^[A-Za-z0-9+/]*={0,2}$/u.test(value.data)) {
    throw new PftBridgeError('INVALID_RESPONSE', 'FreeCAD 视图图像的格式、尺寸或大小无效。');
  }
  const image = Buffer.from(value.data, 'base64');
  if (image.length > MAX_IMAGE_BYTES || image.toString('base64') !== value.data || image.length < 33
    || !image.subarray(0, 8).equals(PNG_SIGNATURE) || image.readUInt32BE(8) !== 13 || image.toString('ascii', 12, 16) !== 'IHDR'
    || image.readUInt32BE(16) !== width || image.readUInt32BE(20) !== height) {
    throw new PftBridgeError('INVALID_RESPONSE', 'FreeCAD 视图必须为不超过 600 KiB 的有效 PNG 头与规范 Base64。');
  }
  return { document: value.document, mime_type: 'image/png', data: value.data, width, height };
}

export class PftFreeCadClient {
  constructor(private readonly options: { url: string; timeoutMs: number }) {}

  private async call(method: string, params: Record<string, unknown> = {}): Promise<unknown> {
    const body = JSON.stringify({ method, params });
    const text = await new Promise<string>((resolve, reject) => {
      const req = request(new URL('/rpc', this.options.url), {
        method: 'POST', family: 4, headers: { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(body) },
      }, response => {
        if (response.statusCode !== 200) {
          response.resume(); reject(new PftBridgeError('HTTP_ERROR', `FreeCAD 插件返回 HTTP ${response.statusCode}`)); return;
        }
        if (!response.headers['content-type']?.includes('application/json')) {
          response.resume(); reject(new PftBridgeError('INVALID_RESPONSE', 'FreeCAD 插件未返回 JSON')); return;
        }
        const chunks: Buffer[] = []; let size = 0;
        response.on('data', (chunk: Buffer) => {
          size += chunk.length;
          if (size > 1024 * 1024) {
            const error = new PftBridgeError('RESPONSE_TOO_LARGE', '响应超过 1 MiB 上限');
            reject(error); req.destroy(error); response.destroy(); return;
          }
          chunks.push(chunk);
        });
        response.on('end', () => {
          try { resolve(new TextDecoder('utf-8', { fatal: true }).decode(Buffer.concat(chunks))); }
          catch { reject(new PftBridgeError('INVALID_RESPONSE', 'FreeCAD 插件返回无效 UTF-8 JSON')); }
        });
        response.on('error', () => reject(new PftBridgeError('CONNECTION_FAILED', 'FreeCAD 插件连接中断')));
      });
      const timer = setTimeout(() => req.destroy(new PftBridgeError('BRIDGE_TIMEOUT', '等待 FreeCAD 超时；远端操作可能已执行，请检查当前视图后再操作')), this.options.timeoutMs);
      req.on('error', error => reject(error instanceof PftBridgeError ? error : new PftBridgeError('CONNECTION_FAILED', '无法连接 Phoenix FreeCAD 插件，请在 FreeCAD 中启动服务')));
      req.on('close', () => clearTimeout(timer));
      req.end(body);
    });
    let result: unknown;
    try { result = JSON.parse(text); } catch { throw new PftBridgeError('INVALID_RESPONSE', 'FreeCAD 插件返回无效 JSON'); }
    if (!isRecord(result)) throw new PftBridgeError('INVALID_RESPONSE', 'FreeCAD 插件响应格式无效');
    if (result.ok === false) {
      const error = isRecord(result.error) ? result.error : undefined;
      throw new PftBridgeError(typeof error?.code === 'string' ? error.code : 'BRIDGE_ERROR', typeof error?.message === 'string' ? error.message : 'FreeCAD 操作失败');
    }
    if (result.ok !== true || !('data' in result)) throw new PftBridgeError('INVALID_RESPONSE', 'FreeCAD 插件缺少结果');
    return result.data;
  }

  async status(): Promise<Record<string, unknown>> {
    const status = await this.call('get_status');
    if (!isRecord(status)) throw new PftBridgeError('INVALID_RESPONSE', '状态格式无效');
    if (status.protocol_version !== 1) throw new PftBridgeError('UNSUPPORTED_PROTOCOL', 'FreeCAD 插件协议版本不受支持');
    if ('supported_methods' in status && (!Array.isArray(status.supported_methods) || status.supported_methods.length > 128
      || !status.supported_methods.every(item => typeof item === 'string' && /^[a-z][a-z0-9_]{0,99}$/u.test(item)))) {
      throw new PftBridgeError('INVALID_RESPONSE', 'FreeCAD 插件能力清单格式无效');
    }
    if (isRecord(status.read_provenance) && status.read_provenance.version === 1
      && !PftReadCapabilitySchema.safeParse(status.read_provenance).success) {
      throw new PftBridgeError('INVALID_RESPONSE', 'FreeCAD 插件读取来源能力声明格式无效');
    }
    if ('recompute_result' in status && !PftRecomputeCapabilitySchema.safeParse(status.recompute_result).success) {
      throw new PftBridgeError('INVALID_RESPONSE', 'FreeCAD 插件重算结果能力声明格式无效；未派发重算。');
    }
    if ('part_approvals' in status && !PftPartApprovalCapabilitySchema.safeParse(status.part_approvals).success) {
      throw new PftBridgeError('INVALID_RESPONSE', 'FreeCAD 插件原生确认能力声明格式无效；未派发请求。');
    }
    if ('pad_length_write' in status && !PftPadLengthCapabilitySchema.safeParse(status.pad_length_write).success) {
      throw new PftBridgeError('INVALID_RESPONSE', 'FreeCAD 插件Pad Length能力声明格式无效；未派发预览。');
    }
    return status;
  }

  private async feature(method: string, params: Record<string, unknown> = {}): Promise<unknown> {
    const status = await this.status();
    if (!Array.isArray(status.supported_methods) || !status.supported_methods.includes(method)) {
      throw new PftBridgeError('UNSUPPORTED_CAPABILITY', '当前 FreeCAD 插件未声明此能力，请更新并重新加载插件后检查连接。');
    }
    return this.call(method, params);
  }

  async listDocuments(): Promise<string[]> {
    await this.status(); const result = await this.call('list_documents');
    if (!Array.isArray(result) || !result.every(item => typeof item === 'string')) throw new PftBridgeError('INVALID_RESPONSE', '文档列表格式无效');
    return result as string[];
  }
  async fitView(document?: string): Promise<unknown> {
    await this.status(); return this.call('view_fit', documentParams(document));
  }
  async setAxonometricView(document?: string, fit = false): Promise<unknown> {
    await this.status(); return this.call('view_axonometric', { ...documentParams(document), fit });
  }
  private async readFeature(method: string, params: Record<string, unknown>, guards: PftReadGuards): Promise<unknown> {
    const parsedGuards = PftReadGuardSchema.safeParse(guards);
    if (!parsedGuards.success) throw new PftBridgeError('INVALID_ARGUMENT', '读取修订必须是非负安全整数，并同时提供有效的文档实例 token。');
    const status = await this.status();
    if (!Array.isArray(status.supported_methods) || !status.supported_methods.includes(method)) {
      throw new PftBridgeError('UNSUPPORTED_CAPABILITY', '当前 FreeCAD 插件未声明此读取能力，请更新并重新加载插件。');
    }
    const capability = PftReadCapabilitySchema.safeParse(status.read_provenance);
    const native = capability.success && capability.data.methods.includes(method);
    const guarded = guards.expectedDocumentInstanceToken !== undefined || guards.expectedRevision !== undefined;
    if (guarded && !native) throw new PftBridgeError('UNSUPPORTED_CAPABILITY', '当前 FreeCAD 插件不支持文档实例与观察修订检查；不会向旧插件派发此读取。');
    const value = await this.call(method, { ...params, ...parsedGuards.data });
    if (!isRecord(value)) throw new PftBridgeError('INVALID_RESPONSE', 'FreeCAD 读取结果不是对象');
    const normalized = native ? value : { ...value, ...PFT_LEGACY_READ_STATE };
    if (Buffer.byteLength(JSON.stringify(normalized), 'utf8') > PFT_MAX_READ_BYTES) {
      throw new PftBridgeError('READ_RESPONSE_TOO_LARGE', 'FreeCAD 读取结果超过 128 KiB 预算，请缩小读取范围。');
    }
    if (!native) return normalized;
    const parsed = PftNativeReadDataSchema.safeParse(value);
    if (!parsed.success) throw new PftBridgeError('INVALID_RESPONSE', 'FreeCAD 文档实例、修订或来源字段格式无效');
    return parsed.data;
  }
  getDocumentInfo(input?: string | PftDocumentInfoInput): Promise<unknown> {
    const { document, ...guards } = typeof input === 'string' ? { document: input } : input ?? {};
    return this.readFeature('get_document_info', documentParams(document), guards);
  }
  listObjects({ document, offset = 0, limit = 50, typeId, ...guards }: ListObjectsInput = {}): Promise<unknown> {
    return this.readFeature('list_objects', { ...documentParams(document), offset, limit, ...(typeId === undefined ? {} : { typeId }) }, guards);
  }
  getObjectInfo({ document, object, properties, ...guards }: ObjectInfoInput): Promise<unknown> {
    return this.readFeature('get_object_info', { ...documentParams(document), objectName: object, ...(properties === undefined ? {} : { properties }) }, guards);
  }
  getSelection(document?: string): Promise<unknown> {
    return this.feature('get_selection', documentParams(document));
  }
  inspectDesign(input: PftDesignInspectionInput): Promise<PftDesignInspectionData> {
    return pftInspectDesign(input, { status: () => this.status(), read: params => this.call('inspect_design', params) }, PftBridgeError);
  }
  measureGeometry({ document, object, subelement }: GeometryInput): Promise<unknown> {
    return this.feature('measure_geometry', { ...documentParams(document), objectName: object, ...(subelement === undefined ? {} : { subelement }) });
  }
  listDrawingPages(document?: string): Promise<unknown> {
    return this.feature('list_drawing_pages', documentParams(document));
  }
  inspectDrawingPage({ document, page }: DrawingPageInput): Promise<unknown> {
    return this.feature('inspect_drawing_page', { ...documentParams(document), pageName: page });
  }
  setView({ document, view, fit = false }: ViewInput): Promise<unknown> {
    return this.feature('view_standard', { ...documentParams(document), view, fit });
  }
  async captureView({ document, width = 800, height = 600 }: CaptureInput = {}): Promise<CapturedView> {
    const result = await this.feature('capture_view', { ...documentParams(document), width, height });
    return captureResult(result, width, height);
  }
  setSelection({ document, objects }: ObjectsInput): Promise<unknown> {
    return this.feature('set_selection', { docName: document, objectNames: objects });
  }
  setVisibility({ document, objects, visible }: VisibilityInput): Promise<unknown> {
    return this.feature('set_visibility', { docName: document, objectNames: objects, visible });
  }
  recomputeDocument(document: string): Promise<PftRecomputeData> {
    return pftRecomputeDocument(document, { status: () => this.status(),
      recompute: params => this.call('recompute_document', params) }, PftBridgeError);
  }
  async preparePartChanges(input: PartChangesInput): Promise<unknown> {
    if (input.operation === 'set_pad_length') {
      return pftPreparePadLength(input, { status: () => this.status(),
        prepare: params => this.call('prepare_part_changes', params) }, PftBridgeError);
    }
    if (Object.hasOwn(input, 'body') || Object.hasOwn(input, 'lengthMm')) {
      throw new PftBridgeError('INVALID_ARGUMENT', '仅set_pad_length允许body和lengthMm参数；未派发预览。');
    }
    const { document, objects, operation, values } = input;
    return this.feature('prepare_part_changes', { docName: document, objectNames: objects, operation, ...(values === undefined ? {} : { values }) });
  }
  requestPartChangeApproval(token: string): Promise<PftPartApprovalData> {
    return pftPartApproval('request_part_change_approval', { token }, { status: () => this.status(),
      call: (method, params) => this.call(method, params) }, PftBridgeError);
  }
  getPartChangeStatus(token: string): Promise<PftPartApprovalData> {
    return pftPartApproval('get_part_change_status', { token }, { status: () => this.status(),
      call: (method, params) => this.call(method, params) }, PftBridgeError);
  }
  applyPartChanges(token: string): Promise<unknown> {
    return this.feature('apply_part_changes', { token });
  }
  executeScript(code: string, requestId: string): Promise<unknown> {
    return this.feature('execute_script', { code, requestId });
  }
  getScriptStatus(requestId: string): Promise<unknown> {
    return this.feature('get_script_status', { requestId });
  }
}
