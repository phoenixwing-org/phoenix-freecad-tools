// SPDX-License-Identifier: Apache-2.0
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { deflateSync, inflateSync } from 'node:zlib';
import { PFT_PNG_LIMITS, PftPngError, sanitizePftPng, type PftPngErrorCode, type PftPngOptions } from '../src/pft-png.js';

// Every image, chunk and diagnostic is constructed here; no disk, CAD, real
// image or account fixture is read. This slower bitwise CRC writer is independent
// from the production lookup-table validator.
const SIGNATURE = Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]);
function crc(bytes: Buffer): number {
  let value = -1;
  for (const byte of bytes) {
    value ^= byte;
    for (let bit = 0; bit < 8; bit++) value = (value >>> 1) ^ (value & 1 ? 0xedb88320 : 0);
  }
  return (value ^ -1) >>> 0;
}
function chunk(kind: string, payload: Buffer = Buffer.alloc(0)): Buffer {
  const body = Buffer.concat([Buffer.from(kind, 'latin1'), payload]), result = Buffer.alloc(payload.length + 12);
  result.writeUInt32BE(payload.length); body.copy(result, 4); result.writeUInt32BE(crc(body), result.length - 4); return result;
}
function header(width = 2, height = 2, colour = 2, depth = 8, compression = 0, filter = 0, interlace = 0): Buffer {
  const data = Buffer.alloc(13); data.writeUInt32BE(width); data.writeUInt32BE(height, 4);
  data.set([depth, colour, compression, filter, interlace], 8); return chunk('IHDR', data);
}
function image(parts: Buffer[]): Buffer { return Buffer.concat([SIGNATURE, ...parts]); }
function fixture(options: { width?: number; height?: number; colour?: 2 | 6; filters?: number[]; packed?: Buffer; before?: Buffer[]; after?: Buffer[] } = {}) {
  const width = options.width ?? 2, height = options.height ?? 2, colour = options.colour ?? 2;
  const rowBytes = 1 + width * (colour === 2 ? 3 : 4), raw = Buffer.alloc(rowBytes * height);
  for (let row = 0; row < height; row++) {
    raw[row * rowBytes] = options.filters?.[row] ?? 0;
    for (let index = 1; index < rowBytes; index++) raw[row * rowBytes + index] = (row * 29 + index * 7) & 255;
  }
  const packed = options.packed ?? deflateSync(raw), ihdr = header(width, height, colour);
  return { raw, packed, ihdr, bytes: image([ihdr, ...(options.before ?? []), chunk('IDAT', packed), ...(options.after ?? []), chunk('IEND')]) };
}
function chunks(bytes: Buffer): Array<{ kind: string; bytes: Buffer; payload: Buffer }> {
  const result = []; let offset = 8;
  while (offset < bytes.length) {
    const length = bytes.readUInt32BE(offset), end = offset + length + 12;
    result.push({ kind: bytes.toString('ascii', offset + 4, offset + 8), bytes: bytes.subarray(offset, end), payload: bytes.subarray(offset + 8, end - 4) });
    offset = end;
  }
  return result;
}
function rejects(bytes: Uint8Array, code: PftPngErrorCode, options?: PftPngOptions): void {
  assert.throws(() => sanitizePftPng(bytes, options), error => {
    assert.ok(error instanceof PftPngError); assert.equal(error.code, code);
    assert.equal(error.message, new PftPngError(code).message); assert.equal(error.cause, undefined); return true;
  });
}

test('accepts constructed RGB/RGBA and every legal scanline filter, returning its own bytes and digest', () => {
  assert.equal(chunk('IEND').readUInt32BE(8), 0xae426082);
  for (const colour of [2, 6] as const) {
    const source = fixture({ colour, height: 5, filters: [0, 1, 2, 3, 4] });
    const result = sanitizePftPng(source.bytes, { expectedWidth: 2, expectedHeight: 5 });
    assert.deepEqual(result.bytes, source.bytes); assert.equal(result.width, 2); assert.equal(result.height, 5);
    assert.equal(result.sha256, createHash('sha256').update(result.bytes).digest('hex'));
    const owned = Buffer.from(result.bytes); source.bytes.fill(0); assert.deepEqual(result.bytes, owned);
  }
});

