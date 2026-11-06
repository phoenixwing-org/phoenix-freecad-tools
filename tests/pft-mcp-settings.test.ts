// SPDX-License-Identifier: Apache-2.0
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { chmodSync, closeSync, existsSync, fstatSync, linkSync, lstatSync, mkdirSync, mkdtempSync,
  openSync, readSync, realpathSync, renameSync, rmSync, symlinkSync, writeFileSync, type BigIntStats } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { PftMcpSettingsError, pftParseMcpSettings, pftReadMcpSettings, pftResolveMcpSettingsPath,
  PFT_MCP_SETTINGS_MAX_BYTES, type PftMcpSettingsErrorCode, type PftMcpSettingsIO } from '../src/pft-mcp-settings.js';

const document = (port = '9876') => `{"schema_version":1,"bridge":{"preferred_port":${port}}}`;
function errorCode(action: () => unknown, code: PftMcpSettingsErrorCode) {
  assert.throws(action, (error: unknown) => error instanceof PftMcpSettingsError && error.code === code);
}
function fixture() {
  const directory = realpathSync(mkdtempSync(path.join(tmpdir(), 'pft-mcp-settings-')));
  chmodSync(directory, 0o700);
  const options = { platform: 'linux' as const, env: {}, home: directory };
  const configPath = pftResolveMcpSettingsPath(options);
  const mcp = path.dirname(configPath), product = path.dirname(mcp);
  const create = (text: string | Buffer = document()) => {
    mkdirSync(mcp, { recursive: true, mode: 0o700 });
    chmodSync(product, 0o700); chmodSync(mcp, 0o700);
    writeFileSync(configPath, text, { mode: 0o600 }); chmodSync(configPath, 0o600);
  };
  return { directory, options, configPath, mcp, product, create, cleanup: () => rmSync(directory, { recursive: true, force: true }) };
}
const realIO: PftMcpSettingsIO = { lstat: filename => lstatSync(filename, { bigint: true }), open: openSync,
  fstat: descriptor => fstatSync(descriptor, { bigint: true }), read: readSync, close: closeSync };

test('v1 parser permits exact field order variants and integer port boundaries', () => {
  for (const port of [1024, 9876, 65535]) {
    const expected = { schema_version: 1, bridge: { preferred_port: port } };
    assert.deepEqual(pftParseMcpSettings(document(String(port))), expected);
    assert.deepEqual(pftParseMcpSettings(Buffer.from(` \n{"bridge":{"preferred_port":${port}},"schema_version":1}\t`)), expected);
  }
  assert.deepEqual(pftParseMcpSettings('{"schema_\\u0076ersion":1,"bridge":{"preferred_port":9876}}'),
    { schema_version: 1, bridge: { preferred_port: 9876 } });
});

test('parser rejects duplicate decoded keys, unknown fields and unknown schema versions', () => {
  for (const text of [
    '{"schema_version":1,"schema_version":1,"bridge":{"preferred_port":9876}}',
    '{"schema_version":1,"schema_\\u0076ersion":1,"bridge":{"preferred_port":9876}}',
    '{"schema_version":1,"bridge":{"preferred_port":9876,"preferred_\\u0070ort":9877}}',
    '{"schema_version":1,"bridge":{"preferred_port":9876},"http":{}}',
    '{"schema_version":1,"bridge":{"preferred_port":9876,"discovery":true}}',
    '{}', '[]', 'null',
    '{"schema_version":1}', '{"bridge":{"preferred_port":9876}}',
  ]) errorCode(() => pftParseMcpSettings(text), 'MCP_SETTINGS_INVALID');
  errorCode(() => pftParseMcpSettings('{"schema_version":3,"bridge":{"preferred_port":9876}}'), 'MCP_SETTINGS_VERSION_UNSUPPORTED');
});

