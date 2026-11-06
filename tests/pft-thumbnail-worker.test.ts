// SPDX-License-Identifier: Apache-2.0
import { test, type TestContext } from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { sanitizePftPng, sanitizePftThumbnailWorkerPng } from '../src/pft-png.js';
import { PftFreeCadClient } from '../src/adapters/pft-freecad-client.js';
import { PFT_THUMBNAIL_WORKER_METHODS, pftDecodeThumbnailJob, pftDecodeThumbnailWorkerStatus, pftThumbnailJobInput, pftThumbnailRenderInput } from '../src/pft-thumbnail-worker.js';

import { IDS, INPUT_HASH, png, status, job } from './helpers/pft-thumbnail-worker-fixture.js';

const clone = <T>(value: T): T => structuredClone(value);

test('worker requests are explicit, bounded literal data; no inferred identity or file expansion', () => {
  const file = '/synthetic/quote" `$(literal)` file.FCStd';
  assert.deepEqual(pftThumbnailRenderInput({ ...IDS, file, expectedSha256: INPUT_HASH }), { ...IDS, file, expectedSha256: INPUT_HASH, width: 512, height: 512 });
  assert.deepEqual(pftThumbnailJobInput(IDS), IDS);
  for (const extra of [{ workerId: 'bad' }, { sessionId: '' }, { jobId: 'opaque' }, { file: 'relative.FCStd' }, { file: '/bad\u0000.FCStd' },
    { file: '/' + 'a'.repeat(4096) }, { expectedSha256: 'A'.repeat(64) }, { width: 0 }, { width: 1025 }, { height: 1.5 }, { width: false }, { recursive: true }]) {
    assert.throws(() => pftThumbnailRenderInput({ ...IDS, file, expectedSha256: INPUT_HASH, ...extra }), { code: 'INVALID_ARGUMENT' });
  }
  assert.throws(() => pftThumbnailJobInput({ ...IDS, file }), { code: 'INVALID_ARGUMENT' });
});

test('worker status validates the complete v1 limits and lifecycle without claiming UUID authentication', () => {
  assert.deepEqual(pftDecodeThumbnailWorkerStatus(status()), status());
  for (const patch of [{ schema_version: 2 }, { max_jobs: 257 }, { saves_cad: true }, { jobs_retained: 0 }, { state: 'busy' },
    { active_job_id: IDS.jobId }, { dimensions: { max_width: 2048, max_height: 1024, max_pixels: 1048576 } }, { native_access: true }]) {
    assert.throws(() => pftDecodeThumbnailWorkerStatus({ ...status(), ...patch }), { code: 'INVALID_RESPONSE' });
  }
  assert.equal(pftDecodeThumbnailWorkerStatus({ ...status(), state: 'quarantined' }).state, 'quarantined');
});

test('job validation binds all IDs, expected source hash, phase and terminal result state', () => {
  for (const state of ['queued', 'freezing', 'opening', 'rendering', 'closing', 'done', 'failed', 'cancelled']) {
    assert.equal(pftDecodeThumbnailJob(job(state), IDS).state, state);
  }
  for (const patch of [{ schema_version: 2 }, { worker_id: IDS.jobId }, { session_id: IDS.jobId }, { job_id: IDS.sessionId },
    { phase: 'closing' }, { error: { code: 'UNEXPECTED', message: 'Should not accompany queued' } }, { file: '/do-not-forward' }]) {
    assert.throws(() => pftDecodeThumbnailJob({ ...job(), ...patch }, IDS), { code: 'INVALID_RESPONSE' });
  }
  assert.throws(() => pftDecodeThumbnailJob(job('done'), { ...IDS, expectedSha256: 'b'.repeat(64) }), { code: 'INVALID_RESPONSE' });
  assert.throws(() => pftDecodeThumbnailJob({ ...job('done'), input_revision: null }, IDS), { code: 'INVALID_RESPONSE' });
  assert.throws(() => pftDecodeThumbnailJob({ ...job('failed'), error: null }, IDS), { code: 'INVALID_RESPONSE' });
});

test('worker PNG validates canonical base64, hash, dimensions, CRC, pixel stream and sanitization', () => {
  const valid = job('done'); assert.deepEqual(pftDecodeThumbnailJob(valid, IDS), valid);
  for (const [key, value] of [['sha256', 'b'.repeat(64)], ['width', 3], ['bytes', 1], ['camera_restored', false], ['documents_closed', false], ['saved_cad', true],
    ['worker_reusable', false], ['png_base64', valid.result!.png_base64 + '\n']]) {
    const changed = clone(valid); Object.assign(changed.result!, { [String(key)]: value });
    assert.throws(() => pftDecodeThumbnailJob(changed, IDS), { code: 'INVALID_RESPONSE' });
  }
  const broken = png(); broken[broken.length - 1] = broken[broken.length - 1]! ^ 1;
  assert.throws(() => pftDecodeThumbnailJob(job('done', broken), IDS), { code: 'INVALID_RESPONSE' });
  assert.throws(() => pftDecodeThumbnailJob(job('done', png(2, 2, true)), IDS), { code: 'INVALID_RESPONSE' });
  assert.throws(() => pftDecodeThumbnailJob(valid, { ...IDS, width: 3, height: 2 }), { code: 'INVALID_RESPONSE' });
});

