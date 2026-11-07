// SPDX-License-Identifier: Apache-2.0
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { gunzipSync } from 'node:zlib';
import test from 'node:test';

const ROOT = fileURLToPath(new URL('../', import.meta.url));
const metadata = () => JSON.parse(fs.readFileSync(path.join(ROOT, 'package.json'), 'utf8'));

test('public npm identity is scoped while CLI and locked dependency identity stay stable', () => {
  const package_ = metadata(), lock = JSON.parse(fs.readFileSync(path.join(ROOT, 'package-lock.json'), 'utf8'));
  assert.equal(package_.name, '@phoenix-wing/phoenix-freecad-mcp');
  assert.equal(package_.version, '0.4.1');
  assert.equal(lock.name, package_.name); assert.equal(lock.packages[''].name, package_.name);
  assert.equal(lock.version, package_.version); assert.equal(lock.packages[''].version, package_.version);
  assert.deepEqual(package_.bin, { 'phoenix-freecad-mcp': 'dist/cli.js' });
  assert.deepEqual(lock.packages[''].bin, package_.bin);
  assert.deepEqual(lock.packages[''].dependencies, package_.dependencies);
  assert.deepEqual(lock.packages[''].devDependencies, package_.devDependencies);
  assert.deepEqual(package_.publishConfig, { access: 'public', registry: 'https://registry.npmjs.org' });
  assert.deepEqual(package_.repository, { type: 'git', url: 'git+https://github.com/phoenixwing-org/phoenix-freecad-tools.git' });
  assert.equal(package_.homepage, 'https://github.com/phoenixwing-org/phoenix-freecad-tools#readme');
  assert.deepEqual(package_.bugs, { url: 'https://github.com/phoenixwing-org/phoenix-freecad-tools/issues' });
});

// Inspect one member without extracting the package or executing packed code.
function tarMember(archive: Buffer, wanted: string): Buffer {
  const tar = gunzipSync(archive, { maxOutputLength: 64 * 1024 * 1024 });
  for (let offset = 0; offset + 512 <= tar.length;) {
    const header = tar.subarray(offset, offset + 512);
    const name = header.subarray(0, 100).toString('utf8').split('\0')[0]!;
    if (!name) break;
    const prefix = header.subarray(345, 500).toString('utf8').split('\0')[0]!;
    const filename = prefix ? prefix + '/' + name : name;
    const octal = header.subarray(124, 136).toString('ascii').replace(/\0.*$/su, '').trim();
    assert.match(octal, /^[0-7]+$/u);
    const size = Number.parseInt(octal, 8);
    assert.ok(Number.isSafeInteger(size) && size >= 0 && offset + 512 + size <= tar.length);
    if (filename === wanted) return tar.subarray(offset + 512, offset + 512 + size);
    offset += 512 + Math.ceil(size / 512) * 512;
  }
  assert.fail('Required package member is missing: ' + wanted);
}

test('actual offline npm tarball has the scoped filename and unchanged CLI bin target', t => {
  const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'pft-npm-package-test-'));
  t.after(() => fs.rmSync(temporary, { recursive: true, force: true }));
  const userConfig = path.join(temporary, 'user.npmrc'), globalConfig = path.join(temporary, 'global.npmrc');
  fs.writeFileSync(userConfig, '', { mode: 0o600 }); fs.writeFileSync(globalConfig, '', { mode: 0o600 });
  const npm = process.env.npm_execpath ?? fs.realpathSync(path.join(path.dirname(process.execPath), 'npm'));
  const output = execFileSync(process.execPath, [npm, 'pack', '--ignore-scripts', '--offline', '--json', '--pack-destination', temporary], {
    cwd: ROOT, encoding: 'utf8', timeout: 30000, maxBuffer: 4 * 1024 * 1024,
    env: { PATH: process.env.PATH, npm_config_userconfig: userConfig, npm_config_globalconfig: globalConfig,
      npm_config_cache: path.join(temporary, 'cache'), npm_config_update_notifier: 'false' },
  });
  const results = JSON.parse(output); assert.equal(results.length, 1);
  const result = results[0], package_ = metadata();
  assert.equal(result.id, package_.name + '@' + package_.version);
  assert.equal(result.name, package_.name);
  assert.equal(result.filename, 'phoenix-wing-phoenix-freecad-mcp-0.4.1.tgz');
  const artifact = fs.readFileSync(path.join(temporary, result.filename));
  assert.deepEqual(JSON.parse(tarMember(artifact, 'package/package.json').toString('utf8')), package_);
  assert.deepEqual(tarMember(artifact, 'package/dist/cli.js'), fs.readFileSync(path.join(ROOT, 'dist/cli.js')));
  const cli = result.files.find((file: { path: string }) => file.path === 'dist/cli.js');
  assert.ok(cli);
  // npm's bin-links sets executable mode at installation; tar mode need not be executable.
  assert.ok(tarMember(artifact, 'package/' + package_.bin['phoenix-freecad-mcp']).toString('utf8').startsWith('#!/usr/bin/env node\n'));
  for (const name of ['LICENSE', 'NOTICE', 'addon/PhoenixFreeCADTools/pft_file_publish.py', 'scripts/install-addon.mjs',
    ...['pft_pdf_contract.py', 'pft_pdf_helper.py', 'pft_pdf_qprocess.py', 'pft_pdf_preview_views.py']
      .map(name => 'addon/PhoenixFreeCADTools/' + name)]) {
    assert.ok(result.files.some((file: { path: string }) => file.path === name));
  }
  assert.ok(!result.files.some((file: { path: string }) => file.path === 'package-lock.json'));
});
