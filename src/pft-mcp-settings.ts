// SPDX-License-Identifier: Apache-2.0
/** Strict user-level bridge/discovery settings. Importing never reads a file. */
import { constants, closeSync, fstatSync, lstatSync, openSync, readSync, type BigIntStats } from 'node:fs';
import { homedir } from 'node:os';
import path from 'node:path';

export const PFT_MCP_SETTINGS_MAX_BYTES = 8 * 1024;
export const PFT_MCP_DEFAULT_PORT = 9876;
export type PftMcpSettings = { schema_version: 1; bridge: { preferred_port: number } }
  | { schema_version: 2; bridge: { preferred_port: number }; discovery: { enabled: boolean } };
export type PftMcpSettingsErrorCode = 'MCP_SETTINGS_INVALID' | 'MCP_SETTINGS_VERSION_UNSUPPORTED' | 'MCP_SETTINGS_PATH_INVALID'
  | 'MCP_SETTINGS_UNSAFE' | 'MCP_SETTINGS_UNREADABLE' | 'MCP_SETTINGS_CHANGED' | 'MCP_SETTINGS_UNSUPPORTED';
const messages: Record<PftMcpSettingsErrorCode, string> = {
  MCP_SETTINGS_INVALID: 'MCP 配置必须是至多8 KiB的有效UTF-8 v1/v2配置，仅允许桥接端口与自动发现开关。',
  MCP_SETTINGS_VERSION_UNSUPPORTED: 'MCP 配置版本不受支持；未读取为当前版本。',
  MCP_SETTINGS_PATH_INVALID: 'MCP 配置位置必须是当前用户配置根下规范的绝对路径。',
  MCP_SETTINGS_UNSAFE: 'MCP 配置必须位于真实私有目录中，文件须归当前用户所有且没有额外链接。',
  MCP_SETTINGS_UNREADABLE: '无法读取已选定的 MCP 配置；不会改用其他配置或默认端口。',
  MCP_SETTINGS_CHANGED: 'MCP 配置或目录在读取期间变化；未使用本次配置，也不会自动重试。',
  MCP_SETTINGS_UNSUPPORTED: '当前平台无法验证 MCP 配置的访问权限；未读取已有配置。',
};
export class PftMcpSettingsError extends Error {
  constructor(public readonly code: PftMcpSettingsErrorCode) {
    super(messages[code]); this.name = 'PftMcpSettingsError';
  }
}
const fail = (code: PftMcpSettingsErrorCode): never => { throw new PftMcpSettingsError(code); };

