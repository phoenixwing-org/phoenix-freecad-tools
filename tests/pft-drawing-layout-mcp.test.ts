// SPDX-License-Identifier: Apache-2.0
/** Formal SDK plus a synthetic loopback bridge. No FreeCAD, model, account or user data. */
import assert from 'node:assert/strict';
import { test, type TestContext } from 'node:test';
import { createServer as httpServer } from 'node:http';
import { Client, InMemoryTransport } from '@modelcontextprotocol/client';
import * as z from 'zod/v4';
import { createServer } from '../src/server.js';
import { PFT_DRAWING_DIMENSION_LAYOUT_CAPABILITY } from '../src/pft-drawing-layout-contract.js';
import { PFT_DRAWING_DIMENSIONS_CAPABILITY } from '../src/pft-drawing-dimensions.js';
import { PFT_PART_APPROVAL_CAPABILITY } from '../src/pft-part-approval.js';
import { neutralAutoInput, neutralAutoPreview } from './helpers/pft-drawing-layout-fixture.js';

const methods = ['prepare_part_changes', 'request_part_change_approval', 'get_part_change_status'];
const bodySchema = z.object({ method: z.string(), params: z.record(z.string(), z.unknown()) }).strict();
async function sdk(t: TestContext, scope: 'full' | 'chat') {
  const calls: z.output<typeof bodySchema>[] = [];
  const state: { cap: unknown; value: unknown } = { cap: PFT_DRAWING_DIMENSION_LAYOUT_CAPABILITY, value: neutralAutoPreview() };
  const bridge = httpServer(async (req, res) => {
    let text = ''; for await (const chunk of req) text += chunk;
    const decoded: unknown = JSON.parse(text); const body = bodySchema.parse(decoded); calls.push(body);
    res.setHeader('Content-Type', 'application/json');
    res.end(JSON.stringify({ ok: true, data: body.method === 'get_status' ? { protocol_version: 1, supported_methods: methods,
      part_approvals: PFT_PART_APPROVAL_CAPABILITY, drawing_dimensions: PFT_DRAWING_DIMENSIONS_CAPABILITY,
      ...(state.cap === undefined ? {} : { drawing_dimension_layout: state.cap }) } : state.value }));
  });
  await new Promise<void>(resolve => bridge.listen(0, '127.0.0.1', resolve));
  t.after(() => new Promise<void>(resolve => bridge.close(() => resolve())));
  const address = bridge.address(); assert(address && typeof address !== 'string');
  const server = createServer({ freecadUrl: `http://127.0.0.1:${address.port}/`, timeoutMs: 1000, toolScope: scope });
  const [left, right] = InMemoryTransport.createLinkedPair(); await server.connect(left);
  const client = new Client({ name: 'neutral-layout-sdk', version: '0.3.2' }); await client.connect(right); t.after(() => client.close());
  return { client, calls, state };
}
function errorCode(value: { structuredContent?: Record<string, unknown> }): unknown {
  const error = value.structuredContent?.error;
  return error !== null && typeof error === 'object' ? Reflect.get(error, 'code') : undefined;
}

test('full28/chat22正式SDK自动布局维持工具数，精确cap且混合意图只派发一次', async t => {
  for (const scope of ['full', 'chat'] as const) {
    const { client, calls } = await sdk(t, scope);
    const tools = (await client.listTools()).tools;
    assert.equal(tools.length, scope === 'full' ? 28 : 22);
    const tool = tools.find(item => item.name === 'prepare_part_changes')!;
    assert.equal(tool.inputSchema.additionalProperties, false);
    assert.match(JSON.stringify(tool.inputSchema), /placement/u); assert.match(JSON.stringify(tool.inputSchema), /xMm/u);
    const manifest = await client.callTool({ name: 'get_capabilities', arguments: {} });
    assert.deepEqual(manifest.structuredContent?.drawing_dimension_layout, PFT_DRAWING_DIMENSION_LAYOUT_CAPABILITY);
    assert.deepEqual(manifest.structuredContent?.drawing_dimensions, PFT_DRAWING_DIMENSIONS_CAPABILITY);
    assert.equal(calls.length, 0);
    const args = neutralAutoInput();
    const value = await client.callTool({ name: 'prepare_part_changes', arguments: args });
    assert.equal(value.isError, undefined); assert.deepEqual(value.structuredContent?.data, neutralAutoPreview());
    assert.deepEqual(calls.filter(call => call.method !== 'get_status'), [{ method: 'prepare_part_changes',
      params: { docName: args.document, objectNames: [args.page], viewName: args.view, operation: args.operation,
        dimensions: neutralAutoPreview().layout!.intent.dimensions } }]);
    const before = calls.length;
    for (const extra of [{ confirmed: true }, { layout: {} }, { dimensions: [{ ...args.dimensions[0], xMm: 0, yMm: 0 }] }])
      assert.equal((await client.callTool({ name: 'prepare_part_changes', arguments: { ...args, ...extra } })).isError, true);
    assert.equal(calls.length, before);
  }
});

test('正式SDK旧插件auto零业务派发、manual兼容；blocked/null与响应篡改不变成完成', async t => {
  for (const scope of ['full', 'chat'] as const) {
    const { client, calls, state } = await sdk(t, scope);
    state.cap = undefined;
    const rejected = await client.callTool({ name: 'prepare_part_changes', arguments: neutralAutoInput() });
    assert.equal(rejected.isError, true); assert.equal(errorCode(rejected), 'UNSUPPORTED_CAPABILITY');
    assert.equal(calls.filter(call => call.method !== 'get_status').length, 0);
    const { layout: _layout, ...manual } = neutralAutoPreview();
    state.value = manual;
    const manualArgs = { document: manual.document, operation: manual.operation, page: manual.design_request!.page,
      view: manual.design_request!.view, dimensions: manual.design_request!.dimensions.map(row =>
        ({ edge: row.edge, kind: row.kind, xMm: row.x_mm, yMm: row.y_mm })) };
    const manualResult = await client.callTool({ name: 'prepare_part_changes', arguments: manualArgs });
    assert.equal(manualResult.isError, undefined); assert.deepEqual(manualResult.structuredContent?.data, manual);
    state.cap = PFT_DRAWING_DIMENSION_LAYOUT_CAPABILITY; state.value = neutralAutoPreview('blocked');
    const blocked = await client.callTool({ name: 'prepare_part_changes', arguments: neutralAutoInput('blocked') });
    assert.equal(blocked.isError, undefined); assert.deepEqual(blocked.structuredContent?.data, state.value);
    const tampered = neutralAutoPreview(); tampered.design_request!.dimensions[0]!.x_mm += 1e-10; state.value = tampered;
    const before = calls.filter(call => call.method === 'prepare_part_changes').length;
    const invalid = await client.callTool({ name: 'prepare_part_changes', arguments: neutralAutoInput() });
    assert.equal(invalid.isError, true); assert.equal(errorCode(invalid), 'INVALID_RESPONSE');
    assert.equal(calls.filter(call => call.method === 'prepare_part_changes').length, before + 1);
    assert.equal(calls.some(call => ['apply_part_changes', 'execute_script', 'request_part_change_approval'].includes(call.method)), false);
  }
});
