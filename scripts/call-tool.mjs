#!/usr/bin/env node
// SPDX-License-Identifier: Apache-2.0
import { Client } from '@modelcontextprotocol/client';
import { StdioClientTransport } from '@modelcontextprotocol/client/stdio';
import { readFile, writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';

const [name, argumentsFile, imageOutput, ...extra] = process.argv.slice(2);
if (!name || !argumentsFile || extra.length) {
  console.error('用法：node scripts/call-tool.mjs <工具名> <参数JSON文件> [新建PNG输出文件]');
  process.exit(1);
}
const client = new Client({ name: 'phoenix-local-tool-client', version: '0.3.2' });
const transport = new StdioClientTransport({ command: process.execPath,
  args: [fileURLToPath(new URL('../dist/cli.js', import.meta.url))],
  env: { ...(process.env.PHOENIX_FREECAD_URL ? { PHOENIX_FREECAD_URL: process.env.PHOENIX_FREECAD_URL } : {}), ...(process.env.PHOENIX_TIMEOUT_MS ? { PHOENIX_TIMEOUT_MS: process.env.PHOENIX_TIMEOUT_MS } : {}), ...Object.fromEntries(['PHOENIX_FREECAD_TOOL_SCOPE', 'PHOENIX_DESK_NODE', 'PHOENIX_DESK_CLI', 'PHOENIX_DESK_STATE_DIR', 'PHOENIX_DESK_CHAT_READS'].filter(key => process.env[key] !== undefined).map(key => [key, process.env[key]])) }, stderr: 'inherit' });
try {
  const args = JSON.parse(await readFile(argumentsFile, 'utf8'));
  await client.connect(transport);
  const result = await client.callTool({ name, arguments: args });
  const images = result.content?.filter(block => block.type === 'image') ?? [];
  if (imageOutput && images.length) {
    if (images.length !== 1 || images[0].mimeType !== 'image/png') throw new Error('只能输出单张 PNG');
    await writeFile(imageOutput, Buffer.from(images[0].data, 'base64'), { flag: 'wx' });
  }
  console.log(JSON.stringify({ ...result, content: result.content?.filter(block => block.type !== 'image'), image_count: images.length, image_written: !!(imageOutput && images.length) }, null, 2));
  if (result.isError) process.exitCode = 1;
} catch (error) {
  console.error(JSON.stringify({ ok: false, message: error instanceof Error ? error.message : '调用失败' }));
  process.exitCode = 1;
} finally { await client.close(); }
