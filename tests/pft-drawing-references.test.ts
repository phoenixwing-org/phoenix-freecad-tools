// SPDX-License-Identifier: Apache-2.0
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { PFT_DRAWING_REFERENCES_CAPABILITY, PftDrawingReferencesCapabilitySchema,
  PftDrawingReferencesInputSchema, PftDrawingReferencesDataSchema, PftDrawingEdgeSchema,
  pftInspectDrawingReferences, PftDrawingNativeMeasurementSchema } from '../src/pft-drawing-references.js';

class Failure extends Error { constructor(public readonly code: string, message: string) { super(message); } }
const input = { document: 'NeutralDocument', page: 'NeutralPage', view: 'NeutralView', offset: 0, limit: 2 };
const status = { supported_methods: ['inspect_drawing_page'], drawing_references: PFT_DRAWING_REFERENCES_CAPABILITY,
  read_provenance: { version: 1, methods: ['inspect_drawing_page'], revision_coverage: 'partial', write_guard: false } };
const instance = 'neutral_document_instance_token0';
function edge(index = 0) {
  return { edge: 'Edge' + String(index), status: 'available', curve: 'line', start: [0, 0, 0], end: [600, 0, 0],
    length_mm: 600, radius_mm: null, center: null, closed: false, supported_kinds: ['Distance', 'DistanceX', 'DistanceY'] };
}
function missing(index = 1) {
  return { edge: 'Edge' + String(index), status: 'missing', curve: null, start: null, end: null,
    length_mm: null, radius_mm: null, center: null, closed: null, supported_kinds: [] };
}
function fixture(): Record<string, any> {
  return { document: input.document, source_kind: 'live_freecad_gui', document_instance_token: instance, instance_token: instance,
    revision: 0, revision_scope: 'observed_document_events_since_registration', revision_coverage: 'partial', revision_status: 'observing',
    provenance: { source: 'live_native_document', geometry_freshness: 'not_verified', recomputed: false, write_guard: false },
    page: { Name: input.page, Label: 'Neutral page', TypeId: 'TechDraw::DrawPage', scale: 0.1, template: null,
      object_count: 1, state: ['Up-to-date'] }, template: null,
    objects: [{ Name: input.view, Label: 'Neutral view', TypeId: 'TechDraw::DrawViewPart', state: ['Up-to-date'],
      sources: [], sources_truncated: false, properties: { Scale: 0.1 } }], total: 1, truncated: false,
    recomputed: false, references_validated: false, warnings: [],
    projected_geometry: { view: input.view, status: 'available', offset: 0, limit: 2, index_base: 0, max_index: 2047,
      items: [edge(), missing()], total_count: null, enumeration_complete: false, length_unit: 'mm',
      coordinate_system: 'view_local_unscaled_qt_y_down', source: 'native_cached_projection',
      projection_verification: 'not_checked', geometry_freshness: 'not_verified' } };
}

test('引用能力exact声明零基有限缓存，不宣称投影完成', () => {
  assert.deepEqual(PftDrawingReferencesCapabilitySchema.parse(PFT_DRAWING_REFERENCES_CAPABILITY), PFT_DRAWING_REFERENCES_CAPABILITY);
  for (const value of [{}, null, { ...PFT_DRAWING_REFERENCES_CAPABILITY, extra: true },
    ...Object.entries(PFT_DRAWING_REFERENCES_CAPABILITY).map(([key, value]) => ({ ...PFT_DRAWING_REFERENCES_CAPABILITY,
      [key]: typeof value === 'number' ? value + 1 : 'other' }))]) {
    assert.equal(PftDrawingReferencesCapabilitySchema.safeParse(value).success, false);
  }
});

