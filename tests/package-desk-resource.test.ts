// SPDX-License-Identifier: Apache-2.0
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { createHash } from 'node:crypto';
import { createServer } from 'node:http';
import { execFileSync } from 'node:child_process';
import { fileURLToPath, pathToFileURL } from 'node:url';
import test, { type TestContext } from 'node:test';
import { Client } from '@modelcontextprotocol/client';
import { StdioClientTransport } from '@modelcontextprotocol/client/stdio';
import { buildDeskResource, packageDeskResource } from '../scripts/package-desk-resource.mjs';
import { TOOL_NAMES, CHAT_TOOL_NAMES } from '../src/server.js';
import { PFT_THUMBNAIL_WORKER_METHODS } from '../src/pft-thumbnail-worker.js';
import { status } from './helpers/pft-thumbnail-worker-fixture.js';
const REPO = fileURLToPath(new URL('../', import.meta.url));
const sha = (value: Uint8Array | string) => createHash('sha256').update(value).digest('hex');
function temporary(t: TestContext) {
  const root = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'pft-desk-package-test-')));
  t.after(() => fs.rmSync(root, { recursive: true, force: true })); return root;
}
const asRecord = (value: unknown) => { assert.ok(value && typeof value === 'object' && !Array.isArray(value)); return value as Record<string, unknown>; };
async function session(t: TestContext, root: string, scope: 'full' | 'chat' | 'worker', env: Record<string, string> = {}) {
  const transport = new StdioClientTransport({ command: process.execPath, args: [path.join(root, 'dist/cli.mjs')], cwd: path.dirname(root), stderr: 'pipe',
    env: { PHOENIX_FREECAD_URL: 'http://127.0.0.1:12345/',
      PHOENIX_FREECAD_TOOL_SCOPE: scope === 'worker' ? 'full' : scope, ...(scope === 'worker' ? { PHOENIX_FREECAD_SCOPE: 'worker' } : {}), ...env } });
  const client = new Client({ name: 'independent-resource-fixture', version: '1.0.0' });
  await client.connect(transport); t.after(() => client.close()); return client;
}

test('Desk resource has deterministic complete hashes, managed legal bytes and exact bundled licenses', async () => {
  const first = await buildDeskResource(), second = await buildDeskResource();
  assert.equal(first.manifestSha256, second.manifestSha256); assert.deepEqual(first.manifest, second.manifest);
  const manifest = JSON.parse(first.manifest.toString('utf8'));
  assert.deepEqual(Object.keys(manifest).sort(), ['addon_dir', 'files', 'installer_entry', 'mcp_entry', 'plugin_version', 'schema_version']);
  assert.equal(manifest.addon_dir, 'addon/PhoenixFreeCADTools'); assert.equal(manifest.mcp_entry, 'dist/cli.mjs');
  assert.equal(manifest.plugin_version, '0.4.1'); assert.equal(Object.keys(manifest.files).length, first.files.size);
  assert.ok(first.files.size <= 512);
  for (const name of ['pft_drawing_export.py', 'pft_drawing_export_profile.py', 'pft_drawing_export_views.py',
    'pft_pdf_contract.py', 'pft_pdf_helper.py', 'pft_pdf_qprocess.py', 'pft_pdf_preview_views.py',
    'pft_drawing_layout.py', 'pft_drawing_layout_profile.py', 'pft_drawing_layout_plan.py', 'pft_drawing_layout_views.py',
    'pft_draft_layout.py', 'pft_draft_layout_views.py']) {
    const relative = 'addon/PhoenixFreeCADTools/' + name;
    assert.deepEqual(first.files.get(relative), fs.readFileSync(path.join(REPO, relative)));
    assert.equal(manifest.files[relative], sha(fs.readFileSync(path.join(REPO, relative))));
  }
  for (const [name, bytes] of first.files) {
    assert.equal(sha(bytes), manifest.files[name]); assert.deepEqual(bytes, second.files.get(name));
    assert.ok(!name.split('/').some(part => part.startsWith('.') || ['node_modules', '__pycache__'].includes(part)));
    assert.ok(bytes.length <= 8 * 1024 * 1024);
  }
  for (const [name, original] of [['LICENSE', 'LICENSE'], ['NOTICE', 'docs/ADDON-NOTICE.txt'], ['README.md', 'docs/ADDON-MANAGED-INSTALL.md']]) {
    assert.deepEqual(first.files.get('addon/PhoenixFreeCADTools/' + name), fs.readFileSync(path.join(REPO, original!)));
  }
  for (const name of ['@modelcontextprotocol/server', '@modelcontextprotocol/core', 'zod']) {
    const filename = 'licenses/' + name.replace(/^@/, '').replaceAll('/', '-') + '-LICENSE.txt';
    assert.deepEqual(first.files.get(filename), fs.readFileSync(path.join(REPO, 'node_modules', name, 'LICENSE')));
  }
  assert.deepEqual(first.files.get('licenses/esbuild-LICENSE.txt'), fs.readFileSync(path.join(REPO, 'node_modules/esbuild/LICENSE.md')));
  const bundle = first.files.get('dist/cli.mjs')!.toString('utf8');
  assert.ok(!bundle.includes(REPO)); assert.ok(!bundle.includes(os.homedir())); assert.ok(!bundle.includes('sourceMappingURL'));
  assert.match(bundle, /adapters\/pft-desk-client\.js/); assert.match(first.files.get('NOTICE')!.toString(), /esbuild 0\.28\.2/);
});

