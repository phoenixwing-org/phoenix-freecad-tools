# SPDX-License-Identifier: Apache-2.0
"""Pure cached-design contracts; no FreeCAD, solving, GUI or native process."""
import importlib.util
import json
from pathlib import Path
import sys
import threading
import types
import unittest
from unittest.mock import patch

ADDON = Path(__file__).resolve().parents[2] / 'addon' / 'PhoenixFreeCADTools'
spec = importlib.util.spec_from_file_location('pft_design_inspection_tests', ADDON / 'pft_design_inspection.py')
design = importlib.util.module_from_spec(spec); spec.loader.exec_module(design)


class Failure(Exception):
    def __init__(self, code, message): super().__init__(message); self.code = code


class Quantity:
    def __init__(self, value, signature=(1,0,0,0,0,0,0,0)):
        self.Value = value
        self.Unit = types.SimpleNamespace(Signature=signature, Type='Angle' if signature[-1] else 'Length')
    @property
    def UserString(self): raise AssertionError('display schema must not be read')


class Document:
    def __init__(self, name='PublicDoc'):
        self.Name = name; self.objects = {}
    def getObject(self, name): return self.objects.get(name)
    @property
    def FileName(self): raise AssertionError('FileName forbidden')
    def recompute(self): raise AssertionError('recompute forbidden')
    def save(self): raise AssertionError('save forbidden')


class Object:
    def __init__(self, document, name='Sketch', kind='Sketcher::SketchObject'):
        self.Document, self.Name, self.Label, self.TypeId = document, name, name, kind
        self.State, self.PropertiesList, self.ExpressionEngine = [], [], []
        self._types = {}; document.objects[name] = self
    def getTypeIdOfProperty(self, name): return self._types.get(name, 'App::PropertyString')
    def solve(self): raise AssertionError('solve forbidden')
    def recompute(self): raise AssertionError('recompute forbidden')
    @property
    def Shape(self): raise AssertionError('Shape/isValid forbidden')
    @property
    def FileName(self): raise AssertionError('FileName forbidden')


class Constraint:
    def __init__(self, index):
        self.Type, self.Name = ('Angle' if index % 2 else 'Distance'), 'Constraint%d' % index
        self.First, self.FirstPos, self.Second, self.SecondPos, self.Third, self.ThirdPos = index, 1, -1, 0, -2000, 0
    @property
    def Value(self): raise AssertionError('raw angular/radian Constraint.Value forbidden')


class Application:
    def __init__(self, document):
        self.ActiveDocument = document; self.documents = {document.Name: document}; self.observers = []
    def getDocument(self, name): return self.documents.get(name)
    def addDocumentObserver(self, observer): self.observers.append(observer)
    def removeDocumentObserver(self, observer): self.observers.remove(observer)
    def setActiveDocument(self, name): raise AssertionError('activation forbidden')


