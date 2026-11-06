#!/usr/bin/env node
// SPDX-License-Identifier: Apache-2.0
import { Client } from '@modelcontextprotocol/client';
import { StdioClientTransport } from '@modelcontextprotocol/client/stdio';
import { fileURLToPath } from 'node:url';

const args = process.argv.slice(2);
if (args.length !== 2 || args[0] !== '--document' || !args[1]) {
  console.error('用法：npm run verify:mcp -- --document <已打开的公开样例文档内部Name>'); process.exit(1);
}
const document = args[1];
const client = new Client({ name: 'phoenix-study-verifier', version: '0.1.0' });
const transport = new StdioClientTransport({
  command: process.execPath, args: [fileURLToPath(new URL('../dist/cli.js', import.meta.url))],
  env: { ...(process.env.PHOENIX_FREECAD_URL ? { PHOENIX_FREECAD_URL: process.env.PHOENIX_FREECAD_URL } : {}) }, stderr: 'inherit',
});
try {
  await client.connect(transport);
  const tools = (await client.listTools()).tools.map(tool => tool.name);
  const call = async (name, arguments_ = {}) => {
    const result = await client.callTool({ name, arguments: arguments_ });
    if (result.isError) throw new Error(JSON.stringify(result.structuredContent ?? result.content));
    if (!result.structuredContent?.ok) throw new Error('MCP 返回缺少成功状态');
    return result.structuredContent.data;
  };
  const status = await call('get_connection_status');
  const documents = await call('list_documents');
  if (!Array.isArray(documents) || !documents.includes(document)) throw new Error('指定样例文档尚未打开');
  const axonometric = await call('set_axonometric_view', { document, fit: false });
  const fit = await call('fit_view', { document });
  console.log(JSON.stringify({ ok: true, protocol: 'MCP stdio → Node.js → JSON HTTP → FreeCAD GUI', tools, status, sample_document: document, axonometric, fit }, null, 2));
} catch (error) {
  console.error(JSON.stringify({ ok: false, message: error instanceof Error ? error.message : '验证失败' })); process.exitCode = 1;
} finally { await client.close(); }
