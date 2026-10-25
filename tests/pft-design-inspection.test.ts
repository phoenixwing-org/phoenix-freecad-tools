// SPDX-License-Identifier: Apache-2.0
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { PftDesignInspectionDataSchema, PftDesignInspectionInputSchema, pftInspectDesign } from '../src/pft-design-inspection.js';

const token = 'sample_document_instance_token01';
const cached = (value: unknown, source: string) => ({ value, source, status: 'available' });
function fixture(): Record<string, any> {
  return { document: 'PublicSample', source_kind: 'live_freecad_gui', document_instance_token: token, instance_token: token,
    revision: 7, revision_scope: 'observed_document_events_since_registration', revision_coverage: 'partial', revision_status: 'observing',
    provenance: { source: 'live_native_document', geometry_freshness: 'not_verified', recomputed: false, write_guard: false },
    object: { Name: 'Sketch', Label: 'Public sketch', TypeId: 'Sketcher::SketchObject', kind: 'sketch', status: 'available', reason: null,
      state: cached(['Touched'], 'DocumentObject.State'), has_error_marker: false }, body: null,
    sketch: { geometry_count: cached(4, 'Sketch.GeometryCount'), degrees_of_freedom: cached(1, 'Sketch.DoF'),
      fully_constrained: cached(false, 'Sketch.FullyConstrained'), solver_invoked: false,
      constraints: { status: 'available', total_count: 0, offset: 0, limit: 50, items: [], has_more: false, next_offset: null } },
    parameters: [], parameters_status: 'available', parameters_source: 'Object.PropertiesList',
    expressions: { status: 'available', items: [], total_count: 0, truncated: false, evaluated: false },
    warnings: [], recomputed: false, solver_invoked: false, geometry_validity: 'not_verified' };
}
function constraint(index: number) {
  return { index, status: 'available', Type: 'Angle', Name: null, First: -1, FirstPos: 0, Second: -2,
    SecondPos: 0, Third: null, ThirdPos: null, is_driving: cached(true, 'Sketch.getDriving'), is_active: cached(true, 'Sketch.getActive'),
    datum: cached({ kind: 'quantity', value: 30, unit: 'deg', unit_signature: [0, 0, 0, 0, 0, 0, 0, 1],
      unit_system: 'freecad_internal', unit_type: 'Angle' }, 'Sketch.getDatum') };
}
class PftTestError extends Error { constructor(public readonly code: string, message: string) { super(message); } }
const status = { supported_methods: ['inspect_design'], read_provenance: { version: 1, methods: ['inspect_design'],
  revision_coverage: 'partial', write_guard: false } };

test('设计输入严格范围、默认分页、Unicode Name与成对修订条件', () => {
  assert.deepEqual(PftDesignInspectionInputSchema.parse({ object: '草图' }), { object: '草图', constraintOffset: 0, constraintLimit: 50 });
  for (const input of [{}, { object: 'Sketch', code: 'pass' }, { object: 'bad\x00name' },
    { object: 'Sketch', constraintOffset: -1 }, { object: 'Sketch', constraintOffset: 2147483648 },
    { object: 'Sketch', constraintLimit: 0 }, { object: 'Sketch', constraintLimit: 101 },
    { object: 'Sketch', constraintLimit: '50' }, { object: 'Sketch', expectedRevision: 0 },
    { object: 'Sketch', expectedDocumentInstanceToken: token, expectedRevision: Number.MAX_SAFE_INTEGER + 1 }]) {
    assert.equal(PftDesignInspectionInputSchema.safeParse(input).success, false, JSON.stringify(input));
  }
  assert.equal(PftDesignInspectionInputSchema.safeParse({ object: '😀'.repeat(256) }).success, true);
  assert.equal(PftDesignInspectionInputSchema.safeParse({ object: '😀'.repeat(257) }).success, false);
  assert.equal(PftDesignInspectionInputSchema.safeParse({ object: 'invalid\u200bname' }).success, false);
  const value = fixture(); value.object.Label = '😀'.repeat(512);
  assert.equal(PftDesignInspectionDataSchema.safeParse(value).success, true);
  value.object.Label += '😀';
  assert.equal(PftDesignInspectionDataSchema.safeParse(value).success, false);
});

