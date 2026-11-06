// SPDX-License-Identifier: Apache-2.0
/** Explicit, bounded, read-only discovery. Registry data never authorizes a PID. */
import { type Stats, constants, closeSync, fstatSync, lstatSync, openSync, opendirSync, readSync } from 'node:fs';
import { homedir } from 'node:os';
import { isAbsolute, join, parse, sep } from 'node:path';
import { createHash } from 'node:crypto';

export class PftInstanceError extends Error {
  constructor(public readonly code: string, message: string) { super(message); this.name = 'PftInstanceError'; }
}
function fail(code = 'INSTANCE_REGISTRY_INVALID'): never { throw new PftInstanceError(code, 'FreeCAD instance registry is unavailable, unsafe or invalid; no target was selected.'); }
export type PftInstanceRecord = Readonly<{ schema_version: 1; owner: 'phoenix-freecad-tools'; instance_id: string;
  service_id: string; pid: number; url: string; protocol_version: 1; addon_version: '0.3.2' }>;
export type PftRegisteredInstance = Readonly<PftInstanceRecord & { fingerprint: string }>;
export type PftInstanceDirectoryOptions = { home?: string; platform?: NodeJS.Platform; env?: NodeJS.ProcessEnv };
const idPattern = /^[0-9a-f]{32}(?![\s\S])/u;
export const pftInstanceIdValid = (value: unknown): value is string => typeof value === 'string' && idPattern.test(value);
function validPath(value: unknown): asserts value is string {
  if (typeof value !== 'string' || !value || Buffer.byteLength(value,'utf8') > 4096 || value !== value.trim() || value.startsWith('//') || !isAbsolute(value)
      || /[\p{Cc}\p{Cf}\p{Cs}]/u.test(value) || value.split(sep).some(p => p === '.' || p === '..')) fail();
}
function entries(directory: string): string[] {
  const stream = opendirSync(directory), names: string[] = [];
  try { for (;;) { const entry = stream.readSync(); if (entry === null) return names; names.push(entry.name); if (names.length > 200) fail('INSTANCE_REGISTRY_LIMIT'); } }
  finally { stream.closeSync(); }
}
function missing(error: unknown): boolean { return (error as NodeJS.ErrnoException)?.code === 'ENOENT'; }
function uid(): number { const get = process.getuid; if (!get) fail('INSTANCE_PLATFORM_UNSUPPORTED'); return get(); }
function privateDirectory(path: string): void {
  if (checkChain(path) === null) { const cause = new Error('missing runtime root') as NodeJS.ErrnoException; cause.code = 'ENOENT'; throw cause; }
  const s = lstatSync(path);
  if (!s.isDirectory() || s.isSymbolicLink() || s.uid !== uid() || (s.mode & 0o7777) !== 0o700) fail();
}
export function pftResolveInstanceDirectory(options: PftInstanceDirectoryOptions = {}): string {
  const platform = options.platform ?? process.platform, env = options.env ?? process.env, home = options.home ?? homedir();
  validPath(home);
  if (platform === 'win32') fail('INSTANCE_PLATFORM_UNSUPPORTED');
  if (platform === 'darwin') return join(home, 'Library', 'Application Support', 'PhoenixTools', 'mcp', 'runtime', 'instances');
  if (platform !== 'linux') fail('INSTANCE_PLATFORM_UNSUPPORTED');
  if (env.XDG_RUNTIME_DIR) {
    validPath(env.XDG_RUNTIME_DIR);
    try { privateDirectory(env.XDG_RUNTIME_DIR); return join(env.XDG_RUNTIME_DIR, 'phoenix-tools', 'mcp', 'instances'); }
    catch (error) { if (!missing(error)) { if (error instanceof PftInstanceError) throw error; fail(); } }
  }
  const state = env.XDG_STATE_HOME || join(home, '.local', 'state'); validPath(state);
  return join(state, 'PhoenixTools', 'mcp', 'runtime', 'instances');
}
function same(a: Stats, b: Stats): boolean {
  return a.dev === b.dev && a.ino === b.ino && a.uid === b.uid && a.mode === b.mode && a.nlink === b.nlink;
}
type Chain = { directory: Stats; stamps: readonly (readonly [string, Stats])[] };
function sameDirectory(a: Stats,b: Stats): boolean { return a.dev===b.dev && a.ino===b.ino && a.uid===b.uid && a.mode===b.mode; }
function sameAncestor(a: Stats,b: Stats): boolean {
  // Shared sticky roots legitimately change when other processes create temp
  // directories. Any renamed private descendant still changes its own ctime.
  const shared = a.uid===0 && (a.mode & 0o1000)!==0;
  return sameDirectory(a,b) && (shared || a.ctimeMs===b.ctimeMs && a.mtimeMs===b.mtimeMs);
}
function checkChain(directory: string): Chain | null {
  validPath(directory); let current = parse(directory).root;
  const stamps: [string,Stats][] = [[current,lstatSync(current)]];
  const paths = directory.slice(current.length).split(sep).filter(Boolean);
  const suffix = paths.slice(-4).join('/');
  const privateFrom = suffix === 'PhoenixTools/mcp/runtime/instances' || paths.slice(-3).join('/') === 'phoenix-tools/mcp/instances' ? paths.length - 4 : paths.length - 1;
  for (const [index,part] of paths.entries()) {
    current = join(current, part);
    let s; try { s = lstatSync(current); } catch (error) { if (missing(error)) return null; fail(); }
    if (!s.isDirectory() || s.isSymbolicLink() || ![0, uid()].includes(s.uid)) fail();
    const stickyRoot = s.uid === 0 && (s.mode & 0o1000) !== 0;
    if ((s.mode & 0o022) !== 0 && !stickyRoot) fail();
    if ((index >= privateFrom) && (s.uid !== uid() || (s.mode & 0o7777) !== 0o700)) fail();
    stamps.push([current,s]);
    let fd: number | undefined;
    try { fd = openSync(current, constants.O_RDONLY | constants.O_DIRECTORY | constants.O_NOFOLLOW); if (!sameDirectory(s, fstatSync(fd))) fail(); }
    finally { if (fd !== undefined) closeSync(fd); }
  }
  return { directory: lstatSync(directory), stamps };
}
function scalarRecord(text: string): unknown {
  // A finite flat scalar object: decoded duplicate keys, floats/exponents and
  // escaped aliases are checked before JSON object construction can lose them.
  let position = 0;
  const skip = () => { while (/[ \t\r\n]/u.test(text[position] ?? '') && position < text.length) position++; };
  const take = (char: string) => { skip(); if (text[position++] !== char) fail(); };
  const string = (): string => {
    skip(); const start = position; if (text[position++] !== '"') fail();
    while (position < text.length) {
      const char = text[position++];
      if (char === '\\') { position++; continue; }
      if (char === '"') { const value: unknown = JSON.parse(text.slice(start,position)); if (typeof value !== 'string') fail(); return value; }
    }
    return fail();
  };
  take('{'); const output: Record<string,unknown> = Object.create(null); let count = 0;
  skip(); if (text[position] === '}') { position++; skip(); if (position !== text.length) fail(); return output; }
  for (;;) {
    const key = string(); if (Object.hasOwn(output,key) || ++count > 8) fail(); take(':'); skip();
    if (text[position] === '"') output[key] = string();
    else {
      const raw = /^-?(?:0|[1-9][0-9]*)/u.exec(text.slice(position))?.[0];
      if (raw === undefined) fail(); position += raw.length; output[key] = Number(raw);
    }
    skip(); const separator = text[position++]; if (separator === '}') break; if (separator !== ',') fail();
  }
  skip(); if (position !== text.length) fail(); return output;
}
function record(value: unknown, filename: string): PftInstanceRecord {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) fail();
  const v = value as Record<string, unknown>;
  if (Object.keys(v).sort().join(',') !== 'addon_version,instance_id,owner,pid,protocol_version,schema_version,service_id,url'
      || v.schema_version !== 1 || v.owner !== 'phoenix-freecad-tools' || !pftInstanceIdValid(v.instance_id)
      || !pftInstanceIdValid(v.service_id) || filename !== v.instance_id + '.json' || v.protocol_version !== 1
      || v.addon_version !== '0.3.2' || !Number.isSafeInteger(v.pid) || (v.pid as number) <= 0
      || typeof v.url !== 'string' || !/^http:\/\/127\.0\.0\.1:[1-9][0-9]{3,4}\/$/u.test(v.url)) fail();
  const url = new URL(v.url as string); if (url.href !== v.url || Number(url.port) < 1024 || Number(url.port) > 65535) fail();
  return Object.freeze({ ...v }) as PftInstanceRecord;
}
export function pftReadInstanceRegistry(options: PftInstanceDirectoryOptions & { directory?: string } = {}): readonly PftRegisteredInstance[] {
  if ((options.platform ?? process.platform) === 'win32') fail('INSTANCE_PLATFORM_UNSUPPORTED');
  const directory = options.directory ?? pftResolveInstanceDirectory(options);
  try {
    const before = checkChain(directory); if (before === null) return Object.freeze([]);
    const initialEntries = entries(directory);
    for (const name of initialEntries.filter(n => /^\.pft-instance-[0-9a-f]{32}\.tmp(?![\s\S])/u.test(n))) {
      const staging = lstatSync(join(directory,name));
      if (!staging.isFile() || staging.isSymbolicLink() || staging.uid !== uid() || (staging.mode & 0o7777) !== 0o600 || staging.nlink !== 1) fail();
    }
    const names = initialEntries.filter(name => !/^\.pft-instance-[0-9a-f]{32}\.tmp(?![\s\S])/u.test(name));
    if (names.length > 100 || names.some(name => !/^[0-9a-f]{32}\.json(?![\s\S])/u.test(name))) fail();
    const records = names.sort().map(name => {
      const path = join(directory, name), s = lstatSync(path);
      if (!s.isFile() || s.isSymbolicLink() || s.uid !== uid() || (s.mode & 0o7777) !== 0o600 || s.nlink !== 1 || s.size > 8192 || s.size === 0) fail();
      let fd: number | undefined;
      try {
        fd = openSync(path, constants.O_RDONLY | constants.O_NOFOLLOW);
        if (!same(s, fstatSync(fd))) fail();
        const buffer = Buffer.alloc(8193); const length = readSync(fd, buffer, 0, 8193, 0); const bytes = buffer.subarray(0,length); if (bytes.length !== s.size || bytes.length > 8192) fail();
        const after = fstatSync(fd); if (!same(s, after) || s.mtimeMs !== after.mtimeMs || s.ctimeMs !== after.ctimeMs) fail();
        const text = new TextDecoder('utf-8', { fatal: true }).decode(bytes);
        const value = record(scalarRecord(text), name);
        const fingerprint = createHash('sha256').update(bytes).update(JSON.stringify([before.stamps.map(([path,st]) => [path,st.dev,st.ino,st.uid,st.mode]),s.dev,s.ino,s.uid,s.mode,s.nlink,s.size,s.mtimeMs,s.ctimeMs])).digest('hex');
        if (!same(s, lstatSync(path))) fail();
        return Object.freeze({ ...value, fingerprint });
      } finally { if (fd !== undefined) closeSync(fd); }
    });
    const after = checkChain(directory); if (!after || before.stamps.length !== after.stamps.length || before.stamps.some(([path,st],index) => path !== after.stamps[index]?.[0] || !sameAncestor(st,after.stamps[index]![1]))
      || before.directory.mtimeMs !== after.directory.mtimeMs || before.directory.ctimeMs !== after.directory.ctimeMs
      || entries(directory).sort().join('\0') !== [...initialEntries].sort().join('\0')) fail();
    return Object.freeze(records);
  } catch (error) { if (error instanceof PftInstanceError) throw error; fail(); }
}