test('strips text, compressed text, EXIF, time, physical dimensions and unknown ancillary data without changing pixel chunks', () => {
  const marker = 'independent-private-fixture-only', payload = Buffer.from(marker);
  const metadata = [chunk('tEXt', Buffer.from('Title\0' + marker)), chunk('zTXt', Buffer.concat([Buffer.from('Comment\0\0'), deflateSync(payload)])),
    chunk('iTXt', payload), chunk('eXIf', payload), chunk('tIME', Buffer.from([7, 234, 1, 1, 0, 0, 0])), chunk('pHYs', Buffer.alloc(9)), chunk('vpAg', payload)];
  const source = fixture({ before: metadata, after: [chunk('tEXt', payload)] }), result = sanitizePftPng(source.bytes);
  assert.deepEqual(chunks(result.bytes).map(c => c.kind), ['IHDR', 'IDAT', 'IEND']);
  const pixels = (data: Buffer) => chunks(data).filter(c => ['IHDR', 'IDAT', 'IEND'].includes(c.kind)).map(c => c.bytes);
  assert.deepEqual(pixels(result.bytes), pixels(source.bytes)); assert.equal(result.bytes.includes(payload), false);
  const idat = chunks(result.bytes).find(c => c.kind === 'IDAT')!;
  assert.deepEqual(inflateSync(idat.payload), source.raw);
});

test('retains exact validated colour, suggested palette and RGB transparency chunks', () => {
  const gamma = Buffer.alloc(4); gamma.writeUInt32BE(45455);
  const chromaticity = Buffer.alloc(32);
  [31270, 32900, 64000, 33000, 30000, 60000, 15000, 6000].forEach((value, index) => chromaticity.writeUInt32BE(value, index * 4));
  const before = [chunk('gAMA', gamma), chunk('cHRM', chromaticity), chunk('sRGB', Buffer.from([0])), chunk('sBIT', Buffer.from([8, 8, 8])),
    chunk('PLTE', Buffer.from([10, 20, 30])), chunk('tRNS', Buffer.from([0, 10, 0, 20, 0, 30]))];
  const source = fixture({ before }); assert.deepEqual(sanitizePftPng(source.bytes).bytes, source.bytes);
  const rgba = fixture({ colour: 6, before: [chunk('sBIT', Buffer.from([8, 8, 8, 8]))] });
  assert.deepEqual(sanitizePftPng(rgba.bytes).bytes, rgba.bytes);
});

test('keeps split contiguous and empty IDAT chunks exact and is idempotent', () => {
  const source = fixture(), middle = Math.floor(source.packed.length / 2);
  const bytes = image([source.ihdr, chunk('IDAT', source.packed.subarray(0, middle)), chunk('IDAT'), chunk('IDAT', source.packed.subarray(middle)), chunk('IEND')]);
  const result = sanitizePftPng(bytes); assert.deepEqual(result.bytes, bytes);
  const again = sanitizePftPng(result.bytes); assert.deepEqual(again, result);
});

test('validates CRC even on metadata that would otherwise be discarded', () => {
  for (const kind of ['IHDR', 'IDAT', 'IEND', 'tEXt']) {
    const source = fixture({ before: [chunk('tEXt', Buffer.from('synthetic'))] }), parts = chunks(source.bytes);
    const item = parts.find(c => c.kind === kind)!; item.bytes[item.bytes.length - 1] ^= 1;
    rejects(source.bytes, 'PNG_INVALID');
  }
});

test('rejects truncated signature/chunks/CRC and forged lengths without native errors', () => {
  const source = fixture().bytes;
  for (let size = 0; size < source.length; size++) rejects(source.subarray(0, size), 'PNG_INVALID');
  const forged = Buffer.from(source); forged.writeUInt32BE(0x7fffffff, 8); rejects(forged, 'PNG_INVALID');
  const huge = Buffer.from(source); huge.writeUInt32BE(0xffffffff, 8); rejects(huge, 'PNG_INVALID');
  const badSignature = Buffer.from(source); badSignature[0] = 0; rejects(badSignature, 'PNG_INVALID');
});

test('requires IHDR first, exactly one header and terminal IEND with no tail', () => {
  const f = fixture();
  for (const bytes of [image([chunk('tEXt'), f.ihdr, chunk('IDAT', f.packed), chunk('IEND')]),
    image([f.ihdr, f.ihdr, chunk('IDAT', f.packed), chunk('IEND')]), image([f.ihdr, chunk('IEND')]),
    image([f.ihdr, chunk('IDAT', f.packed)]), image([f.ihdr, chunk('IDAT', f.packed), chunk('IEND', Buffer.from([0]))]),
    Buffer.concat([f.bytes, Buffer.from([0])]), Buffer.concat([f.bytes, chunk('IEND')])]) rejects(bytes, 'PNG_INVALID');
});