test('严格DTO保留角度deg和未知量值，拒绝顶层与深层任意字段', () => {
  const value = fixture();
  value.sketch.constraints = { status: 'available', total_count: 1, offset: 0, limit: 50, items: [constraint(0)], has_more: false, next_offset: null };
  const parsed = PftDesignInspectionDataSchema.parse(value);
  assert.equal(parsed.sketch!.constraints.items[0]!.datum.value!.unit, 'deg');
  assert.equal(parsed.sketch!.constraints.items[0]!.datum.value!.value, 30);
  value.sketch.constraints.items[0].datum.value.unit = null;
  value.sketch.constraints.items[0].datum.value.unit_signature = null;
  value.sketch.constraints.items[0].datum.value.unit_system = null;
  assert.equal(PftDesignInspectionDataSchema.safeParse(value).success, true);
  for (const mutate of [(item: any) => { item.FileName = '/private/never-forward'; },
    (item: any) => { item.object.native = {}; }, (item: any) => { item.sketch.constraints.items[0].Value = 0.5236; },
    (item: any) => { item.provenance.write_guard = true; }, (item: any) => { item.solver_invoked = true; },
    (item: any) => { item.sketch.degrees_of_freedom.value = -1; },
    (item: any) => { item.sketch.constraints.items[0].datum.value.unit = 'mm'; },
    (item: any) => { item.sketch.constraints.items[0].datum.value.unit_signature = [0, 0, 0, 0, 0, 0, 0, 1]; item.sketch.constraints.items[0].datum.value.unit = 'mm'; },
    (item: any) => { item.sketch.constraints.items[0].datum.value.unit = 'mm'; item.sketch.constraints.items[0].datum.value.unit_signature = null; },
    (item: any) => { item.sketch.constraints.items[0].datum.value.unit_system = 'display'; },
    (item: any) => { item.parameters_status = 'read_failed'; }]) {
    const invalid = structuredClone(value); mutate(invalid);
    assert.equal(PftDesignInspectionDataSchema.safeParse(invalid).success, false);
  }
});

test('量值内部基础单位覆盖常见维度且active/参数来源未知保持明确null', () => {
  for (const [signature, unit] of [[[1, 0, 0, 0, 0, 0, 0, 0], 'mm'], [[2, 0, 0, 0, 0, 0, 0, 0], 'mm^2'],
    [[0, 1, 0, 0, 0, 0, 0, 0], 'kg'], [[1, 1, -2, 0, 0, 0, 0, 0], 'mm*kg*s^-2'],
    [[0, 0, 0, 0, 0, 0, 0, 0], '1']] as const) {
    const value = fixture(), item = constraint(0);
    item.datum.value.unit_signature = [...signature]; item.datum.value.unit = unit;
    item.is_active = { value: null, status: 'unavailable', source: null } as any;
    value.sketch.constraints = { status: 'available', total_count: 1, offset: 0, limit: 50, items: [item], has_more: false, next_offset: null };
    value.parameters_status = 'read_failed'; value.parameters_source = null;
    assert.equal(PftDesignInspectionDataSchema.safeParse(value).success, true);
  }
});

test('约束分页和cached未知状态不容伪造完整度', () => {
  const value = fixture();
  value.sketch.constraints = { status: 'available', total_count: 3, offset: 1, limit: 1,
    items: [constraint(1)], has_more: true, next_offset: 2 };
  assert.equal(PftDesignInspectionDataSchema.safeParse(value).success, true);
  for (const change of [{ next_offset: null }, { next_offset: 3 }, { has_more: false }, { offset: 0 }, { items: [] }]) {
    const invalid = structuredClone(value); Object.assign(invalid.sketch.constraints, change);
    assert.equal(PftDesignInspectionDataSchema.safeParse(invalid).success, false);
  }
  value.sketch.constraints = { status: 'read_failed', total_count: null, offset: 0, limit: 50,
    items: [], has_more: null, next_offset: null };
  value.sketch.degrees_of_freedom = { value: null, source: null, status: 'unavailable' };
  assert.equal(PftDesignInspectionDataSchema.safeParse(value).success, true);
  value.sketch.degrees_of_freedom.value = 0;
  assert.equal(PftDesignInspectionDataSchema.safeParse(value).success, false);
});

