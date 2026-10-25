// SPDX-License-Identifier: Apache-2.0
export interface Config {
  freecadUrl: string;
  timeoutMs: number;
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

export function readConfig(env: NodeJS.ProcessEnv = process.env): Config {
  const timeoutMs = Number(env.PHOENIX_TIMEOUT_MS ?? 15000);
  if (!Number.isInteger(timeoutMs) || timeoutMs < 100 || timeoutMs > 120000) {
    throw new Error('PHOENIX_TIMEOUT_MS 必须是 100–120000 的整数');
  }
  return {
    freecadUrl: localUrl(env.PHOENIX_FREECAD_URL ?? 'http://127.0.0.1:9876/', 'PHOENIX_FREECAD_URL'),
    timeoutMs,
  };
}
