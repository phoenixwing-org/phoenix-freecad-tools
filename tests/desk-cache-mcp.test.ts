// SPDX-License-Identifier: Apache-2.0
import { test, type TestContext } from 'node:test';
import assert from 'node:assert/strict';
import { realpathSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { Client } from '@modelcontextprotocol/client';
import { StdioClientTransport } from '@modelcontextprotocol/client/stdio';
import { fixture, REL, SECOND, PRIVATE, hash, THUMBNAIL } from './helpers/desk-cache-fixture.js';
function record(value: unknown): Record<string, unknown> {
  assert.ok(value !== null && typeof value === 'object' && !Array.isArray(value)); return value as Record<string, unknown>;
}
async function connection(t: TestContext) {
  const f = await fixture(t);
  const client = new Client({ name: 'pft-neutral-cache-stdio', version: '0.3.2' });
  const transport = new StdioClientTransport({ command: realpathSync(process.execPath),
    args: [fileURLToPath(new URL('../dist/cli.js', import.meta.url))],
    env: { PHOENIX_FREECAD_URL: f.origin + '/', PHOENIX_FREECAD_TOOL_SCOPE: 'chat',
      PHOENIX_DESK_CHAT_READS: '1', PHOENIX_DESK_NODE: f.deskConfig.nodeExecutable,
      PHOENIX_DESK_CLI: f.deskConfig.cliEntry, PHOENIX_DESK_STATE_DIR: f.stateDir }, stderr: 'pipe' });
  await client.connect(transport); t.after(() => client.close()); return { f, client };
}

test('cached metadata and image use real chat-scope stdio, distinct provenance and one sanitized image payload', async t => {
  const { f, client } = await connection(t); f.setMode('explicit-missing');
  const metadata = await client.callTool({ name: 'desk_query_cached_metadata', arguments: { workspace: f.workspace, rels: [REL, SECOND] } });
  assert.equal(metadata.isError, undefined); assert.equal(record(metadata.structuredContent).source_kind, 'desk_saved_cache');
  const saved = record(record(metadata.structuredContent).data);
  const items = saved.items as Array<{ rel: string; thumbnail: { revision: string }; geometry: { box_mm: unknown } }>;
  assert.equal(items[1]!.geometry.box_mm, null); assert.equal(saved.native_memory, false);
  f.noPrivate(metadata); assert.ok(Buffer.byteLength(JSON.stringify(metadata)) <= 128 * 1024);
  f.setMode('normal');
  const image = await client.callTool({ name: 'desk_get_cached_thumbnail', arguments: { workspace: f.workspace, rel: REL, revision: items[0]!.thumbnail.revision } });
  assert.equal(image.isError, undefined); assert.equal(image.content.length, 2);
  const content = record(image.content[0]); assert.equal(content.type, 'image'); assert.equal(content.mimeType, 'image/png');
  const data = String(content.data), bytes = Buffer.from(data, 'base64'), value = record(image.structuredContent), details = record(value.data);
  assert.equal(value.source_kind, 'desk_saved_cache'); assert.equal(details.native_memory, false);
  assert.equal(details.sanitized_image_sha256, hash(bytes)); assert.equal(details.image_scope, 'saved_thumbnail_not_live_view');
  assert.equal(bytes.includes(Buffer.from(PRIVATE)), false); assert.equal('data' in details, false);
  assert.deepEqual(JSON.parse(String(record(image.content[1]).text)), value);
  assert.equal(JSON.stringify(image).split(data).length - 1, 1);
  assert.ok(bytes.length <= 600 * 1024); assert.ok(Buffer.byteLength(JSON.stringify(image)) <= 1024 * 1024);
  f.noPrivate(image); assert.equal(f.calls.filter(call => call.url.pathname === THUMBNAIL).length, 1);
});

test('real stdio cache failures retain fixed codes with zero image request on missing capability or changed revision', async t => {
  const { f, client } = await connection(t);
  for (const [mode, code] of [['feature-404', 'DESK_CACHE_FEATURE_UNSUPPORTED'], ['revision-changed', 'DESK_CACHE_CHANGED']] as const) {
    f.setMode(mode);
    const result = await client.callTool({ name: 'desk_get_cached_thumbnail', arguments: { workspace: f.workspace, rel: REL, revision: 'a'.repeat(64) } });
    assert.equal(result.isError, true); assert.equal(record(record(result.structuredContent).error).code, code); f.noPrivate(result);
  }
  assert.equal(f.calls.filter(call => call.url.pathname === THUMBNAIL).length, 0);
});