test('表达式状态失败不能伪装空清单，完整度严格对应原生前50项', () => {
  for (const state of ['unavailable', 'read_failed', 'unsupported']) {
    const unknown = fixture();
    unknown.expressions = { status: state, items: [], total_count: null, truncated: false, evaluated: false };
    assert.equal(PftDesignInspectionDataSchema.safeParse(unknown).success, true);
    for (const change of [{ total_count: 0 }, { truncated: true },
      { items: [{ path: null, expression: null, status: 'unsupported' }] }]) {
      const invalid = structuredClone(unknown); Object.assign(invalid.expressions, change);
      assert.equal(PftDesignInspectionDataSchema.safeParse(invalid).success, false);
    }
  }
  const prefix = fixture();
  prefix.expressions = { status: 'available', total_count: 51, truncated: true, evaluated: false,
    items: Array.from({ length: 50 }, () => ({ path: 'Length', expression: '2 mm', status: 'available' })) };
  assert.equal(PftDesignInspectionDataSchema.safeParse(prefix).success, true);
  for (const change of [{ total_count: null }, { total_count: 49 }, { truncated: false }, { items: prefix.expressions.items.slice(1) }]) {
    const invalid = structuredClone(prefix); Object.assign(invalid.expressions, change);
    assert.equal(PftDesignInspectionDataSchema.safeParse(invalid).success, false);
  }
});

test('Body保留未知成员及无Tip语义，允许同文档引用被过滤后的截断清单', () => {
  const body = fixture(); body.object.kind = 'body'; body.object.TypeId = 'PartDesign::Body'; body.sketch = null;
  body.body = { status: 'available', tip: null, tip_status: 'available', member_count: 1, members: [], members_truncated: true };
  assert.equal(PftDesignInspectionDataSchema.safeParse(body).success, true); // One invalid reference was omitted.
  body.body = { status: 'available', tip: null, tip_status: 'available', member_count: 103,
    members: Array.from({ length: 100 }, (_, index) => ({ object: 'Feature' + index, subelements: [] })), members_truncated: true };
  assert.equal(PftDesignInspectionDataSchema.safeParse(body).success, true);
  for (const change of [{ member_count: null }, { member_count: 99 }, { members_truncated: false }]) {
    const invalid = structuredClone(body); Object.assign(invalid.body, change);
    assert.equal(PftDesignInspectionDataSchema.safeParse(invalid).success, false);
  }
  for (const state of ['unavailable', 'read_failed', 'unsupported']) {
    body.body = { status: state, tip: null, tip_status: 'available', member_count: null, members: [], members_truncated: false };
    assert.equal(PftDesignInspectionDataSchema.safeParse(body).success, true);
    for (const change of [{ member_count: 0 }, { members_truncated: true },
      { members: [{ object: 'Feature', subelements: [] }] }]) {
      const invalid = structuredClone(body); Object.assign(invalid.body, change);
      assert.equal(PftDesignInspectionDataSchema.safeParse(invalid).success, false);
    }
  }
  body.body.tip_status = 'read_failed'; body.body.tip = { object: 'Feature', subelements: [] };
  assert.equal(PftDesignInspectionDataSchema.safeParse(body).success, false);
});

test('不可读取对象状态不能声称没有错误，available的不完整标记仍可未知', () => {
  for (const state of ['unavailable', 'read_failed', 'unsupported']) {
    const value = fixture(); value.object.state = { status: state, value: null, source: null };
    value.object.has_error_marker = null;
    assert.equal(PftDesignInspectionDataSchema.safeParse(value).success, true);
    for (const marker of [false, true]) {
      value.object.has_error_marker = marker;
      assert.equal(PftDesignInspectionDataSchema.safeParse(value).success, false);
    }
  }
  const partial = fixture(); partial.object.has_error_marker = null;
  assert.equal(PftDesignInspectionDataSchema.safeParse(partial).success, true);
});

