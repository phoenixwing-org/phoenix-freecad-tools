// SPDX-License-Identifier: Apache-2.0
// Release-only verification; deliberately outside the distributed scripts/ directory.
import fs from 'node:fs';
import path from 'node:path';
import { createHash } from 'node:crypto';
import { fileURLToPath } from 'node:url';

export const NPM_PACKAGE = '@phoenix-wing/phoenix-freecad-mcp';
const ROOT = fileURLToPath(new URL('../../', import.meta.url));
const SUMS = 'SHA256SUMS';
const MAX_ASSET = 64 * 1024 * 1024;
const fail = message => { throw new Error(message); };
const record = value => value !== null && typeof value === 'object' && !Array.isArray(value);
const exact = (value, keys) => record(value) && Object.keys(value).sort().join('\0') === [...keys].sort().join('\0');
const sameNames = (actual, expected) => actual.length === expected.length && [...actual].sort().join('\0') === [...expected].sort().join('\0');
const versionOK = value => typeof value === 'string' && value.length <= 32 && /^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?![\s\S])/u.test(value);

export function releaseAssetNames(version) {
  if (!versionOK(version)) fail('Invalid release version');
  return [`phoenix-freecad-tools-addon-${version}.zip`, `phoenix-wing-phoenix-freecad-mcp-${version}.tgz`];
}

export function validateReleaseManifest(packageMetadata, manifest, tag) {
  if (!record(packageMetadata) || packageMetadata.name !== NPM_PACKAGE || !versionOK(packageMetadata.version)) fail('Release package identity mismatch');
  const version = packageMetadata.version;
  if (tag !== `v${version}`) fail('Release tag does not match package version');
  if (!exact(manifest, ['schema_version', 'version', 'npm_package', 'assets']) || manifest.schema_version !== 1 || manifest.version !== version || manifest.npm_package !== NPM_PACKAGE) fail('Release manifest identity mismatch');
  const names = releaseAssetNames(version);
  if (!Array.isArray(manifest.assets) || manifest.assets.length !== names.length) fail('Release manifest must contain exactly two assets');
  const assets = names.map(name => {
    const matches = manifest.assets.filter(asset => record(asset) && asset.name === name);
    if (matches.length !== 1) fail('Release manifest asset set mismatch');
    const asset = matches[0];
    if (!exact(asset, ['name', 'sha256']) || typeof asset.sha256 !== 'string' || asset.sha256.length !== 64 || !/^[0-9a-f]{64}$/u.test(asset.sha256)) fail('Invalid release asset checksum');
    return Object.freeze({ name, sha256: asset.sha256 });
  });
  return Object.freeze({ version, tag, npm_package: NPM_PACKAGE, assets: Object.freeze(assets) });
}

function regularBytes(filename, maximum) {
  const before = fs.lstatSync(filename);
  if (!before.isFile() || before.isSymbolicLink() || before.nlink !== 1 || before.size <= 0 || before.size > maximum) fail('Release input must be a bounded regular file');
  const fd = fs.openSync(filename, fs.constants.O_RDONLY | (fs.constants.O_NOFOLLOW ?? 0));
  try {
    const opened = fs.fstatSync(fd);
    if (opened.dev !== before.dev || opened.ino !== before.ino || opened.size !== before.size) fail('Release input changed');
    const data = fs.readFileSync(fd);
    const after = fs.fstatSync(fd), current = fs.lstatSync(filename);
    if (data.length !== before.size || !after.isFile() || after.nlink !== 1 || after.size !== before.size || after.mtimeMs !== before.mtimeMs || after.ctimeMs !== before.ctimeMs || current.dev !== before.dev || current.ino !== before.ino || current.isSymbolicLink()) fail('Release input changed');
    return data;
  } finally { fs.closeSync(fd); }
}

function jsonFile(filename) {
  return JSON.parse(regularBytes(filename, 256 * 1024).toString('utf8'));
}