test('rejects resumed IDAT, unknown critical chunks and animated PNG controls', () => {
  const f = fixture();
  rejects(image([f.ihdr, chunk('IDAT', f.packed), chunk('tEXt'), chunk('IDAT'), chunk('IEND')]), 'PNG_INVALID');
  for (const kind of ['ABCD', 'acTL', 'fcTL', 'fdAT']) rejects(fixture({ before: [chunk(kind)] }).bytes, 'PNG_INVALID');
});

test('rejects invalid raw chunk type bytes and the lowercase reserved bit', () => {
  for (const kind of ['a1Ag', 'abca', '\u00f6pAg']) rejects(fixture({ before: [chunk(kind)] }).bytes, 'PNG_INVALID');
});

test('refuses unsupported bit depths, colour models, compression, filtering and interlace', () => {
  const f = fixture();
  for (const ihdr of [header(2, 2, 2, 16), header(2, 2, 0), header(2, 2, 3), header(2, 2, 4),
    header(2, 2, 2, 8, 1), header(2, 2, 2, 8, 0, 1), header(2, 2, 2, 8, 0, 0, 1)]) {
    rejects(image([ihdr, chunk('IDAT', f.packed), chunk('IEND')]), 'PNG_UNSUPPORTED');
  }
});

test('rejects ICC without decompressing or echoing its profile', () => {
  const privateProfile = Buffer.from('independent-profile-only');
  const input = fixture({ before: [chunk('iCCP', privateProfile)] }).bytes;
  rejects(input, 'PNG_COLOR_UNSUPPORTED');
  assert.throws(() => sanitizePftPng(input), error => error instanceof PftPngError && !error.message.includes(privateProfile.toString()));
});

test('requires unique valid pre-IDAT colour chunks and the correct alpha shape', () => {
  const gamma = Buffer.alloc(4); gamma.writeUInt32BE(1000);
  const invalidBefore = [[chunk('gAMA')], [chunk('gAMA', Buffer.alloc(4))], [chunk('gAMA', gamma), chunk('gAMA', gamma)],
    [chunk('sRGB', Buffer.from([4]))], [chunk('sRGB', Buffer.from([0, 0]))], [chunk('cHRM', Buffer.alloc(31))],
    [chunk('sBIT', Buffer.from([0, 8, 8]))], [chunk('sBIT', Buffer.from([9, 8, 8]))], [chunk('sBIT', Buffer.from([8, 8]))],
    [chunk('PLTE', Buffer.alloc(2))], [chunk('PLTE', Buffer.alloc(769))], [chunk('PLTE', Buffer.alloc(3)), chunk('sRGB', Buffer.from([0]))],
    [chunk('tRNS', Buffer.alloc(6)), chunk('PLTE', Buffer.alloc(3))], [chunk('tRNS', Buffer.from([1, 0, 0, 0, 0, 0]))]];
  for (const before of invalidBefore) rejects(fixture({ before }).bytes, 'PNG_INVALID');
  rejects(fixture({ colour: 6, before: [chunk('tRNS', Buffer.alloc(6))] }).bytes, 'PNG_INVALID');
  rejects(fixture({ after: [chunk('gAMA', gamma)] }).bytes, 'PNG_INVALID');
  rejects(fixture({ after: [chunk('PLTE', Buffer.alloc(3))] }).bytes, 'PNG_INVALID');
  const badXy = Buffer.alloc(32); badXy.writeUInt32BE(70000); badXy.writeUInt32BE(40000, 4);
  rejects(fixture({ before: [chunk('cHRM', badXy)] }).bytes, 'PNG_INVALID');
});

test('rejects truncated, corrupt, short and trailing zlib streams, including a second stream', () => {
  const f = fixture();
  const corrupt = Buffer.from(f.packed); corrupt[corrupt.length - 1] ^= 1;
  for (const packed of [f.packed.subarray(0, -1), corrupt, deflateSync(f.raw.subarray(1)),
    Buffer.concat([f.packed, Buffer.from([0])]), Buffer.concat([f.packed, f.packed])]) rejects(fixture({ packed }).bytes, 'PNG_INVALID');
});

