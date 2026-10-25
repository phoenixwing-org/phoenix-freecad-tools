// SPDX-License-Identifier: Apache-2.0
import { request } from 'node:http';

export class BridgeError extends Error {
  constructor(public readonly code: string, message: string) { super(message); this.name = 'BridgeError'; }
}

export type StandardView = 'front' | 'rear' | 'left' | 'right' | 'top' | 'bottom' | 'axonometric';
type DocumentInput = { document?: string | undefined };
export type ListObjectsInput = DocumentInput & { offset?: number | undefined; limit?: number | undefined; typeId?: string | undefined };
export type ObjectInfoInput = DocumentInput & { object: string; properties?: string[] | undefined };
export type GeometryInput = DocumentInput & { object: string; subelement?: string | undefined };
export type DrawingPageInput = DocumentInput & { page: string };
export type ViewInput = DocumentInput & { view: StandardView; fit?: boolean | undefined };
export type CaptureInput = DocumentInput & { width?: number | undefined; height?: number | undefined };
export type ObjectsInput = { document: string; objects: string[] };
export type VisibilityInput = ObjectsInput & { visible: boolean };
export type PartOperation = 'normalize_names' | 'sync_link_labels' | 'initialize_properties' | 'recommend_properties' | 'set_properties';
export type BomField = 'PartNumber' | 'PartVersion' | 'TypeCode' | 'ModelSeries' | 'PartName';
export type PartChangesInput = ObjectsInput & { operation: PartOperation; values?: Record<string, Partial<Record<BomField, string | undefined>>> | undefined };
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
    throw new BridgeError('INVALID_RESPONSE', 'FreeCAD 视图图像的格式、尺寸或大小无效。');
  }
  const image = Buffer.from(value.data, 'base64');
  if (image.length > MAX_IMAGE_BYTES || image.toString('base64') !== value.data || image.length < 33
    || !image.subarray(0, 8).equals(PNG_SIGNATURE) || image.readUInt32BE(8) !== 13 || image.toString('ascii', 12, 16) !== 'IHDR'
    || image.readUInt32BE(16) !== width || image.readUInt32BE(20) !== height) {
    throw new BridgeError('INVALID_RESPONSE', 'FreeCAD 视图必须为不超过 600 KiB 的有效 PNG 头与规范 Base64。');
  }
  return { document: value.document, mime_type: 'image/png', data: value.data, width, height };
}

export class FreeCadClient {
  constructor(private readonly options: { url: string; timeoutMs: number }) {}

  private async call(method: string, params: Record<string, unknown> = {}): Promise<unknown> {
    const body = JSON.stringify({ method, params });
    const text = await new Promise<string>((resolve, reject) => {
      const req = request(new URL('/rpc', this.options.url), {
        method: 'POST', family: 4, headers: { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(body) },
      }, response => {
        if (response.statusCode !== 200) {
          response.resume(); reject(new BridgeError('HTTP_ERROR', `FreeCAD 插件返回 HTTP ${response.statusCode}`)); return;
        }
        if (!response.headers['content-type']?.includes('application/json')) {
          response.resume(); reject(new BridgeError('INVALID_RESPONSE', 'FreeCAD 插件未返回 JSON')); return;
        }
        const chunks: Buffer[] = []; let size = 0;
        response.on('data', (chunk: Buffer) => {
          size += chunk.length;
          if (size > 1024 * 1024) {
            const error = new BridgeError('RESPONSE_TOO_LARGE', '响应超过 1 MiB 上限');
            reject(error); req.destroy(error); response.destroy(); return;
          }
          chunks.push(chunk);
        });
        response.on('end', () => {
          try { resolve(new TextDecoder('utf-8', { fatal: true }).decode(Buffer.concat(chunks))); }
          catch { reject(new BridgeError('INVALID_RESPONSE', 'FreeCAD 插件返回无效 UTF-8 JSON')); }
        });
        response.on('error', () => reject(new BridgeError('CONNECTION_FAILED', 'FreeCAD 插件连接中断')));
      });
      const timer = setTimeout(() => req.destroy(new BridgeError('BRIDGE_TIMEOUT', '等待 FreeCAD 超时；远端操作可能已执行，请检查当前视图后再操作')), this.options.timeoutMs);
      req.on('error', error => reject(error instanceof BridgeError ? error : new BridgeError('CONNECTION_FAILED', '无法连接 Phoenix FreeCAD 插件，请在 FreeCAD 中启动服务')));
      req.on('close', () => clearTimeout(timer));
      req.end(body);
    });
    let result: unknown;
    try { result = JSON.parse(text); } catch { throw new BridgeError('INVALID_RESPONSE', 'FreeCAD 插件返回无效 JSON'); }
    if (!isRecord(result)) throw new BridgeError('INVALID_RESPONSE', 'FreeCAD 插件响应格式无效');
    if (result.ok === false) {
      const error = isRecord(result.error) ? result.error : undefined;
      throw new BridgeError(typeof error?.code === 'string' ? error.code : 'BRIDGE_ERROR', typeof error?.message === 'string' ? error.message : 'FreeCAD 操作失败');
    }
    if (result.ok !== true || !('data' in result)) throw new BridgeError('INVALID_RESPONSE', 'FreeCAD 插件缺少结果');
    return result.data;
  }