test('v2 permits only an explicit boolean discovery flag while v1 remains unchanged', () => {
  for (const enabled of [true, false]) {
    const expected = { schema_version: 2, bridge: { preferred_port: 12340 }, discovery: { enabled } };
    assert.deepEqual(pftParseMcpSettings(JSON.stringify(expected)), expected);
    assert.deepEqual(pftParseMcpSettings(`{"discovery":{"enabled":${enabled}},"bridge":{"preferred_port":12340},"schema_version":2}`), expected);
  }
  assert.deepEqual(pftParseMcpSettings(document()), { schema_version: 1, bridge: { preferred_port: 9876 } });
  for (const enabled of ['0', '1', '"true"', '"false"', 'null', '[]', '{}', 'TRUE', 'falsee', 'true false']) {
    errorCode(() => pftParseMcpSettings(`{"schema_version":2,"bridge":{"preferred_port":9876},"discovery":{"enabled":${enabled}}}`), 'MCP_SETTINGS_INVALID');
  }
  for (const text of [
    '{"schema_version":2,"bridge":{"preferred_port":9876}}',
    '{"schema_version":1,"bridge":{"preferred_port":9876},"discovery":{"enabled":false}}',
    '{"schema_version":2,"bridge":{"preferred_port":9876},"discovery":{}}',
    '{"schema_version":2,"bridge":{"preferred_port":9876},"discovery":{"enabled":true,"directory":"/neutral/unsupported"}}',
    '{"schema_version":2,"bridge":{"preferred_port":9876},"discovery":{"enabled":true,"enabled":false}}',
    '{"schema_version":2,"bridge":{"preferred_port":9876},"discovery":{"enabled":true},"discovery":{"enabled":false}}',
    '{"schema_version":2,"bridge":{"preferred_port":9876},"discovery":{"enabled":true},"extra":false}',
  ]) errorCode(() => pftParseMcpSettings(text), 'MCP_SETTINGS_INVALID');
});

test('parser rejects float/exponent/bool/string/NaN port literals and malformed trailing JSON', () => {
  for (const port of ['9876.0', '9.876e3', '1e4', 'true', 'false', 'null', '"9876"', 'NaN', 'Infinity', '-1', '1023', '65536',
    '09876', '+9876', '9007199254740993', '[]', '{}']) errorCode(() => pftParseMcpSettings(document(port)), 'MCP_SETTINGS_INVALID');
  for (const text of [document() + '{}', document() + ' trailing', document().slice(0, -1), document().slice(0, -1) + ',',
    '{"schema_version":1,"bridge":{"preferred_port":9876,}}', '{"schema_version":1,"bridge":{}}',
    '{"schema_version":1.0,"bridge":{"preferred_port":9876}}', '\uFEFF' + document(),
    document().replace('"bridge"', '"bad\nkey"'), document() + '\u0000']) errorCode(() => pftParseMcpSettings(text), 'MCP_SETTINGS_INVALID');
});

test('parser validates actual UTF8 bytes and the complete 8 KiB budget', () => {
  assert.deepEqual(pftParseMcpSettings(document() + ' '.repeat(PFT_MCP_SETTINGS_MAX_BYTES - Buffer.byteLength(document()))),
    { schema_version: 1, bridge: { preferred_port: 9876 } });
  errorCode(() => pftParseMcpSettings(document() + ' '.repeat(PFT_MCP_SETTINGS_MAX_BYTES)), 'MCP_SETTINGS_INVALID');
  errorCode(() => pftParseMcpSettings(Buffer.concat([Buffer.from(document()), Buffer.from([0xc3, 0x28])])), 'MCP_SETTINGS_INVALID');
  errorCode(() => pftParseMcpSettings(Buffer.from([0xef, 0xbb, 0xbf, ...Buffer.from(document())])), 'MCP_SETTINGS_INVALID');
  errorCode(() => pftParseMcpSettings('\ud800' + document()), 'MCP_SETTINGS_INVALID');
});

