# SPDX-License-Identifier: Apache-2.0
"""Native-style/owned-writer guards using neutral independent fixtures only."""
from copy import deepcopy
import types
import unittest
from unittest.mock import patch

import test_pft_drawing_dimensions as fixtures
from test_pft_drawing_layout_profile import load

D = fixtures.dimensions
NS = types.SimpleNamespace


class Quantity:
    __module__ = 'Base'
    def __init__(self, value, signature=(1, 0, 0, 0, 0, 0, 0, 0)):
        self.Value = float(value)
        self.Unit = NS(Signature=list(signature))


STYLE = {'LabelFont': 'osifont', 'FontSize': 5., 'ArrowSize': 3.5, 'LineWidth': .35,
         'GapISO': 0., 'GapASME': 0., 'LineSpacingFactorISO': 2., 'ShowUnits': False, 'Color': 0x000000FF}


def configure_dimension(dim):
    dim.ShowUnits = False
    dim.ExpressionEngine = []
    dim.ArbitraryTolerances = dim.TheoreticalExact = dim.Inverted = dim.AngleOverride = False
    dim.EqualTolerance = True
    dim.OverTolerance = Quantity(0.); dim.UnderTolerance = Quantity(-0.)
    dim.LineAngle = Quantity(0.); dim.ExtensionAngle = Quantity(0.)
    dim.ViewObject = NS(Object=dim, TypeId='TechDrawGui::ViewProviderDimension', PropertiesList=[],
        Font='osifont', Fontsize=Quantity(5.), Arrowsize=Quantity(3.5), LineWidth=Quantity(.35),
        Color=(0.,0.,0.,1.),
        ArrowStyle='Filled arrow', StandardAndStyle='ISO Oriented', RenderingExtent='Normal',
        FlipArrowheads=False, GapFactorISO=0., GapFactorASME=0., LineSpacingFactorISO=2.)


