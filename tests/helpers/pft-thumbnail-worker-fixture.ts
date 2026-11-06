// SPDX-License-Identifier: Apache-2.0
/** Independently generated, neutral PNG fixtures; no files or CAD data. */
import { createHash } from 'node:crypto';
import { deflateSync } from 'node:zlib';

export const IDS = { workerId: '11111111-1111-4111-8111-111111111111', sessionId: '22222222-2222-4222-8222-222222222222', jobId: '33333333-3333-4333-8333-333333333333' };
export const INPUT_HASH = 'a'.repeat(64);
function chunk(kind: string, data = Buffer.alloc(0)) {
  const body = Buffer.concat([Buffer.from(kind), data]), output = Buffer.alloc(body.length + 8); output.writeUInt32BE(data.length); body.copy(output, 4);
  let crc = 0xffffffff;
  for (const value of body) { crc ^= value; for (let bit = 0; bit < 8; bit++) crc = crc & 1 ? (crc >>> 1) ^ 0xedb88320 : crc >>> 1; }
  output.writeUInt32BE((crc ^ 0xffffffff) >>> 0, output.length - 4); return output;
}
export function png(width = 2, height = 2, metadata = false) {
  const header = Buffer.alloc(13); header.writeUInt32BE(width); header.writeUInt32BE(height, 4); header[8] = 8; header[9] = 2;
  const row = width * 3 + 1, raw = Buffer.alloc(row * height);
  for (let offset = 0; offset < raw.length; offset++) raw[offset] = offset % row === 0 ? 0 : offset * 19 & 255;
  return Buffer.concat([Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]), chunk('IHDR', header),
    ...(metadata ? [chunk('tEXt', Buffer.from('Comment\0Synthetic fixture metadata'))] : []), chunk('IDAT', deflateSync(raw, { level: 0 })), chunk('IEND')]);
}
export function status() {
  return { schema_version: 1, worker_id: IDS.workerId, session_id: IDS.sessionId, state: 'ready', active_job_id: null, saves_cad: false,
    render_profile: 'native-isometric-white-v1', supported_file_scope: 'trusted-native-self-contained', jobs_retained: 20, max_jobs: 256,
    max_input_bytes: 67108864, max_png_bytes: 4194304, dimensions: { max_width: 1024, max_height: 1024, max_pixels: 1048576 } };
}
export function job(state = 'queued', image = png()) {
  return { schema_version: 1, worker_id: IDS.workerId, session_id: IDS.sessionId, job_id: IDS.jobId, state, phase: state, cancel_requested: state === 'cancelled',
    input_revision: state === 'queued' ? null : { sha256: INPUT_HASH, bytes: 1024 }, result: state === 'done' ? {
      png_base64: image.toString('base64'), sha256: createHash('sha256').update(image).digest('hex'), width: image.readUInt32BE(16), height: image.readUInt32BE(20),
      bytes: image.length, camera_restored: true, documents_closed: true, saved_cad: false, worker_reusable: true, render_profile: 'native-isometric-white-v1',
    } : null, error: state === 'failed' ? { code: 'OPEN_FAILED', message: 'Synthetic failure' } : null };
}