/** Parse exact v1/v2 grammars; duplicate decoded keys and coerced literals fail. */
export function pftParseMcpSettings(input: string | Uint8Array): PftMcpSettings {
  let text: string;
  try {
    if (typeof input === 'string') {
      if (input.length > PFT_MCP_SETTINGS_MAX_BYTES || Buffer.byteLength(input, 'utf8') > PFT_MCP_SETTINGS_MAX_BYTES
        || /\p{Cs}/u.test(input)) return fail('MCP_SETTINGS_INVALID');
      text = input;
    } else if (input instanceof Uint8Array && input.byteLength <= PFT_MCP_SETTINGS_MAX_BYTES) {
      // Keep a BOM visible to the grammar, rather than silently consuming it.
      text = new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(input);
    } else return fail('MCP_SETTINGS_INVALID');
  } catch { return fail('MCP_SETTINGS_INVALID'); }
  let index = 0;
  const space = () => { while (index < text.length && /[\x20\t\r\n]/u.test(text[index]!)) index++; };
  const punctuation = (expected: string) => {
    space(); if (text[index] !== expected) fail('MCP_SETTINGS_INVALID'); index++;
  };
  const key = (): string => {
    space(); const start = index;
    if (text[index++] !== '"') return fail('MCP_SETTINGS_INVALID');
    while (index < text.length) {
      const char = text[index++];
      if (char === '\\') { index++; continue; }
      if (char === '"') {
        try {
          const value: unknown = JSON.parse(text.slice(start, index));
          return typeof value === 'string' ? value : fail('MCP_SETTINGS_INVALID');
        } catch { return fail('MCP_SETTINGS_INVALID'); }
      }
    }
    return fail('MCP_SETTINGS_INVALID');
  };
  const integer = (): number => {
    space(); const match = /^(?:0|[1-9][0-9]*)/u.exec(text.slice(index));
    if (!match) return fail('MCP_SETTINGS_INVALID');
    index += match[0].length;
    const value = Number(match[0]);
    return Number.isSafeInteger(value) ? value : fail('MCP_SETTINGS_INVALID');
  };
  const boolean = (): boolean => {
    space();
    if (text.startsWith('true', index)) { index += 4; return true; }
    if (text.startsWith('false', index)) { index += 5; return false; }
    return fail('MCP_SETTINGS_INVALID');
  };
  const object = (allowed: readonly string[], readValue: (name: string) => void, required = allowed) => {
    punctuation('{'); space();
    const seen = new Set<string>();
    let closed = false;
    if (text[index] === '}') return fail('MCP_SETTINGS_INVALID');
    while (index < text.length) {
      const name = key();
      if (!allowed.includes(name) || seen.has(name)) return fail('MCP_SETTINGS_INVALID');
      seen.add(name); punctuation(':'); readValue(name); space();
      if (text[index] === '}') { index++; closed = true; break; }
      punctuation(',');
    }
    if (!closed || required.some(name => !seen.has(name))) fail('MCP_SETTINGS_INVALID');
    return seen;
  };
  let version: number | undefined, port: number | undefined, enabled: boolean | undefined;
  const fields = object(['schema_version', 'bridge', 'discovery'], name => {
    if (name === 'schema_version') version = integer();
    else if (name === 'bridge') object(['preferred_port'], () => { port = integer(); });
    else object(['enabled'], () => { enabled = boolean(); });
  }, ['schema_version', 'bridge']);
  space();
  if (index !== text.length || port === undefined || port < 1024 || port > 65535) {
    return fail('MCP_SETTINGS_INVALID');
  }
  if (version !== 1 && version !== 2) return fail('MCP_SETTINGS_VERSION_UNSUPPORTED');
  if (version === 1) {
    if (fields.has('discovery')) return fail('MCP_SETTINGS_INVALID');
    return { schema_version: 1, bridge: { preferred_port: port } };
  }
  if (!fields.has('discovery') || enabled === undefined) return fail('MCP_SETTINGS_INVALID');
  return { schema_version: 2, bridge: { preferred_port: port }, discovery: { enabled } };
}

export interface PftMcpSettingsLocationOptions {
  platform?: NodeJS.Platform;
  env?: Readonly<NodeJS.ProcessEnv>;
  home?: string;
}
function pathApi(platform: NodeJS.Platform): typeof path.posix {
  if (platform !== 'darwin' && platform !== 'linux' && platform !== 'win32') return fail('MCP_SETTINGS_UNSUPPORTED');
  return platform === 'win32' ? path.win32 : path.posix;
}
function cleanPath(value: unknown, api: typeof path.posix): string {
  if (typeof value !== 'string' || !value || value.length > 4096 || value !== value.trim()
    || /[\p{Cc}\p{Cf}\p{Cs}]/u.test(value) || !api.isAbsolute(value) || api.resolve(value) !== value) {
    return fail('MCP_SETTINGS_PATH_INVALID');
  }
  // This is a local user configuration, never a UNC share or device namespace.
  if (api === path.win32 && !/^[A-Za-z]:\\/u.test(value)) return fail('MCP_SETTINGS_PATH_INVALID');
  return value;
}
export function pftResolveMcpSettingsPath(options: PftMcpSettingsLocationOptions = {}): string {
  const platform = options.platform ?? process.platform, api = pathApi(platform);
  const env = options.env ?? process.env, home = cleanPath(options.home ?? homedir(), api);
  let base: string;
  if (platform === 'darwin') base = api.join(home, 'Library', 'Application Support');
  else if (platform === 'win32') base = cleanPath(env.LOCALAPPDATA, api);
  else {
    const xdg = env.XDG_CONFIG_HOME;
    if (xdg !== undefined && /[\p{Cc}\p{Cf}\p{Cs}]/u.test(xdg)) return fail('MCP_SETTINGS_PATH_INVALID');
    base = xdg && api.isAbsolute(xdg) ? cleanPath(xdg, api) : api.join(home, '.config');
  }
  return api.join(base, 'PhoenixTools', 'mcp', 'config.json');
}