class StyleReadTests(unittest.TestCase):
    def setUp(self):
        self.p = load('pft_drawing_layout_profile')
        self.doc = NS(Name='NeutralDocument')
        self.view = NS(Name='Top', Document=self.doc)
        self.page = NS(Name='Page', Document=self.doc, Views=[self.view])
        self.objects = {'Page': self.page, 'Top': self.view}
        self.doc.getObject = self.objects.get
        self.app = NS(getDocument=lambda name: self.doc if name == self.doc.Name else None)
        self.controller = NS(_ensure_main_thread=lambda: None)
        self.context = {'_profile': self.p._MARKER, 'document': self.doc, 'page': self.page,
            'view': self.view, 'intent': {'dimensions': []}, 'frozen': (None, ('defaults',))}
        self.patch = patch.object(self.p, '_defaults', return_value=(STYLE.copy(), ('defaults',)))
        self.patch.start(); self.addCleanup(self.patch.stop)
        self.created = []
        for i, kind in enumerate(('Distance', 'DistanceX', 'DistanceY', 'Diameter', 'Radius')):
            dim = fixtures.Dimension('NewDimension' + str(i)); dim.Document = self.doc
            configure_dimension(dim)
            dim.Type, dim.MeasureType, dim.Arbitrary = kind, 'Projected', False
            dim.References2D = [(self.view, ('Edge' + str(i),))]
            dim.FormatSpec = D._format(kind)
            self.objects[dim.Name] = dim; self.page.Views.append(dim); self.created.append(dim)
            self.context['intent']['dimensions'].append({'edge': 'Edge' + str(i), 'kind': kind})

    def read(self, created=None):
        return self.p.verify_created_style(self.app, None, self.context,
                                          self.created if created is None else created, self.controller)

    def test_five_native_kinds_and_in_progress_prefix_are_read_without_repairs(self):
        before = [(d.FormatSpec, d.ViewObject.Font, d.ViewObject.Fontsize.Value) for d in self.created]
        self.assertTrue(self.read())
        self.assertTrue(self.read(self.created[:1]))
        self.assertEqual(before, [(d.FormatSpec, d.ViewObject.Font, d.ViewObject.Fontsize.Value) for d in self.created])

    def test_changed_native_provider_styles_are_refused_without_repair(self):
        changes = {'Font': 'different-font', 'Fontsize': Quantity(8.), 'Arrowsize': Quantity(2.),
            'LineWidth': Quantity(.7), 'ArrowStyle': 'Open arrow', 'StandardAndStyle': 'ISO Referencing',
            'RenderingExtent': 'Confined', 'FlipArrowheads': True, 'GapFactorISO': 1.,
            'GapFactorASME': 1., 'LineSpacingFactorISO': 3., 'Color': (1.,1.,1.,1.)}
        provider = self.created[0].ViewObject
        for field, bad in changes.items():
            with self.subTest(field=field):
                good = getattr(provider, field); setattr(provider, field, bad)
                with self.assertRaises(self.p.PftDrawingLayoutProfileError) as error: self.read()
                self.assertEqual(error.exception.code, 'DRAWING_LAYOUT_STYLE_UNSUPPORTED')
                self.assertIs(getattr(provider, field), bad)
                setattr(provider, field, good)

    def test_nonfinite_wrong_unit_or_non_native_quantity_are_refused(self):
        provider = self.created[0].ViewObject
        for bad in (Quantity(float('nan')), Quantity(5., (0,)*8), NS(Value=5.), 5.):
            with self.subTest(bad=bad):
                provider.Fontsize = bad
                with self.assertRaises(self.p.PftDrawingLayoutProfileError): self.read()

    def test_native_packed_color_uses_float32_rgba_not_rounded_decimal(self):
        style = STYLE.copy(); style['Color'] = 0x804020FF
        expected = (.501960813999176, .250980406999588, .125490203499794, 1.)
        with patch.object(self.p, '_defaults', return_value=(style, ('defaults',))):
            for dim in self.created: dim.ViewObject.Color = expected
            self.assertTrue(self.read())
            self.created[0].ViewObject.Color = (128/255., 64/255., 32/255., 1.)
            with self.assertRaises(self.p.PftDrawingLayoutProfileError): self.read()

    def test_format_units_tolerances_and_geometry_overrides_cannot_expand_the_label(self):
        changes = {'FormatSpec': '%100.2f', 'ShowUnits': True, 'Arbitrary': True,
            'ArbitraryTolerances': True, 'TheoreticalExact': True, 'EqualTolerance': False,
            'OverTolerance': Quantity(1.), 'UnderTolerance': Quantity(-1.), 'Inverted': True,
            'AngleOverride': True, 'LineAngle': Quantity(30.), 'ExtensionAngle': Quantity(10.),
            'ExpressionEngine': [('X','1 mm')]}
        dim = self.created[0]
        for field, bad in changes.items():
            with self.subTest(field=field):
                good = getattr(dim, field); setattr(dim, field, bad)
                with self.assertRaises(self.p.PftDrawingLayoutProfileError): self.read()
                setattr(dim, field, good)

    def test_foreign_replaced_missing_or_proxy_bindings_are_refused(self):
        for phase in ('foreign-document', 'not-member', 'replaced', 'provider-object', 'provider-type', 'proxy'):
            with self.subTest(phase=phase):
                self.setUp(); dim = self.created[0]
                if phase == 'foreign-document': dim.Document = object()
                elif phase == 'not-member': self.page.Views.remove(dim)
                elif phase == 'replaced': self.objects[dim.Name] = object()
                elif phase == 'provider-object': dim.ViewObject.Object = self.created[1]
                elif phase == 'provider-type': dim.ViewObject.TypeId = 'Gui::ViewProviderDocumentObject'
                else: dim.ViewObject.PropertiesList = ['Proxy']
                with self.assertRaises(self.p.PftDrawingLayoutProfileError): self.read()

    def test_cleanup_unknown_from_a_native_style_read_is_preserved(self):
        class UnknownCleanup(RuntimeError): cleanup_state = 'unknown'
        provider = self.created[0].ViewObject
        class BrokenProvider:
            def __getattr__(self, name):
                if name == 'Font': raise UnknownCleanup('private native detail')
                return getattr(provider, name)
        self.created[0].ViewObject = BrokenProvider()
        with self.assertRaises(self.p.PftDrawingLayoutProfileError) as error: self.read()
        self.assertEqual(error.exception.cleanup_state, 'unknown')
        self.assertNotIn('private native detail', str(error.exception))

    def test_late_native_style_getter_cannot_silently_change_an_earlier_font(self):
        provider = self.created[0].ViewObject
        class ObservedProvider:
            changed = False
            def __getattr__(self, name):
                if name == 'LineSpacingFactorISO' and not self.changed:
                    self.changed = True
                    provider.Font = 'changed-during-later-native-read'
                return getattr(provider, name)
        self.created[0].ViewObject = ObservedProvider()
        with self.assertRaises(self.p.PftDrawingLayoutProfileError): self.read()

    def test_unreadable_cleanup_attribute_is_fixed_unknown(self):
        class UnknownCleanup(RuntimeError):
            @property
            def cleanup_state(self): raise RuntimeError('private cleanup detail')
        with patch.object(self.p, '_defaults', side_effect=UnknownCleanup()):
            with self.assertRaises(self.p.PftDrawingLayoutProfileError) as error: self.read()
        self.assertEqual(error.exception.cleanup_state, 'unknown')
        self.assertNotIn('private cleanup detail', str(error.exception))


