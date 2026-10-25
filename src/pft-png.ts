// SPDX-License-Identifier: Apache-2.0
import { createHash } from 'node:crypto';
import { inflateSync } from 'node:zlib';

export type PftPngLimits = {
  maxInputBytes: number;
  maxOutputBytes: number;
  maxWidth: number;
  maxHeight: number;
  maxInflatedBytes: number;
  maxChunks: number;
};
export const PFT_PNG_LIMITS: Readonly<PftPngLimits> = Object.freeze({
  maxInputBytes: 4 * 1024 * 1024,
  maxOutputBytes: 1024 * 1024,
  maxWidth: 4096,
  maxHeight: 4096,
  maxInflatedBytes: 64 * 1024 * 1024,
  maxChunks: 4096,
});
export type PftPngOptions = {
  expectedWidth?: number;
  expectedHeight?: number;
  /** Each override may only tighten the default bound. */
  limits?: Partial<PftPngLimits>;
};
export type PftSanitizedPng = { bytes: Buffer; width: number; height: number; sha256: string };
export type PftPngErrorCode = 'PNG_INVALID' | 'PNG_TOO_LARGE' | 'PNG_UNSUPPORTED'
  | 'PNG_COLOR_UNSUPPORTED' | 'PNG_DIMENSIONS_MISMATCH' | 'PNG_INVALID_OPTIONS';
const messages: Record<PftPngErrorCode, string> = {
  PNG_INVALID: 'PNG data is invalid.',
  PNG_TOO_LARGE: 'PNG data exceeds the configured bounds.',
  PNG_UNSUPPORTED: 'Only non-interlaced 8-bit RGB or RGBA PNG is supported.',
  PNG_COLOR_UNSUPPORTED: 'PNG colour configuration is not supported.',
  PNG_DIMENSIONS_MISMATCH: 'PNG dimensions do not match the expected dimensions.',
  PNG_INVALID_OPTIONS: 'PNG validation options are invalid.',
};
export class PftPngError extends Error {
  constructor(readonly code: PftPngErrorCode) {
    super(messages[code]); this.name = 'PftPngError';
  }
}
function fail(code: PftPngErrorCode): never { throw new PftPngError(code); }
const SIGNATURE = Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]);
const colourChunks = new Set(['gAMA', 'cHRM', 'sRGB', 'sBIT', 'tRNS']);
const animationChunks = new Set(['acTL', 'fcTL', 'fdAT']);
const unsupportedColourChunks = new Set(['iCCP', 'cICP', 'mDCV', 'cLLI']);
const srgbChromaticities = [31270, 32900, 64000, 33000, 30000, 60000, 15000, 6000] as const;
const crcTable = Uint32Array.from({ length: 256 }, (_, index) => {
  let value = index;
  for (let bit = 0; bit < 8; bit++) value = value & 1 ? 0xedb88320 ^ value >>> 1 : value >>> 1;
  return value >>> 0;
});
function crc32(bytes: Buffer, start: number, end: number): number {
  let value = 0xffffffff;
  for (let index = start; index < end; index++) value = crcTable[(value ^ bytes[index]!) & 255]! ^ value >>> 8;
  return (value ^ 0xffffffff) >>> 0;
}
function options(value: PftPngOptions): { limits: PftPngLimits; expectedWidth?: number; expectedHeight?: number } {
  if (!value || typeof value !== 'object' || Array.isArray(value)
    || Object.keys(value).some(key => !['limits', 'expectedWidth', 'expectedHeight'].includes(key))) fail('PNG_INVALID_OPTIONS');
  const limits = { ...PFT_PNG_LIMITS };
  if (Object.hasOwn(value, 'limits')) {
    const supplied = value.limits;
    if (!supplied || typeof supplied !== 'object' || Array.isArray(supplied)) fail('PNG_INVALID_OPTIONS');
    for (const key of Object.keys(supplied)) {
      if (!Object.hasOwn(limits, key)) fail('PNG_INVALID_OPTIONS');
      const name = key as keyof PftPngLimits, bound = supplied[name];
      if (!Number.isSafeInteger(bound) || typeof bound !== 'number' || bound < 1 || bound > PFT_PNG_LIMITS[name]) fail('PNG_INVALID_OPTIONS');
      limits[name] = bound;
    }
  }
  const result: { limits: PftPngLimits; expectedWidth?: number; expectedHeight?: number } = { limits };
  for (const key of ['expectedWidth', 'expectedHeight'] as const) {
    if (Object.hasOwn(value, key)) {
      const dimension = value[key];
      if (typeof dimension !== 'number' || !Number.isSafeInteger(dimension) || dimension < 1) fail('PNG_INVALID_OPTIONS');
      result[key] = dimension;
    }
  }
  return result;
}
function validateColour(kind: string, data: Buffer, colour: number): void {
  if (kind === 'gAMA') {
    if (data.length !== 4 || data.readUInt32BE(0) === 0 || data.readUInt32BE(0) > 0x7fffffff) fail('PNG_INVALID');
  } else if (kind === 'cHRM') {
    if (data.length !== 32) fail('PNG_INVALID');
    for (let offset = 0; offset < 32; offset += 8) {
      const x = data.readUInt32BE(offset), y = data.readUInt32BE(offset + 4);
      if (x > 100000 || y > 100000 || x + y > 100000) fail('PNG_INVALID');
    }
  } else if (kind === 'sRGB') {
    if (data.length !== 1 || data[0]! > 3) fail('PNG_INVALID');
  } else if (kind === 'sBIT') {
    if (data.length !== (colour === 2 ? 3 : 4) || data.some(value => value < 1 || value > 8)) fail('PNG_INVALID');
  } else if (kind === 'tRNS') {
    if (colour !== 2 || data.length !== 6) fail('PNG_INVALID');
    for (let offset = 0; offset < 6; offset += 2) if (data.readUInt16BE(offset) > 255) fail('PNG_INVALID');
  } else fail('PNG_INVALID');
}
// Retained sRGB and fallback colour fields must agree in either chunk order.
// W3C PNG 3, 11.3.2.5/Table 17 supplies these fixed sRGB fallback values.
function validateSrgbColour(fields: ReadonlyMap<string, Buffer>): void {
  if (!fields.has('sRGB')) return;
  const gamma = fields.get('gAMA'), chromaticity = fields.get('cHRM');
  if (gamma && gamma.readUInt32BE(0) !== 45455) fail('PNG_INVALID');
  if (chromaticity && srgbChromaticities.some((value, index) => chromaticity.readUInt32BE(index * 4) !== value)) fail('PNG_INVALID');
}
function inflateFrame(compressed: Buffer, expected: number, rowBytes: number): void {
  let result: unknown;
  try { result = inflateSync(compressed, { info: true, maxOutputLength: expected }); }
  catch (error) {
    if (error && typeof error === 'object' && 'code' in error && error.code === 'ERR_BUFFER_TOO_LARGE') fail('PNG_TOO_LARGE');
    fail('PNG_INVALID');
  }
  // Node's info result exposes consumed compressed input. Reject a second zlib
  // stream or trailing compressed bytes rather than silently accepting either.
  if (!result || typeof result !== 'object' || !('buffer' in result) || !Buffer.isBuffer(result.buffer)
    || !('engine' in result) || !result.engine || typeof result.engine !== 'object'
    || !('bytesWritten' in result.engine) || result.engine.bytesWritten !== compressed.length
    || result.buffer.length !== expected) fail('PNG_INVALID');
  for (let start = 0; start < expected; start += rowBytes) if (result.buffer[start]! > 4) fail('PNG_INVALID');
}

