# SPDX-License-Identifier: Apache-2.0
import importlib.util
from pathlib import Path
import sys
import threading
import types
import unittest
from unittest.mock import patch

PATH = Path(__file__).resolve().parents[2] / 'addon/PhoenixFreeCADTools/pft_runtime.py'
spec = importlib.util.spec_from_file_location('phoenix_runtime_test', PATH)
runtime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)


class Error(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, message


class ScriptTests(unittest.TestCase):
    def setUp(self):
        runtime._scripts.clear()
        runtime._seen.clear()
        self.app = types.SimpleNamespace(counter=0)
        self.gui = types.SimpleNamespace()

    def run_script(self, code, request='test-script'):
        params = {'code':code, 'requestId':request}
        self.assertTrue(runtime.claim(params,Error)[0])
        return runtime.execute(params,self.app,self.gui,Error)

    def test_script_result_stdout_and_replay_without_reexecution(self):
        code = 'App.counter += 1\nprint("complete")\nresult = {"counter": App.counter}'
        result = self.run_script(code)
        self.assertEqual(result['state'],'done')
        self.assertEqual(result['result'],{'counter':1})
        self.assertEqual(result['stdout'],'complete\n')
        fresh, repeated = runtime.claim({'code':code,'requestId':'test-script'},Error)
        self.assertFalse(fresh)
        self.assertEqual(repeated,result)
        self.assertEqual(self.app.counter,1)
        with self.assertRaises(Error) as conflict:
            runtime.claim({'code':'pass','requestId':'test-script'},Error)
        self.assertEqual(conflict.exception.code,'REQUEST_ID_CONFLICT')

    def test_failure_retains_state_and_does_not_claim_rollback(self):
        with self.assertRaises(Error) as failure:
            self.run_script('App.counter = 7\nraise ValueError("bad")')
        self.assertEqual(failure.exception.code,'SCRIPT_FAILED')
        self.assertEqual(self.app.counter,7)
        snapshot = runtime.status('test-script',Error)
        self.assertEqual(snapshot['state'],'failed')
        self.assertIn('ValueError',snapshot['error']['message'])
        self.assertIsNotNone(snapshot['finished_at'])

    def test_status_query_during_running_does_not_enter_gui(self):
        entered, release = threading.Event(), threading.Event()
        self.app.entered, self.app.release = entered, release
        params = {'requestId':'running','code':'App.entered.set()\nApp.release.wait(2)\nresult = 1'}
        runtime.claim(params,Error)
        worker = threading.Thread(target=runtime.execute,args=(params,self.app,self.gui,Error))
        worker.start()
        try:
            self.assertTrue(entered.wait(1))
            self.assertEqual(runtime.status('running',Error)['state'],'running')
            runtime.dispatch_error('running',Error('GUI_TIMEOUT','timeout'))
            self.assertEqual(runtime.status('running',Error)['state'],'running')
        finally:
            release.set()
            worker.join(3)
        self.assertEqual(runtime.status('running',Error)['state'],'done')

    def test_cancelled_queue_and_expired_id_never_reexecute(self):
        params = {'requestId':'cancel','code':'pass'}
        runtime.claim(params,Error)
        runtime.dispatch_error('cancel',Error('GUI_QUEUE_TIMEOUT','cancelled'))
        self.assertEqual(runtime.status('cancel',Error)['state'],'cancelled')
        self.assertFalse(runtime.claim(params,Error)[0])
        for index in range(21):
            self.run_script('result = 1', 'history-' + str(index))
        with self.assertRaises(Error) as expired:
            runtime.claim(params,Error)
        self.assertEqual(expired.exception.code,'SCRIPT_RESULT_EXPIRED')

    def test_cancelled_results_remain_bounded_after_64_queued_requests(self):
        params = [{'requestId':'cancel-burst-'+str(index),'code':'App.counter += 1'} for index in range(64)]
        for item in params:
            self.assertTrue(runtime.claim(item,Error)[0])
        for item in params:
            runtime.dispatch_error(item['requestId'],Error('GUI_QUEUE_TIMEOUT','cancelled before GUI execution'))
            terminal = [record for record in runtime._scripts.values() if record['state'] in {'done','failed','cancelled'}]
            self.assertLessEqual(len(terminal),20)
        self.assertEqual(len(runtime._scripts),20)
        self.assertEqual(set(runtime._seen),{item['requestId'] for item in params})
        self.assertEqual(set(runtime._scripts),{item['requestId'] for item in params[-20:]})
        for item in params:
            if item['requestId'] in runtime._scripts:
                fresh, snapshot = runtime.claim(item,Error)
                self.assertFalse(fresh)
                self.assertEqual(snapshot['state'],'cancelled')
            else:
                with self.assertRaises(Error) as expired:
                    runtime.claim(item,Error)
                self.assertEqual(expired.exception.code,'SCRIPT_RESULT_EXPIRED')
        self.assertEqual(self.app.counter,0)
        self.assertEqual(len(runtime._seen),64)

    def test_bounded_output_and_non_json_return(self):
        result = self.run_script('print("x" * 20000)\nresult = {"nan": float("nan"), "obj": object()}')
        self.assertTrue(result['output_truncated'])
        self.assertEqual(len(result['stdout']),8192)
        self.assertIsNone(result['result']['nan'])
        self.assertFalse(result['result']['obj']['serialized'])
        large = self.run_script('result = ["x" * 8192 for _ in range(100)]','large')
        self.assertTrue(large['result']['truncated'])

    def test_unprintable_exception_and_surrogate_output(self):
        with self.assertRaises(Error):
            self.run_script('class Broken(Exception):\n def __str__(self): raise RuntimeError("nested")\nraise Broken()')
        self.assertEqual(runtime.status('test-script',Error)['state'],'failed')
        out = self.run_script('print(chr(0xD800))','surrogate')
        import json
        self.assertTrue(json.dumps(out,ensure_ascii=True).encode('ascii'))

    def test_started_gap_timeout_does_not_falsely_cancel(self):
        params = {'requestId':'gap','code':'App.counter += 1'}
        runtime.claim(params,Error)
        runtime.dispatch_error('gap',Error('GUI_TIMEOUT','started timeout'))
        self.assertEqual(runtime.status('gap',Error)['state'],'starting')
        runtime.execute(params,self.app,self.gui,Error)
        self.assertEqual(self.app.counter,1)
        self.assertEqual(runtime.status('gap',Error)['state'],'done')

    def test_completed_results_remain_bounded_after_queued_burst(self):
        for index in range(30):
            runtime.claim({'requestId':'burst-'+str(index),'code':'pass'},Error)
        for index in range(30):
            runtime.execute({'requestId':'burst-'+str(index),'code':'pass'},self.app,self.gui,Error)
        self.assertEqual(len(runtime._scripts),20)
        self.assertEqual(len(runtime._seen),30)

    def test_strict_params(self):
        invalid = [
            ('execute_script',{'code':'pass'}),
            ('execute_script',{'requestId':'a','code':'x'*32769}),
            ('execute_script',{'requestId':'a','code':'pass','save':True}),
            ('execute_script',{'requestId':'a','code':'\x00'}),
            ('get_script_status',{'requestId':'../a'}),
            ('capture_view',{'width':True}),
            ('capture_view',{'width':1025}),
            ('capture_view',{'docName':''}),
        ]
        for method,params in invalid:
            with self.subTest(method=method,params=list(params)):
                with self.assertRaises(Error):
                    runtime.validate(method,params,Error)
        self.assertFalse(runtime.validate('other',{},Error))


class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.written = []
        self.doc = types.SimpleNamespace(Name='PublicSample')
        self.app = types.SimpleNamespace(ActiveDocument=self.doc,getDocument=lambda name:self.doc if name=='PublicSample' else None)
        self.view = types.SimpleNamespace(redraw=lambda:None,saveImage=self.save)
        self.gui_document = types.SimpleNamespace(Document=self.doc,activeView=lambda:self.view)
        self.gui = types.SimpleNamespace(activeDocument=lambda:self.gui_document)

    def save(self,path,width,height,background):
        self.written.append((path,width,height,background))
        Path(path).write_bytes(b'\x89PNG\r\n\x1a\n' + b'fixture')

    def test_target_view_only_and_temporary_output_removed(self):
        result = runtime.capture({'docName':'PublicSample','width':320,'height':240},self.app,self.gui,Error)
        self.assertEqual(result['mime_type'],'image/png')
        self.assertNotIn('path',result)
        path,width,height,background = self.written[0]
        self.assertEqual((width,height,background),(320,240,'Current'))
        self.assertFalse(Path(path).exists())
        self.assertIs(self.app.ActiveDocument,self.doc)

    def test_capture_refuses_background_document_without_switching_or_writing(self):
        background = types.SimpleNamespace(Document=types.SimpleNamespace(Name='BackgroundSample'),activeView=lambda:self.view)
        self.gui.activeDocument = lambda:background
        def forbidden_lookup(name):
            raise AssertionError('capture must not resolve or activate background views')
        self.gui.getDocument = forbidden_lookup
        with self.assertRaises(Error) as caught:
            runtime.capture({'docName':'PublicSample'},self.app,self.gui,Error)
        self.assertEqual(caught.exception.code,'VIEW_UNAVAILABLE')
        self.assertEqual(self.written,[])
        self.assertIs(self.gui.activeDocument(),background)
        self.assertIs(self.app.ActiveDocument,self.doc)

    def test_capture_refuses_active_drawing_page_despite_existing_3d_view(self):
        drawing_page = types.SimpleNamespace()
        self.gui_document.activeView = lambda:drawing_page
        def forbidden_fallback(kind):
            raise AssertionError('capture must not resolve or activate another existing view')
        self.gui_document.mdiViewsOfType = forbidden_fallback
        with self.assertRaises(Error) as caught:
            runtime.capture({'docName':'PublicSample'},self.app,self.gui,Error)
        self.assertEqual(caught.exception.code,'VIEW_UNAVAILABLE')
        self.assertEqual(self.written,[])
        self.assertIs(self.gui_document.activeView(),drawing_page)
        self.assertIs(self.app.ActiveDocument,self.doc)

    def test_capture_failure_cleans_tempfile(self):
        def fail(path,*args):
            self.written.append((path,))
            Path(path).write_bytes(b'invalid')
            raise RuntimeError('capture failed')
        self.view.saveImage = fail
        with self.assertRaises(RuntimeError):
            runtime.capture({},self.app,self.gui,Error)
        self.assertFalse(Path(self.written[0][0]).exists())

    def test_capture_refuses_missing_or_non_3d_view(self):
        with self.assertRaises(Error) as missing:
            runtime.capture({'docName':'Missing'},self.app,self.gui,Error)
        self.assertEqual(missing.exception.code,'DOCUMENT_NOT_FOUND')
        self.view = object()
        with self.assertRaises(Error) as unsupported:
            runtime.capture({},self.app,self.gui,Error)
        self.assertEqual(unsupported.exception.code,'VIEW_UNAVAILABLE')


if __name__ == '__main__':
    unittest.main()
