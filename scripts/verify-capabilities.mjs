#!/usr/bin/env node
// SPDX-License-Identifier: Apache-2.0
// Explicit smoke test of the existing public demo. Changes only its camera;
// never creates, recomputes, saves, or closes a FreeCAD document.
import { Client } from '@modelcontextprotocol/client';
import { StdioClientTransport } from '@modelcontextprotocol/client/stdio';
import { lstat, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const DOCUMENT = 'PhoenixParametricDemo';
const PAGE = 'PhoenixDemoPage';
const TOOLS = ['get_capabilities', 'get_connection_status', 'list_documents', 'fit_view', 'set_axonometric_view',
  'get_document_info', 'list_objects', 'get_object_info', 'get_selection', 'measure_geometry',
  'list_drawing_pages', 'inspect_drawing_page', 'set_view', 'capture_view', 'execute_script', 'get_script_status', 'set_selection', 'set_visibility', 'recompute_document', 'prepare_part_changes', 'apply_part_changes'];
const TIMEOUT = { timeout: 20_000, maxTotalTimeout: 20_000 };
const USAGE = '用法：node scripts/verify-capabilities.mjs [--image <新PNG文件>] [--report <新JSON文件>]';

class VerificationError extends Error {
  constructor(code, message) { super(message); this.code = code; }
}
function requireThat(condition, message) {
  if (!condition) throw new VerificationError('VERIFICATION_FAILED', message);
}
const isRecord = value => value !== null && typeof value === 'object' && !Array.isArray(value);
const near = (value, expected, tolerance = 1e-6) => typeof value === 'number' && Number.isFinite(value) && Math.abs(value - expected) <= tolerance;

function options(args) {
  const result = {};
  for (let index = 0; index < args.length; index++) {
    const flag = args[index];
    if (!['--image', '--report'].includes(flag) || result[flag.slice(2)] !== undefined
      || !args[index + 1] || args[index + 1].startsWith('--')) {
      throw new VerificationError('INVALID_ARGUMENTS', USAGE);
    }
    result[flag.slice(2)] = resolve(args[++index]);
  }
  requireThat(!result.image || result.image !== result.report, 'PNG 与 JSON 必须使用不同的新文件。');
  return result;
}

async function requireNewFile(path) {
  if (!path) return;
  try { await lstat(path); }
  catch (error) {
    if (error?.code === 'ENOENT') return;
    throw new VerificationError('OUTPUT_UNAVAILABLE', '无法核对输出位置。');
  }
  throw new VerificationError('OUTPUT_EXISTS', '输出文件已存在，拒绝覆盖。');
}

function references(value, result = []) {
  if (Array.isArray(value)) for (const item of value) references(item, result);
  else if (isRecord(value)) {
    if (typeof value.document === 'string' && typeof value.object === 'string') result.push(value);
    else for (const item of Object.values(value)) references(item, result);
  }
  return result;
}

// Keep the complete bounded public results, while removing transport/session,
// filesystem, external-document and image payload information from the report.
function sanitized(value) {
  const externalDocuments = new Set();
  const findExternal = item => {
    if (Array.isArray(item)) item.forEach(findExternal);
    else if (isRecord(item)) for (const [key, nested] of Object.entries(item)) {
      if (/^(?:document|docName|DocumentName)$/u.test(key) && typeof nested === 'string' && nested !== DOCUMENT) externalDocuments.add(nested);
      findExternal(nested);
    }
  };
  findExternal(value);
  const clean = item => {
    if (Array.isArray(item)) return item.map(clean);
    if (isRecord(item)) {
      const result = {};
      for (const [key, nested] of Object.entries(item)) {
        if (/(?:session|uuid|base64|local_?path|file_?name|file_?path|^path$)/iu.test(key)) continue;
        const safeKey = clean(key);
        result[safeKey] = clean(nested);
      }
      return result;
    }
    if (typeof item !== 'string') return item;
    if (/(?:^|[\s"'=([{])(?:\/|[A-Za-z]:[\\/]|\\\\)|file:\/\/|https?:\/\/|\bBearer\s|\bsk-[A-Za-z0-9_-]{12,}|<\s*(?:svg|\?xml|!DOCTYPE)\b/iu.test(item)
      || /^[A-Za-z0-9+/]{256,}={0,2}$/u.test(item)) return '[redacted]';
    let result = item.replace(/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/giu, '[redacted]');
    for (const name of [...externalDocuments].sort((a, b) => b.length - a.length)) {
      if (name) result = result.replaceAll(name, '[external-document]');
    }
    return result;
  };
  return clean(value);
}

async function main(args) {
  if (args.length === 1 && ['--help', '-h'].includes(args[0])) { console.log(USAGE); return; }
  const output = options(args);
  await requireNewFile(output.image);
  await requireNewFile(output.report);
  const client = new Client({ name: 'phoenix-capabilities-verifier', version: '0.3.0' });
  const transport = new StdioClientTransport({
    command: process.execPath, args: [fileURLToPath(new URL('../dist/cli.js', import.meta.url))],
    env: { ...(process.env.PHOENIX_FREECAD_URL ? { PHOENIX_FREECAD_URL: process.env.PHOENIX_FREECAD_URL } : {}),
      PHOENIX_TIMEOUT_MS: '20000' }, stderr: 'ignore',
  });
  let step = 'connect';
  try {
    await client.connect(transport, TIMEOUT);
    step = 'list_tools';
    const tools = (await client.listTools({}, TIMEOUT)).tools.map(tool => tool.name).sort();
    requireThat(JSON.stringify(tools) === JSON.stringify([...TOOLS].sort()), '必须发现当前 21 个 MCP 工具。');
    const calls = [];
    const call = async (name, arguments_ = {}) => {
      step = name;
      const response = await client.callTool({ name, arguments: arguments_ }, TIMEOUT);
      if (response.isError) {
        const code = response.structuredContent?.error?.code;
        throw new VerificationError(typeof code === 'string' && /^[A-Z][A-Z0-9_]{0,79}$/u.test(code) ? code : 'TOOL_FAILED', 'MCP 工具返回错误。');
      }
      requireThat(isRecord(response.structuredContent) && response.structuredContent.ok === true, 'MCP 返回缺少成功状态。');
      const data = response.structuredContent.data;
      if (name !== 'get_connection_status') {
        requireThat(isRecord(data) && data.document === DOCUMENT, '工具未返回固定公开演示文档。');
        calls.push({ tool: name, arguments: arguments_, result: data });
      }
      return { data, response };
    };
    const { data: status } = await call('get_connection_status');
    requireThat(status.protocol_version === 1 && status.running === true && status.addon_version === '0.3.0', '需要运行中的 0.3.0 协议 v1 插件。');
    const needed = ['get_document_info', 'list_objects', 'get_object_info', 'get_selection', 'measure_geometry', 'list_drawing_pages', 'inspect_drawing_page', 'view_standard', 'capture_view'];
    requireThat(Array.isArray(status.supported_methods) && needed.every(name => status.supported_methods.includes(name)), '插件未声明全部待验能力。');

    const { data: document } = await call('get_document_info', { document: DOCUMENT });
    requireThat(document.Name === DOCUMENT && document.core_units?.length === 'mm', '公开演示文档或基本长度单位不符。');
    const objects = [];
    let offset = 0;
    do {
      const { data: page } = await call('list_objects', { document: DOCUMENT, offset, limit: 100 });
      requireThat(Array.isArray(page.objects) && page.offset === offset, '对象分页结果无效。');
      objects.push(...page.objects);
      if (!page.has_more) break;
      requireThat(page.objects.length > 0 && objects.length < 1000, '对象分页未结束或超出公开演示规模。');
      offset += page.objects.length;
    } while (true);
    for (const name of ['PlateBody', 'Pad', 'HoleSketch', 'Pocket', PAGE]) requireThat(objects.some(object => object.Name === name), '公开演示缺少预期特征或图纸。');

    const { data: pad } = await call('get_object_info', { document: DOCUMENT, object: 'Pad', properties: ['Length', 'Profile'] });
    const length = pad.properties?.Length;
    requireThat(length?.kind === 'quantity' && near(length.value, 8) && length.unit === 'mm'
      && length.unit_system === 'freecad_internal' && JSON.stringify(length.unit_signature) === JSON.stringify([1, 0, 0, 0, 0, 0, 0, 0]), 'Pad.Length 必须为 8 mm 的原生量值。');
    requireThat(references(pad.properties?.Profile).some(ref => ref.document === DOCUMENT && ref.object === 'OutlineSketch'), 'Pad.Profile 没有指向公开演示轮廓草图。');
    const { data: holes } = await call('get_object_info', { document: DOCUMENT, object: 'HoleSketch' });
    requireThat(holes.sketch?.fully_constrained === true && holes.sketch.geometry_length_unit === 'mm', 'HoleSketch 必须已完全约束，几何单位必须为 mm。');
    const constraintUnits = [...new Set((holes.sketch.constraints ?? []).map(item => item.value_unit).filter(Boolean))].sort();
    requireThat(constraintUnits.includes('mm'), '草图尺寸约束缺少 mm 单位证据。');
    const { data: selection } = await call('get_selection', { document: DOCUMENT });
    requireThat(Array.isArray(selection.selection), '选择结果必须是数组；允许为空。');

    const { data: pocket } = await call('measure_geometry', { document: DOCUMENT, object: 'Pocket' });
    const geometry = pocket.geometry, box = geometry?.bounding_box;
    requireThat(geometry?.units?.length === 'mm' && geometry.units.volume === 'mm^3' && geometry.volume_applicable === true && geometry.volume > 0, 'Pocket 必须有正体积和明确的 mm/mm^3 单位。');
    requireThat(geometry.bounding_box_source === 'native_shape_bound_box'
      && geometry.bounding_box_precision === 'kernel_enclosure_not_exact_clearance' && geometry.bounding_box_error_bound_mm === null, '包围盒必须明确来自原生包络且不声称精确间隙或已知误差界。');
    const expectedBounds = { XMin: -30, YMin: -20, ZMin: 0, XMax: 30, YMax: 20, ZMax: 8, XLength: 60, YLength: 40, ZLength: 8 };
    requireThat(isRecord(box) && Object.entries(expectedBounds).every(([key, value]) => near(box[key], value)), 'Pocket 包围盒必须为居中的 60 × 40 × 8 mm。');
    const expectedVolume = (60 * 40 - 4 * Math.PI * 3 ** 2) * 8;
    requireThat(near(geometry.volume, expectedVolume, 1e-5), 'Pocket 体积不符合四孔公开演示的解析值。');
    const { data: face } = await call('measure_geometry', { document: DOCUMENT, object: 'Pocket', subelement: 'Face1' });
    requireThat(face.subelement === 'Face1' && face.geometry?.shape_type === 'Face' && face.geometry.area > 0 && face.geometry.units?.area === 'mm^2', 'Face1 测量应返回正面积面及 mm^2 单位。');
    requireThat(face.geometry.volume === null && face.geometry.volume_applicable === false, 'Face1 不是实体，其体积必须为不适用的 null。');
    const { data: pages } = await call('list_drawing_pages', { document: DOCUMENT });
    requireThat(Array.isArray(pages.pages) && pages.pages.some(page => page.Name === PAGE), '未找到公开演示 TechDraw 页。');
    const { data: drawing } = await call('inspect_drawing_page', { document: DOCUMENT, page: PAGE });
    requireThat(drawing.page?.Name === PAGE && Array.isArray(drawing.objects), '图纸检查结果无效。');
    const views = drawing.objects.filter(object => object.TypeId === 'TechDraw::DrawViewPart');
    const dimensions = drawing.objects.filter(object => typeof object.TypeId === 'string' && object.TypeId.startsWith('TechDraw::DrawViewDimension'));
    requireThat(views.length === 4 && dimensions.length >= 1, '公开图纸应有四个投影视图和至少一个尺寸。');
    const nativeSources = object => (object.sources ?? []).filter(ref => ref.document === DOCUMENT && typeof ref.object === 'string');
    requireThat(views.every(view => nativeSources(view).some(ref => ref.field === 'Source')), '每个图纸视图必须保留原生来源引用。');
    const viewNames = new Set(views.map(view => view.Name));
    requireThat(dimensions.every(dimension => nativeSources(dimension).some(ref => ref.field === 'References2D' && viewNames.has(ref.object))), '每个演示尺寸必须保留关联到投影视图的 References2D。');

    await call('set_view', { document: DOCUMENT, view: 'axonometric', fit: true });
    const { data: capture, response: captured } = await call('capture_view', { document: DOCUMENT, width: 800, height: 600 });
    const images = captured.content?.filter(block => block.type === 'image') ?? [];
    requireThat(images.length === 1 && images[0].mimeType === 'image/png' && capture.width === 800 && capture.height === 600, 'MCP 必须提供一张 800 × 600 PNG 图片内容。');
    const image = Buffer.from(images[0].data, 'base64');
    requireThat(image.length > 32 && image.length <= 600 * 1024 && image.toString('base64') === images[0].data
      && image.subarray(0, 8).equals(Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]))
      && image.toString('ascii', 12, 16) === 'IHDR' && image.readUInt32BE(16) === 800 && image.readUInt32BE(20) === 600, 'PNG 内容校验失败。');
    requireThat(!JSON.stringify(captured.structuredContent).includes(images[0].data), '图片 Base64 不应重复出现在结构化结果。');
    const sourceCounts = { views: views.reduce((sum, view) => sum + nativeSources(view).length, 0),
      dimensions: dimensions.reduce((sum, dimension) => sum + nativeSources(dimension).length, 0) };
    const summary = { ok: true, document: DOCUMENT,
      counts: { tools: tools.length, objects: objects.length, selected: selection.selection.length,
        sketchGeometry: holes.sketch.geometry_count, sketchConstraints: holes.sketch.constraint_count,
        drawingPages: pages.pages.length, views: views.length, dimensions: dimensions.length },
      actualVolume: geometry.volume, volumeUnit: geometry.units.volume,
      boundingBoxMM: box,
      featureFieldUnits: { padLength: { value: length.value, unit: length.unit, unitSystem: length.unit_system, signature: length.unit_signature, type: length.unit_type }, sketchGeometry: holes.sketch.geometry_length_unit, sketchConstraints: constraintUnits, faceArea: face.geometry.units.area },
      pageSourceRefCounts: { ...sourceCounts, total: sourceCounts.views + sourceCounts.dimensions }, capturePNGSize: image.length };
    const report = sanitized({ summary, connection: { running: true, protocol_version: status.protocol_version, addon_version: status.addon_version }, calls });
    step = 'write_output';
    if (output.image) await writeFile(output.image, image, { flag: 'wx' });
    if (output.report) await writeFile(output.report, JSON.stringify(report, null, 2) + '\n', { flag: 'wx' });
    console.log(JSON.stringify(summary));
  } catch (error) {
    // Do not print remote error messages, stacks, paths, session IDs or other
    // document names. Fixed verification messages describe the failing check.
    console.error(JSON.stringify({ ok: false, step,
      code: error instanceof VerificationError ? error.code : 'VERIFICATION_ERROR',
      message: error instanceof VerificationError ? error.message : 'MCP 调用或输出失败，请检查连接和输出位置。' }));
    process.exitCode = 1;
  } finally { await client.close(); }
}

try { await main(process.argv.slice(2)); }
catch (error) {
  console.error(JSON.stringify({ ok: false, code: error instanceof VerificationError ? error.code : 'VERIFICATION_ERROR',
    message: error instanceof VerificationError ? error.message : '无法开始验收。' }));
  process.exitCode = 1;
}