  async status(): Promise<Record<string, unknown>> {
    const status = await this.call('get_status');
    if (!isRecord(status)) throw new BridgeError('INVALID_RESPONSE', '状态格式无效');
    if (status.protocol_version !== 1) throw new BridgeError('UNSUPPORTED_PROTOCOL', 'FreeCAD 插件协议版本不受支持');
    if ('supported_methods' in status && (!Array.isArray(status.supported_methods) || status.supported_methods.length > 128
      || !status.supported_methods.every(item => typeof item === 'string' && /^[a-z][a-z0-9_]{0,99}$/u.test(item)))) {
      throw new BridgeError('INVALID_RESPONSE', 'FreeCAD 插件能力清单格式无效');
    }
    return status;
  }

  private async feature(method: string, params: Record<string, unknown> = {}): Promise<unknown> {
    const status = await this.status();
    if (!Array.isArray(status.supported_methods) || !status.supported_methods.includes(method)) {
      throw new BridgeError('UNSUPPORTED_CAPABILITY', '当前 FreeCAD 插件未声明此能力，请更新并重新加载插件后检查连接。');
    }
    return this.call(method, params);
  }

  async listDocuments(): Promise<string[]> {
    await this.status(); const result = await this.call('list_documents');
    if (!Array.isArray(result) || !result.every(item => typeof item === 'string')) throw new BridgeError('INVALID_RESPONSE', '文档列表格式无效');
    return result as string[];
  }
  async fitView(document?: string): Promise<unknown> {
    await this.status(); return this.call('view_fit', documentParams(document));
  }
  async setAxonometricView(document?: string, fit = false): Promise<unknown> {
    await this.status(); return this.call('view_axonometric', { ...documentParams(document), fit });
  }
  getDocumentInfo(document?: string): Promise<unknown> {
    return this.feature('get_document_info', documentParams(document));
  }
  listObjects({ document, offset = 0, limit = 50, typeId }: ListObjectsInput = {}): Promise<unknown> {
    return this.feature('list_objects', { ...documentParams(document), offset, limit, ...(typeId === undefined ? {} : { typeId }) });
  }
  getObjectInfo({ document, object, properties }: ObjectInfoInput): Promise<unknown> {
    return this.feature('get_object_info', { ...documentParams(document), objectName: object, ...(properties === undefined ? {} : { properties }) });
  }
  getSelection(document?: string): Promise<unknown> {
    return this.feature('get_selection', documentParams(document));
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
  recomputeDocument(document: string): Promise<unknown> {
    return this.feature('recompute_document', { docName: document });
  }
  preparePartChanges({ document, objects, operation, values }: PartChangesInput): Promise<unknown> {
    return this.feature('prepare_part_changes', { docName: document, objectNames: objects, operation, ...(values === undefined ? {} : { values }) });
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
