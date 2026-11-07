# SPDX-License-Identifier: Apache-2.0
"""Independent paper geometry and strict DTO checks; no CAD or Qt runtime."""
from copy import deepcopy
import importlib.util
import itertools
import json
import math
from pathlib import Path
import unittest

path = Path(__file__).resolve().parents[2] / 'addon/PhoenixFreeCADTools/pft_draft_layout.py'
spec = importlib.util.spec_from_file_location('neutral_draft_slots', path)
layout = importlib.util.module_from_spec(spec)
spec.loader.exec_module(layout)


class DraftSlotsTests(unittest.TestCase):
    def plan(self, spans=(60., 40., 8.)):
        return layout.plan('Source', 'NewPage', ['NewFront', 'NewTop', 'NewIso'], spans)

    def rows(self, value):
        return [{'object': name, 'field': 'TechDraw draft node', 'old': None,
                 'new': 'Independent draft node', 'status': 'change', 'reason': '',
                 'source': 'cached_native_typed_snapshot', 'note': 'Independent finite estimate'}
                for name in [value['page'], 'NewTemplate', *(r['name'] for r in value['views'])]]

    def test_three_views_largest_finite_scale_and_fixed_paper_annotation_space(self):
        for spans in ((60.,40.,8.), (70.,50.,8.), (1e-6,2e-6,3e-6), (6000.,4000.,800.), (1000.,1.,1.), (1.,1.,1000.)):
            with self.subTest(spans=spans):
                value = self.plan(spans)
                self.assertEqual(layout.validate(value), value)
                self.assertEqual([r['kind'] for r in value['views']], ['front','top','axonometric'])
                self.assertLess(value['views'][1]['center_mm'][1], value['views'][0]['center_mm'][1])
                self.assertEqual(value['views'][0]['center_mm'][0], value['views'][1]['center_mm'][0])
                for row in value['views']:
                    a, b = row['estimated_geometry_bounds_mm'], row['annotation_envelope_mm']
                    for delta in (a[0]-b[0],a[1]-b[1],b[2]-a[2],b[3]-a[3]):
                        self.assertAlmostEqual(delta,20.,places=10)
                    self.assertTrue(b[0]>=10.-1e-8 and b[1]>=81.-1e-8 and b[2]<=287.+1e-8 and b[3]<=200.+1e-8)
                index=layout.SCALES.index(value['scale'])
                for scale in layout.SCALES[:index]:
                    x,y,z=spans
                    self.assertTrue(max(x*scale,x*scale)+40.+8.+(x+y)/math.sqrt(2.)*scale+40.>277.
                        or max((z+y)*scale+88.,(x+y+2*z)/math.sqrt(6.)*scale+40.)>119.)
                for left,right in itertools.combinations(value['views'],2):
                    a,b=left['annotation_envelope_mm'],right['annotation_envelope_mm']
                    self.assertTrue(a[2]<=b[0] or b[2]<=a[0] or a[3]<=b[1] or b[3]<=a[1])
        self.assertEqual(self.plan()['scale'],.5)

    def test_projected_spans_match_independent_eight_corner_projection_and_translation(self):
        spans=(73.,29.,11.)
        value=self.plan(spans)
        axes=(((1.,0.,0.),(0.,0.,1.)),((1.,0.,0.),(0.,1.,0.)),
              ((1/math.sqrt(2),1/math.sqrt(2),0.),(-1/math.sqrt(6),1/math.sqrt(6),2/math.sqrt(6))))
        for row,(axis_x,axis_y) in zip(value['views'],axes):
            for translation in ((0.,0.,0.),(123.,-57.,9.)):
                points=[tuple(c+t for c,t in zip(p,translation)) for p in itertools.product(*[(0.,s) for s in spans])]
                actual=[]
                for axis in (axis_x,axis_y):
                    projected=[sum(a*b for a,b in zip(axis,p)) for p in points]
                    actual.append((max(projected)-min(projected))*value['scale'])
                for a,b in zip(actual,row['projected_span_mm']):self.assertAlmostEqual(a,b,places=12)

    def test_no_space_is_whole_plan_failure_at_minimum_existing_scale(self):
        for spans in ((1e12,1.,1.),(1.,1.,1e12)):
            with self.assertRaises(layout.PftDraftLayoutError) as error:self.plan(spans)
            self.assertEqual(error.exception.code,'DRAWING_DRAFT_LAYOUT_NO_SPACE')

    def test_bad_native_names_or_values_never_coerce(self):
        for spans in ((True,40,8),(0,40,8),(-1,40,8),(math.nan,40,8),(math.inf,40,8),(10**1000,40,8),(5e-324,5e-324,5e-324),'60,40,8'):
            with self.subTest(spans=repr(spans)[:40]):
                with self.assertRaises(layout.PftDraftLayoutError):self.plan(spans)
        for name in ('','../Source','Source\n','Source\u200b'):
            with self.assertRaises(layout.PftDraftLayoutError):layout.plan(name,'Page',['F','T','I'],[60,40,8])
        with self.assertRaises(layout.PftDraftLayoutError):layout.plan('Source','Page',['F','F','I'],[60,40,8])
        value=layout.plan('中性源','中性页',['主视','俯视','轴测'],[60,40,8]);self.assertEqual(layout.validate(value),value)

    def test_policy_and_semantics_are_exact_while_derived_geometry_has_bounded_tolerance(self):
        original=self.plan()
        for change in (lambda v:v.update(version=1.),lambda v:v.update(version=True),lambda v:v.update(scale=.5+1e-10),
                       lambda v:v['paper'].update(width_mm=297.+1e-10),lambda v:v['annotation'].update(margin_mm=20.+1e-10),
                       lambda v:v['views'][0]['direction'].__setitem__(0,1e-10),lambda v:v['views'][0].update(rotation_degrees=1e-10),
                       lambda v:v['views'].reverse(),lambda v:v['views'].pop(),lambda v:v.update(projection_verification='ready'),
                       lambda v:v['views'][0]['center_mm'].__setitem__(0,v['views'][0]['center_mm'][0]+1e-5)):
            v=deepcopy(original);change(v)
            with self.assertRaises(layout.PftDraftLayoutError):layout.validate(v)
        value=deepcopy(original);value['views'][0]['center_mm'][0]+=1e-9
        frozen=layout.validate(value)
        self.assertEqual(frozen,value)  # Do not silently replace approved centers.
        value['views'][0]['center_mm'][0]+=1.;self.assertNotEqual(frozen,value)

    def test_unknown_keys_custom_containers_nonfinite_or_partial_views_refused(self):
        class Unknown(dict):
            def __getitem__(self,key):raise AssertionError('custom mapping must not be read')
        for change in (lambda v:v.update(extra=True),lambda v:v['views'][0].update(extra=True),
                       lambda v:v['views'].__setitem__(0,Unknown(v['views'][0])),
                       lambda v:v['views'][0].update(center_mm=tuple(v['views'][0]['center_mm'])),
                       lambda v:v['views'][0]['center_mm'].__setitem__(0,math.nan),
                       lambda v:v['views'][0]['center_mm'].__setitem__(0,True)):
            v=self.plan();change(v)
            with self.assertRaises(layout.PftDraftLayoutError):layout.validate(v)

    def test_preview_pair_binds_all_five_rows_source_and_original_request(self):
        dto=self.plan();pair={'draft_layout':dto,'draft_layout_reason':None};rows=self.rows(dto);request={'source':'Source','page':'NewPage'}
        result=layout.validate_preview(pair,request,rows,can_apply=True);self.assertEqual(result,pair)
        dto['views'][0]['center_mm'][0]=0.;self.assertNotEqual(pair,result)
        for change in (lambda r:r[1].update(object=r[0]['object']),lambda r:r[2].update(object='WrongView'),
                       lambda r:r[0].update(object='Source'),lambda r:r[3].update(source='unknown'),
                       lambda r:r[0].update(extra=True),lambda r:r[4].update(note='x'*2049)):
            dto=self.plan();rows=self.rows(dto);change(rows)
            with self.assertRaises(layout.PftDraftLayoutError):layout.validate_preview({'draft_layout':dto,'draft_layout_reason':None},request,rows,can_apply=True)

    def test_blocked_preview_has_no_page_coordinate_or_partial_rows(self):
        row={'object':'Source','field':'TechDraw draft node','old':None,'new':None,'status':'blocked','reason':'No complete layout'}
        request={'source':'Source','page':None}
        for code in layout.ERROR_CODES:
            pair={'draft_layout':None,'draft_layout_reason':code}
            self.assertEqual(layout.validate_preview(pair,request,[row],can_apply=False),pair)
        for pair in ({'draft_layout':None,'draft_layout_reason':None},{'draft_layout':self.plan(),'draft_layout_reason':'DRAWING_DRAFT_LAYOUT_NO_SPACE'}):
            with self.assertRaises(layout.PftDraftLayoutError):layout.validate_preview(pair,request,[row],can_apply=False)

    def test_budget_and_import_contract_are_finite_detached_json(self):
        value=self.plan()
        self.assertLessEqual(len(json.dumps(value).encode()),layout.MAX_RESULT_BYTES)
        self.assertEqual(layout.CAPABILITIES['max_input_bytes'],4096)
        self.assertEqual(layout.CAPABILITIES['max_result_bytes'],16384)
        self.assertFalse(layout.CAPABILITIES['saves_document'])
        with self.assertRaises(layout.PftDraftLayoutError):layout._budget({'value':'x'*4096},4096)


if __name__=='__main__':unittest.main()