class DesignInspectionTests(unittest.TestCase):
    def setUp(self):
        self.doc = Document(); self.obj = Object(self.doc); self.app = Application(self.doc)
        self.gui = types.SimpleNamespace()  # No selection/view/solve API is needed.
        self.ops = design._operations(Failure); self.helper = self.ops._document_state_helper()
        self.helper._tracker.close(); self.addCleanup(self.helper._tracker.close)
        self.obj.GeometryCount, self.obj.DoF, self.obj.FullyConstrained = 2, 4, False
        self.obj.Constraints = [Constraint(index) for index in range(153)]
        self.datum_reads, self.driving_reads, self.active_reads = [], [], []
        def datum(index):
            self.datum_reads.append(index)
            return Quantity(30.0 if index % 2 else 10.0, (0,0,0,0,0,0,0,1) if index % 2 else (1,0,0,0,0,0,0,0))
        def driving(index): self.driving_reads.append(index); return index % 2 == 0
        def active(index): self.active_reads.append(index); return index % 3 != 0
        self.obj.getDatum, self.obj.getDriving, self.obj.getActive = datum, driving, active

    def read(self, **params):
        params.setdefault('objectName', self.obj.Name)
        result = design.perform('inspect_design', params, self.app, self.gui, Failure)
        json.dumps(result, allow_nan=False)
        return result

    def assert_error(self, code, callback):
        with self.assertRaises(Failure) as error: callback()
        self.assertEqual(error.exception.code, code)

    def test_strict_request_guards_reject_without_native_access(self):
        self.assertFalse(design.validate('another_method', None, Failure))
        self.assertFalse(design.validate([], None, Failure))
        for params in (None, {}, {'objectName':''}, {'objectName':'bad\x00name'}, {'objectName':'Sketch','docName':None},
                       {'objectName':'Sketch','unexpected':True}, {'objectName':'Sketch','constraintOffset':True},
                       {'objectName':'Sketch','constraintOffset':-1}, {'objectName':'Sketch','constraintOffset':2147483648},
                       {'objectName':'Sketch','constraintLimit':0}, {'objectName':'Sketch','constraintLimit':101},
                       {'objectName':'Sketch','constraintLimit':1.5}, {'objectName':'Sketch','expectedRevision':0},
                       {'objectName':'Sketch','expectedDocumentInstanceToken':'short'},
                       {'objectName':'Sketch','expectedDocumentInstanceToken':'x'*32,'expectedRevision':True},
                       {'objectName':'Sketch','expectedDocumentInstanceToken':'x'*32,'expectedRevision':9007199254740992}):
            with self.subTest(params=params):
                self.assert_error('INVALID_PARAMS', lambda: design.perform('inspect_design',params,None,None,Failure))
        self.assert_error('METHOD_NOT_FOUND', lambda: design.perform('other',{},None,None,Failure))

    def test_default_page_cached_sources_and_units_never_read_raw_constraint_value(self):
        result = self.read(); sketch = result['sketch']; page = sketch['constraints']
        self.assertEqual(sketch['geometry_count'], {'value':2,'status':'available','source':'Sketch.GeometryCount'})
        self.assertEqual(sketch['degrees_of_freedom'], {'value':4,'status':'available','source':'Sketch.DoF'})
        self.assertEqual(sketch['fully_constrained']['value'], False)
        self.assertEqual((page['offset'],page['limit'],page['total_count'],page['has_more'],page['next_offset']), (0,50,153,True,50))
        self.assertEqual([row['index'] for row in page['items']], list(range(50)))
        self.assertEqual(self.datum_reads,list(range(50))); self.assertEqual(self.driving_reads,list(range(50)))
        self.assertEqual(self.active_reads,list(range(50)))
        self.assertEqual(page['items'][0]['is_active'], {'value':False,'status':'available','source':'Sketch.getActive'})
        self.assertTrue(page['items'][0]['is_driving']['value'])
        angle = page['items'][1]['datum']['value']; length = page['items'][0]['datum']['value']
        self.assertEqual((angle['value'], angle['unit'], angle['unit_signature']), (30.0,'deg',[0,0,0,0,0,0,0,1]))
        self.assertEqual((length['value'], length['unit']), (10.0,'mm'))
        self.assertEqual(page['items'][0]['Third'], -2000)
        self.assertFalse(result['solver_invoked']); self.assertFalse(result['recomputed'])
        self.assertEqual(result['geometry_validity'],'not_verified')
        self.assertEqual(result['object']['has_error_marker'], False)
        self.assertEqual(len(self.app.observers),1)

    def test_pagination_offsets_last_and_empty_pages_are_consistent(self):
        for offset, limit, count, more, following in ((50,100,100,True,150),(150,100,3,False,None),(153,1,0,False,None),(2147483647,1,0,False,None)):
            with self.subTest(offset=offset):
                self.datum_reads.clear(); page=self.read(constraintOffset=offset,constraintLimit=limit)['sketch']['constraints']
                self.assertEqual((len(page['items']),page['has_more'],page['next_offset']),(count,more,following))
                self.assertEqual(self.datum_reads,list(range(offset,offset+count)))

    def test_missing_failed_and_invalid_cached_apis_remain_explicit_unknown(self):
        del self.obj.DoF; del self.obj.GeometryCount; del self.obj.getDriving
        self.obj.FullyConstrained = 'false'
        self.obj.getDatum=lambda index: (_ for _ in ()).throw(RuntimeError('/private/native/error'))
        self.obj.Constraints=[Constraint(0)]
        result=self.read(); sketch=result['sketch']; item=sketch['constraints']['items'][0]
        self.assertEqual(sketch['degrees_of_freedom']['status'],'unavailable')
        self.assertEqual(sketch['fully_constrained']['status'],'unsupported')
        self.assertEqual(item['is_driving'], {'value':None,'status':'unavailable','source':None})
        self.assertEqual(item['datum'], {'value':None,'status':'read_failed','source':None})
        self.assertNotIn('/private',json.dumps(result))
        self.obj.DoF=-1
        self.assertEqual(self.read()['sketch']['degrees_of_freedom']['status'],'unsupported')
        del self.obj.Constraints
        page=self.read()['sketch']['constraints']
        self.assertEqual((page['status'],page['total_count'],page['items'],page['has_more'],page['next_offset']),('unavailable',None,[],None,None))

    def test_body_members_are_bounded_same_document_and_tip_is_explicit(self):
        self.obj.TypeId='PartDesign::Body'
        self.obj.Group=[Object(self.doc,'Member%d'%index,'PartDesign::Pad') for index in range(103)]
        self.obj.Tip=self.obj.Group[-1]
        result=self.read(); body=result['body']
        self.assertIsNone(result['sketch'])
        self.assertEqual(body['tip'],{'object':'Member102','subelements':[]})
        self.assertEqual((body['member_count'],len(body['members']),body['members_truncated']),(103,100,True))
        self.obj.Tip=None
        self.assertEqual(self.read()['body']['tip_status'],'available')
        external=Object(Document('External'),'NeverReadExternal')
        self.obj.Tip=external
        self.assertEqual(self.read()['body']['tip_status'],'unsupported')

    def test_feature_parameters_use_property_type_whitelist_and_safe_quantities_and_references(self):
        self.obj.TypeId='PartDesign::Pad'
        profile=Object(self.doc,'Profile')
        values={'Length':Quantity(12.5),'Length2':Quantity(3),'Angle':Quantity(30,(0,0,0,0,0,0,0,1)),
                'Midplane':True,'Reversed':False,'Type':'Length','Profile':(profile,['Edge1'])}
        self.obj.PropertiesList=list(values)+['SecretPath']
        self.obj._types={'Length':'App::PropertyLength','Length2':'App::PropertyLength','Angle':'App::PropertyAngle',
                         'Midplane':'App::PropertyBool','Reversed':'App::PropertyBool','Type':'App::PropertyEnumeration','Profile':'App::PropertyLinkSub'}
        for name,value in values.items():setattr(self.obj,name,value)
        rows={row['name']:row for row in self.read()['parameters']}
        self.assertEqual(set(rows),set(values))
        self.assertTrue(all(row['status']=='available' for row in rows.values()))
        self.assertEqual(rows['Profile']['value'],{'kind':'reference','object':'Profile','subelements':['Edge1']})
        self.assertEqual(rows['Length']['value']['unit'],'mm')
        self.obj._types['Length']='App::PropertyFile'
        self.assertEqual(self.read()['parameters'][0]['status'],'unsupported')
        self.obj._types['Length']='App::PropertyLength'; self.obj.Length=Quantity(float('nan'))
        self.assertIsNone(self.read()['parameters'][0]['value']['value'])

    def test_parameter_collection_status_distinguishes_empty_missing_invalid_and_failed(self):
        result = self.read(constraintLimit=1)
        self.assertEqual((result['parameters'],result['parameters_status'],result['parameters_source']),([], 'available', 'Object.PropertiesList'))
        del self.obj.PropertiesList
        result = self.read(constraintLimit=1)
        self.assertEqual((result['parameters'],result['parameters_status'],result['parameters_source']),([], 'unavailable', None))
        for invalid in (None, 'Length', [12]):
            with self.subTest(invalid=invalid):
                self.obj.PropertiesList=invalid
                result=self.read(constraintLimit=1)
                self.assertEqual((result['parameters'],result['parameters_status'],result['parameters_source']),([], 'unsupported', None))
        def broken(_self): raise RuntimeError('/private/native/property-list-failed')
        with patch.object(Object, 'PropertiesList', new=property(broken), create=True):
            result=self.read(constraintLimit=1)
            self.assertEqual((result['parameters'],result['parameters_status'],result['parameters_source']),([], 'read_failed', None))
            self.assertNotIn('/private',json.dumps(result))

    def test_constraint_active_cache_is_independent_of_driving_and_preserves_unknowns(self):
        self.obj.Constraints=self.obj.Constraints[:1]
        row=self.read()['sketch']['constraints']['items'][0]
        self.assertFalse(row['is_active']['value']);self.assertTrue(row['is_driving']['value'])
        del self.obj.getActive
        row=self.read()['sketch']['constraints']['items'][0]
        self.assertEqual(row['is_active'],{'value':None,'status':'unavailable','source':None})
        self.obj.getActive=lambda index:'false'
        self.assertEqual(self.read()['sketch']['constraints']['items'][0]['is_active']['status'],'unsupported')
        def broken(index): raise RuntimeError('/private/native/active-error')
        self.obj.getActive=broken
        result=self.read();row=result['sketch']['constraints']['items'][0]
        self.assertEqual(row['is_active'],{'value':None,'status':'read_failed','source':None})
        self.assertNotIn('/private',json.dumps(result))

    def test_link_and_unsupported_objects_do_not_inspect_source_or_expressions(self):
        self.obj.TypeId='App::Link'; self.obj.State=['Invalid']
        class DangerousSource:
            def __getattribute__(self,name): raise AssertionError('no link source access')
        self.obj.LinkedObject=DangerousSource()
        result=self.read()
        self.assertEqual(result['object']['kind'],'unsupported')
        self.assertEqual(result['object']['reason'],'link_source_not_inspected')
        self.assertTrue(result['object']['has_error_marker'])
        self.assertEqual(result['parameters'],[]);self.assertIsNone(result['sketch']);self.assertIsNone(result['body'])
        self.assertEqual(self.datum_reads,[])
        self.obj.TypeId='Mesh::Feature'
        self.assertEqual(self.read()['object']['reason'],'unsupported_object_type')

    def test_expressions_are_bounded_redacted_and_never_evaluated(self):
        self.obj.ExpressionEngine=[('Length','10 mm'),('Length2','/private/confidential.FCStd'),('Angle','x'*513)]+[('Radius','2 mm')]*51
        expressions=self.read()['expressions']
        self.assertEqual((expressions['total_count'],len(expressions['items']),expressions['truncated'],expressions['evaluated']),(54,50,True,False))
        self.assertEqual(expressions['items'][0],{'path':'Length','expression':'10 mm','status':'available'})
        self.assertEqual(expressions['items'][1],{'path':None,'expression':None,'status':'unsupported'})
        self.assertEqual(expressions['items'][2]['status'],'unsupported')

    def test_expression_arithmetic_division_is_preserved_without_relaxing_global_path_filter(self):
        expressions = ('Pad.Length / 2', '(Pad.Length + 2 mm) / (2 + 3)', 'Length / -2',
                       '10 mm / 2', 'Pad.Length/2', 'Spreadsheet.A1 * 2', 'Constraints.Width + 1 mm')
        self.assertTrue(self.ops._is_private_text('Pad.Length / 2'))
        self.obj.ExpressionEngine=[('Length',value) for value in expressions]
        rows=self.read()['expressions']['items']
        self.assertEqual([row['expression'] for row in rows],list(expressions))
        self.assertTrue(all(row['status']=='available' for row in rows))
        # Only expression text uses the arithmetic recognizer; global text stays conservative.
        self.assertTrue(self.ops._is_private_text('Pad.Length / 2'))
        self.obj.Label='Pad.Length / 2'
        self.assertIsNone(self.read()['object']['Label'])

    def test_expression_paths_urls_source_controls_and_ambiguous_syntax_are_not_echoed(self):
        expressions = ('/Users/private/model.FCStd', '../models/part.FCStd', './models/part.step',
                       'models/part.FCStd', 'models/part', 'model.FCStd / 2',
                       'file:/private/model.FCStd', 'file:///private/model.FCStd',
                       'C:\\private\\model.FCStd', '\\\\server\\private\\model.FCStd',
                       'https://private.example/model', 'http://127.0.0.1/private',
                       '"../private/model.FCStd"', '__import__("os").system("private")',
                       'open("/private/model").read()', 'import os', 'Length; private()',
                       '<svg private="data"/>', 'Pad.Length / 2\n', 'Pad.Length\u202e / 2',
                       'Pad.Length / PrivateName', 'unknown_name', 'sin(Angle)',
                       'private.example/2', 'private.csv / 2', 'models.path/2', 'Unknown.Member / 2')
        self.obj.ExpressionEngine=[('Length',value) for value in expressions]
        result=self.read();rows=result['expressions']['items']
        self.assertEqual(len(rows),len(expressions))
        self.assertTrue(all(row=={'path':None,'expression':None,'status':'unsupported'} for row in rows))
        text=json.dumps(result)
        for marker in ('/Users/private','private.example','__import__','<svg','model.FCStd'):
            self.assertNotIn(marker,text)

    def test_same_source_tracker_shared_with_other_reads_and_reload_does_not_reset_it(self):
        first=self.read(constraintLimit=1)
        other=self.ops.perform('get_object_info',{'objectName':self.obj.Name},self.app,self.gui,Failure)
        self.assertEqual(first['document_instance_token'],other['document_instance_token'])
        self.assertEqual(len(self.app.observers),1)
        spec=importlib.util.spec_from_file_location('pft_design_reloaded_tests',ADDON/'pft_design_inspection.py')
        reloaded=importlib.util.module_from_spec(spec);spec.loader.exec_module(reloaded)
        again=reloaded.perform('inspect_design',{'objectName':self.obj.Name,'constraintLimit':1},self.app,self.gui,Failure)
        self.assertEqual(first['document_instance_token'],again['document_instance_token'])
        self.assertEqual(len(self.app.observers),1)

    def test_expected_identity_revision_guards_and_same_name_reopen(self):
        first=self.read(constraintLimit=1)
        guards={'expectedDocumentInstanceToken':first['document_instance_token'],'expectedRevision':first['revision']}
        self.assertEqual(self.read(constraintLimit=1,**guards)['revision'],0)
        self.app.observers[0].slotChangedObject(self.obj,'Length')
        self.assert_error('DOCUMENT_REVISION_MISMATCH',lambda:self.read(**guards))
        self.assert_error('DOCUMENT_INSTANCE_MISMATCH',lambda:self.read(expectedDocumentInstanceToken='x'*32))
        replacement=Document(self.doc.Name);self.app.documents[self.doc.Name]=replacement;self.app.ActiveDocument=replacement
        Object(replacement,self.obj.Name,'Part::Box')
        self.assert_error('DOCUMENT_INSTANCE_MISMATCH',lambda:self.read(**guards))

    def test_mid_read_revision_and_document_replacement_are_rejected(self):
        def changed(index):
            self.app.observers[0].slotChangedObject(self.obj,'Constraints')
            return Quantity(10)
        self.obj.getDatum=changed
        self.assert_error('DOCUMENT_REVISION_MISMATCH',lambda:self.read(constraintLimit=1))
        def replaced(index):
            replacement=Document(self.doc.Name);self.app.documents[self.doc.Name]=replacement
            return Quantity(10)
        self.obj.getDatum=replaced
        self.assert_error('DOCUMENT_INSTANCE_MISMATCH',lambda:self.read(constraintLimit=1))

    def test_unknown_observer_revision_is_null_and_guard_requires_available_counter(self):
        self.helper._tracker.close();self.app.addDocumentObserver=None
        result=self.read(constraintLimit=1)
        self.assertIsNone(result['revision']);self.assertEqual(result['revision_coverage'],'unknown')
        self.assert_error('DOCUMENT_REVISION_UNAVAILABLE',lambda:self.read(expectedDocumentInstanceToken=result['document_instance_token'],expectedRevision=0))

    def test_response_utf8_budget_refuses_whole_result_and_strings_never_leak_paths(self):
        for constraint in self.obj.Constraints:
            constraint.Type='界'*512;constraint.Name='界'*512
        self.assert_error('READ_RESPONSE_TOO_LARGE',lambda:self.read(constraintLimit=100))
        self.obj.Constraints=[Constraint(0)];self.obj.Label='/private/confidential.FCStd'
        result=self.read()
        self.assertIsNone(result['object']['Label']);self.assertNotIn('/private',json.dumps(result))

    def test_main_thread_and_unknown_module_source_are_guarded(self):
        errors=[]
        def worker():
            try:self.read(constraintLimit=1)
            except Failure as error:errors.append(error.code)
        thread=threading.Thread(target=worker);thread.start();thread.join()
        self.assertEqual(errors,['MAIN_THREAD_REQUIRED'])
        with patch.dict(sys.modules,{'pft_operations':types.SimpleNamespace(__file__='/private/unknown/pft_operations.py')}):
            self.assert_error('STATE_SOURCE_MISMATCH',lambda:self.read())
        with patch.dict(sys.modules,{'pft_document_state':types.SimpleNamespace(__file__='/private/unknown/pft_document_state.py')}):
            self.assert_error('STATE_SOURCE_MISMATCH',lambda:self.read())


if __name__=='__main__':unittest.main()
