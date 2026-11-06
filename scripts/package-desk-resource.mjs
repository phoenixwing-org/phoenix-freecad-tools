// SPDX-License-Identifier: Apache-2.0
/** Build an offline, self-contained Desk resource. Never installs or starts CAD. */
import fs from 'node:fs';
import path from 'node:path';
import { createHash } from 'node:crypto';
import { isBuiltin } from 'node:module';
import { fileURLToPath } from 'node:url';
import { parseArgs } from 'node:util';
import { build, version as esbuildVersion } from 'esbuild';

const ROOT = fileURLToPath(new URL('../', import.meta.url));
const ADDON = 'addon/PhoenixFreeCADTools';
const MANIFEST = 'resource.json';
const BUNDLE = 'dist/cli.mjs';
const ESBUILD = '0.28.2';
const DEPENDENCIES = Object.freeze({ '@modelcontextprotocol/server': '2.4.0', '@modelcontextprotocol/core': '2.4.0', zod: '4.6.7' });
const SCRIPTS = ['scripts/install-addon.mjs', 'scripts/dev-freecad.mjs', 'scripts/thumbnail-worker.mjs'];
const DOCUMENTS = { LICENSE: 'LICENSE', NOTICE: 'docs/ADDON-NOTICE.txt', 'README.md': 'docs/ADDON-MANAGED-INSTALL.md' };
const MAX_FILE = 8 * 1024 * 1024, MAX_TOTAL = 64 * 1024 * 1024;
const sha = bytes => createHash('sha256').update(bytes).digest('hex');
const json = value => Buffer.from(JSON.stringify(value, null, 2) + '\n');
const fail = message => { throw new Error(message); };
const relative = value => typeof value === 'string' && value.length > 0 && value.length <= 1024
  && !/[\\:\p{Cc}\p{Cf}\p{Cs}]/u.test(value) && !value.startsWith('/')
  && value.split('/').length <= 24 && value.split('/').every(part => part && part !== '.' && part !== '..');