test('the fixed worker PNG profile permits larger frames while existing PNG limits stay unchanged', () => {
  const image = png(1024, 1024); assert.ok(image.length > 1024 * 1024 && image.length < 4194304);
  assert.equal(sanitizePftThumbnailWorkerPng(image, 1024, 1024).bytes.length, image.length);
  assert.throws(() => sanitizePftPng(image), { code: 'PNG_TOO_LARGE' });
  assert.throws(() => sanitizePftThumbnailWorkerPng(png(1025, 1), 1025, 1), { code: 'PNG_TOO_LARGE' });
});

type Call = { method: string; params: Record<string, unknown> };
async function fixture(t: TestContext) {
  const calls: Call[] = [];
  const state: { methods: string[]; value: unknown; error?: string; large?: number } = { methods: [...PFT_THUMBNAIL_WORKER_METHODS, 'capture_view'], value: status() };
  const server = createServer(async (req, res) => {
    let text = ''; for await (const chunk of req) text += chunk;
    const call = JSON.parse(text) as Call; calls.push(call); res.setHeader('Content-Type', 'application/json');
    if (call.method === 'get_status') { res.end(JSON.stringify({ ok: true, data: { protocol_version: 1, supported_methods: state.methods } })); return; }
    if (state.large) { res.end(JSON.stringify({ ok: true, data: 'x'.repeat(state.large) })); return; }
    if (state.error) { res.end(JSON.stringify({ ok: false, error: { code: state.error, message: 'Synthetic worker envelope' } })); return; }
    res.end(JSON.stringify({ ok: true, data: state.value }));
  });
  await new Promise<void>(resolve => server.listen(0, '127.0.0.1', resolve));
  t.after(async () => { server.closeAllConnections(); await new Promise<void>(resolve => server.close(() => resolve())); });
  return { calls, state, client: new PftFreeCadClient({ url: `http://127.0.0.1:${(server.address() as { port: number }).port}/`, timeoutMs: 1000 }) };
}

test('typed adapter checks runtime methods, defaults parameters and preserves terminal/query/cancel semantics', async (t) => {
  const f = await fixture(t); assert.deepEqual(await f.client.getThumbnailWorkerStatus(), status());
  f.state.value = job();
  await f.client.renderDocumentThumbnail({ ...IDS, file: '/synthetic/sample.FCStd', expectedSha256: INPUT_HASH });
  assert.deepEqual(f.calls.at(-1), { method: 'render_document_thumbnail', params: { ...IDS, file: '/synthetic/sample.FCStd', expectedSha256: INPUT_HASH, width: 512, height: 512 } });
  f.state.value = job('cancelled'); assert.equal((await f.client.cancelThumbnailJob(IDS)).state, 'cancelled');
  f.state.value = job('done'); assert.equal((await f.client.getThumbnailJob(IDS)).state, 'done');
  const before = f.calls.length;
  await assert.rejects(f.client.renderDocumentThumbnail({ ...IDS, file: 'relative', expectedSha256: INPUT_HASH }), { code: 'INVALID_ARGUMENT' });
  assert.equal(f.calls.length, before);
  f.state.error = 'JOB_RESULT_EXPIRED'; await assert.rejects(f.client.getThumbnailJob(IDS), { code: 'JOB_RESULT_EXPIRED' });
  assert.equal(f.calls.at(-1)?.method, 'get_thumbnail_job');
  f.state.methods = []; const after = f.calls.length;
  await assert.rejects(f.client.getThumbnailJob(IDS), { code: 'UNSUPPORTED_CAPABILITY' });
  assert.equal(f.calls.length, after + 1); assert.equal(f.calls.at(-1)?.method, 'get_status');
});

test('worker HTTP budget is 6MiB; regular RPC remains 1MiB and ordinary capture remains 600KiB', async (t) => {
  const f = await fixture(t); f.state.value = job('done', png(1024, 1024));
  assert.equal((await f.client.getThumbnailJob(IDS)).result?.width, 1024);
  f.state.large = 6 * 1024 * 1024;
  await assert.rejects(f.client.getThumbnailJob(IDS), { code: 'RESPONSE_TOO_LARGE' });
  f.state.large = 1024 * 1024;
  await assert.rejects(f.client.captureView({ width: 800, height: 600 }), { code: 'RESPONSE_TOO_LARGE' });
  delete f.state.large;
  const capture = png(800, 260); assert.ok(capture.length > 600 * 1024 && capture.length * 4 / 3 < 1024 * 1024);
  f.state.value = { document: 'Synthetic', mime_type: 'image/png', data: capture.toString('base64'), width: 800, height: 260 };
  await assert.rejects(f.client.captureView({ width: 800, height: 260 }), { code: 'INVALID_RESPONSE' });
});