test('settings path resolution is platform-specific and never creates paths', () => {
  assert.equal(pftResolveMcpSettingsPath({ platform: 'darwin', home: '/home/Example', env: {} }),
    '/home/Example/Library/Application Support/PhoenixTools/mcp/config.json');
  assert.equal(pftResolveMcpSettingsPath({ platform: 'win32', home: 'C:\\Users\\Example', env: { LOCALAPPDATA: 'C:\\Users\\Example\\AppData\\Local' } }),
    'C:\\Users\\Example\\AppData\\Local\\PhoenixTools\\mcp\\config.json');
  assert.equal(pftResolveMcpSettingsPath({ platform: 'linux', home: '/home/Example', env: { XDG_CONFIG_HOME: '/var/example-config' } }),
    '/var/example-config/PhoenixTools/mcp/config.json');
  for (const XDG_CONFIG_HOME of ['', 'relative/config']) {
    assert.equal(pftResolveMcpSettingsPath({ platform: 'linux', home: '/home/Example', env: { XDG_CONFIG_HOME } }),
      '/home/Example/.config/PhoenixTools/mcp/config.json');
  }
  for (const home of ['relative', '/home/Example/../Other', '/home/Example/', '/home/Exam\u200bple', '/home/Example\n']) {
    errorCode(() => pftResolveMcpSettingsPath({ platform: 'linux', home, env: {} }), 'MCP_SETTINGS_PATH_INVALID');
  }
  errorCode(() => pftResolveMcpSettingsPath({ platform: 'linux', home: '/home/Example', env: { XDG_CONFIG_HOME: '/var/config/../other' } }), 'MCP_SETTINGS_PATH_INVALID');
  errorCode(() => pftResolveMcpSettingsPath({ platform: 'linux', home: '/home/Example', env: { XDG_CONFIG_HOME: 'bad\nrelative' } }), 'MCP_SETTINGS_PATH_INVALID');
  errorCode(() => pftResolveMcpSettingsPath({ platform: 'win32', home: 'C:\\Users\\Example', env: { LOCALAPPDATA: 'relative' } }), 'MCP_SETTINGS_PATH_INVALID');
  errorCode(() => pftResolveMcpSettingsPath({ platform: 'win32', home: 'C:\\Users\\Example', env: { LOCALAPPDATA: '\\\\server\\share' } }), 'MCP_SETTINGS_PATH_INVALID');
});

test('missing config reports default without creating a directory or changing permissions', () => {
  const f = fixture();
  try {
    const before = lstatSync(f.directory).mode;
    assert.deepEqual(pftReadMcpSettings(f.options), { source: 'default', configPath: f.configPath, preferredPort: 9876,
      settings: { schema_version: 1, bridge: { preferred_port: 9876 } } });
    assert.equal(existsSync(f.product), false);
    assert.equal(lstatSync(f.directory).mode, before);
  } finally { f.cleanup(); }
});

test('private existing config is read exactly and relative XDG deterministically uses the fallback', () => {
  const f = fixture();
  try {
    f.create(document('12000'));
    const before = lstatSync(f.configPath, { bigint: true });
    const result = pftReadMcpSettings({ ...f.options, env: { XDG_CONFIG_HOME: 'relative' } });
    assert.equal(result.source, 'user_config'); assert.equal(result.preferredPort, 12000);
    assert.equal(result.configPath, f.configPath);
    const after = lstatSync(f.configPath, { bigint: true });
    assert.equal(after.mode, before.mode); assert.equal(after.ino, before.ino);
    assert.equal(after.mtimeNs, before.mtimeNs); assert.equal(after.size, before.size);
    assert.equal(pftReadMcpSettings({ ...f.options, paths: { configPath: f.configPath } }).preferredPort, 12000);
    errorCode(() => pftReadMcpSettings({ ...f.options, paths: { configPath: path.join(f.directory, 'config.json') } }), 'MCP_SETTINGS_PATH_INVALID');
  } finally { f.cleanup(); }
});

test('v2 private read preserves exact bytes identity and explicit disabled state', () => {
  const f = fixture();
  try {
    for (const enabled of [true, false]) {
      const selected = { schema_version: 2, bridge: { preferred_port: 12341 }, discovery: { enabled } };
      f.create(JSON.stringify(selected));
      const before = lstatSync(f.configPath, { bigint: true });
      const result = pftReadMcpSettings(f.options);
      assert.deepEqual(result.settings, selected);
      assert.equal(result.preferredPort, 12341);
      const after = lstatSync(f.configPath, { bigint: true });
      assert.equal(after.ino, before.ino); assert.equal(after.mode, before.mode);
      assert.equal(after.mtimeNs, before.mtimeNs); assert.equal(after.size, before.size);
    }
  } finally { f.cleanup(); }
});

