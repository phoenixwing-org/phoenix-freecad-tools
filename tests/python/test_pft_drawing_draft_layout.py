# SPDX-License-Identifier: Apache-2.0
"""Native API boundary fixtures; actual FreeCAD acceptance is independent."""
from copy import deepcopy
import hashlib
import sys
import types
import unittest
from unittest.mock import patch
import test_pft_drawing as fixtures

D,NS,controller=fixtures.drawing,fixtures.NS,fixtures.controller


class NativeDraftTests(unittest.TestCase):
    setUp_fixture=fixtures.DrawingTests.setUp
    error=fixtures.DrawingTests.error

    def setUp(self):
        self.setUp_fixture()
        self.app.Version=lambda:('1','1','4','Independent build')
        patches=(patch.dict(sys.modules,{'PySide6.QtCore':NS(qVersion=lambda:'6.8.3')}),
                 patch.object(D,'_DRAFT_TEMPLATE_SHA256',hashlib.sha256(self.template.read_bytes()).hexdigest()))
        for p in patches:p.start();self.addCleanup(p.stop)

    def prepare(self):return D.prepare_plan(self.app,self.gui,self.doc,self.source,controller,draft_layout=True)
    def apply(self,plan):return D.apply_plan(self.app,self.gui,self.doc,plan,controller)

    def test_opt_in_read_only_plan_has_exact_frozen_paper_slots_and_legacy_rows(self):
        plan,rows=self.prepare()
        self.assertEqual(plan['scale'],.5)
        self.assertEqual(plan['draft_layout']['source_spans_mm'],[60.,40.,8.])
        self.assertEqual(plan['request'],{'source':self.source.Name,'page':plan['names'][0]})
        self.assertTrue(all(set(r)=={'object','field','old','new','status','reason','source','note'} for r in rows))
        self.assertTrue(all('20mm' in r['note'] for r in rows[2:]))
        self.assertEqual((self.doc.created_calls,self.doc.events,self.doc.recompute_calls,self.prime_calls),([],[],[],[]))

    def test_no_opt_in_keeps_old_scale_xy_fields_and_zero_profile_or_sibling_reads(self):
        with patch.object(D,'_draft_profile',side_effect=AssertionError('legacy profile read')),patch.object(D,'_sibling',side_effect=AssertionError('legacy pure import')):
            plan,rows=D.prepare_plan(self.app,self.gui,self.doc,self.source,controller)
            result=self.apply(plan)
        self.assertEqual(plan['scale'],1.)
        self.assertFalse(any(k.startswith('_draft') or k=='draft_layout' for k in plan))
        self.assertEqual([(v.X,v.Y) for v in self.doc.getObject(result['page']).Views],[(85.,160.),(85.,100.),(215.,150.)])

    def test_exact_native_version_qt_and_actual_template_bytes_are_required(self):
        for mode in ('version','qt','template','boolean'):
            with self.subTest(mode=mode):
                self.setUp()
                if mode=='version':self.app.Version=lambda:('1','1','0')
                elif mode=='qt':sys.modules['PySide6.QtCore'].qVersion=lambda:'6.7.0'
                elif mode=='template':self.template.write_text(fixtures.SVG.replace('synthetic','different known-size geometry'))
                action=self.prepare if mode!='boolean' else lambda:D.prepare_plan(self.app,self.gui,self.doc,self.source,controller,draft_layout=1)
                self.error(action,'DRAWING_DRAFT_LAYOUT_UNAVAILABLE')
                self.assertEqual((self.doc.events,self.doc.created_calls),([],[]))

    def test_no_space_stops_before_native_initialization_or_transaction(self):
        self.source.Shape=fixtures.Shape(1e12,40.,8.)
        self.error(self.prepare,'DRAWING_DRAFT_LAYOUT_NO_SPACE')
        self.assertEqual((self.prime_calls,self.doc.events,self.doc.created_calls),([],[],[]))

    def test_owned_write_uses_frozen_specs_and_explicit_projection_flags(self):
        plan,_=self.prepare();result=self.apply(plan)
        self.assertEqual(self.doc.events,['open','commit']);self.assertEqual(len(self.doc.created_calls),5)
        page=self.doc.getObject(result['page']);self.assertEqual(page.Scale,.5)
        for view,row in zip(page.Views,plan['draft_layout']['views']):
            self.assertEqual([view.X,view.Y],row['center_mm']);self.assertEqual(view.Scale,.5)
            self.assertEqual(view.Rotation,0.);self.assertIs(view.Perspective,False);self.assertIs(view.CoarseView,False)
        self.assertEqual(len(self.doc.recompute_calls),1)
        self.assertEqual(result['projection_verification'],'not_checked')
        self.assertNotIn('draft_layout',result)

    def test_global_parent_aabb_is_used_without_double_transform_or_source_writes(self):
        self.source.Shape=fixtures.Shape(1000.,1.,1.)
        parent=fixtures.DrawingTests.parent(self)
        parent.Placement=fixtures.placement(quaternion=(0.,2**-.5,0.,2**-.5))
        original=self.source.Shape
        plan,_=self.prepare()
        for actual,expected in zip(plan['draft_layout']['source_spans_mm'],(1.,1.,1000.)):self.assertAlmostEqual(actual,expected,places=9)
        self.assertIs(self.source.Shape,original);self.assertEqual(self.doc.created_calls,[])

    def test_plan_mutations_between_preview_and_write_fail_before_projection(self):
        for mutate in (lambda p:p.update(scale=1.),lambda p:p['draft_layout']['views'][0]['center_mm'].__setitem__(0,100.),
                       lambda p:p.update(_draft_specs=D._SPECS),lambda p:p['request'].update(page='OtherPage'),
                       lambda p:p.update(names=('a','b','c','d','e'))):
            self.setUp();plan,_=self.prepare();mutate(plan)
            self.error(lambda:self.apply(plan),'DRAWING_DRAFT_LAYOUT_STALE')
            self.assertEqual((self.prime_calls,self.doc.events,self.doc.created_calls),([],[],[]))

    def test_projection_callback_cannot_replace_plan_or_drop_opt_in(self):
        for mode in ('scale','public','drop'):
            self.setUp();plan,_=self.prepare()
            def change(app):
                if mode=='scale':plan['scale']=1.
                elif mode=='public':plan['draft_layout']['views'][0]['center_mm'][0]+=1e-10
                else:
                    for key in ('draft_layout','_draft_specs','_draft_profile','_draft_stamp'):plan.pop(key)
            with patch.object(D,'_prime_projection_engine',side_effect=change):
                self.error(lambda:self.apply(plan),'DRAWING_DRAFT_LAYOUT_STALE')
            self.assertEqual((self.doc.events,self.doc.created_calls),([],[]))

    def test_constructor_callback_mutating_frozen_names_aborts_once_without_foreign_close(self):
        plan,_=self.prepare()
        self.doc.add_hook=lambda node:plan.update(names=('OtherPage',*plan['names'][1:]))
        self.error(lambda:self.apply(plan),'PART_ROLLBACK_UNVERIFIED',controller.PftWriteError)
        self.assertEqual(self.doc.events,['open','abort']);self.assertEqual(self.doc.Objects,[self.source])

    def test_native_setter_callback_cannot_change_slots_or_disable_opt_in(self):
        original=fixtures.DraftNode.__setattr__
        for mode in ('coordinate','spec','drop'):
            with self.subTest(mode=mode):
                self.setUp();plan,_=self.prepare();changed=[]
                def setter(node,name,value):
                    original(node,name,value)
                    if name=='Source' and hasattr(node,'Document') and not changed:
                        changed.append(True)
                        if mode=='coordinate':plan['draft_layout']['views'][0]['center_mm'][0]+=1e-10
                        elif mode=='spec':plan['_draft_specs']=D._SPECS
                        else:
                            for key in ('draft_layout','_draft_specs','_draft_profile','_draft_stamp'):plan.pop(key)
                with patch.object(fixtures.DraftNode,'__setattr__',setter):
                    self.error(lambda:self.apply(plan),'PART_ROLLBACK_UNVERIFIED',controller.PftWriteError)
                self.assertEqual(changed,[True]);self.assertEqual(self.doc.events,['open','abort'])
                self.assertEqual(self.doc.Objects,[self.source])

    def test_recompute_geometry_flags_or_native_positions_are_verified(self):
        for mode in ('rotation','perspective','coarse'):
            self.setUp();plan,_=self.prepare()
            def mutate(nodes):setattr(nodes[2],{'rotation':'Rotation','perspective':'Perspective','coarse':'CoarseView'}[mode],30. if mode=='rotation' else True)
            self.doc.recompute_hook=mutate
            self.error(lambda:self.apply(plan),'DRAWING_CREATE_FAILED');self.assertEqual(self.doc.events,['open','abort'])

    def test_native_recompute_position_drift_is_restored_only_to_the_frozen_new_plan(self):
        plan,_=self.prepare();self.doc.recompute_hook=lambda nodes:setattr(nodes[2],'X',0.)
        self.apply(plan);self.assertEqual(self.doc.Objects[3].X,plan['draft_layout']['views'][0]['center_mm'][0])

    def test_postcommit_changed_flags_are_uncertain_without_late_abort(self):
        plan,_=self.prepare();self.doc.commit_hook=lambda:setattr(self.doc.Objects[3],'Perspective',True)
        self.error(lambda:self.apply(plan),'PART_COMMIT_UNVERIFIED',controller.PftWriteError)
        self.assertEqual(self.doc.events,['open','commit'])

    def test_runtime_or_template_after_preview_changes_are_stale(self):
        for mode in ('runtime','template'):
            self.setUp();plan,_=self.prepare()
            if mode=='runtime':self.app.Version=lambda:('1','1','4','Changed build')
            else:self.template.write_text(fixtures.SVG.replace('synthetic','changed'))
            self.error(lambda:self.apply(plan))
            self.assertEqual(self.doc.events,[])


if __name__=='__main__':unittest.main()