/** Injectable filesystem operations are for deterministic tests, never user JSON. */
export interface PftMcpSettingsIO {
  lstat(filename: string): BigIntStats;
  open(filename: string, flags: number): number;
  fstat(descriptor: number): BigIntStats;
  read(descriptor: number, buffer: Buffer, offset: number, length: number, position: number): number;
  close(descriptor: number): void;
}
const nativeIO: PftMcpSettingsIO = {
  lstat: filename => lstatSync(filename, { bigint: true }),
  open: openSync,
  fstat: descriptor => fstatSync(descriptor, { bigint: true }),
  read: readSync,
  close: closeSync,
};
export interface PftMcpSettingsReadOptions extends PftMcpSettingsLocationOptions {
  uid?: number;
  paths?: { configPath: string };
  io?: PftMcpSettingsIO;
}
export type PftMcpSettingsReadResult = { source: 'default' | 'user_config'; preferredPort: number;
  configPath: string; settings: PftMcpSettings };
const missing = (error: unknown): boolean => error !== null && typeof error === 'object'
  && Reflect.get(error, 'code') === 'ENOENT';
function sameIdentity(first: BigIntStats, second: BigIntStats): boolean {
  return first.dev === second.dev && first.ino === second.ino && first.mode === second.mode
    && first.uid === second.uid && first.gid === second.gid;
}
function sameFile(first: BigIntStats, second: BigIntStats): boolean {
  return sameIdentity(first, second) && first.nlink === second.nlink && first.size === second.size
    && first.mtimeNs === second.mtimeNs && first.ctimeNs === second.ctimeNs;
}