class LayoutWriterTests(unittest.TestCase):
    setUp_fixture = fixtures.DrawingDimensionsTests.setUp
    prepare = fixtures.DrawingDimensionsTests.prepare
    error = fixtures.DrawingDimensionsTests.error

    def setUp(self):
        self.setUp_fixture()
        self.profile = load('pft_drawing_layout_profile')
        self.context = {'_profile': self.profile._MARKER, 'document': self.doc, 'page': self.page,
            'view': self.view, 'intent': {'dimensions': self.request}, 'frozen': (None, ('defaults',))}
        self.verify_calls, self.style_calls = [], []
        self.verify_hook = self.style_hook = None
        self.adapter = NS(verify=self.verify, verify_created=self.verify_created)
        original = D._sibling
        patchers = [patch.object(D, '_sibling', side_effect=lambda name: self.adapter if name == 'pft_drawing_layout_plan' else original(name)),
                    patch.object(self.profile, '_defaults', return_value=(STYLE.copy(), ('defaults',)))]
        for patcher in patchers:
            patcher.start(); self.addCleanup(patcher.stop)
        self.doc.add_hook = configure_dimension

    def verify(self, app, gui, plan, controller, *, defaults_only=False):
        self.verify_calls.append((defaults_only, len(self.doc.created_calls)))
        if self.verify_hook: self.verify_hook(defaults_only)

    def verify_created(self, app, gui, plan, created, controller):
        self.style_calls.append(tuple(created))
        if self.style_hook: self.style_hook(created)
        return self.profile.verify_created_style(app, gui, plan['native_context'], created, controller)

    def make_plan(self, rows=None):
        plan, unused = self.prepare(rows)
        self.context['intent']['dimensions'] = plan['request']['dimensions']
        self.layout = {'native_context': self.context, 'request': deepcopy(plan['request'])}
        return plan

    def apply(self, plan, layout=True):
        return D.apply_plan(self.app, self.gui, self.doc, plan, fixtures.controller,
                            layout_plan=self.layout if layout else None)

    def test_manual_path_does_not_import_or_read_layout_components(self):
        plan = self.make_plan()
        result = self.apply(plan, layout=False)
        self.assertEqual(result['dimensions'][0]['value_mm'], 5.)
        self.assertEqual((self.verify_calls, self.style_calls), ([], []))
        self.assertEqual(self.doc.events, ['open', 'commit'])

    def test_auto_path_checks_full_before_open_and_scoped_style_before_commit(self):
        plan = self.make_plan()
        self.assertEqual(self.apply(plan)['dimensions'][0]['value_mm'], 5.)
        self.assertEqual(self.verify_calls[0], (False, 0))
        self.assertTrue(any(defaults for defaults, count in self.verify_calls))
        self.assertTrue(self.style_calls)
        self.assertEqual(self.doc.events, ['open', 'commit'])
        self.assertEqual(self.page.Views, [self.view, self.doc.Objects[-1]])

    def test_wrong_document_or_resolved_request_is_refused_before_open(self):
        for mode in ('identity', 'resolved'):
            with self.subTest(mode=mode):
                self.setUp(); plan = self.make_plan()
                if mode == 'identity': self.context['document'] = NS(Name=self.doc.Name)
                else: self.layout['request']['dimensions'][0]['x_mm'] += 1.
                self.error(lambda: self.apply(plan), 'DIMENSION_PLAN_STALE')
                self.assertEqual((self.doc.events,self.doc.created_calls,self.verify_calls), ([],[],[]))

    def test_initial_native_verifier_cannot_change_the_frozen_resolved_position(self):
        for field in ('x_mm', 'y_mm'):
            with self.subTest(field=field):
                self.setUp(); plan = self.make_plan()
                original = D.verify_plan
                def verify(*args, **kwargs):
                    result = original(*args, **kwargs)
                    plan['request']['dimensions'][0][field] += 1.
                    return result
                with patch.object(D, 'verify_plan', side_effect=verify):
                    self.error(lambda: self.apply(plan), 'DIMENSION_PLAN_STALE')
                self.assertEqual((self.doc.events, self.doc.created_calls), ([], []))

    def test_layout_callback_cannot_replace_native_names_values_or_capture(self):
        for field in ('names', 'values', 'snapshot'):
            with self.subTest(field=field):
                self.setUp(); plan = self.make_plan()
                def change(defaults_only):
                    if field == 'names': plan['names'] = ('PhoenixDimension_replaced',)
                    elif field == 'values': plan['values'] = (6.,)
                    else: plan['snapshot']['views'] = ()
                self.verify_hook = change
                self.error(lambda: self.apply(plan), 'DIMENSION_PLAN_STALE')
                self.assertEqual((self.doc.events, self.doc.created_calls), ([], []))

    def test_native_setter_callback_cannot_change_resolved_positions_or_names(self):
        original = fixtures.Dimension.__setattr__
        for field in ('x_mm', 'y_mm', 'names', 'values'):
            with self.subTest(field=field):
                self.setUp(); plan = self.make_plan()
                changed = []
                def setter(dim, name, value):
                    original(dim, name, value)
                    if name == 'FormatSpec' and hasattr(dim, 'ViewObject') and not changed:
                        changed.append(True)
                        if field in ('x_mm', 'y_mm'):
                            plan['request']['dimensions'][0][field] += 1.
                        elif field == 'names': plan['names'] = ('PhoenixDimension_replaced',)
                        else: plan['values'] = (6.,)
                with patch.object(fixtures.Dimension, '__setattr__', setter):
                    # A mutated rollback capture is not accepted as a verified
                    # return to the original plan, even when abort removed it.
                    self.error(lambda: self.apply(plan), 'PART_ROLLBACK_UNVERIFIED',
                               fixtures.controller.PftWriteError)
                self.assertEqual(changed, [True])
                self.assertEqual(self.doc.events, ['open', 'abort'])
                self.assertEqual(self.page.Views, [self.view])

    def test_new_style_callback_cannot_change_the_resolved_position(self):
        plan = self.make_plan()
        self.style_hook = lambda created: plan['request']['dimensions'][0].__setitem__('x_mm', 300.)
        self.error(lambda: self.apply(plan), 'PART_ROLLBACK_UNVERIFIED', fixtures.controller.PftWriteError)
        self.assertEqual(self.doc.events, ['open', 'abort'])
        self.assertEqual(self.page.Views, [self.view])

    def test_native_setter_callback_style_corruption_aborts_owned_once(self):
        original = fixtures.Dimension.__setattr__
        for field in ('FormatSpec', 'X', 'Y'):
            with self.subTest(field=field):
                self.setUp(); plan = self.make_plan()
                def setter(dim, name, value):
                    original(dim,name,value)
                    if name == field and hasattr(dim,'ViewObject'):
                        dim.ViewObject.Font = 'observer-changed-font'
                with patch.object(fixtures.Dimension, '__setattr__', setter):
                    self.error(lambda: self.apply(plan), 'DIMENSION_LAYOUT_STYLE_UNVERIFIED')
                self.assertEqual(self.doc.events, ['open','abort'])
                self.assertEqual(self.page.Views, [self.view])

    def test_recompute_callback_style_corruption_is_caught_before_commit(self):
        plan = self.make_plan()
        self.doc.recompute_hook = lambda objects: setattr(objects[-1].ViewObject,'Arrowsize',Quantity(9.))
        self.error(lambda: self.apply(plan), 'DIMENSION_LAYOUT_STYLE_UNVERIFIED')
        self.assertEqual(self.doc.events,['open','abort'])

    def test_second_constructor_callback_changing_an_earlier_new_style_aborts_batch(self):
        rows = [self.request[0], dict(self.request[0], kind='DistanceX')]
        plan = self.make_plan(rows)
        created = []
        def construct(dim):
            configure_dimension(dim)
            if created: created[0].ViewObject.LineWidth = Quantity(1.)
            created.append(dim)
        self.doc.add_hook = construct
        self.error(lambda: self.apply(plan), 'DIMENSION_LAYOUT_STYLE_UNVERIFIED')
        self.assertEqual(self.doc.events, ['open','abort'])
        self.assertEqual(self.page.Views, [self.view])
        self.assertEqual(len(self.doc.created_calls), 2)

    def test_postcommit_style_corruption_is_unknown_without_second_close(self):
        plan = self.make_plan()
        self.doc.commit_hook = lambda: setattr(self.doc.Objects[-1].ViewObject,'Fontsize',Quantity(9.))
        self.error(lambda: self.apply(plan),'PART_COMMIT_UNVERIFIED',fixtures.controller.PftWriteError)
        self.assertEqual(self.doc.events,['open','commit'])
        self.assertEqual(len(self.page.Views),2)

    def test_style_read_switching_foreign_transaction_never_closes_it(self):
        plan = self.make_plan()
        def switch(created): self.doc.active_transaction=('foreign',900)
        self.style_hook = switch
        self.error(lambda: self.apply(plan),'PART_TRANSACTION_CHANGED',fixtures.controller.PftWriteError)
        self.assertEqual(self.doc.events,['open'])
        self.assertEqual(self.doc.active_transaction,('foreign',900))

    def test_unknown_cleanup_survives_transaction_failure_replacement(self):
        class UnknownCleanup(RuntimeError): cleanup_state='unknown'
        for abort_uncertain in (False,True):
            with self.subTest(abort_uncertain=abort_uncertain):
                self.setUp();plan=self.make_plan()
                self.style_hook=lambda created: (_ for _ in ()).throw(UnknownCleanup('private raw context'))
                if abort_uncertain: self.doc.abortTransaction=lambda: self.doc.events.append('abort')
                code='PART_ROLLBACK_UNVERIFIED' if abort_uncertain else 'DIMENSION_LAYOUT_STYLE_UNVERIFIED'
                kind=fixtures.controller.PftWriteError if abort_uncertain else None
                error=self.error(lambda:self.apply(plan),code,kind)
                self.assertEqual(error.cleanup_state,'unknown')
                self.assertEqual(self.doc.events,['open','abort'])


if __name__ == '__main__': unittest.main()
