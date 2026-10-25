// SPDX-License-Identifier: Apache-2.0
import * as z from 'zod/v4';
import { PftNativeReadDataSchema, PftReadCapabilitySchema, PftReadGuardFields,
  pftRefineReadGuards, PFT_MAX_READ_BYTES } from './pft-read-provenance.js';

// Python len counts Unicode code points; JS string.length counts UTF-16 units.
const boundedText = (maximum: number) => z.string().max(maximum * 2)
  .refine(value => Array.from(value).length <= maximum, '文本超过 Unicode 字符数量上限')
  .regex(/^[^\p{Cs}]*$/u);
const name = boundedText(256).min(1).regex(/^[^\p{Cc}\p{Cf}\p{Cs}]+$/u);
const text = boundedText(512);
const count = z.number().int().min(0).max(2147483647);
const finite = z.number().finite().min(-Number.MAX_SAFE_INTEGER).max(Number.MAX_SAFE_INTEGER);
const readStatus = z.enum(['available', 'unavailable', 'read_failed', 'unsupported']);
const reference = z.object({ object: name, subelements: z.array(text).max(100) }).strict();
const cached = <T extends z.ZodType>(schema: T) => z.object({ value: schema.nullable(), status: readStatus,
  source: text.nullable() }).strict().superRefine((value, context) => {
    if ((value.status === 'available') !== (Reflect.get(value, 'value') !== null)
      || (value.status === 'available' ? !value.source : value.source !== null)) {
      context.addIssue({ code: 'custom', message: '原生缓存状态与值不一致' });
    }
  });
const quantity = z.object({ kind: z.literal('quantity'), value: finite.nullable(),
  unit: boundedText(128).nullable(), unit_signature: z.array(z.number().int().min(-128).max(127)).length(8).nullable(),
  unit_system: z.literal('freecad_internal').nullable(), unit_type: text.nullable() }).strict().superRefine((value, context) => {
    const symbols = ['mm', 'kg', 's', 'A', 'K', 'mol', 'cd', 'deg'];
    const expected = value.unit_signature === null ? null : value.unit_signature.flatMap((power, index) =>
      power === 0 ? [] : [power === 1 ? symbols[index]! : `${symbols[index]!}^${power}`]).join('*') || '1';
    if (value.unit !== expected || value.unit_system !== (value.unit_signature === null ? null : 'freecad_internal')) {
      context.addIssue({ code: 'custom', message: '量值单位与内部基础单位维度不一致' });
    }
  });
const designValue = z.discriminatedUnion('kind', [quantity,
  z.object({ kind: z.literal('boolean'), value: z.boolean() }).strict(),
  z.object({ kind: z.literal('number'), value: finite }).strict(),
  z.object({ kind: z.literal('string'), value: text }).strict(),
  z.object({ kind: z.literal('reference'), object: name, subelements: z.array(text).max(100) }).strict(),
  z.object({ kind: z.literal('references'), items: z.array(reference).max(100), truncated: z.boolean() }).strict(),
]);
const topologyIndex = z.number().int().min(-2147483648).max(2147483647).nullable();
const constraint = z.object({ index: count, status: z.enum(['available', 'partial']), Type: text.nullable(),
  Name: text.nullable(), First: topologyIndex, FirstPos: topologyIndex, Second: topologyIndex,
  SecondPos: topologyIndex, Third: topologyIndex, ThirdPos: topologyIndex,
  is_driving: cached(z.boolean()), is_active: cached(z.boolean()), datum: cached(quantity) }).strict();
const constraints = z.object({ status: readStatus, total_count: count.nullable(), offset: count,
  limit: z.number().int().min(1).max(100), items: z.array(constraint).max(100),
  has_more: z.boolean().nullable(), next_offset: count.nullable() }).strict().superRefine((page, context) => {
    const fail = () => context.addIssue({ code: 'custom', message: '约束分页字段不一致' });
    if (page.status !== 'available') {
      if (page.total_count !== null || page.has_more !== null || page.next_offset !== null || page.items.length) fail();
      return;
    }
    if (page.total_count === null || page.has_more === null || page.items.length > page.limit
      || page.items.some((item, index) => item.index !== page.offset + index)) { fail(); return; }
    const expectedLength = Math.min(page.limit, Math.max(0, page.total_count - page.offset));
    const hasMore = page.offset + page.items.length < page.total_count;
    if (page.items.length !== expectedLength || page.has_more !== hasMore
      || page.next_offset !== (hasMore ? page.offset + page.items.length : null)) fail();
  });