test('对象kind必须有对应分节，unsupported不能携带可用参数或表达式', () => {
  for (const kind of ['body', 'sketch']) {
    const invalid = fixture(); invalid.object.kind = kind; invalid.body = invalid.sketch = null;
    assert.equal(PftDesignInspectionDataSchema.safeParse(invalid).success, false);
  }
  const link = fixture(); link.object.kind = 'unsupported'; link.object.TypeId = 'App::Link';
  link.object.status = 'unsupported'; link.object.reason = 'link_source_not_inspected'; link.sketch = null;
  link.parameters_status = 'unsupported'; link.parameters_source = null;
  link.expressions = { status: 'unsupported', items: [], total_count: null, truncated: false, evaluated: false };
  assert.equal(PftDesignInspectionDataSchema.safeParse(link).success, true);
  for (const mutate of [(value: any) => { value.object.status = 'available'; },
    (value: any) => { value.object.reason = null; }, (value: any) => { value.object.reason = 'arbitrary_reason'; },
    (value: any) => { value.parameters_status = 'available'; value.parameters_source = 'Object.PropertiesList'; },
    (value: any) => { value.parameters.push({ name: 'Midplane', property_type: 'App::PropertyBool', status: 'available', value: { kind: 'boolean', value: false } }); },
    (value: any) => { value.expressions = fixture().expressions; },
    (value: any) => { value.sketch = fixture().sketch; }]) {
    const invalid = structuredClone(link); mutate(invalid);
    assert.equal(PftDesignInspectionDataSchema.safeParse(invalid).success, false);
  }
});

test('native-only client helper预检双能力、精确映射、响应匹配且不自动重试', async () => {
  const calls: unknown[] = [];
  const transport = { status: async () => status, read: async (params: unknown) => { calls.push(params); return fixture(); } };
  const value = await pftInspectDesign({ document: 'PublicSample', object: 'Sketch', expectedDocumentInstanceToken: token,
    expectedRevision: 7 }, transport, PftTestError);
  assert.equal(value.object.Name, 'Sketch');
  assert.deepEqual(calls, [{ docName: 'PublicSample', objectName: 'Sketch', constraintOffset: 0, constraintLimit: 50,
    expectedDocumentInstanceToken: token, expectedRevision: 7 }]);
  for (const invalid of [{}, { ...status, read_provenance: undefined }, { ...status, supported_methods: [] },
    { ...status, read_provenance: { ...status.read_provenance, methods: [] } }]) {
    await assert.rejects(pftInspectDesign({ object: 'Sketch' }, { ...transport, status: async () => invalid }, PftTestError), { code: 'UNSUPPORTED_CAPABILITY' });
  }
  assert.equal(calls.length, 1);
  const invalid = fixture(); invalid.object.Name = 'OtherSketch';
  await assert.rejects(pftInspectDesign({ object: 'Sketch' }, { ...transport, read: async () => invalid }, PftTestError), { code: 'INVALID_RESPONSE' });
});

test('设计诊断128KiB预算整笔拒绝，不裁剪后继续返回', async () => {
  const oversized = fixture();
  oversized.sketch.constraints = { status: 'available', total_count: 100, offset: 0, limit: 100,
    items: Array.from({ length: 100 }, (_, index) => ({ ...constraint(index), Type: 'x'.repeat(512), Name: 'x'.repeat(512) })),
    has_more: false, next_offset: null };
  assert.equal(PftDesignInspectionDataSchema.safeParse(oversized).success, true);
  await assert.rejects(pftInspectDesign({ object: 'Sketch', constraintLimit: 100 },
    { status: async () => status, read: async () => oversized }, PftTestError), { code: 'READ_RESPONSE_TOO_LARGE' });
});