test('relocated actual bundle speaks SDK full28/chat22/worker4 without node_modules', async t => {
  const folder = temporary(t), root = path.join(folder, 'resource');
  await packageDeskResource({ outputDirectory: root });
  const relocated = path.join(folder, 'relocated'); fs.renameSync(root, relocated);
  assert.equal(fs.existsSync(path.join(relocated, 'node_modules')), false);
  for (const [scope, expected] of [['full', TOOL_NAMES], ['chat', CHAT_TOOL_NAMES], ['worker', PFT_THUMBNAIL_WORKER_METHODS]] as const) {
    const client = await session(t, relocated, scope), listed = await client.listTools();
    assert.deepEqual(listed.tools.map(tool => tool.name).sort(), [...expected].sort());
    if (scope !== 'worker') {
      const capability = await client.callTool({ name: 'get_capabilities', arguments: {} });
      assert.equal(asRecord(capability.structuredContent).connection_checked, false);
      assert.deepEqual((asRecord(capability.structuredContent).tools as string[]).sort(), [...expected].sort());
    }
    await client.close();
  }
  const server = createServer(async (req, res) => {
    let text = ''; for await (const chunk of req) text += chunk;
    const call = JSON.parse(text); res.setHeader('Content-Type', 'application/json');
    res.end(JSON.stringify({ ok: true, data: call.method === 'get_status'
      ? { protocol_version: 1, supported_methods: [...PFT_THUMBNAIL_WORKER_METHODS] } : status() }));
  });
  await new Promise<void>(resolve => server.listen(0, '127.0.0.1', resolve));
  t.after(async () => { server.closeAllConnections(); await new Promise<void>(resolve => server.close(() => resolve())); });
  const client = await session(t, relocated, 'worker', { PHOENIX_FREECAD_URL: 'http://127.0.0.1:' + (server.address() as { port: number }).port + '/' });
  const response = await client.callTool({ name: 'get_thumbnail_worker_status', arguments: {} });
  assert.equal(response.isError, undefined); assert.equal(asRecord(asRecord(response.structuredContent).data).worker_id, status().worker_id);
  await client.close();
});

test('actual managed installer dry-run install idempotence and uninstall use only private Mod', async t => {
  const folder = temporary(t), root = path.join(folder, 'resource'), mod = path.join(folder, 'private', 'Mod');
  await packageDeskResource({ outputDirectory: root });
  const invoke = (script: string, args: string[]) => execFileSync(process.execPath, [path.join(root, script), '--mod-dir', mod, ...args],
    { cwd: folder, env: {}, encoding: 'utf8' });
  assert.match(invoke('scripts/install-addon.mjs', ['--dry-run']), /"action": "install"/); assert.equal(fs.existsSync(mod), false);
  assert.match(invoke('scripts/install-addon.mjs', []), /"action": "install"/);
  const addon = path.join(mod, 'PhoenixFreeCADTools'); assert.equal(fs.lstatSync(addon).isSymbolicLink(), false);
  for (const filename of ['LICENSE', 'NOTICE', 'README.md']) assert.deepEqual(fs.readFileSync(path.join(addon, filename)), fs.readFileSync(path.join(root, 'addon/PhoenixFreeCADTools', filename)));
  assert.match(invoke('scripts/install-addon.mjs', []), /"action": "unchanged"/);
  // Uninstall uses the same actual installer module's exported owned-tree API.
  const control = `const m=await import(process.argv[1]);console.log(JSON.stringify(m.uninstallAddon({modDir:process.argv[2]})));`;
  const removed = execFileSync(process.execPath, ['--input-type=module', '-e', control, pathToFileURL(path.join(root, 'scripts/install-addon.mjs')).href, mod], { cwd: folder, env: {}, encoding: 'utf8' });
  assert.equal(JSON.parse(removed).action, 'uninstall'); assert.equal(fs.existsSync(addon), false);
  assert.ok(fs.existsSync(JSON.parse(removed).backup));
});

test('identical output may be reused but changed or unknown output is preserved', async t => {
  const folder = temporary(t), root = path.join(folder, 'resource');
  const first = await packageDeskResource({ outputDirectory: root }), again = await packageDeskResource({ outputDirectory: root });
  assert.equal(again.action, 'unchanged'); assert.equal(first.manifest_sha256, again.manifest_sha256);
  const foreign = path.join(root, 'unlisted-neutral.txt'); fs.writeFileSync(foreign, 'Independent unknown content');
  await assert.rejects(packageDeskResource({ outputDirectory: root }), /unknown output/);
  assert.equal(fs.readFileSync(foreign, 'utf8'), 'Independent unknown content'); fs.unlinkSync(foreign);
  const bundle = path.join(root, 'dist/cli.mjs'); fs.appendFileSync(bundle, '\n// Independent local change\n');
  await assert.rejects(packageDeskResource({ outputDirectory: root }), /unknown output/); assert.match(fs.readFileSync(bundle, 'utf8'), /Independent local change/);
});

