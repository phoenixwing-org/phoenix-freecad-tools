// SPDX-License-Identifier: Apache-2.0
import { request } from 'node:http';
export class BridgeError extends Error {
  constructor(public readonly code: string, message: string) { super(message); this.name = 'BridgeError'; }
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
          if (size > 1024 * 1024) { req.destroy(new BridgeError('RESPONSE_TOO_LARGE', '响应超过 1 MiB 上限')); return; }
          chunks.push(chunk);
        });
        response.on('end', () => resolve(Buffer.concat(chunks).toString('utf8')));
        response.on('error', () => reject(new BridgeError('CONNECTION_FAILED', 'FreeCAD 插件连接中断')));
      });
      const timer = setTimeout(() => req.destroy(new BridgeError('BRIDGE_TIMEOUT', '等待 FreeCAD 超时；远端操作可能已执行，请检查当前视图后再操作')), this.options.timeoutMs);
      req.on('error', error => reject(error instanceof BridgeError ? error : new BridgeError('CONNECTION_FAILED', '无法连接 Phoenix FreeCAD 插件，请在 FreeCAD 中启动服务')));
      req.on('close', () => clearTimeout(timer));
      req.end(body);
    });
    let result: unknown;
    try { result = JSON.parse(text); } catch { throw new BridgeError('INVALID_RESPONSE', 'FreeCAD 插件返回无效 JSON'); }
    if (!result || typeof result !== 'object' || !('ok' in result)) throw new BridgeError('INVALID_RESPONSE', 'FreeCAD 插件响应格式无效');
    const envelope = result as Record<string, unknown>;
    if (envelope.ok === false) {
      const error = envelope.error as Record<string, unknown> | undefined;
      throw new BridgeError(typeof error?.code === 'string' ? error.code : 'BRIDGE_ERROR', typeof error?.message === 'string' ? error.message : 'FreeCAD 操作失败');
    }
    if (envelope.ok !== true || !('data' in envelope)) throw new BridgeError('INVALID_RESPONSE', 'FreeCAD 插件缺少结果');
    return envelope.data;
  }

  async status(): Promise<Record<string, unknown>> {
    const result = await this.call('get_status');
    if (!result || typeof result !== 'object' || Array.isArray(result)) throw new BridgeError('INVALID_RESPONSE', '状态格式无效');
    const status = result as Record<string, unknown>;
    if (status.protocol_version !== 1) throw new BridgeError('UNSUPPORTED_PROTOCOL', 'FreeCAD 插件协议版本不受支持');
    return status;
  }
  async listDocuments(): Promise<string[]> {
    await this.status(); const result = await this.call('list_documents');
    if (!Array.isArray(result) || !result.every(item => typeof item === 'string')) throw new BridgeError('INVALID_RESPONSE', '文档列表格式无效');
    return result as string[];
  }
  async fitView(document?: string): Promise<unknown> {
    await this.status(); return this.call('view_fit', document ? { docName: document } : {});
  }
  async setAxonometricView(document?: string, fit = false): Promise<unknown> {
    await this.status(); return this.call('view_axonometric', { ...(document ? { docName: document } : {}), fit });
  }
}