const parameterNames = ['Length', 'Length2', 'Radius', 'Angle', 'Midplane', 'Reversed', 'Type', 'Profile',
  'Width', 'Height', 'Thickness', 'Offset'] as const;
const parameter = z.object({ name: z.enum(parameterNames), property_type: boundedText(128).nullable(),
  status: readStatus, value: designValue.nullable() }).strict().superRefine((value, context) => {
    if ((value.status === 'available') !== (value.value !== null)) {
      context.addIssue({ code: 'custom', message: '设计参数状态与值不一致' });
    }
  });

export const PftDesignInspectionInputSchema = z.object({
  document: name.describe('已打开的 FreeCAD 文档内部 Name；省略使用活动文档').optional(),
  object: name.describe('明确的 Body、Sketch 或特征内部 Name；不会展开 Link 来源'),
  constraintOffset: count.default(0), constraintLimit: z.number().int().min(1).max(100).default(50),
  ...PftReadGuardFields,
}).strict().superRefine(pftRefineReadGuards);
export type PftDesignInspectionInput = z.input<typeof PftDesignInspectionInputSchema>;
export type PftParsedDesignInspectionInput = z.output<typeof PftDesignInspectionInputSchema>;

export const PftDesignInspectionDataSchema = PftNativeReadDataSchema.safeExtend({
  document: name,
  object: z.object({ Name: name, Label: text.nullable(), TypeId: boundedText(256).nullable(),
    kind: z.enum(['body', 'sketch', 'feature', 'unsupported']), status: z.enum(['available', 'unsupported']),
    reason: text.nullable(), state: cached(z.array(text).max(100)), has_error_marker: z.boolean().nullable() }).strict(),
  body: z.object({ status: readStatus, tip: reference.nullable(), tip_status: readStatus,
    members: z.array(reference).max(100), member_count: count.nullable(), members_truncated: z.boolean() }).strict().nullable(),
  sketch: z.object({ geometry_count: cached(count), degrees_of_freedom: cached(count),
    fully_constrained: cached(z.boolean()), constraints, solver_invoked: z.literal(false) }).strict().nullable(),
  parameters: z.array(parameter).max(12), parameters_status: readStatus,
  parameters_source: z.literal('Object.PropertiesList').nullable(),
  expressions: z.object({ status: readStatus, items: z.array(z.object({ path: text.nullable(), expression: text.nullable(),
    status: readStatus }).strict()).max(50), total_count: count.nullable(), truncated: z.boolean(),
    evaluated: z.literal(false) }).strict(),
  warnings: z.array(text).max(30), recomputed: z.literal(false), solver_invoked: z.literal(false),
  geometry_validity: z.literal('not_verified'),
}).strict().superRefine((value, context) => {
  const unsupported = value.object.kind === 'unsupported';
  if ((value.body !== null) !== (value.object.kind === 'body') || (value.sketch !== null) !== (value.object.kind === 'sketch')
    || value.object.status !== (unsupported ? 'unsupported' : 'available')
    || (unsupported ? !['link_source_not_inspected', 'unsupported_object_type'].includes(value.object.reason ?? '') : value.object.reason !== null)
    || (unsupported && (value.parameters.length > 0 || value.parameters_status !== 'unsupported'
      || value.parameters_source !== null || value.expressions.status !== 'unsupported'))
    || new Set(value.parameters.map(item => item.name)).size !== value.parameters.length) {
    context.addIssue({ code: 'custom', message: '设计诊断类型、分节或参数重复' });
  }
  if (value.object.state.status !== 'available' && value.object.has_error_marker !== null) {
    context.addIssue({ code: 'custom', message: '对象状态不可读取时错误标记必须为未知' });
  }
  if (value.body !== null) {
    const body = value.body;
    const invalidMembers = body.status === 'available'
      ? body.member_count === null || body.members.length > Math.min(body.member_count, 100)
        || body.members_truncated !== (body.member_count > 100 || body.members.length < Math.min(body.member_count, 100))
      : body.member_count !== null || body.members.length > 0 || body.members_truncated;
    if (invalidMembers || (body.tip_status !== 'available' && body.tip !== null)) {
      context.addIssue({ code: 'custom', message: 'Body 成员、完整度或 Tip 状态不一致' });
    }
  }
  const expressions = value.expressions;
  const invalidExpressions = expressions.status === 'available'
    ? expressions.total_count === null || expressions.items.length !== Math.min(expressions.total_count, 50)
      || expressions.truncated !== (expressions.total_count > 50)
    : expressions.total_count !== null || expressions.items.length > 0 || expressions.truncated;
  if (invalidExpressions) {
    context.addIssue({ code: 'custom', message: '表达式清单状态或完整度不一致' });
  }
  if (value.expressions.items.some(item => item.status === 'available'
    ? item.path === null || item.expression === null : item.path !== null || item.expression !== null)) {
    context.addIssue({ code: 'custom', message: '表达式读取状态与文本不一致' });
  }
  if (value.parameters_status === 'available' ? value.parameters_source !== 'Object.PropertiesList'
    : value.parameters_source !== null || value.parameters.length > 0) {
    context.addIssue({ code: 'custom', message: '参数清单来源未知时不能冒充完整清单' });
  }
});
export type PftDesignInspectionData = z.output<typeof PftDesignInspectionDataSchema>;
export const PftDesignInspectionSuccessSchema = z.object({ ok: z.literal(true), source_kind: z.literal('live_freecad_gui'),
  observed_at: z.string(), data: PftDesignInspectionDataSchema }).strict();

