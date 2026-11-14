// SPDX-License-Identifier: Apache-2.0
import * as z from 'zod/v4';

export const PFT_BASIC_PART_OPERATION = 'create_basic_part';
export const PFT_BASIC_PART_CAPABILITY = Object.freeze({
  version: 1, operation: PFT_BASIC_PART_OPERATION, confirmation: 'native_gui_only',
  target: 'new_unsaved_document', profiles: Object.freeze(['annulus', 'rectangle', 'circle']),
  length_unit: 'mm', minimum_mm: 0.1, maximum_mm: 1000, saves_document: false,
} as const);
export const PftBasicPartCapabilitySchema = z.object({
  version: z.literal(1), operation: z.literal(PFT_BASIC_PART_OPERATION), confirmation: z.literal('native_gui_only'),
  target: z.literal('new_unsaved_document'), profiles: z.tuple([z.literal('annulus'), z.literal('rectangle'), z.literal('circle')]),
  length_unit: z.literal('mm'), minimum_mm: z.literal(0.1), maximum_mm: z.literal(1000), saves_document: z.literal(false),
}).strict();

const documentName = z.string().regex(/^[A-Za-z][A-Za-z0-9_]{0,63}(?![\s\S])/u);
const size = z.number().min(PFT_BASIC_PART_CAPABILITY.minimum_mm).max(PFT_BASIC_PART_CAPABILITY.maximum_mm);
const base = { name: documentName, thicknessMm: size };
const annulusFields = { outerDiameterMm: size, innerDiameterMm: size };
const rectangleFields = { widthMm: size, heightMm: size };
const circleFields = { diameterMm: size };
const validWall = (outer: number, inner: number): boolean => (outer - inner) / 2 + 1e-10 >= PFT_BASIC_PART_CAPABILITY.minimum_mm;

export const PftBasicPartRequestSchema = z.discriminatedUnion('profile', [
  z.strictObject({ profile: z.literal('annulus'), ...base, ...annulusFields }),
  z.strictObject({ profile: z.literal('rectangle'), ...base, ...rectangleFields }),
  z.strictObject({ profile: z.literal('circle'), ...base, ...circleFields }),
]).refine(value => value.profile !== 'annulus' || validWall(value.outerDiameterMm, value.innerDiameterMm),
  '圆环的径向壁厚至少为 0.1 mm');
export const PftBasicPartInputSchema = z.strictObject({
  operation: z.literal(PFT_BASIC_PART_OPERATION), newPart: PftBasicPartRequestSchema,
});
export type PftBasicPartInput = z.output<typeof PftBasicPartInputSchema>;
type Request = PftBasicPartInput['newPart'];

const rowSchema = z.strictObject({
  object: documentName, field: z.string().min(1).max(32), old: z.null(),
  new: z.union([size, z.string().min(1).max(32)]), status: z.literal('change'), reason: z.literal(''),
});
type RowValue = [object: string, field: string, value: string | number];

export function pftBasicPartRowCount(profile: Request['profile']): number {
  return profile === 'circle' ? 6 : 7;
}

function expectedRows(request: Request): RowValue[] {
  const rows: RowValue[] = [
    [request.name, 'Document', 'New unsaved document'], ['Body', 'TypeId', 'PartDesign::Body'],
    ['Profile', 'Sketcher::SketchObject', request.profile], ['Pad', 'TypeId', 'PartDesign::Pad'],
  ];
  if (request.profile === 'annulus') rows.push(['Profile', 'OuterDiameter (mm)', request.outerDiameterMm],
    ['Profile', 'InnerDiameter (mm)', request.innerDiameterMm]);
  if (request.profile === 'rectangle') rows.push(['Profile', 'Width (mm)', request.widthMm],
    ['Profile', 'Height (mm)', request.heightMm]);
  if (request.profile === 'circle') rows.push(['Profile', 'Diameter (mm)', request.diameterMm]);
  rows.push(['Pad', 'Length (mm)', request.thicknessMm]);
  return rows;
}

const previewSchema = z.strictObject({
  operation: z.literal(PFT_BASIC_PART_OPERATION), document: documentName, design_request: PftBasicPartRequestSchema,
  token: z.string().regex(/^[A-Za-z0-9_-]{16,128}(?![\s\S])/u), state: z.literal('prepared'),
  can_apply: z.literal(true), expires_at: z.iso.datetime(), ttl_seconds: z.literal(300),
  changed_count: z.union([z.literal(6), z.literal(7)]), rows: z.array(rowSchema).min(6).max(7),
  requires_confirmation: z.literal(true), saves_document: z.literal(false), storage: z.literal('memory_only'),
});
export type PftBasicPartPreview = z.output<typeof previewSchema>;

export function pftBasicPartPreview(value: unknown, expected: Request): PftBasicPartPreview | undefined {
  const parsed = previewSchema.safeParse(value);
  if (!parsed.success) return;
  const preview = parsed.data;
  const rows = expectedRows(expected);
  if (preview.document !== expected.name
    || JSON.stringify(expectedRows(preview.design_request)) !== JSON.stringify(rows)
    || preview.rows.length !== rows.length || preview.changed_count !== rows.length
    || rows.some(([object, field, datum], index) => {
      const row = preview.rows[index];
      return row?.object !== object || row.field !== field || row.new !== datum;
    })) return;
  return preview;
}

const coordinate = z.number().min(-1000.0000001).max(1000.0000001);
const resultBase = {
  kind: z.literal('basic_part'), document: documentName, body: z.literal('Body'),
  sketch: z.literal('Profile'), pad: z.literal('Pad'), saves_document: z.literal(false),
  bounds_mm: z.tuple([coordinate, coordinate, coordinate, coordinate, coordinate, coordinate]),
  volume_mm3: z.number().positive().max(1.0000001e9),
};
const closeEnough = (actual: number, expected: number): boolean => (
  Math.abs(actual - expected) <= Math.max(1e-7, 1e-8 * Math.max(Math.abs(actual), Math.abs(expected)))
);

export const PftBasicPartResultSchema = z.discriminatedUnion('profile', [
  z.strictObject({ ...resultBase, profile: z.literal('annulus'), through_hole: z.literal(true),
    dimensions_mm: z.strictObject({ ...annulusFields, thicknessMm: size }) }),
  z.strictObject({ ...resultBase, profile: z.literal('rectangle'), through_hole: z.literal(false),
    dimensions_mm: z.strictObject({ ...rectangleFields, thicknessMm: size }) }),
  z.strictObject({ ...resultBase, profile: z.literal('circle'), through_hole: z.literal(false),
    dimensions_mm: z.strictObject({ ...circleFields, thicknessMm: size }) }),
]).refine(value => {
  let width: number, height: number, area: number;
  if (value.profile === 'rectangle') {
    ({ widthMm: width, heightMm: height } = value.dimensions_mm);
    area = width * height;
  } else {
    const outer = value.profile === 'annulus' ? value.dimensions_mm.outerDiameterMm : value.dimensions_mm.diameterMm;
    const inner = value.profile === 'annulus' ? value.dimensions_mm.innerDiameterMm : 0;
    if (value.profile === 'annulus' && !validWall(outer, inner)) return false;
    width = height = outer;
    area = Math.PI * (outer - inner) * (outer + inner) / 4;
  }
  const length = value.dimensions_mm.thicknessMm;
  const expectedBounds = [-width / 2, -height / 2, 0, width / 2, height / 2, length];
  return closeEnough(value.volume_mm3, area * length)
    && value.bounds_mm.every((bound, index) => closeEnough(bound, expectedBounds[index]!));
}, '新建结果的包围盒或体积与原生尺寸不一致');