test('bounds inflated output before a compressed bomb or overlong pixel stream can be accepted', () => {
  const bomb = deflateSync(Buffer.alloc(2 * 1024 * 1024));
  rejects(fixture({ width: 1, height: 1, packed: bomb }).bytes, 'PNG_TOO_LARGE');
  const f = fixture(); rejects(fixture({ packed: deflateSync(Buffer.concat([f.raw, Buffer.from([0])])) }).bytes, 'PNG_TOO_LARGE');
  rejects(image([header(4096, 4096, 6), chunk('IDAT', Buffer.alloc(0)), chunk('IEND')]), 'PNG_TOO_LARGE');
});

test('checks each scanline filter rather than treating a successful inflate as enough', () => {
  for (const colour of [2, 6] as const) rejects(fixture({ colour, height: 3, filters: [0, 5, 0] }).bytes, 'PNG_INVALID');
});

test('enforces dimension bounds and optional expected dimensions without coercion', () => {
  const f = fixture();
  rejects(f.bytes, 'PNG_DIMENSIONS_MISMATCH', { expectedWidth: 3 });
  rejects(f.bytes, 'PNG_DIMENSIONS_MISMATCH', { expectedHeight: 3 });
  rejects(image([header(0, 2), chunk('IDAT', f.packed), chunk('IEND')]), 'PNG_INVALID');
  rejects(image([header(0x80000000, 2), chunk('IDAT', f.packed), chunk('IEND')]), 'PNG_INVALID');
  rejects(image([header(4097, 2), chunk('IDAT', f.packed), chunk('IEND')]), 'PNG_TOO_LARGE');
  rejects(image([header(2, 4097), chunk('IDAT', f.packed), chunk('IEND')]), 'PNG_TOO_LARGE');
});

test('allows each configured limit only to be tightened and applies every tighter bound', () => {
  const f = fixture();
  for (const limits of [{ maxInputBytes: f.bytes.length - 1 }, { maxOutputBytes: f.bytes.length - 1 },
    { maxWidth: 1 }, { maxHeight: 1 }, { maxInflatedBytes: f.raw.length - 1 }, { maxChunks: 2 }]) rejects(f.bytes, 'PNG_TOO_LARGE', { limits });
  assert.deepEqual(sanitizePftPng(f.bytes, { limits: { maxInputBytes: f.bytes.length, maxOutputBytes: f.bytes.length,
    maxWidth: 2, maxHeight: 2, maxInflatedBytes: f.raw.length, maxChunks: 3 } }).bytes, f.bytes);
  for (const [key, max] of Object.entries(PFT_PNG_LIMITS)) {
    for (const bad of [0, -1, 1.5, NaN, Infinity, max + 1, true, '1', undefined]) {
      rejects(f.bytes, 'PNG_INVALID_OPTIONS', { limits: { [key]: bad } as PftPngOptions['limits'] });
    }
  }
});

test('counts stripped chunks and rejects unknown or malformed options', () => {
  const f = fixture({ before: [chunk('tEXt')] }); rejects(f.bytes, 'PNG_TOO_LARGE', { limits: { maxChunks: 3 } });
  const malformed: unknown[] = [null, [], { extra: true }, { limits: undefined }, { limits: null }, { limits: [] },
    { limits: { unknown: 1 } }, { expectedWidth: 0 }, { expectedHeight: 1.5 }, { expectedWidth: '2' }, { expectedHeight: undefined }];
  for (const value of malformed) rejects(f.bytes, 'PNG_INVALID_OPTIONS', value as PftPngOptions);
  rejects('not-bytes' as unknown as Uint8Array, 'PNG_INVALID');
});

test('keeps independent input and consumer output budgets distinct', () => {
  const marker = Buffer.alloc(2 * 1024 * 1024, 65), f = fixture({ before: [chunk('tEXt', marker)] });
  assert.ok(f.bytes.length > PFT_PNG_LIMITS.maxOutputBytes);
  assert.deepEqual(sanitizePftPng(f.bytes).bytes, fixture().bytes);
  rejects(Buffer.alloc(PFT_PNG_LIMITS.maxInputBytes + 1), 'PNG_TOO_LARGE');
  // A valid uncompressed RGB image fits the input bound but exceeds the explicit
  // one-MiB consumer output budget even without any removable metadata.
  const raw = Buffer.alloc((1024 * 3 + 1) * 400), packed = deflateSync(raw, { level: 0 });
  rejects(image([header(1024, 400), chunk('IDAT', packed), chunk('IEND')]), 'PNG_TOO_LARGE');
});