/** Missing settings use the default; existing untrusted settings never fall back. */
export function pftReadMcpSettings(options: PftMcpSettingsReadOptions = {}): PftMcpSettingsReadResult {
  const platform = options.platform ?? process.platform, api = pathApi(platform), io = options.io ?? nativeIO;
  const configPath = options.paths === undefined ? pftResolveMcpSettingsPath(options) : cleanPath(options.paths.configPath, api);
  const directory = api.dirname(configPath), productDirectory = api.dirname(directory);
  if (api.basename(configPath) !== 'config.json' || api.basename(directory) !== 'mcp'
    || api.basename(productDirectory) !== 'PhoenixTools') return fail('MCP_SETTINGS_PATH_INVALID');
  const privateDirectories = new Set([productDirectory, directory]);
  const currentUid = options.uid ?? (typeof process.getuid === 'function' ? process.getuid() : undefined);
  if (platform !== 'win32' && (currentUid === undefined || !Number.isSafeInteger(currentUid) || currentUid < 0)) {
    return fail('MCP_SETTINGS_UNSUPPORTED');
  }
  const privateInfo = (info: BigIntStats, directory = false) => {
    const forbidden = directory ? 0o077n : 0o7177n;
    if (platform !== 'win32' && (info.uid !== BigInt(currentUid!) || (info.mode & forbidden) !== 0n)) fail('MCP_SETTINGS_UNSAFE');
  };
  const defaultResult = (): PftMcpSettingsReadResult => ({ source: 'default', configPath,
    preferredPort: PFT_MCP_DEFAULT_PORT, settings: { schema_version: 1, bridge: { preferred_port: PFT_MCP_DEFAULT_PORT } } });
  const ancestors: { filename: string; info: BigIntStats }[] = [];
  const root = api.parse(configPath).root;
  const components = configPath.slice(root.length).split(api.sep);
  let current = root, target: BigIntStats | undefined;
  try {
    for (let index = 0; index < components.length; index++) {
      current = api.join(current, components[index]!);
      let info: BigIntStats;
      try { info = io.lstat(current); }
      catch (error) { if (missing(error)) return defaultResult(); throw error; }
      if (info.isSymbolicLink()) return fail('MCP_SETTINGS_UNSAFE');
      if (index < components.length - 1) {
        if (!info.isDirectory()) return fail('MCP_SETTINGS_UNSAFE');
        ancestors.push({ filename: current, info });
      } else {
        if (!info.isFile() || info.nlink !== 1n) return fail('MCP_SETTINGS_UNSAFE');
        if (platform === 'win32') return fail('MCP_SETTINGS_UNSUPPORTED');
        for (const ancestor of ancestors) {
          if (privateDirectories.has(ancestor.filename)) privateInfo(ancestor.info, true);
        }
        privateInfo(info); target = info;
      }
    }
    if (target === undefined) return fail('MCP_SETTINGS_UNREADABLE');
    if (target.size < 0n || target.size > BigInt(PFT_MCP_SETTINGS_MAX_BYTES)) return fail('MCP_SETTINGS_INVALID');
    if (typeof constants.O_NOFOLLOW !== 'number') return fail('MCP_SETTINGS_UNSUPPORTED');
    let descriptor: number | undefined;
    let succeeded = false;
    try {
      descriptor = io.open(configPath, constants.O_RDONLY | constants.O_NOFOLLOW | (constants.O_NONBLOCK ?? 0));
      const before = io.fstat(descriptor);
      if (!before.isFile() || before.nlink !== 1n) return fail('MCP_SETTINGS_UNSAFE');
      privateInfo(before);
      if (!sameFile(target, before)) return fail('MCP_SETTINGS_CHANGED');
      const buffer = Buffer.alloc(PFT_MCP_SETTINGS_MAX_BYTES + 1);
      let size = 0;
      while (size < buffer.length) {
        const count = io.read(descriptor, buffer, size, buffer.length - size, size);
        if (!Number.isInteger(count) || count < 0 || count > buffer.length - size) return fail('MCP_SETTINGS_UNREADABLE');
        if (count === 0) break;
        size += count;
      }
      const after = io.fstat(descriptor);
      if (size > PFT_MCP_SETTINGS_MAX_BYTES || BigInt(size) !== before.size || !sameFile(before, after)) {
        return fail('MCP_SETTINGS_CHANGED');
      }
      for (const ancestor of ancestors) {
        const latest = io.lstat(ancestor.filename);
        if (!latest.isDirectory() || latest.isSymbolicLink() || !sameIdentity(ancestor.info, latest)) return fail('MCP_SETTINGS_CHANGED');
        if (privateDirectories.has(ancestor.filename)) privateInfo(latest, true);
      }
      const latest = io.lstat(configPath);
      if (latest.isSymbolicLink() || !latest.isFile() || !sameFile(after, latest)) return fail('MCP_SETTINGS_CHANGED');
      const settings = pftParseMcpSettings(buffer.subarray(0, size));
      succeeded = true;
      return { source: 'user_config', configPath, preferredPort: settings.bridge.preferred_port, settings };
    } finally {
      if (descriptor !== undefined) {
        try { io.close(descriptor); }
        catch { if (succeeded) fail('MCP_SETTINGS_UNREADABLE'); }
      }
    }
  } catch (error) {
    if (error instanceof PftMcpSettingsError) throw error;
    return fail('MCP_SETTINGS_UNREADABLE');
  }
}