type PftDesignTransport = { status: () => Promise<Record<string, unknown>>;
  read: (params: Record<string, unknown>) => Promise<unknown> };
type PftDesignErrorConstructor = new (code: string, message: string) => Error;

/** A new native-only read: no legacy provenance fallback, retry or CAD mutations. */
export async function pftInspectDesign(input: PftDesignInspectionInput, transport: PftDesignTransport,
  ErrorClass: PftDesignErrorConstructor): Promise<PftDesignInspectionData> {
  const parsedInput = PftDesignInspectionInputSchema.safeParse(input);
  if (!parsedInput.success) throw new ErrorClass('INVALID_ARGUMENT', '设计诊断参数无效；修订检查须同时提供文档实例 token。');
  const status = await transport.status();
  const capability = PftReadCapabilitySchema.safeParse(status.read_provenance);
  if (!Array.isArray(status.supported_methods) || !status.supported_methods.includes('inspect_design')
    || !capability.success || !capability.data.methods.includes('inspect_design')) {
    throw new ErrorClass('UNSUPPORTED_CAPABILITY', '当前 FreeCAD 插件未声明原生设计诊断与读取来源能力；请更新插件后重试。');
  }
  const { document, object, ...rest } = parsedInput.data;
  const value = await transport.read({ ...(document === undefined ? {} : { docName: document }), objectName: object, ...rest });
  let encoded: string | undefined;
  try { encoded = JSON.stringify(value); } catch { /* Treat non-JSON DTOs as invalid. */ }
  if (encoded !== undefined && Buffer.byteLength(encoded, 'utf8') > PFT_MAX_READ_BYTES) {
    throw new ErrorClass('READ_RESPONSE_TOO_LARGE', 'FreeCAD 设计诊断结果超过 128 KiB，请缩小约束分页范围。');
  }
  const parsed = encoded === undefined ? null : PftDesignInspectionDataSchema.safeParse(value);
  if (!parsed?.success || parsed.data.object.Name !== object
    || (document !== undefined && parsed.data.document !== document)
    || (rest.expectedDocumentInstanceToken !== undefined && parsed.data.document_instance_token !== rest.expectedDocumentInstanceToken)
    || (rest.expectedRevision !== undefined && parsed.data.revision !== rest.expectedRevision)
    || (parsed.data.sketch !== null && (parsed.data.sketch.constraints.offset !== rest.constraintOffset
      || parsed.data.sketch.constraints.limit !== rest.constraintLimit))) {
    throw new ErrorClass('INVALID_RESPONSE', 'FreeCAD 设计诊断返回无效、不完整或不匹配的原生数据。');
  }
  return parsed.data;
}