test('unexpected input accessors cannot leak arbitrary diagnostics through the public error', () => {
  const diagnostic = '/private/synthetic/fixture-only';
  const settings = Object.defineProperty({}, 'limits', { enumerable: true, get() { throw new Error(diagnostic); } });
  rejects(fixture().bytes, 'PNG_INVALID', settings);
  assert.throws(() => sanitizePftPng(fixture().bytes, settings), error =>
    error instanceof PftPngError && !error.message.includes(diagnostic) && error.cause === undefined);
});


test('retains consistent sRGB fallback fields in either order and valid independent colour fields', () => {
  const gamma = Buffer.alloc(4); gamma.writeUInt32BE(45455);
  const chromaticity = Buffer.alloc(32);
  [31270, 32900, 64000, 33000, 30000, 60000, 15000, 6000].forEach((value, index) => chromaticity.writeUInt32BE(value, index * 4));
  const srgb = chunk('sRGB', Buffer.from([0])), g = chunk('gAMA', gamma), c = chunk('cHRM', chromaticity);
  for (const before of [[g, c, srgb], [srgb, g, c], [g, srgb, c], [c, srgb, g], [g, srgb], [srgb, g], [c, srgb], [srgb, c], [srgb]]) {
    const f = fixture({ before }); assert.deepEqual(sanitizePftPng(f.bytes).bytes, f.bytes);
  }
  gamma.writeUInt32BE(100000);
  [34567, 35850, 64000, 33000, 21000, 71000, 15000, 6000].forEach((value, index) => chromaticity.writeUInt32BE(value, index * 4));
  for (const before of [[chunk('gAMA', gamma)], [chunk('cHRM', chromaticity)], [chunk('gAMA', gamma), chunk('cHRM', chromaticity)]]) {
    const f = fixture({ before }); assert.deepEqual(sanitizePftPng(f.bytes).bytes, f.bytes);
  }
});

test('rejects conflicting sRGB gamma or chromaticity in both chunk orders', () => {
  const srgb = chunk('sRGB', Buffer.from([3]));
  for (const gammaValue of [45454, 45456, 100000]) {
    const gamma = Buffer.alloc(4); gamma.writeUInt32BE(gammaValue);
    for (const before of [[chunk('gAMA', gamma), srgb], [srgb, chunk('gAMA', gamma)]]) rejects(fixture({ before }).bytes, 'PNG_INVALID');
  }
  for (let changed = 0; changed < 8; changed++) {
    const data = Buffer.alloc(32);
    [31270, 32900, 64000, 33000, 30000, 60000, 15000, 6000].forEach((value, index) => data.writeUInt32BE(value + (index === changed ? 1 : 0), index * 4));
    for (const before of [[chunk('cHRM', data), srgb], [srgb, chunk('cHRM', data)]]) rejects(fixture({ before }).bytes, 'PNG_INVALID');
  }
});

test('refuses unsupported PNG 3 colour and HDR interpretation instead of stripping it', () => {
  const fields = [chunk('cICP', Buffer.from([0x0c, 0x0d, 0, 1])), chunk('mDCV', Buffer.alloc(24)), chunk('cLLI', Buffer.alloc(8))];
  for (const field of fields) {
    rejects(fixture({ before: [field] }).bytes, 'PNG_COLOR_UNSUPPORTED');
    rejects(fixture({ before: [chunk('sRGB', Buffer.from([0])), field] }).bytes, 'PNG_COLOR_UNSUPPORTED');
  }
});


test('keeps gAMA within the PNG unsigned four-byte integer range', () => {
  const gamma = Buffer.alloc(4);
  for (const value of [0, 0x80000000, 0xffffffff]) {
    gamma.writeUInt32BE(value); rejects(fixture({ before: [chunk('gAMA', gamma)] }).bytes, 'PNG_INVALID');
  }
  gamma.writeUInt32BE(0x7fffffff);
  const valid = fixture({ before: [chunk('gAMA', gamma)] }); assert.deepEqual(sanitizePftPng(valid.bytes).bytes, valid.bytes);
});