/** Validate one bounded static PNG, remove ancillary metadata and retain exact
 * original IHDR/IDAT/IEND and validated colour/alpha chunks. This verifies format
 * and filtered pixel-stream structure, not the safety or privacy of image content.
 * ICC and unsupported PNG 3 colour/HDR fields are refused rather than removed
 * because dropping them could change the interpretation of the same pixels.
 * No files, CAD, image decoder process or business cache are accessed.
 */
function sanitize(input: Uint8Array, settings: PftPngOptions): PftSanitizedPng {
  const configured = options(settings), limits = configured.limits;
  if (!(input instanceof Uint8Array)) fail('PNG_INVALID');
  if (input.byteLength > limits.maxInputBytes) fail('PNG_TOO_LARGE');
  // Own the input and all returned bytes; neither aliases the caller's buffer.
  const bytes = Buffer.from(input);
  if (bytes.length < SIGNATURE.length || !bytes.subarray(0, SIGNATURE.length).equals(SIGNATURE)) fail('PNG_INVALID');
  const seen = new Set<string>(), fields = new Map<string, Buffer>(), kept: Buffer[] = [SIGNATURE], compressed: Buffer[] = [];
  let offset = SIGNATURE.length, chunks = 0, keptBytes = SIGNATURE.length;
  let width = 0, height = 0, colour = 0, rowBytes = 0, inflatedBytes = 0;
  let idatStarted = false, idatEnded = false, ended = false;
  while (offset < bytes.length) {
    if (++chunks > limits.maxChunks) fail('PNG_TOO_LARGE');
    if (bytes.length - offset < 12) fail('PNG_INVALID');
    const length = bytes.readUInt32BE(offset), typeStart = offset + 4, payloadStart = offset + 8;
    if (length > 0x7fffffff || length > bytes.length - offset - 12) fail('PNG_INVALID');
    const end = offset + length + 12, payloadEnd = payloadStart + length;
    const kind = bytes.toString('ascii', typeStart, payloadStart);
    if (!/^[A-Za-z]{4}$/.test(kind) || bytes[typeStart + 2]! & 32) fail('PNG_INVALID');
    // ASCII decoding masks high bits; check the raw type bytes as well.
    for (let index = typeStart; index < payloadStart; index++) {
      const value = bytes[index]!;
      if (!(value >= 65 && value <= 90 || value >= 97 && value <= 122)) fail('PNG_INVALID');
    }
    if (crc32(bytes, typeStart, payloadEnd) !== bytes.readUInt32BE(payloadEnd)) fail('PNG_INVALID');
    if (!seen.size && kind !== 'IHDR') fail('PNG_INVALID');
    if (kind !== 'IDAT' && idatStarted) idatEnded = true;
    const payload = bytes.subarray(payloadStart, payloadEnd);
    let retain = false;
    if (kind === 'IHDR') {
      if (seen.size || length !== 13) fail('PNG_INVALID');
      width = payload.readUInt32BE(0); height = payload.readUInt32BE(4); colour = payload[9]!;
      if (!width || !height || width > 0x7fffffff || height > 0x7fffffff) fail('PNG_INVALID');
      if (width > limits.maxWidth || height > limits.maxHeight) fail('PNG_TOO_LARGE');
      if (payload[8] !== 8 || ![2, 6].includes(colour) || payload[10] !== 0 || payload[11] !== 0 || payload[12] !== 0) fail('PNG_UNSUPPORTED');
      if (configured.expectedWidth !== undefined && width !== configured.expectedWidth
        || configured.expectedHeight !== undefined && height !== configured.expectedHeight) fail('PNG_DIMENSIONS_MISMATCH');
      rowBytes = width * (colour === 2 ? 3 : 4) + 1; inflatedBytes = rowBytes * height;
      if (!Number.isSafeInteger(inflatedBytes) || inflatedBytes > limits.maxInflatedBytes) fail('PNG_TOO_LARGE');
      retain = true;
    } else if (kind === 'PLTE') {
      if (seen.has(kind) || idatStarted || seen.has('tRNS') || length < 3 || length > 768 || length % 3) fail('PNG_INVALID');
      retain = true;
    } else if (kind === 'IDAT') {
      if (idatEnded) fail('PNG_INVALID');
      idatStarted = true; retain = true; compressed.push(payload);
    } else if (kind === 'IEND') {
      if (length || !idatStarted || end !== bytes.length) fail('PNG_INVALID');
      ended = true; retain = true;
    } else if (colourChunks.has(kind)) {
      if (seen.has(kind) || idatStarted || kind !== 'tRNS' && seen.has('PLTE')) fail('PNG_INVALID');
      validateColour(kind, payload, colour);
      fields.set(kind, payload); validateSrgbColour(fields); retain = true;
    } else if (unsupportedColourChunks.has(kind)) fail('PNG_COLOR_UNSUPPORTED');
    else if (animationChunks.has(kind) || !(bytes[typeStart]! & 32)) fail('PNG_INVALID');
    if (retain) {
      keptBytes += end - offset;
      if (keptBytes > limits.maxOutputBytes) fail('PNG_TOO_LARGE');
      kept.push(bytes.subarray(offset, end));
    }
    seen.add(kind); offset = end;
    if (ended) break;
  }
  if (!ended || !width || !height || !rowBytes) fail('PNG_INVALID');
  inflateFrame(Buffer.concat(compressed), inflatedBytes, rowBytes);
  const output = Buffer.concat(kept, keptBytes);
  return { bytes: output, width, height, sha256: createHash('sha256').update(output).digest('hex') };
}

/** Public boundary: unexpected input accessors or native errors never expose
 * arbitrary diagnostic text. See the format/content distinction above. */
export function sanitizePftPng(input: Uint8Array, settings: PftPngOptions = {}): PftSanitizedPng {
  try { return sanitize(input, settings); }
  catch (error) {
    if (error instanceof PftPngError && Object.hasOwn(messages, error.code)) throw new PftPngError(error.code);
    throw new PftPngError('PNG_INVALID');
  }
}