test('既有无view摘要不注入默认分页，有view索引窗口严格且Unicode有界', () => {
  assert.deepEqual(PftDrawingReferencesInputSchema.parse({ page: input.page }), { page: input.page });
  assert.deepEqual(PftDrawingReferencesInputSchema.parse({ page: input.page, view: input.view }),
    { page: input.page, view: input.view });
  assert.equal(PftDrawingReferencesInputSchema.safeParse({ ...input, offset: 2047, limit: 1 }).success, true);
  assert.equal(PftDrawingReferencesInputSchema.safeParse({ ...input, document: '😀'.repeat(256) }).success, true);
  for (const value of [{}, { page: input.page, offset: 0 }, { page: input.page, limit: 1 },
    ...[-1, 2048, true, '0'].map(offset => ({ ...input, offset })),
    ...[0, 51, true, '2'].map(limit => ({ ...input, limit })),
    { ...input, offset: 2047, limit: 2 }, { ...input, page: 'bad\u200bname' }, { ...input, page: 'NeutralPage\n' },
    { ...input, document: '😀'.repeat(257) }, { ...input, code: 'arbitrary' }]) {
    assert.equal(PftDrawingReferencesInputSchema.safeParse(value).success, false, JSON.stringify(value));
  }
});

test('严格边DTO区分line闭圆及missing，不以0假造失败量值', () => {
  assert.equal(PftDrawingEdgeSchema.safeParse(edge()).success, true);
  const circle = { ...edge(), curve: 'circle', start: null, end: null, radius_mm: 40, center: [-0, 10, 0],
    length_mm: 80 * Math.PI, closed: true, supported_kinds: ['Radius', 'Diameter'] };
  assert.equal(PftDrawingEdgeSchema.safeParse(circle).success, true);
  assert.equal(PftDrawingEdgeSchema.safeParse(missing()).success, true);
  const unavailable = { ...missing(), status: 'unsupported', curve: 'unsupported' };
  assert.equal(PftDrawingEdgeSchema.safeParse(unavailable).success, true);
  for (const value of [{ ...edge(), edge: 'Edge01' }, { ...edge(), edge: 'Edge2048' }, { ...edge(), edge: 'Edge0\n' }, { ...edge(), start: null },
    { ...edge(), end: [0, 0, 0] }, { ...edge(), closed: true }, { ...edge(), length_mm: 0 },
    { ...edge(), length_mm: NaN }, { ...edge(), radius_mm: 1 }, { ...edge(), supported_kinds: ['Distance'] },
    { ...edge(), arbitrary: true }, { ...circle, closed: false }, { ...circle, start: [0, 0, 0] },
    { ...missing(), length_mm: 0 }, { ...missing(), curve: 'line' }, { ...unavailable, center: [0, 0, 0] }]) {
    assert.equal(PftDrawingEdgeSchema.safeParse(value).success, false, JSON.stringify(value));
  }
});

test('完整查询窗口不在missing处短路，拒绝跳号/假总数/空结果/ready声明', () => {
  assert.equal(PftDrawingReferencesDataSchema.safeParse(fixture()).success, true);
  for (const change of [{ items: [edge()] }, { items: [missing(), edge()] }, { total_count: 1 },
    { enumeration_complete: true }, { offset: 1 }, { limit: 1 }, { max_index: 4095 },
    { source: 'fresh_projection' }, { projection_verification: 'ready' }, { geometry_freshness: 'verified' },
    { coordinate_system: 'world' }, { extra: true }]) {
    const value = fixture(); Object.assign(value.projected_geometry, change);
    assert.equal(PftDrawingReferencesDataSchema.safeParse(value).success, false, JSON.stringify(change));
  }
  for (const state of ['unsupported', 'read_failed']) {
    const value = fixture(); value.projected_geometry.status = state; value.projected_geometry.items = [];
    assert.equal(PftDrawingReferencesDataSchema.safeParse(value).success, true);
    value.projected_geometry.items = [missing()];
    assert.equal(PftDrawingReferencesDataSchema.safeParse(value).success, false);
  }
  for (const mutate of [(value: any) => { value.page.object_count = 2; },
    (value: any) => { value.references_validated = true; }, (value: any) => { value.private_file = 'private'; },
    (value: any) => { value.objects[0].properties.Proxy = 'private'; }, (value: any) => { value.document_instance_token = null; }]) {
    const value = fixture(); mutate(value);
    assert.equal(PftDrawingReferencesDataSchema.safeParse(value).success, false);
  }
});