export function verifyReleaseAssets({ packageMetadata, manifest, tag, assetsDirectory, writeChecksums = false }) {
  const release = validateReleaseManifest(packageMetadata, manifest, tag);
  const directory = fs.lstatSync(assetsDirectory);
  if (!directory.isDirectory() || directory.isSymbolicLink()) fail('Release asset directory must be real');
  const names = release.assets.map(asset => asset.name), entries = fs.readdirSync(assetsDirectory);
  const withSums = [...names, SUMS];
  const hasSums = entries.includes(SUMS);
  if (!sameNames(entries, hasSums ? withSums : names) || (!writeChecksums && !hasSums)) fail('Release directory asset set mismatch');
  for (const asset of release.assets) {
    const bytes = regularBytes(path.join(assetsDirectory, asset.name), MAX_ASSET);
    if (createHash('sha256').update(bytes).digest('hex') !== asset.sha256) fail('Release asset checksum mismatch');
  }
  const checksums = release.assets.map(asset => `${asset.sha256}  ${asset.name}\n`).join('');
  const sumsPath = path.join(assetsDirectory, SUMS);
  if (hasSums) {
    if (!regularBytes(sumsPath, 4096).equals(Buffer.from(checksums))) fail('Release checksum file mismatch');
  } else {
    // Never replace an existing checksum file. A failed/uncertain write is retained.
    fs.writeFileSync(sumsPath, checksums, { flag: 'wx', mode: 0o644 });
    if (!regularBytes(sumsPath, 4096).equals(Buffer.from(checksums))) fail('Release checksum publication unverified');
  }
  if (!sameNames(fs.readdirSync(assetsDirectory), withSums)) fail('Release directory changed');
  return release;
}

export function verifyRemoteRelease(value, release, target, previous = undefined) {
  const validID = value => Number.isSafeInteger(value) && value > 0;
  if (typeof target !== 'string' || target.length !== 40 || !/^[0-9a-f]{40}$/u.test(target)) fail('Invalid release commit');
  if (!record(value) || !validID(value.id) || value.tag_name !== release.tag || value.target_commitish !== target || value.draft !== true || value.prerelease !== false) fail('Remote draft identity mismatch');
  if (previous !== undefined && (!record(previous) || previous.id !== value.id)) fail('Remote release identity changed');
  if (!Array.isArray(value.assets) || value.assets.length !== 3 || !sameNames(value.assets.map(asset => asset?.name), [...release.assets.map(asset => asset.name), SUMS])) fail('Remote release asset set mismatch');
  const ids = new Set();
  for (const asset of value.assets) {
    if (!record(asset) || !validID(asset.id) || ids.has(asset.id) || asset.state !== 'uploaded' || !Number.isSafeInteger(asset.size) || asset.size <= 0 || asset.size > MAX_ASSET) fail('Remote release asset not complete');
    if (previous !== undefined) {
      const before = previous.assets?.find(item => item.name === asset.name);
      if (!before || before.id !== asset.id || before.size !== asset.size) fail('Remote release asset identity changed');
    }
    ids.add(asset.id);
  }
  return value.id;
}

function main(args) {
  const values = {}, switches = new Set(['--metadata-only', '--write-checksums']);
  const options = new Set(['--tag', '--assets-dir', '--remote-metadata', '--same-release-as', '--target']);
  for (let index = 0; index < args.length; index++) {
    const key = args[index];
    if (!(switches.has(key) || options.has(key)) || Object.hasOwn(values, key)) fail('Invalid release verification arguments');
    if (switches.has(key)) values[key] = true;
    else {
      const value = args[++index];
      if (!value || value.startsWith('--')) fail('Missing release verification argument');
      values[key] = value;
    }
  }
  if (!values['--tag']) fail('Release tag is required');
  const packageMetadata = jsonFile(path.join(ROOT, 'package.json'));
  if (!versionOK(packageMetadata.version)) fail('Invalid release version');
  const manifest = jsonFile(path.join(ROOT, '.github', 'releases', `${packageMetadata.version}.json`));
  const release = validateReleaseManifest(packageMetadata, manifest, values['--tag']);
  if (values['--metadata-only']) {
    if (Object.keys(values).length !== 2) fail('Metadata-only verification cannot publish or inspect assets');
  } else {
    if (!values['--assets-dir']) fail('Release asset directory is required');
    verifyReleaseAssets({ packageMetadata, manifest, tag: values['--tag'], assetsDirectory: values['--assets-dir'], writeChecksums: values['--write-checksums'] === true });
    if (values['--remote-metadata']) {
      const previous = values['--same-release-as'] ? jsonFile(values['--same-release-as']) : undefined;
      if (previous) verifyRemoteRelease(previous, release, values['--target']);
      verifyRemoteRelease(jsonFile(values['--remote-metadata']), release, values['--target'], previous);
    } else if (values['--same-release-as'] || values['--target']) fail('Remote metadata is required');
  }
  process.stdout.write(JSON.stringify({ version: release.version, tag: release.tag, npm_package: release.npm_package, assets: release.assets }) + '\n');
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try { main(process.argv.slice(2)); }
  catch (error) { process.stderr.write(`Release verification failed: ${error instanceof Error ? error.message : 'unknown error'}\n`); process.exitCode = 1; }
}
