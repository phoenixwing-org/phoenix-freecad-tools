// SPDX-License-Identifier: Apache-2.0
/** Fixed local business-read routes only; POST is a bounded read query, never a job. */
import { request, type IncomingHttpHeaders } from 'node:http';
import { performance } from 'node:perf_hooks';
import { PftDeskError } from '../pft-desk-error.js';

export type PftDeskHttpBudget = { remaining: number };
export type PftDeskHttpResponse = { bytes: Buffer; headers: IncomingHttpHeaders; status: number };
export async function pftDeskHttpRead(origin: string, route: string, deadline: number,
  budget: PftDeskHttpBudget, options: { method?: 'GET' | 'POST'; body?: Buffer; maximum: number;
    kind?: 'json' | 'png'; allowFailure?: boolean }): Promise<PftDeskHttpResponse> {
  const method = options.method ?? 'GET', url = new URL(route, origin);
  const allowed = ['/api/desk-service', '/api/runtime/features', '/api/cad/index/search/',
    '/api/cad/bom/xref/counts', '/api/cad/bom/xref/outgoing',
    '/api/cad/cache/v1/query', '/api/cad/cache/v1/thumbnail'];
  if (!/^http:\/\/127\.0\.0\.1:[1-9][0-9]{0,4}$/u.test(origin) || url.origin !== origin
    || !allowed.includes(url.pathname) || url.hash || url.username || url.password
    || (method === 'POST') !== (url.pathname === '/api/cad/cache/v1/query')
    || method === 'POST' && (!options.body || options.body.length > 32 * 1024)
    || method === 'GET' && options.body !== undefined) {
    throw new PftDeskError('INVALID_ARGUMENT', 'Desk 读取只允许固定本机接口和有界查询。');
  }
  const duration = Math.floor(deadline - performance.now());
  if (duration <= 0) throw new PftDeskError('DESK_TIMEOUT', 'Desk 读取超过截止时间；不会重试或停止后台服务。');
  return new Promise((resolve, reject) => {
    let settled = false;
    const headers: Record<string, string> = { Accept: options.kind === 'png' ? 'image/png' : 'application/json' };
    if (options.body) { headers['Content-Type'] = 'application/json'; headers['Content-Length'] = String(options.body.length); }
    const req = request(url, { method, family: 4, agent: false, headers }, response => {
      const status = response.statusCode ?? 0;
      if (status >= 300 && status < 400 || status !== 200 && !options.allowFailure) {
        fail(new PftDeskError(status >= 300 && status < 400 ? 'DESK_REDIRECT_FORBIDDEN' : 'DESK_HTTP_ERROR',
          'Desk 读取接口拒绝请求或发生重定向，未跟随其他地址。'));
        response.destroy(); req.destroy(); return;
      }
      const declared = response.headers['content-length'];
      if (declared !== undefined && Number.isSafeInteger(Number(declared)) && Number(declared) > options.maximum) {
        fail(new PftDeskError('RESPONSE_TOO_LARGE', 'Desk 读取结果超过总大小限制，未返回不完整结果。'));
        response.destroy(); req.destroy(); return;
      }
      if (declared !== undefined && (typeof declared !== 'string' || !/^[0-9]+$/u.test(declared)
        || !Number.isSafeInteger(Number(declared)))
        || response.headers['content-encoding'] && response.headers['content-encoding'] !== 'identity') {
        fail(new PftDeskError('INVALID_RESPONSE', 'Desk 响应长度或编码不受支持。'));
        response.destroy(); req.destroy(); return;
      }
      const chunks: Buffer[] = []; let size = 0;
      response.on('data', (chunk: Buffer) => {
        size += chunk.length; budget.remaining -= chunk.length;
        if (size > options.maximum || budget.remaining < 0) {
          fail(new PftDeskError('RESPONSE_TOO_LARGE', 'Desk 读取结果超过总大小限制，未返回不完整结果。'));
          response.destroy(); req.destroy(); return;
        }
        chunks.push(chunk);
      });
      response.once('end', () => {
        if (settled) return;
        if (declared !== undefined && Number(declared) !== size) {
          fail(new PftDeskError('INVALID_RESPONSE', 'Desk 响应的实际长度不匹配。')); return;
        }
        if (performance.now() >= deadline) {
          fail(new PftDeskError('DESK_TIMEOUT', 'Desk 读取超过截止时间；不会重试或停止后台服务。')); return;
        }
        settled = true; clearTimeout(timer);
        resolve({ bytes: Buffer.concat(chunks, size), headers: response.headers, status });
      });
      response.once('aborted', () => fail(new PftDeskError('DESK_CONNECTION_FAILED', 'Desk 读取连接在响应完成前中断。')));
      response.once('error', () => fail(new PftDeskError('DESK_CONNECTION_FAILED', 'Desk 读取连接在响应完成前中断。')));
    });
    const fail = (error: PftDeskError): void => { if (settled) return; settled = true; clearTimeout(timer); reject(error); };
    const timer = setTimeout(() => {
      fail(new PftDeskError('DESK_TIMEOUT', 'Desk 读取超过截止时间；不会重试或停止后台服务。')); req.destroy();
    }, duration);
    req.once('error', () => fail(new PftDeskError('DESK_CONNECTION_FAILED', '无法连接已验证的 Desk 实例。')));
    req.end(options.body);
  });
}