const present = filename => { try { fs.lstatSync(filename); return true; } catch (error) { if (error.code === 'ENOENT') return false; throw error; } };
function realDirectory(directory) {
  const normalized = path.resolve(directory); let current = path.parse(normalized).root;
  for (const part of normalized.slice(current.length).split(path.sep).filter(Boolean)) {
    current = path.join(current, part); const info = fs.lstatSync(current);
    if (!info.isDirectory() || info.isSymbolicLink()) fail('Resource directories must be real directories.');
  }
  return normalized;
}
function makeParents(directory) {
  if (present(directory)) { realDirectory(directory); return; }
  makeParents(path.dirname(directory)); fs.mkdirSync(directory, { mode: 0o700 });
}
function checkedBytes(root, name, maximum = MAX_FILE) {
  if (!relative(name)) fail('Invalid resource file name.');
  realDirectory(path.dirname(path.join(root, name)));
  const filename = path.join(root, name), before = fs.lstatSync(filename, { bigint: true });
  if (!before.isFile() || before.isSymbolicLink() || before.nlink !== 1n || before.size > BigInt(maximum)) fail('Resource source must be a bounded regular file without extra links.');
  const stamp = value => [value.dev, value.ino, value.size, value.mtimeNs, value.ctimeNs, value.nlink].join(':');
  const fd = fs.openSync(filename, fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW | fs.constants.O_NONBLOCK);
  try {
    if (stamp(fs.fstatSync(fd, { bigint: true })) !== stamp(before)) fail('Resource source changed.');
    const buffer = Buffer.alloc(Number(before.size) + 1); let offset = 0;
    while (offset < buffer.length) {
      const count = fs.readSync(fd, buffer, offset, Math.min(65536, buffer.length - offset), offset);
      if (!count) break; offset += count;
    }
    const bytes = buffer.subarray(0, offset);
    if (BigInt(bytes.length) !== before.size || stamp(fs.fstatSync(fd, { bigint: true })) !== stamp(before)
        || stamp(fs.lstatSync(filename, { bigint: true })) !== stamp(before)) fail('Resource source changed.');
    return bytes;
  } finally { fs.closeSync(fd); }
}
function sourceReader(root) {
  const snapshots = new Map();
  return {
    read(name, maximum = MAX_FILE) {
      const bytes = checkedBytes(root, name, maximum), previous = snapshots.get(name);
      if (previous !== undefined && !previous.equals(bytes)) fail('Resource source changed during build.');
      snapshots.set(name, bytes); return bytes;
    },
    verify() { for (const [name, bytes] of snapshots) if (!checkedBytes(root, name).equals(bytes)) fail('Resource source changed during build.'); },
  };
}
function addonFiles(bytes) {
  // Consume only the existing authoritative literal whitelist, never execute Python.
  const text = bytes.toString('utf8'), match = /^ADDON_FILES = \(\r?\n([\s\S]*?)^\)\r?$/m.exec(text);
  if (!match) fail('The explicit Addon whitelist is missing.');
  let names;
  try { names = JSON.parse('[' + match[1].replace(/,\s*$/, '') + ']'); } catch { fail('The explicit Addon whitelist is not a string literal list.'); }
  if (!Array.isArray(names) || !names.length || names.length > 400 || new Set(names).size !== names.length
      || names.some(name => !relative(name) || !(name.endsWith('.py') || name === 'package.xml' || name === 'HELP.md' || /^Resources\/icons\/[a-z0-9-]+\.svg$/u.test(name)))) fail('The explicit Addon whitelist is invalid.');
  return names;
}
/** Build all bytes before publishing. The manifest contains no machine paths or timestamps. */
export async function buildDeskResource({ root = ROOT } = {}) {
  root = realDirectory(root);
  const source = sourceReader(root), read = (name, maximum) => source.read(name, maximum);
  const package_ = JSON.parse(read('package.json').toString('utf8'));
  const lock = JSON.parse(read('package-lock.json').toString('utf8'));
  const version = package_.version;
  if (!/^\d+\.\d+\.\d+(?:-[a-zA-Z0-9.-]+)?$/u.test(version) || version !== '0.3.2'
      || lock.version !== version || lock.packages?.['']?.version !== version
      || package_.devDependencies?.esbuild !== ESBUILD || lock.packages?.['']?.devDependencies?.esbuild !== ESBUILD
      || lock.packages?.['node_modules/esbuild']?.version !== ESBUILD || esbuildVersion !== ESBUILD) fail('Package, lock and exact build dependency versions must agree.');
  const files = new Map();
  const add = (name, bytes) => {
    if (!relative(name) || name.split('/').some(part => part.startsWith('.') || ['node_modules', '__pycache__'].includes(part))
        || !Buffer.isBuffer(bytes) || bytes.length > MAX_FILE || files.has(name)) fail('Invalid or duplicate resource output.');
    files.set(name, bytes);
  };
  for (const name of addonFiles(read('scripts/package-addon.py'))) add(ADDON + '/' + name, read(ADDON + '/' + name, 2 * 1024 * 1024));
  const xml = files.get(ADDON + '/package.xml').toString('utf8');
  const bridge = files.get(ADDON + '/pft_bridge.py').toString('utf8');
  const server = read('src/server.ts').toString('utf8');
  if (/<!DOCTYPE|<!ENTITY/iu.test(xml) || [...xml.matchAll(/<version>\s*([^<]+)\s*<\/version>/gu)].map(row => row[1]).join('|') !== version
      || !bridge.includes('ADDON_VERSION = "' + version + '"') || !server.includes("export const VERSION = '" + version + "';")) fail('Addon and MCP versions must agree.');
  for (const [name, original] of Object.entries(DOCUMENTS)) {
    const bytes = read(original, 2 * 1024 * 1024); if (!bytes.length) fail('Required Addon legal or installation material is empty.');
    add(ADDON + '/' + name, bytes);
  }
  for (const name of ['LICENSE', 'docs/ADDON-NOTICE.txt', 'docs/ADDON-MANAGED-INSTALL.md', ...SCRIPTS]) add(name, read(name));
  const bundled = new Set(); let rebased = 0;
  const output = await build({ absWorkingDir: root, entryPoints: ['src/cli.ts'], outfile: BUNDLE,
    bundle: true, write: false, platform: 'node', format: 'esm', target: 'node22',
    sourcemap: false, metafile: true, legalComments: 'eof', logLevel: 'silent',
    plugins: [{ name: 'pft-verified-build-inputs', setup(builder) {
      builder.onLoad({ filter: /.*/ }, args => {
        const name = path.relative(root, args.path).split(path.sep).join('/');
        if (!/\.[cm]?[jt]s$/u.test(name)) fail('An unreviewed source format entered the bundle.');
        let contents = read(name).toString('utf8');
        if (name.startsWith('node_modules/')) {
          const parts = name.slice('node_modules/'.length).split('/');
          const dependency = parts[0].startsWith('@') ? parts.slice(0, 2).join('/') : parts[0];
          if (!Object.hasOwn(DEPENDENCIES, dependency)) fail('An unreviewed dependency entered the bundle.');
          bundled.add(dependency);
        } else if (!/^src\/[a-zA-Z0-9_/-]+\.[cm]?[jt]s$/u.test(name)) fail('An unlisted source entered the bundle.');
        if (contents.includes('import.meta.url')) {
          if (name === 'src/adapters/pft-desk-client.ts') {
            const exact = "const OWN_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..');";
            if (contents.split(exact).length !== 2 || contents.split('import.meta.url').length !== 2) fail('The Desk module location contract changed.');
            contents = contents.replace(exact, "const OWN_ROOT = path.resolve(path.dirname(fileURLToPath(new URL('./adapters/pft-desk-client.js', import.meta.url).href)), '..', '..');");
            rebased++;
          } else if (!['src/server.ts', 'src/pft-thumbnail-worker-tools.ts'].includes(name)) fail('An unreviewed module-relative runtime resource entered the bundle.');
        }
        return { contents, loader: name.endsWith('.ts') ? 'ts' : 'js' };
      });
    } }],
  });
  if (rebased !== 1 || output.outputFiles.length !== 1 || Object.values(output.metafile.outputs).some(value => value.imports.some(item => !item.external || !isBuiltin(item.path)))) fail('The self-contained Node runtime could not be verified.');
  add(BUNDLE, Buffer.from(output.outputFiles[0].contents));
  const notices = [];
  for (const name of [...bundled].sort()) {
    const expected = DEPENDENCIES[name], metadata = JSON.parse(read('node_modules/' + name + '/package.json').toString('utf8'));
    if (metadata.version !== expected || lock.packages?.['node_modules/' + name]?.version !== expected) fail('A bundled runtime dependency differs from the lock.');
    const license = read('node_modules/' + name + '/LICENSE'); if (!license.length) fail('A bundled dependency license is empty.');
    const destination = 'licenses/' + name.replace(/^@/u, '').replaceAll('/', '-') + '-LICENSE.txt';
    add(destination, license); notices.push(name + ' ' + expected + ': ' + destination);
    for (const notice of ['NOTICE', 'NOTICE.txt']) {
      if (present(path.join(root, 'node_modules', name, notice))) {
        const target = destination.replace('-LICENSE.txt', '-' + notice); add(target, read('node_modules/' + name + '/' + notice));
        notices.push('  Additional notice: ' + target);
      }
    }
  }
  if (bundled.size !== Object.keys(DEPENDENCIES).length) fail('The runtime dependency coverage changed.');
  const buildLicense = read('node_modules/esbuild/LICENSE.md'); if (!buildLicense.length) fail('The build dependency license is empty.');
  add('licenses/esbuild-LICENSE.txt', buildLicense);
  const originalNotice = read('docs/DESK-RESOURCE-NOTICE.txt').toString('utf8');
  add('NOTICE', Buffer.from(originalNotice + '\nBundled runtime dependencies (complete license texts are included):\n' + notices.map(line => '- ' + line).join('\n')
    + '\nBuild tool: esbuild ' + ESBUILD + ' (MIT; licenses/esbuild-LICENSE.txt). Its executable is not distributed.\n'));
  if (files.size > 512 || [...files.values()].reduce((total, bytes) => total + bytes.length, 0) > MAX_TOTAL) fail('Resource count or total byte limit exceeded.');
  const hashes = Object.fromEntries([...files].sort(([a], [b]) => a.localeCompare(b, 'en')).map(([name, bytes]) => [name, sha(bytes)]));
  const manifest = json({ schema_version: 1, plugin_version: version, addon_dir: ADDON,
    installer_entry: 'scripts/install-addon.mjs', mcp_entry: BUNDLE, files: hashes });
  source.verify();
  return { version, files, manifest, manifestSha256: sha(manifest) };
}
function tree(root) {
  const names = [], directories = [];
  function walk(relative_ = '') {
    for (const item of fs.readdirSync(path.join(root, relative_), { withFileTypes: true })) {
      const name = relative_ ? relative_ + '/' + item.name : item.name;
      if (!relative(name) || names.length + directories.length >= 1024 || item.isSymbolicLink()) fail('Existing resource output contains unknown content.');
      if (item.isDirectory()) { realDirectory(path.join(root, name)); directories.push(name); walk(name); }
      else if (item.isFile()) names.push(name); else fail('Existing resource output is not regular.');
    }
  }
  walk(); return { names: names.sort(), directories: directories.sort() };
}
function verifyOutput(root, files) {
  realDirectory(root);
  const actual = tree(root), names = [...files.keys()].sort();
  const directories = [...new Set(names.flatMap(name => { const pieces = name.split('/'); return pieces.slice(0, -1).map((_, index) => pieces.slice(0, index + 1).join('/')); }))].sort();
  if (JSON.stringify(actual.names) !== JSON.stringify(names) || JSON.stringify(actual.directories) !== JSON.stringify(directories)
      || names.some(name => !checkedBytes(root, name).equals(files.get(name)))) fail('Existing different or unknown output was retained.');
}
/** Manifest publication is the commit point. No rename can overwrite an unknown directory. */
export async function packageDeskResource({ outputDirectory, root = ROOT } = {}) {
  const built = await buildDeskResource({ root });
  const destination = path.resolve(outputDirectory ?? path.join(root, 'artifacts', 'phoenix-freecad-tools-desk-resource-' + built.version));
  const inside = (ancestor, candidate) => { const value = path.relative(ancestor, candidate); return value === '' || !path.isAbsolute(value) && value !== '..' && !value.startsWith('..' + path.sep); };
  const sourceRoot = realDirectory(root);
  if (destination === path.parse(destination).root || inside(destination, sourceRoot)
      || inside(sourceRoot, destination) && !inside(path.join(sourceRoot, 'artifacts'), destination)) fail('Output must be outside source or inside its artifacts directory.');
  const parent = path.dirname(destination);
  if (!present(parent)) makeParents(parent);
  realDirectory(parent);
  const files = new Map(built.files);
  files.set(MANIFEST + '.sha256', Buffer.from(built.manifestSha256 + '  ' + MANIFEST + '\n'));
  files.set(MANIFEST, built.manifest);
  const result = { directory: destination, manifest: path.join(destination, MANIFEST), manifest_sha256: built.manifestSha256,
    version: built.version, files: built.files.size };
  if (present(destination)) { verifyOutput(destination, files); return { ...result, action: 'unchanged' }; }
  const stage = fs.mkdtempSync(path.join(parent, '.pft-desk-resource-')); fs.chmodSync(stage, 0o700);
  let claimed = false, committed = false;
  const published = new Map(), createdDirectories = new Map(); let destinationIdentity;
  const sameIdentity = (before, after) => before && before.dev === after.dev && before.ino === after.ino;
  function publicationDirectory(relative_) {
    const pieces = relative_.split('/').slice(0, -1);
    for (let index = 0; index < pieces.length; index++) {
      const name = pieces.slice(0, index + 1).join('/'), directory = path.join(destination, name);
      const previous = createdDirectories.get(name);
      if (previous !== undefined) {
        const current = fs.lstatSync(directory);
        if (!current.isDirectory() || current.isSymbolicLink() || !sameIdentity(previous, current)) fail('Resource output directory changed.');
      } else {
        fs.mkdirSync(directory, { mode: 0o700 }); createdDirectories.set(name, fs.lstatSync(directory));
      }
    }
  }
  try {
    for (const [name, bytes] of files) {
      const filename = path.join(stage, name); fs.mkdirSync(path.dirname(filename), { recursive: true, mode: 0o700 });
      fs.writeFileSync(filename, bytes, { flag: 'wx', mode: 0o600 });
    }
    verifyOutput(stage, files);
    fs.mkdirSync(destination, { mode: 0o700 }); claimed = true; destinationIdentity = fs.lstatSync(destination);
    // Publish complete files with exclusive links; resource.json is last. The
    // brief staging hard link is removed before any manifest becomes visible.
    for (const name of files.keys()) {
      const filename = path.join(destination, name); publicationDirectory(name);
      fs.linkSync(path.join(stage, name), filename); fs.unlinkSync(path.join(stage, name));
      published.set(name, fs.lstatSync(filename));
      if (name === MANIFEST) committed = true;
    }
    verifyOutput(destination, files);
    return { ...result, action: 'created' };
  } catch (error) {
    if (claimed && !committed) {
      // Remove only our still-identical files. Unknown concurrent content is
      // preserved without publishing or adopting a partial resource manifest.
      try {
        const current = fs.lstatSync(destination), actual = tree(destination);
        if (sameIdentity(destinationIdentity, current)
            && actual.directories.every(name => sameIdentity(createdDirectories.get(name), fs.lstatSync(path.join(destination, name))))
            && actual.names.every(name => sameIdentity(published.get(name), fs.lstatSync(path.join(destination, name)))
              && checkedBytes(destination, name).equals(files.get(name)))) {
          for (const name of actual.names) {
            if (!sameIdentity(published.get(name), fs.lstatSync(path.join(destination, name)))) fail('Resource output changed during cleanup.');
            fs.unlinkSync(path.join(destination, name));
          }
          // rmdir never recursively deletes content created after our checks.
          for (const name of actual.directories.sort((a, b) => b.split('/').length - a.split('/').length)) {
            if (!sameIdentity(createdDirectories.get(name), fs.lstatSync(path.join(destination, name)))) fail('Resource output changed during cleanup.');
            fs.rmdirSync(path.join(destination, name));
          }
          if (sameIdentity(destinationIdentity, fs.lstatSync(destination))) fs.rmdirSync(destination);
        }
      } catch { /* Preserve any unknown or uncertain output, without a manifest. */ }
    }
    throw error;
  } finally { fs.rmSync(stage, { recursive: true, force: true }); }
}
async function main() {
  const { values } = parseArgs({ options: { 'output-dir': { type: 'string' }, help: { type: 'boolean' } }, strict: true, allowPositionals: false });
  if (values.help) { process.stdout.write('Usage: node scripts/package-desk-resource.mjs [--output-dir DIRECTORY]\nBuild the pinned offline Desk resource without installing or starting FreeCAD.\n'); return; }
  process.stdout.write(JSON.stringify(await packageDeskResource({ outputDirectory: values['output-dir'] }), null, 2) + '\n');
}
if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) main().catch(() => {
  process.stderr.write('Desk resource build failed; no unknown output was replaced. Inspect the chosen output directory before retrying.\n'); process.exitCode = 1;
});
