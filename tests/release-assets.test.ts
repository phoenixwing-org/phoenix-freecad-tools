// SPDX-License-Identifier: Apache-2.0
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { createHash } from 'node:crypto';
import test, { type TestContext } from 'node:test';
import { releaseAssetNames, validateReleaseManifest, verifyReleaseAssets, verifyRemoteRelease } from '../.github/scripts/verify-release.mjs';

const packageMetadata = { name: '@phoenix-wing/phoenix-freecad-mcp', version: '0.3.2' };
const tag = 'v0.3.2';
const hash = (bytes: string | Buffer) => createHash('sha256').update(bytes).digest('hex');
function fixture(t: TestContext) {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'pft-release-assets-'));
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  const names = releaseAssetNames('0.3.2');
  const files = ['independent addon fixture\n', 'independent scoped package fixture\n'];
  names.forEach((name: string, i: number) => fs.writeFileSync(path.join(directory, name), files[i]!));
  const manifest = { schema_version: 1, version: '0.3.2', npm_package: packageMetadata.name,
    assets: names.map((name: string, i: number) => ({ name, sha256: hash(files[i]!) })) };
  return { directory, names, manifest, check: (writeChecksums = true) => verifyReleaseAssets({ packageMetadata, manifest, tag, assetsDirectory: directory, writeChecksums }) };
}

test('approved package bytes generate exact checksums and repeated checks never rewrite', t => {
  const f = fixture(t), result = f.check();
  assert.equal(result.version, '0.3.2'); assert.equal(result.npm_package, packageMetadata.name);
  const filename = path.join(f.directory, 'SHA256SUMS');
  const expected = f.manifest.assets.map((asset: { name: string; sha256: string }) => `${asset.sha256}  ${asset.name}\n`).join('');
  assert.equal(fs.readFileSync(filename, 'utf8'), expected);
  const before = fs.statSync(filename); f.check(); f.check(false);
  const after = fs.statSync(filename);
  assert.equal(before.ino, after.ino); assert.equal(before.mtimeMs, after.mtimeMs);
  assert.deepEqual(fs.readdirSync(f.directory).sort(), [...f.names, 'SHA256SUMS'].sort());
});

test('version tag and npm organization identity are independent mandatory gates', t => {
  const f = fixture(t);
  for (const wrong of ['0.3.2', 'v0.3.3', 'v0.3.2\n', 'v0.3.2-beta']) assert.throws(() => validateReleaseManifest(packageMetadata, f.manifest, wrong));
  assert.throws(() => validateReleaseManifest({ ...packageMetadata, name: 'phoenix-freecad-mcp' }, f.manifest, tag));
  assert.throws(() => validateReleaseManifest(packageMetadata, { ...f.manifest, npm_package: 'phoenix-freecad-mcp' }, tag));
  assert.throws(() => validateReleaseManifest(packageMetadata, { ...f.manifest, version: '0.3.3' }, tag));
  for (const version of ['00.3.2', '0.3.2\n', '0.3.2/other', '0.3.2;false']) assert.throws(() => releaseAssetNames(version));
});

test('manifest is exactly the Addon zip and scoped tgz with canonical complete hashes', t => {
  const f = fixture(t), copy = () => structuredClone(f.manifest);
  const missing = copy(); missing.assets.pop(); assert.throws(() => validateReleaseManifest(packageMetadata, missing, tag));
  const duplicate = copy(); duplicate.assets[1] = duplicate.assets[0]; assert.throws(() => validateReleaseManifest(packageMetadata, duplicate, tag));
  const legacy = copy(); legacy.assets[1]!.name = 'phoenix-freecad-mcp-0.3.2.tgz'; assert.throws(() => validateReleaseManifest(packageMetadata, legacy, tag));
  const escape = copy(); escape.assets[0]!.name = '../source.zip'; assert.throws(() => validateReleaseManifest(packageMetadata, escape, tag));
  for (const sha256 of ['0'.repeat(63), 'A'.repeat(64), '0'.repeat(64) + '\n']) {
    const invalid = copy(); invalid.assets[0]!.sha256 = sha256;
    assert.throws(() => validateReleaseManifest(packageMetadata, invalid, tag));
  }
  assert.throws(() => validateReleaseManifest(packageMetadata, { ...f.manifest, source_zip: 'not allowed' }, tag));
});