test('无view输入或缺能力/坏能力时零派发，无旧插件回退', async () => {
  for (const badInput of [{ page: input.page }, { ...input, limit: 51 }]) {
    let queried = 0;
    await assert.rejects(pftInspectDrawingReferences(badInput, { status: async () => { queried++; return status; },
      read: async () => { queried++; return fixture(); } }, Failure), { code: 'INVALID_ARGUMENT' });
    assert.equal(queried, 0);
  }
  for (const value of [{}, { ...status, supported_methods: [] }, { ...status, drawing_references: undefined },
    { ...status, read_provenance: undefined }, { ...status, read_provenance: { ...status.read_provenance, methods: [] } }]) {
    let dispatched = 0;
    await assert.rejects(pftInspectDrawingReferences(input, { status: async () => value,
      read: async () => { dispatched++; return fixture(); } }, Failure), { code: 'UNSUPPORTED_CAPABILITY' });
    assert.equal(dispatched, 0);
  }
  for (const value of [{ ...status, drawing_references: { ...PFT_DRAWING_REFERENCES_CAPABILITY, index_base: 1 } },
    { ...status, drawing_references: { ...PFT_DRAWING_REFERENCES_CAPABILITY, extra: true } },
    { ...status, read_provenance: { ...status.read_provenance, write_guard: true } }]) {
    let dispatched = 0;
    await assert.rejects(pftInspectDrawingReferences(input, { status: async () => value,
      read: async () => { dispatched++; return fixture(); } }, Failure), { code: 'INVALID_RESPONSE' });
    assert.equal(dispatched, 0);
  }
});

test('只派发一次明确page/view索引窗口，物理mm不乘页面Scale', async () => {
  const calls: unknown[] = [];
  const result = await pftInspectDrawingReferences(input, { status: async () => status,
    read: async params => { calls.push(params); return fixture(); } }, Failure);
  assert.deepEqual(calls, [{ docName: input.document, pageName: input.page, viewName: input.view, offset: 0, limit: 2 }]);
  assert.equal(result.projected_geometry.items[0]!.length_mm, 600);
  result.projected_geometry.items[0]!.end![0] = 30;
  assert.equal(fixture().projected_geometry.items[0].end[0], 600);
});

test('坏结果/超预算/不匹配目标拒绝且不重试，保留原生失败', async () => {
  for (const mutate of [(value: any) => { value.document = 'Other'; }, (value: any) => { value.page.Name = 'Other'; },
    (value: any) => { value.projected_geometry.view = 'Other'; }, (value: any) => { value.projected_geometry.offset = 1; },
    (value: any) => { value.projected_geometry.items = []; }, (value: any) => { value.provenance.write_guard = true; },
    (value: any) => { value.projected_geometry.items[0].length_mm = Infinity; }]) {
    let dispatched = 0;
    const value = fixture(); mutate(value);
    await assert.rejects(pftInspectDrawingReferences(input, { status: async () => status,
      read: async () => { dispatched++; return value; } }, Failure), { code: 'INVALID_RESPONSE' });
    assert.equal(dispatched, 1);
  }
  const huge = fixture(); huge.warnings = ['x'.repeat(128 * 1024)];
  await assert.rejects(pftInspectDrawingReferences(input, { status: async () => status,
    read: async () => huge }, Failure), { code: 'READ_RESPONSE_TOO_LARGE' });
  const native = new Failure('DRAWING_REFERENCES_STALE', 'Neutral stale read');
  let dispatched = 0;
  await assert.rejects(pftInspectDrawingReferences(input, { status: async () => status,
    read: async () => { dispatched++; throw native; } }, Failure), value => value === native);
  assert.equal(dispatched, 1);
});