test('invalid existing bytes, versions and oversized files cannot fall back to defaults', () => {
  const f = fixture();
  try {
    for (const data of [document('9876.0'),
      Buffer.from([0xc3, 0x28]), 'x'.repeat(PFT_MCP_SETTINGS_MAX_BYTES + 1), '']) {
      f.create(data); errorCode(() => pftReadMcpSettings(f.options), 'MCP_SETTINGS_INVALID');
    }
    f.create('{"schema_version":99,"bridge":{"preferred_port":9876}}');
    errorCode(() => pftReadMcpSettings(f.options), 'MCP_SETTINGS_VERSION_UNSUPPORTED');
  } finally { f.cleanup(); }
});

test('missing config does not disable an existing nonprivate product directory', () => {
  const f = fixture();
  try {
    mkdirSync(f.mcp, { recursive: true, mode: 0o755 });
    chmodSync(f.product, 0o755); chmodSync(f.mcp, 0o755);
    assert.equal(pftReadMcpSettings(f.options).source, 'default');
    assert.equal(lstatSync(f.product).mode & 0o777, 0o755);
    assert.equal(lstatSync(f.mcp).mode & 0o777, 0o755);
    assert.equal(existsSync(f.configPath), false);
    writeFileSync(f.configPath, document(), { mode: 0o600 });
    errorCode(() => pftReadMcpSettings(f.options), 'MCP_SETTINGS_UNSAFE');
  } finally { f.cleanup(); }
});

test('product directory, mcp directory and config must remain owner-private', () => {
  const f = fixture();
  try {
    f.create();
    for (const filename of [f.product, f.mcp, f.configPath]) {
      chmodSync(filename, filename === f.configPath ? 0o640 : 0o750);
      errorCode(() => pftReadMcpSettings(f.options), 'MCP_SETTINGS_UNSAFE');
      chmodSync(filename, filename === f.configPath ? 0o600 : 0o700);
    }
    for (const mode of [0o700, 0o1600]) {
      chmodSync(f.configPath, mode);
      errorCode(() => pftReadMcpSettings(f.options), 'MCP_SETTINGS_UNSAFE');
    }
    chmodSync(f.configPath, 0o600);
    // macOS may strip setuid/setgid from chmod on regular files. Inject their
    // observed stat bits so the rejection remains deterministic on those hosts.
    for (const bits of [0o2000n, 0o4000n]) {
      const io: PftMcpSettingsIO = { ...realIO, lstat(filename) {
        const info = realIO.lstat(filename);
        return filename === f.configPath ? Object.assign(Object.create(Object.getPrototypeOf(info)), info,
          { mode: info.mode | bits }) as BigIntStats : info;
      } };
      errorCode(() => pftReadMcpSettings({ ...f.options, io }), 'MCP_SETTINGS_UNSAFE');
    }
    errorCode(() => pftReadMcpSettings({ ...f.options, uid: process.getuid!() + 1 }), 'MCP_SETTINGS_UNSAFE');
  } finally { f.cleanup(); }
});

test('symlink ancestors, symlink files, directories and hard-linked files are refused', () => {
  const f = fixture();
  try {
    f.create();
    linkSync(f.configPath, path.join(f.directory, 'second-link'));
    errorCode(() => pftReadMcpSettings(f.options), 'MCP_SETTINGS_UNSAFE');
    rmSync(path.join(f.directory, 'second-link'));
    const target = path.join(f.directory, 'original-config'); renameSync(f.configPath, target);
    symlinkSync(target, f.configPath);
    errorCode(() => pftReadMcpSettings(f.options), 'MCP_SETTINGS_UNSAFE');
    rmSync(f.configPath); mkdirSync(f.configPath);
    errorCode(() => pftReadMcpSettings(f.options), 'MCP_SETTINGS_UNSAFE');
    rmSync(f.configPath, { recursive: true }); renameSync(target, f.configPath);
    const realProduct = path.join(f.directory, 'real-product'); renameSync(f.product, realProduct);
    symlinkSync(realProduct, f.product);
    errorCode(() => pftReadMcpSettings(f.options), 'MCP_SETTINGS_UNSAFE');
  } finally { f.cleanup(); }
});