test('missing, extra, or mutated assets fail without producing checksums', t => {
  const f = fixture(t), first = path.join(f.directory, f.names[0]!);
  const bytes = fs.readFileSync(first); fs.unlinkSync(first); assert.throws(() => f.check());
  fs.writeFileSync(first, bytes); fs.writeFileSync(path.join(f.directory, 'unexpected.txt'), 'extra'); assert.throws(() => f.check());
  fs.unlinkSync(path.join(f.directory, 'unexpected.txt')); fs.appendFileSync(first, 'changed'); assert.throws(() => f.check());
  assert.equal(fs.existsSync(path.join(f.directory, 'SHA256SUMS')), false);
});

test('missing or modified SHA256SUMS is rejected by readback and never clobbered', t => {
  const f = fixture(t); assert.throws(() => f.check(false)); f.check();
  const filename = path.join(f.directory, 'SHA256SUMS'); fs.writeFileSync(filename, 'retain this invalid checksum file\n');
  assert.throws(() => f.check()); assert.throws(() => f.check(false));
  assert.equal(fs.readFileSync(filename, 'utf8'), 'retain this invalid checksum file\n');
});

test('symlink, extra hardlink, and directory substitutes are rejected', t => {
  const f = fixture(t), first = path.join(f.directory, f.names[0]!), other = path.join(f.directory, f.names[1]!);
  const bytes = fs.readFileSync(first); fs.unlinkSync(first); fs.symlinkSync(other, first); assert.throws(() => f.check());
  fs.unlinkSync(first); fs.writeFileSync(first, bytes); const linked = path.join(f.directory, 'linked'); fs.linkSync(first, linked);
  // Remove the extra name from the candidate directory while retaining its link.
  const outside = f.directory + '-link'; fs.renameSync(linked, outside); t.after(() => fs.rmSync(outside, { force: true }));
  assert.throws(() => f.check()); fs.unlinkSync(outside); fs.unlinkSync(first); fs.mkdirSync(first); assert.throws(() => f.check());
});

function remote(release: ReturnType<typeof validateReleaseManifest>) {
  return { id: 1, tag_name: tag, target_commitish: 'a'.repeat(40), draft: true, prerelease: false,
    assets: [...release.assets.map((asset: { name: string }) => asset.name), 'SHA256SUMS'].map((name, i) => ({ id: 10 + i, name, state: 'uploaded', size: 100 })) };
}

test('only the newly checked same draft with complete exact assets can proceed', t => {
  const f = fixture(t), release = f.check(), first = remote(release), target = 'a'.repeat(40);
  assert.equal(verifyRemoteRelease(first, release, target), 1);
  assert.equal(verifyRemoteRelease(structuredClone(first), release, target, first), 1);
  for (const changed of [{ ...first, id: 2 }, { ...first, draft: false }, { ...first, tag_name: 'v0.3.3' }, { ...first, target_commitish: 'b'.repeat(40) }, { ...first, prerelease: true }]) {
    assert.throws(() => verifyRemoteRelease(changed, release, target, first));
  }
  const extra = structuredClone(first); extra.assets.push({ id: 99, name: 'extra.txt', state: 'uploaded', size: 1 }); assert.throws(() => verifyRemoteRelease(extra, release, target));
  const uploading = structuredClone(first); uploading.assets[0]!.state = 'new'; assert.throws(() => verifyRemoteRelease(uploading, release, target));
  const replaced = structuredClone(first); replaced.assets[0]!.id = 99; assert.throws(() => verifyRemoteRelease(replaced, release, target, first));
});