test('build source and output failures never finalize or adopt an unknown resource', async t => {
  const folder = temporary(t), source = path.join(folder, 'incomplete-source'), output = path.join(folder, 'output'); fs.mkdirSync(source);
  fs.copyFileSync(path.join(REPO, 'package.json'), path.join(source, 'package.json'));
  await assert.rejects(packageDeskResource({ root: source, outputDirectory: output })); assert.equal(fs.existsSync(output), false);
  const target = path.join(folder, 'unknown'); fs.mkdirSync(target); const link = path.join(folder, 'linked-output'); fs.symlinkSync(target, link);
  await assert.rejects(packageDeskResource({ outputDirectory: link })); assert.deepEqual(fs.readdirSync(target), []);
  const parentLink = path.join(folder, 'linked-parent'); fs.symlinkSync(target, parentLink);
  await assert.rejects(packageDeskResource({ outputDirectory: path.join(parentLink, 'never-created', 'resource') }));
  assert.deepEqual(fs.readdirSync(target), []);
  for (const destination of [REPO, path.dirname(REPO), path.parse(REPO).root, path.join(REPO, 'src', 'never-created-resource')]) {
    await assert.rejects(packageDeskResource({ outputDirectory: destination }), /outside source/);
  }
  assert.equal(fs.existsSync(path.join(REPO, 'src', 'never-created-resource')), false);
});

test('publication failure removes only owned output and preserves foreign empty directories', async t => {
  const folder = temporary(t), output = path.join(folder, 'output'), original = fs.linkSync;
  let calls = 0;
  fs.linkSync = ((source: fs.PathLike, destination: fs.PathLike) => {
    if (String(destination).startsWith(output + path.sep) && ++calls === 2) throw new Error('Independent publication failure');
    return original(source, destination);
  }) as typeof fs.linkSync;
  try { await assert.rejects(packageDeskResource({ outputDirectory: output }), /Independent publication failure/); }
  finally { fs.linkSync = original; }
  assert.equal(fs.existsSync(output), false);
  calls = 0; const foreign = path.join(output, 'foreign-empty-directory');
  fs.linkSync = ((source: fs.PathLike, destination: fs.PathLike) => {
    if (String(destination).startsWith(output + path.sep) && ++calls === 2) { fs.mkdirSync(foreign); throw new Error('Independent publication failure'); }
    return original(source, destination);
  }) as typeof fs.linkSync;
  try { await assert.rejects(packageDeskResource({ outputDirectory: output }), /Independent publication failure/); }
  finally { fs.linkSync = original; }
  assert.ok(fs.lstatSync(foreign).isDirectory()); assert.equal(fs.existsSync(path.join(output, 'resource.json')), false);
  await assert.rejects(packageDeskResource({ outputDirectory: output }), /unknown output/);
  assert.equal(fs.readdirSync(folder).some(name => name.startsWith('.pft-desk-resource-')), false);
});

test('bundle module-relative Desk guard rejects its resource but permits a private sibling', async t => {
  const folder = temporary(t), root = path.join(folder, 'resource'), scripts = path.join(folder, 'desk-source', 'scripts');
  await packageDeskResource({ outputDirectory: root }); fs.mkdirSync(scripts, { recursive: true });
  const called = path.join(folder, 'desk-called.txt'), cli = path.join(scripts, 'desk-web-service.mjs');
  fs.writeFileSync(cli, `import fs from 'node:fs';fs.writeFileSync(${JSON.stringify(called)},'called');process.stdout.write('{}');`);
  const env = { PHOENIX_DESK_NODE: fs.realpathSync(process.execPath), PHOENIX_DESK_CLI: cli };
  const inside = await session(t, root, 'full', { ...env, PHOENIX_DESK_STATE_DIR: path.join(root, 'private-state') });
  const refused = await inside.callTool({ name: 'desk_get_connection_status', arguments: {} });
  assert.equal(asRecord(asRecord(refused.structuredContent).error).code, 'INVALID_STATE_DIRECTORY'); assert.equal(fs.existsSync(called), false); await inside.close();
  const sibling = path.join(folder, 'private-state'); fs.mkdirSync(sibling, { mode: 0o700 });
  const allowed = await session(t, root, 'full', { ...env, PHOENIX_DESK_STATE_DIR: sibling });
  const checked = await allowed.callTool({ name: 'desk_get_connection_status', arguments: {} });
  assert.notEqual(asRecord(asRecord(checked.structuredContent).error).code, 'INVALID_STATE_DIRECTORY');
  assert.equal(fs.readFileSync(called, 'utf8'), 'called'); await allowed.close();
});
