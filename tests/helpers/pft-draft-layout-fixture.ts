// SPDX-License-Identifier: Apache-2.0
/** Plain independently constructed spans, without CAD/Qt/config/model access. */
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import * as z from 'zod/v4';
import { PftDrawingDraftLayoutCapabilitySchema, PftDrawingDraftLayoutSchema } from '../../src/pft-draft-layout-contract.js';
const fixtureSchema = z.object({ capability: PftDrawingDraftLayoutCapabilitySchema,
  plans: z.array(PftDrawingDraftLayoutSchema), no_space_code: z.literal('DRAWING_DRAFT_LAYOUT_NO_SPACE') }).strict();
export function neutralDraftLayouts() {
  const path = fileURLToPath(new URL('../../addon/PhoenixFreeCADTools/pft_draft_layout.py', import.meta.url));
  const script = `import importlib.util,json,sys
spec=importlib.util.spec_from_file_location('neutral_draft_layout',sys.argv[1])
f=importlib.util.module_from_spec(spec);spec.loader.exec_module(f)
spans=[[60,40,8],[70,50,8],[.006,.004,.0008],[6000,4000,800],[10000,1,1],[1,1,10000],[18,18,13],[18,18,13.000000000001]]
plans=[f.plan('NeutralPart','NeutralPage',['NeutralFront','NeutralTop','NeutralIso'],value) for value in spans]
for value in plans:f.validate(value)
try:f.plan('NeutralPart','NeutralPage',['NeutralFront','NeutralTop','NeutralIso'],[1e12,1e12,1e12])
except f.PftDraftLayoutError as error:no_space=error.code
else:raise AssertionError('Expected bounded no-space result')
assert 'FreeCAD' not in sys.modules and not any(name.startswith('PySide') for name in sys.modules)
print(json.dumps({'capability':f.CAPABILITIES,'plans':plans,'no_space_code':no_space},ensure_ascii=False))
`;
  const result = spawnSync('python3', ['-I', '-B', '-c', script, path], { encoding: 'utf8', maxBuffer: 128 * 1024 });
  assert.equal(result.status, 0, result.stderr);
  const value: unknown = JSON.parse(result.stdout);
  return fixtureSchema.parse(value);
}
