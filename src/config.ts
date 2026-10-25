// SPDX-License-Identifier: Apache-2.0
import { isAbsolute, resolve } from 'node:path';
import type { PftDeskConfig } from './adapters/pft-desk-client.js';

export interface Config {
  freecadUrl: string;
  timeoutMs: number;
  toolScope?: 'full' | 'chat';
  desk?: PftDeskConfig;
  deskChatReads?: boolean;
}

function localUrl(value: string, name: string): string {
  let url: URL;
  try { url = new URL(value); } catch { throw new Error(`${name} 必须为有效的本机 HTTP 地址`); }
  if (url.protocol !== 'http:' || !['localhost', '127.0.0.1'].includes(url.hostname)
    || url.username || url.password || url.search || url.hash || url.pathname !== '/') {
    throw new Error(`${name} 仅支持 IPv4 本机 HTTP 根地址，例如 http://127.0.0.1:9876/`);
  }
  return url.toString();
}

/** Optional trusted executable configuration; parsing never probes or starts Desk. */
function deskConfig(env: NodeJS.ProcessEnv): PftDeskConfig | undefined {
  const keys = ['PHOENIX_DESK_NODE', 'PHOENIX_DESK_CLI', 'PHOENIX_DESK_STATE_DIR'] as const;
  if (keys.every(key => env[key] === undefined)) return undefined;
  const values = keys.map(key => env[key]);
  if (values.some(value => typeof value !== 'string' || !value || value !== value.trim()
    || value.length > 4096 || /[\p{Cc}\p{Cf}\p{Cs}]/u.test(value) || !isAbsolute(value) || resolve(value) !== value)) {
    throw new Error('Desk 可选连接须同时提供三个规范绝对路径：PHOENIX_DESK_NODE、PHOENIX_DESK_CLI、PHOENIX_DESK_STATE_DIR');
  }
  return { nodeExecutable: values[0]!, cliEntry: values[1]!, stateDir: values[2]! };
}

export function readConfig(env: NodeJS.ProcessEnv = process.env): Config {
  const timeoutMs = Number(env.PHOENIX_TIMEOUT_MS ?? 15000);
  if (!Number.isInteger(timeoutMs) || timeoutMs < 100 || timeoutMs > 120000) {
    throw new Error('PHOENIX_TIMEOUT_MS 必须是 100–120000 的整数');
  }
  const toolScope = env.PHOENIX_FREECAD_TOOL_SCOPE ?? 'full';
  if (toolScope !== 'full' && toolScope !== 'chat') {
    throw new Error('PHOENIX_FREECAD_TOOL_SCOPE 仅允许 full 或 chat');
  }
  const desk = deskConfig(env);
  const deskChatFlag = env.PHOENIX_DESK_CHAT_READS;
  if (deskChatFlag !== undefined && deskChatFlag !== '0' && deskChatFlag !== '1') {
    throw new Error('PHOENIX_DESK_CHAT_READS 仅允许 0 或 1，聊天共享须明确选择');
  }
  if (deskChatFlag === '1' && desk === undefined) {
    throw new Error('启用聊天 Desk 数据工具前必须完整提供三个 Desk 路径');
  }
  return {
    ...(desk === undefined ? {} : { desk }),
    ...(deskChatFlag === undefined ? {} : { deskChatReads: deskChatFlag === '1' }),
    freecadUrl: localUrl(env.PHOENIX_FREECAD_URL ?? 'http://127.0.0.1:9876/', 'PHOENIX_FREECAD_URL'),
    timeoutMs,
    toolScope,
  };
}