test('既有原生尺寸仅返回有限正raw毫米，未知与unsupported没有伪造零值', () => {
  const measurement = { status: 'available', kind: 'Distance', value_mm: 800, unit: 'mm',
    source: 'native_cached_dimension', geometry_freshness: 'not_verified' };
  for (const kind of ['Distance', 'DistanceX', 'DistanceY', 'Diameter', 'Radius']) {
    assert.equal(PftDrawingNativeMeasurementSchema.safeParse({ ...measurement, kind }).success, true);
  }
  for (const state of ['unsupported', 'unknown', 'read_failed']) {
    assert.equal(PftDrawingNativeMeasurementSchema.safeParse({ ...measurement, status: state, value_mm: null }).success, true);
    assert.equal(PftDrawingNativeMeasurementSchema.safeParse({ ...measurement, status: state, kind: null, value_mm: null }).success, true);
    assert.equal(PftDrawingNativeMeasurementSchema.safeParse({ ...measurement, status: state }).success, false);
  }
  for (const value of [{ ...measurement, value_mm: 0 }, { ...measurement, value_mm: -1 },
    { ...measurement, value_mm: Infinity }, { ...measurement, value_mm: 1e9 + 1 }, { ...measurement, value_mm: null },
    { ...measurement, kind: null }, { ...measurement, kind: 'Angle' }, { ...measurement, unit: 'deg' },
    { ...measurement, source: 'formatted_label' }, { ...measurement, geometry_freshness: 'verified' },
    { ...measurement, formatted_text: '800 mm' }]) {
    assert.equal(PftDrawingNativeMeasurementSchema.safeParse(value).success, false);
  }
  const value = fixture();
  value.objects.push({ Name: 'NeutralDimension', Label: 'Neutral dimension', TypeId: 'TechDraw::DrawViewDimension',
    state: ['Up-to-date'], sources: [], sources_truncated: false, properties: { Type: 'Distance', MeasureType: 'Projected' },
    native_measurement: measurement });
  value.total = 2; value.page.object_count = 2;
  assert.equal(PftDrawingReferencesDataSchema.safeParse(value).success, true);
  value.objects[1].properties.Type = 'Diameter';
  assert.equal(PftDrawingReferencesDataSchema.safeParse(value).success, false);
  value.objects[1].properties.Type = 'Distance';
  value.objects[1].properties.MeasureType = 'True';
  assert.equal(PftDrawingReferencesDataSchema.safeParse(value).success, false);
  value.objects[1].properties.MeasureType = 'Projected';
  value.objects[1].TypeId = 'TechDraw::DrawViewPart';
  assert.equal(PftDrawingReferencesDataSchema.safeParse(value).success, false);
});

test('真实Python只读路径DTO可被严格TS合同解析，无需CAD或伪造后端结果', async () => {
  const { spawnSync } = await import('node:child_process');
  const { fileURLToPath } = await import('node:url');
  const script = `import importlib.util, json, sys
spec=importlib.util.spec_from_file_location('neutral_reference_fixture',sys.argv[1])
module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
case=module.DrawingReferenceTests(); case.setUp()
dimension=module.Dimension(case.document,value=800.); case.page.Views.append(dimension)
try:
 result=module.ops.perform('inspect_drawing_page',{'pageName':case.page.Name,'viewName':case.view.Name,'offset':0,'limit':2},case.app,case.gui,module.BridgeError)
 print(json.dumps(result,allow_nan=False))
finally:
 case.doCleanups()
`;
  const result = spawnSync('python3', ['-c', script,
    fileURLToPath(new URL('./python/test_pft_drawing_references.py', import.meta.url))],
  { encoding: 'utf8', maxBuffer: 256 * 1024 });
  assert.equal(result.status, 0, result.stderr);
  const value = PftDrawingReferencesDataSchema.parse(JSON.parse(result.stdout));
  assert.equal(value.projected_geometry.items[0]!.length_mm, 600);
  assert.equal(value.projected_geometry.items[1]!.status, 'missing');
  assert.equal(value.projected_geometry.projection_verification, 'not_checked');
  assert.equal(value.provenance.recomputed, false);
  assert.equal(value.objects[1]!.native_measurement!.value_mm, 800);
});
