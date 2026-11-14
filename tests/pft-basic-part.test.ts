// SPDX-License-Identifier: Apache-2.0
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { PftBasicPartInputSchema, pftBasicPartPreview } from '../src/pft-basic-part.js';
import { PftPartApprovalDataSchema } from '../src/pft-part-approval.js';

const part = { name: 'FixtureDisk', profile: 'circle', diameterMm: 12, thicknessMm: 2 } as const;
const token = 'neutral_basic_part_token_1';

test('basic part preview only accepts its explicit blank-document inputs and binds each displayed row', () => {
  assert.equal(PftBasicPartInputSchema.safeParse({ operation: 'create_basic_part', newPart: part }).success, true);
  for (const newPart of [{ ...part, widthMm: 2 }, { ...part, name: 'FixtureDisk\n' },
    { ...part, thicknessMm: Infinity }, { ...part, profile: 'annulus', diameterMm: undefined,
      innerDiameterMm: 4, outerDiameterMm: 4.1 }])
    assert.equal(PftBasicPartInputSchema.safeParse({ operation: 'create_basic_part', newPart }).success, false);
  assert.equal(PftBasicPartInputSchema.safeParse({ operation: 'create_basic_part', newPart: part, document: 'Other' }).success, false);

  const rows = [
    ['FixtureDisk', 'Document', 'New unsaved document'], ['Body', 'TypeId', 'PartDesign::Body'],
    ['Profile', 'Sketcher::SketchObject', 'circle'], ['Pad', 'TypeId', 'PartDesign::Pad'],
    ['Profile', 'Diameter (mm)', 12], ['Pad', 'Length (mm)', 2],
  ].map(([object, field, value]) => ({ object, field, old: null, new: value, status: 'change', reason: '' }));
  const preview = { document: part.name, operation: 'create_basic_part', state: 'prepared',
    token, ttl_seconds: 300, expires_at: '2026-01-01T00:05:00Z', can_apply: true, design_request: part,
    changed_count: 6, rows, requires_confirmation: true, saves_document: false, storage: 'memory_only' };
  assert.deepEqual(pftBasicPartPreview(preview, part), preview);
  assert.equal(pftBasicPartPreview({ ...preview, rows: [...rows.slice(0, -1), { ...rows.at(-1), new: 5 }] }, part), undefined);
  assert.equal(pftBasicPartPreview(preview, { ...part, diameterMm: 8 }), undefined);
});

test('applied basic part status requires native confirmation and a dimensionally consistent result for the new document', () => {
  const result = { kind: 'basic_part', profile: 'circle', document: part.name, body: 'Body', sketch: 'Profile',
    pad: 'Pad', dimensions_mm: { diameterMm: 12, thicknessMm: 2 }, bounds_mm: [-6, -6, 0, 6, 6, 2],
    volume_mm3: 72 * Math.PI, through_hole: false, saves_document: false };
  const status = { document: part.name, operation: 'create_basic_part', token, state: 'applied', changed_count: 6,
    requires_confirmation: true, saves_document: false, storage: 'memory_only', native_confirmation: true,
    result: { changed_count: 6, saves_document: false, design_result: result }, error_code: null };
  assert.equal(PftPartApprovalDataSchema.safeParse(status).success, true);
  for (const design_result of [{ ...result, document: 'Other' }, { ...result, volume_mm3: 17 },
    { ...result, through_hole: true }, { ...result, bounds_mm: [-6, -6, 0, 6, 6, 3] }])
    assert.equal(PftPartApprovalDataSchema.safeParse({ ...status, result: { ...status.result, design_result } }).success, false);
  assert.equal(PftPartApprovalDataSchema.safeParse({ ...status, native_confirmation: false }).success, false);
});
