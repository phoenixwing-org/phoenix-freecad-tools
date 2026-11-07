// SPDX-License-Identifier: Apache-2.0
/** Independently constructed plain geometry; no FreeCAD, Qt, accounts, files or network. */
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import * as z from 'zod/v4';
import { PftDrawingLayoutGraphicsSchema } from '../../src/pft-drawing-layout-contract.js';

const fixtureSchema = z.object({ name: z.string(), graphics: PftDrawingLayoutGraphicsSchema }).strict();
export type NeutralLayoutCase = z.output<typeof fixtureSchema>;
let cached: NeutralLayoutCase[] | undefined;
export function neutralLayoutCases(): NeutralLayoutCase[] {
  if (cached === undefined) {
    const path = fileURLToPath(new URL('../python/test_pft_drawing_layout.py', import.meta.url));
    const script = `import importlib.util,json,sys
spec=importlib.util.spec_from_file_location('neutral_layout_cases',sys.argv[1])
f=importlib.util.module_from_spec(spec);spec.loader.exec_module(f)
rows=[]
def add(name,value):
 result=f.layout.suggest_layout(value)
 f.layout.validate_result(result,expected=value)
 rows.append({'name':name,'graphics':{'version':1,'input':value,'result':result}})
add('five',f.source(True))
for scale in (1,.5,.1,.05):add('scale_'+str(scale),f.rectangle_sheet(scale))
value=f.source(True);old=f.layout.suggest_layout(value)['dimensions'][-1]
value['dimensions'][-1]=f.explicit(value['dimensions'][-1],old['x_mm'],old['y_mm'])
add('mixed',value)
value=f.source();value['dimensions']=[f.circle(),f.circle(kind='Diameter')];add('same_circle',value)
value=f.rectangle_sheet(.1);value['dimensions']=[f.explicit(value['dimensions'][0],-17,-11.5),f.explicit(value['dimensions'][1],-17,-20.5)];add('shared_projection',value)
value=f.source();value['dimensions']=[f.line(kind='Distance',start=[80,100],end=[119.4,128.3])];add('diagonal',value)
value=f.source(True);value['paper']['reserved_rects']=[[0,0,297,210]];add('blocked',value)
value=f.source();value['paper'].update(width_mm=10000,height_mm=300);value['view'].update(origin_mm=[500,205],bounds_mm=[100,200,850,210]);value['spacing']['lane_mm']=100
value['dimensions']=[f.line('Edge'+str(i),start=[100+i*100,200],end=[150+i*100,200]) for i in range(8)]
initial=f.layout.suggest_layout(value)
for i in range(1,8):value['dimensions'][i]=f.explicit(value['dimensions'][i],initial['dimensions'][i]['x_mm'],initial['dimensions'][i]['y_mm'])
for row in value['dimensions']:row['text']='📐'*256
value['obstacles']['segments']=[[[6000.123456789011+i,7000.234567890122+i],[6100.345678901234+i,7100.456789012345+i]] for i in range(512)]
value['obstacles']['rects']=[{'name':'障'*2+str(i),'kind':'view','bounds_mm':[8000.123456789011+i,8000.234567890122+i,8100.345678901234+i,8100.456789012345+i]} for i in range(128)]
add('bounded_eight',value)
assert 'FreeCAD' not in sys.modules and not any(name.startswith('PySide') for name in sys.modules)
print(json.dumps(rows,ensure_ascii=False,separators=(',',':')))
`;
    const run = spawnSync('python3', ['-I', '-B', '-c', script, path], { encoding: 'utf8', maxBuffer: 1024 * 1024 });
    assert.equal(run.status, 0, run.stderr);
    const value: unknown = JSON.parse(run.stdout);
    cached = z.array(fixtureSchema).parse(value);
  }
  return structuredClone(cached);
}

import type { PftDrawingDimensionsPreview } from '../../src/pft-drawing-dimensions.js';
export function neutralAutoPreview(name = 'mixed'): PftDrawingDimensionsPreview {
  const graphics = neutralLayoutCases().find(row => row.name === name)!.graphics;
  const intent = { page: 'NeutralPage', view: graphics.input.view.name, dimensions: graphics.input.dimensions.map(row =>
    row.placement === 'auto' ? { edge: row.edge, kind: row.kind, placement: 'auto' as const }
      : { edge: row.edge, kind: row.kind, x_mm: row.x_mm, y_mm: row.y_mm }) };
  const design_request = graphics.result.can_apply ? { page: intent.page, view: intent.view,
    dimensions: graphics.result.dimensions.map(({ edge, kind, x_mm, y_mm }) => ({ edge, kind, x_mm, y_mm })) } : null;
  const rows = graphics.result.can_apply ? graphics.result.dimensions.map((row, index) => ({ object: 'NeutralDimension' + index,
    field: 'TechDraw dimension' as const, old: null, new: 50 + index, status: 'change' as const, edge: row.edge, kind: row.kind,
    value_mm: 50 + index, x_mm: row.x_mm, y_mm: row.y_mm, source: 'native_cached_projected_geometry' as const,
    note: 'Neutral cached geometry; paper-mm placement estimate; native confirmation remains required.' }))
    : [{ object: intent.page, field: 'TechDraw dimension' as const, old: null, new: null, status: 'blocked' as const,
      reason: 'Neutral complete-batch layout conflict.' }];
  return { document: 'NeutralDocument', operation: 'add_drawing_dimensions', token: 'neutral_layout_confirmation_token', state: 'prepared',
    can_apply: graphics.result.can_apply, expires_at: '2030-01-01T00:00:00Z', ttl_seconds: 300,
    changed_count: design_request?.dimensions.length ?? 0, rows, requires_confirmation: true, saves_document: false,
    storage: 'memory_only', design_request, layout: { version: 1, layout_verification: 'suggested_only', intent, graphics, reason_code: null } };
}
export function neutralAutoInput(name = 'mixed') {
  const preview = neutralAutoPreview(name);
  return { document: preview.document, operation: preview.operation, page: preview.layout!.intent.page, view: preview.layout!.intent.view,
    dimensions: preview.layout!.intent.dimensions.map(row => 'placement' in row ? { edge: row.edge, kind: row.kind, placement: 'auto' as const }
      : { edge: row.edge, kind: row.kind, xMm: row.x_mm, yMm: row.y_mm }) };
}