test('unreadable selected config produces a redacted fixed error and no fallback', () => {
  const f = fixture();
  try {
    f.create();
    const io = { ...realIO, lstat(filename: string) {
      if (filename === f.configPath) throw Object.assign(new Error('sensitive payload path'), { code: 'EACCES' });
      return realIO.lstat(filename);
    } };
    assert.throws(() => pftReadMcpSettings({ ...f.options, io }), (error: unknown) => {
      assert.ok(error instanceof PftMcpSettingsError); assert.equal(error.code, 'MCP_SETTINGS_UNREADABLE');
      assert.equal(error.message.includes('sensitive'), false); assert.equal(error.message.includes(f.directory), false); return true;
    });
  } finally { f.cleanup(); }
});

test('file replacement between inspection and open is rejected and descriptor is closed', () => {
  const f = fixture();
  try {
    f.create(); let closed = 0;
    const io = { ...realIO, open(filename: string, flags: number) {
      const replacement = path.join(f.mcp, 'replacement'); writeFileSync(replacement, document('12000'), { mode: 0o600 });
      renameSync(replacement, filename); return realIO.open(filename, flags);
    }, close(descriptor: number) { closed++; realIO.close(descriptor); } };
    errorCode(() => pftReadMcpSettings({ ...f.options, io }), 'MCP_SETTINGS_CHANGED');
    assert.equal(closed, 1);
  } finally { f.cleanup(); }
});

test('in-place changes and late symlink replacement are rejected after bounded reads', () => {
  const f = fixture();
  try {
    for (const change of ['content', 'symlink']) {
      f.create(); let changed = false, closed = 0;
      const io = { ...realIO, read(descriptor: number, buffer: Buffer, offset: number, length: number, position: number) {
        const size = realIO.read(descriptor, buffer, offset, length, position);
        if (!changed) {
          changed = true;
          if (change === 'content') writeFileSync(f.configPath, document('12000'));
          else { renameSync(f.configPath, path.join(f.directory, 'moved')); symlinkSync(path.join(f.directory, 'moved'), f.configPath); }
        }
        return size;
      }, close(descriptor: number) { closed++; realIO.close(descriptor); } };
      errorCode(() => pftReadMcpSettings({ ...f.options, io }), 'MCP_SETTINGS_CHANGED');
      assert.equal(closed, 1);
      if (change === 'symlink') rmSync(f.configPath);
    }
  } finally { f.cleanup(); }
});

test('Windows missing file can default but existing file is refused without an ACL verifier', () => {
  const configPath = 'C:\\Users\\Example\\AppData\\Local\\PhoenixTools\\mcp\\config.json';
  const f = fixture();
  try {
    f.create();
    const directoryInfo = lstatSync(f.mcp, { bigint: true }), fileInfo = lstatSync(f.configPath, { bigint: true });
    let opened = false;
    const io: PftMcpSettingsIO = { ...realIO, lstat(filename: string): BigIntStats {
      if (filename === configPath) return fileInfo;
      return directoryInfo;
    }, open() { opened = true; throw new Error('must not open a Windows config without ACL verification'); } };
    const options = { platform: 'win32' as const, env: {}, home: 'C:\\Users\\Example', paths: { configPath }, io };
    errorCode(() => pftReadMcpSettings(options), 'MCP_SETTINGS_UNSUPPORTED'); assert.equal(opened, false);
    const absentIO = { ...io, lstat(filename: string): BigIntStats {
      if (filename === configPath) throw Object.assign(new Error('missing'), { code: 'ENOENT' });
      return directoryInfo;
    } };
    const result = pftReadMcpSettings({ ...options, io: absentIO });
    assert.equal(result.source, 'default'); assert.equal(result.preferredPort, 9876); assert.equal(opened, false);
  } finally { f.cleanup(); }
});
