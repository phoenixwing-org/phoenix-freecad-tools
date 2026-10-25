# SPDX-License-Identifier: Apache-2.0
"""Synthetic RPC lifecycle tests; never launches Codex or reads authentication."""
import importlib.util
import json
import os
from pathlib import Path
import types
import unittest
from unittest.mock import patch
import tomllib

ADDON = Path(__file__).resolve().parents[2] / "addon" / "PhoenixFreeCADTools"
spec = importlib.util.spec_from_file_location("pft_chat_controller", ADDON / "pft_chat_controller.py")
chat = importlib.util.module_from_spec(spec)
spec.loader.exec_module(chat)
EXPECTED_CHAT_TOOLS = ('get_capabilities', 'get_connection_status', 'list_documents', 'get_document_info',
    'list_objects', 'get_object_info', 'get_selection', 'measure_geometry', 'list_drawing_pages',
    'inspect_drawing_page', 'fit_view', 'set_axonometric_view', 'set_view', 'set_appearance', 'get_script_status', 'inspect_design',
    'prepare_part_changes', 'request_part_change_approval', 'get_part_change_status')

EXPECTED_DESK_TOOLS = ("desk_get_connection_status", "desk_search_index", "desk_query_references",
                   "desk_query_cached_metadata", "desk_get_cached_thumbnail")


class PftTransport:
    def __init__(self):
        self.messages, self.starts, self.stops, self.timeouts = [], [], 0, []
        self.timeout_callback = None
    def start(self, program, args, env, cwd, on_data, on_exit, on_error):
        self.starts.append((program, args, env, cwd, on_data, on_exit, on_error))
        self.data, self.exit, self.error = on_data, on_exit, on_error
    def write(self, data):
        assert isinstance(data, bytes) and data.endswith(b'\n')
        self.messages.append(json.loads(data))
    def stop(self): self.stops += 1
    def arm_timeout(self, value, on_timeout=None):
        self.timeouts.append(value)
        self.timeout_callback = on_timeout
    def clear_timeout(self):
        self.timeouts.append(None)
        self.timeout_callback = None
    def expire_timeout(self):
        if self.timeout_callback is not None:
            self.timeout_callback()
        else:
            self.error(types.SimpleNamespace(code='CHAT_TIMEOUT'))
    def result(self, method, result):
        request = next(message for message in reversed(self.messages) if message.get('method') == method)
        self.data({'id': request['id'], 'result': result})
    def notify(self, method, params): self.data({'method': method, 'params': params})
    def methods(self): return [message.get('method') for message in self.messages]


class PftChatControllerTests(unittest.TestCase):
    def setUp(self):
        self.transport, self.events, self.urls = PftTransport(), [], []
        self.controller = chat.PftChatController(self.transport, self.events.append, lambda url: self.urls.append(url) or True)
        self.cwd = '/var/example/freecad-chat/clean'
        self.configured_mcp = None
    def mcp_config(self):
        return {'node_executable': '/opt/example/node', 'cli_entry': '/opt/example/freecad/dist/cli.js',
                'bridge_url': 'http://127.0.0.1:9876'}
    def connect(self, account=None, mcp=None, model_id=None, load_models=True):
        self.configured_mcp = mcp
        self.controller.connect('/opt/example/codex', '/var/example/freecad-chat/config', self.cwd,
                                mcp=mcp, model_id=model_id)
        self.transport.result('initialize', {'userAgent': 'codex_cli_rs/0.160.0 (fixture)'})
        self.transport.result('account/read', {'account': account})
        if isinstance(account, dict) and account.get('type') == 'chatgpt' and load_models:
            self.transport.result('model/list', {'data': [self.model_row()]})
    def ready(self, mcp=None, model_id=None, load_models=True):
        self.connect({'type': 'chatgpt', 'email': 'unused@example.invalid'}, mcp=mcp,
                     model_id=model_id, load_models=load_models)
    def model_row(self, **changes):
        result = {'id': 'fixture-picker-id', 'model': 'fixture-model', 'displayName': 'Fixture model',
                  'hidden': False, 'isDefault': True, 'inputModalities': ['text', 'image'],
                  'description': 'Unused catalog prose', 'defaultReasoningEffort': 'medium',
                  'supportedReasoningEfforts': [{'reasoningEffort': 'medium', 'description': 'Unused'}]}
        result.update(changes)
        return result
    def effective_config(self):
        result = {'approval_policy': 'never', 'sandbox_mode': 'read-only', 'model_provider': 'openai',
                'forced_login_method': 'chatgpt', 'web_search': 'disabled', 'cli_auth_credentials_store': 'file',
                'features': {key: False for key in ('shell_tool', 'unified_exec', 'apps', 'hooks',
                    'multi_agent', 'remote_plugin', 'plugins', 'apply_patch_freeform')},
                'tools': {'view_image': False}}
        if self.configured_mcp is not None:
            result['mcp_servers'] = {'phoenix_freecad': {'command': self.configured_mcp['node_executable'],
                'args': [self.configured_mcp['cli_entry']], 'env': {'PHOENIX_FREECAD_TOOL_SCOPE': 'chat',
                'PHOENIX_FREECAD_URL': self.configured_mcp['bridge_url']}, 'required': True,
                'enabled_tools': list(self.expected_tools())}}
            desk = self.configured_mcp.get('desk_config')
            if desk is not None:
                result['mcp_servers']['phoenix_freecad']['env'].update(PHOENIX_DESK_NODE=desk['node_executable'],
                    PHOENIX_DESK_CLI=desk['cli_entry'], PHOENIX_DESK_STATE_DIR=desk['state_dir'], PHOENIX_DESK_CHAT_READS='1')
        return result
    def expected_tools(self):
        return EXPECTED_CHAT_TOOLS + (EXPECTED_DESK_TOOLS if self.configured_mcp is not None
                                     and 'desk_config' in self.configured_mcp else ())
    def desk_mcp_config(self):
        return dict(self.mcp_config(), desk_config={'node_executable': '/opt/neutral/desk-node',
            'cli_entry': '/opt/neutral/desk/scripts/desk-web-service.mjs', 'state_dir': '/var/neutral/desk-state'})
    def pass_config(self):
        self.transport.result('config/read', {'config': self.effective_config(),
                                             'origins': {'synthetic-private-origin': '/private/example/config'}})
    def inventory(self, for_thread=False):
        if self.configured_mcp is None:
            return {'data': []}
        return {'data': [{'name': 'phoenix_freecad', 'authStatus': 'unsupported',
            'runtimeStatus': 'connected' if for_thread else None, 'resources': [], 'resourceTemplates': [],
            'tools': {name: {'name': name, 'inputSchema': {'type': 'object'},
                **({'annotations': {'readOnlyHint': True, 'destructiveHint': False, 'idempotentHint': True,
                    'openWorldHint': False}} if name in EXPECTED_DESK_TOOLS else {})} for name in self.expected_tools()}}]}
    def thread_result(self, **changes):
        result = {'thread': {'id': 'private-thread-fixture', 'cliVersion': '0.160.0', 'ephemeral': True},
                  'model': 'fixture-model', 'modelProvider': 'openai', 'cwd': self.cwd,
                  'approvalPolicy': 'never', 'approvalsReviewer': 'user',
                  'sandbox': {'type': 'readOnly', 'networkAccess': False}}
        result.update(changes)
        return result
    def start_turn(self, text='Synthetic question'):
        self.controller.send(text)
        self.pass_config()
        self.transport.result('mcpServerStatus/list', self.inventory())
        self.transport.result('thread/start', self.thread_result())
        self.transport.result('mcpServerStatus/list', self.inventory(True))
        self.transport.result('turn/start', {'turn': {'id': 'private-turn-fixture', 'items': [], 'status': 'inProgress'}})
    def event(self, **extras):
        return dict({'threadId': 'private-thread-fixture', 'turnId': 'private-turn-fixture'}, **extras)
    def complete(self, status='completed', items=None):
        self.transport.notify('turn/completed', {'threadId': 'private-thread-fixture',
            'turn': {'id': 'private-turn-fixture', 'items': items or [], 'status': status}})

    def test_model_catalog_uses_official_fields_and_publishes_only_complete_visible_text_choices(self):
        self.ready(load_models=False)
        request = self.transport.messages[-1]
        self.assertEqual(request['method'], 'model/list')
        self.assertEqual(request['params'], {'includeHidden': False, 'limit': 20})
        self.assertTrue(self.controller.snapshot['can_send'])
        self.assertEqual(self.controller.snapshot['models_status'], 'loading')
        self.transport.result('model/list', {'data': [self.model_row()], 'nextCursor': 'private-next'})
        self.assertEqual(self.controller.snapshot['models'], [])
        self.assertFalse(self.controller.select_model('fixture-model'))
        self.assertEqual(self.transport.messages[-1]['params']['cursor'], 'private-next')
        self.transport.result('model/list', {'data': [
            self.model_row(id='second-id', model='second-model', displayName='Second', isDefault=False),
            self.model_row(id='hidden-id', model='hidden-model', hidden=True),
            self.model_row(id='image-id', model='image-model', inputModalities=['image'])], 'nextCursor': None})
        snapshot = self.controller.snapshot
        self.assertEqual(snapshot['models'], [
            {'id': 'fixture-picker-id', 'model': 'fixture-model', 'display_name': 'Fixture model', 'is_default': True},
            {'id': 'second-id', 'model': 'second-model', 'display_name': 'Second', 'is_default': False}])
        self.assertEqual(snapshot['models_status'], 'ready')
        self.assertIsNone(snapshot['selected_model'])
        self.assertIsNone(snapshot['models_error'])
        self.assertTrue(snapshot['can_refresh_models'])
        snapshot['models'][0]['model'] = 'mutated-outside'
        self.assertEqual(self.controller.snapshot['models'][0]['model'], 'fixture-model')
        public = json.dumps(self.controller.snapshot)
        self.assertNotIn('private-next', public)
        self.assertNotIn('Unused catalog prose', public)
        self.assertNotIn('supportedReasoningEfforts', public)

    def test_explicit_model_requires_this_connections_model_value_and_is_verified_before_turn(self):
        self.ready(model_id='fixture-model', load_models=False)
        self.assertFalse(self.controller.snapshot['can_send'])
        self.controller.send('No request before catalog verification')
        self.assertEqual(self.controller.snapshot['error']['code'], 'CHAT_MODEL_UNAVAILABLE')
        self.assertNotIn('config/read', self.transport.methods())
        self.transport.result('model/list', {'data': [self.model_row()]})
        self.assertTrue(self.controller.snapshot['can_send'])
        self.assertFalse(self.controller.select_model('fixture-picker-id'))
        self.assertEqual(self.controller.snapshot['selected_model'], 'fixture-model')
        self.controller.send('Use the selected model')
        self.pass_config()
        self.transport.result('mcpServerStatus/list', self.inventory())
        self.assertEqual(self.transport.messages[-1]['params']['model'], 'fixture-model')
        # Codex CLI 0.160.0 stable ThreadStartParams has no fallback override.
        self.assertNotIn('allowProviderModelFallback', self.transport.messages[-1]['params'])
        self.transport.result('thread/start', self.thread_result())
        self.transport.result('mcpServerStatus/list', self.inventory(True))
        self.transport.result('turn/start', {'turn': {'id': 'private-turn-fixture', 'items': [], 'status': 'inProgress'}})
        self.complete()
        self.assertEqual(self.controller.snapshot['model'], 'fixture-model')
        before, count = self.controller.snapshot, len(self.transport.messages)
        self.assertFalse(self.controller.snapshot['can_select_model'])
        self.assertFalse(self.controller.select_model(None))
        self.assertFalse(self.controller.refresh_models())
        self.assertEqual(self.controller.snapshot['transcript'], before['transcript'])
        self.assertEqual(self.controller.snapshot['model'], before['model'])
        self.assertEqual(self.controller.snapshot['selected_model'], before['selected_model'])
        self.assertEqual(len(self.transport.messages), count)
        self.assertEqual(self.transport.stops, 0)
        self.assertTrue(self.controller.snapshot['can_send'])

    def test_model_mismatch_rejects_thread_before_user_text_or_inventory(self):
        self.ready(model_id='fixture-model')
        self.controller.send('Must not reach fallback model')
        self.pass_config()
        self.transport.result('mcpServerStatus/list', self.inventory())
        self.transport.result('thread/start', self.thread_result(model='provider-fallback'))
        self.assertEqual(self.controller.snapshot['error']['code'], 'CHAT_MODEL_MISMATCH')
        self.assertEqual(self.controller.snapshot['transcript'], [])
        self.assertNotIn('turn/start', self.transport.methods())
        self.assertEqual(self.transport.methods().count('mcpServerStatus/list'), 1)
        self.assertEqual(self.transport.stops, 1)
        self.assertNotIn('provider-fallback', json.dumps(self.controller.snapshot))

    def test_provider_mismatch_rejects_thread_before_user_text_or_inventory(self):
        self.ready(model_id='fixture-model')
        self.controller.send('Must not reach another provider')
        self.pass_config()
        self.transport.result('mcpServerStatus/list', self.inventory())
        self.transport.result('thread/start', self.thread_result(modelProvider='unexpected-provider'))
        self.assertEqual(self.controller.snapshot['error']['code'], 'CHAT_POLICY_REJECTED')
        self.assertEqual(self.controller.snapshot['transcript'], [])
        self.assertNotIn('turn/start', self.transport.methods())
        self.assertEqual(self.transport.methods().count('mcpServerStatus/list'), 1)
        self.assertEqual(self.transport.stops, 1)
        self.assertNotIn('unexpected-provider', json.dumps(self.controller.snapshot))

    def test_default_model_omits_model_override_and_accepts_service_reported_model(self):
        self.ready()
        self.assertTrue(self.controller.select_model('fixture-model'))
        self.assertTrue(self.controller.select_model(''))
        self.controller.send('Choose the service default')
        self.pass_config()
        self.transport.result('mcpServerStatus/list', self.inventory())
        self.assertNotIn('model', self.transport.messages[-1]['params'])
        self.assertNotIn('allowProviderModelFallback', self.transport.messages[-1]['params'])
        self.transport.result('thread/start', self.thread_result(model='service-chose-another-model'))
        self.transport.result('mcpServerStatus/list', self.inventory(True))
        self.assertEqual(self.controller.snapshot['model'], 'service-chose-another-model')
        self.assertIsNone(self.controller.snapshot['selected_model'])

    def test_unknown_saved_model_never_silently_changes_to_default_and_can_be_corrected(self):
        self.ready(model_id='unavailable-saved-model')
        snapshot = self.controller.snapshot
        self.assertFalse(snapshot['can_send'])
        self.assertEqual(snapshot['selected_model'], 'unavailable-saved-model')
        self.assertEqual(snapshot['models_error']['code'], 'CHAT_MODEL_UNAVAILABLE')
        self.controller.send('Do not replace my selection')
        self.assertNotIn('config/read', self.transport.methods())
        self.assertTrue(self.controller.select_model('fixture-model'))
        self.assertTrue(self.controller.snapshot['can_send'])
        self.assertIsNone(self.controller.snapshot['models_error'])
        self.assertTrue(self.controller.select_model(None))
        self.controller.disconnect()
        self.ready(model_id='fixture-model', load_models=False)
        self.assertFalse(self.controller.snapshot['can_send'])
        self.assertFalse(self.controller.select_model('fixture-model'))
        self.transport.result('model/list', {'data': []})
        self.assertFalse(self.controller.snapshot['can_send'])
        self.assertTrue(self.controller.select_model(None))
        self.assertTrue(self.controller.snapshot['can_send'])

    def test_catalog_rpc_failure_and_timeout_are_optional_sanitized_and_refreshable(self):
        for failure in ('rpc', 'timeout'):
            with self.subTest(failure=failure):
                self.setUp()
                self.ready(model_id='fixture-model', load_models=False)
                identifier = self.transport.messages[-1]['id']
                if failure == 'rpc':
                    self.transport.data({'id': identifier, 'error': {'message': '/private/token?secret=fixture'}})
                else:
                    self.transport.expire_timeout()
                snapshot = self.controller.snapshot
                self.assertEqual(snapshot['phase'], 'ready')
                self.assertEqual(snapshot['models_status'], 'error')
                self.assertFalse(snapshot['can_send'])
                self.assertEqual(self.transport.stops, 0)
                self.assertNotIn('/private', json.dumps(snapshot))
                self.assertTrue(self.controller.select_model(None))
                self.assertTrue(self.controller.snapshot['can_send'])
                self.assertTrue(self.controller.refresh_models())
                self.transport.data({'id': identifier, 'result': {'data': [self.model_row(model='stale') ]}})
                self.assertEqual(self.controller.snapshot['models_status'], 'loading')
                self.transport.result('model/list', {'data': [self.model_row()]})
                self.assertEqual(self.controller.snapshot['models_status'], 'ready')
                self.assertEqual(self.controller.snapshot['models'][0]['model'], 'fixture-model')

    def test_default_send_during_catalog_discovery_cancels_only_catalog_and_ignores_late_reply(self):
        self.ready(load_models=False)
        identifier = self.transport.messages[-1]['id']
        self.assertTrue(self.controller.snapshot['can_send'])
        with patch.object(chat.time, 'monotonic', return_value=100.0):
            self.start_turn()
        self.assertEqual(self.controller.snapshot['phase'], 'sending')
        self.assertEqual(self.controller.snapshot['models_status'], 'error')
        self.assertEqual(self.transport.timeouts[-1], 120000)
        self.transport.data({'id': identifier, 'error': {'message': '/private/stale-error'}})
        self.assertEqual(self.controller.snapshot['phase'], 'sending')
        self.assertEqual(self.transport.timeouts[-1], 120000)
        self.complete()
        self.assertTrue(self.controller.snapshot['can_send'])
        self.assertEqual(self.transport.stops, 0)

    def test_model_catalog_has_total_page_item_and_time_limits_without_partial_choices(self):
        with patch.object(chat.time, 'monotonic', return_value=100.0) as clock:
            self.ready(load_models=False)
            self.assertEqual(self.transport.timeouts[-1], 30000)
            clock.return_value = 120.0
            self.transport.result('model/list', {'data': [self.model_row()], 'nextCursor': 'next'})
            self.assertEqual(self.transport.timeouts[-1], 10000)
            clock.return_value = 131.0
            self.transport.result('model/list', {'data': [], 'nextCursor': 'another'})
            self.assertEqual(self.controller.snapshot['models_error']['code'], 'CHAT_MODELS_TIMEOUT')
            self.assertEqual(self.transport.methods().count('model/list'), 2)
            self.assertEqual(self.controller.snapshot['models'], [])
            self.assertTrue(self.controller.snapshot['can_send'])
        self.controller.disconnect()
        self.ready(load_models=False)
        for index in range(5):
            self.transport.result('model/list', {'data': [], 'nextCursor': 'page-' + str(index)})
        self.assertEqual(self.controller.snapshot['models_error']['code'], 'CHAT_MODELS_LIMIT')
        self.assertEqual(self.transport.methods().count('model/list'), 7)
        self.assertTrue(self.controller.snapshot['can_send'])
        self.assertTrue(self.controller.refresh_models())
        self.transport.result('model/list', {'data': [self.model_row(id=str(i), model=str(i)) for i in range(21)]})
        self.assertEqual(self.controller.snapshot['models_error']['code'], 'CHAT_MODELS_INVALID')
        self.assertEqual(self.controller.snapshot['models'], [])

    def test_malformed_duplicate_hidden_and_unselectable_catalog_entries_are_not_choices(self):
        invalid = [None, {}, {'data': 'bad'}, {'data': [None]},
            {'data': [self.model_row(hidden=1)]}, {'data': [self.model_row(isDefault='true')]},
            {'data': [self.model_row(model='x' * 257)]}, {'data': [self.model_row(displayName='bad\nname')]},
            {'data': [self.model_row(inputModalities=None)]},
            {'data': [self.model_row(), self.model_row(id='other')]},
            {'data': [self.model_row()], 'nextCursor': 'bad\ncursor'}]
        for result in invalid:
            with self.subTest(result=result):
                self.setUp()
                self.ready(load_models=False)
                self.transport.result('model/list', result)
                self.assertEqual(self.controller.snapshot['models_status'], 'error')
                self.assertEqual(self.controller.snapshot['models'], [])
                self.assertEqual(self.controller.snapshot['phase'], 'ready')
                self.assertTrue(self.controller.snapshot['can_send'])
                self.assertEqual(self.transport.stops, 0)
        self.setUp()
        self.ready(load_models=False)
        self.transport.result('model/list', {'data': [], 'nextCursor': 'loop'})
        self.transport.result('model/list', {'data': [], 'nextCursor': 'loop'})
        self.assertEqual(self.controller.snapshot['models_error']['code'], 'CHAT_MODELS_INVALID')
        self.assertEqual(self.transport.methods().count('model/list'), 2)
        self.assertNotIn('loop', json.dumps(self.controller.snapshot))

    def test_stale_optional_timeouts_cannot_cancel_new_discovery_or_an_active_turn(self):
        self.ready(load_models=False)
        old_timeout = self.transport.timeout_callback
        self.assertTrue(callable(old_timeout))
        self.transport.expire_timeout()
        self.assertEqual(self.controller.snapshot['models_status'], 'error')
        self.assertTrue(self.controller.refresh_models())
        old_timeout()
        self.assertEqual(self.controller.snapshot['models_status'], 'loading')
        current_timeout = self.transport.timeout_callback
        self.start_turn()
        self.assertIsNone(self.transport.timeout_callback)
        current_timeout()
        self.assertEqual(self.controller.snapshot['phase'], 'sending')
        self.assertEqual(self.transport.stops, 0)
        self.complete()
        self.assertTrue(self.controller.snapshot['can_send'])
        self.controller.disconnect()
        self.ready(load_models=False)
        current_timeout()
        self.assertEqual(self.controller.snapshot['models_status'], 'loading')
        self.assertEqual(self.controller.snapshot['phase'], 'ready')

    def test_catalog_discovery_cannot_change_login_or_account_update_timeouts(self):
        self.ready(load_models=False)
        old_identifier = self.transport.messages[-1]['id']
        self.controller.login()
        self.transport.result('account/login/start', {'type': 'chatgpt', 'loginId': 'new-login',
            'authUrl': 'https://auth.openai.com/authorize'})
        self.assertEqual(self.transport.timeouts[-1], 180000)
        self.transport.data({'id': old_identifier, 'result': {'data': [self.model_row()]}})
        self.assertEqual(self.transport.timeouts[-1], 180000)
        self.assertEqual(self.controller.snapshot['phase'], 'logging_in')
        self.transport.notify('account/login/completed', {'success': True, 'loginId': 'new-login'})
        self.transport.result('account/read', {'account': {'type': 'chatgpt'}})
        self.assertEqual(self.controller.snapshot['models_status'], 'loading')
        identifier = self.transport.messages[-1]['id']
        self.transport.notify('account/updated', {'authMode': None})
        self.assertEqual(self.controller.snapshot['phase'], 'connecting')
        self.transport.result('account/read', {'account': None})
        self.assertEqual(self.controller.snapshot['phase'], 'signed_out')
        self.assertIsNone(self.transport.timeouts[-1])
        before = self.controller.snapshot
        self.transport.data({'id': identifier, 'error': {'message': '/private/old-account'}})
        self.assertEqual(self.controller.snapshot, before)
        self.assertEqual(self.transport.stops, 0)

    def test_full_bounded_catalog_and_refresh_revalidate_selected_model(self):
        self.ready(model_id='chosen-99', load_models=False)
        for page in range(5):
            rows = [self.model_row(id='id-' + str(i), model='chosen-' + str(i),
                                  isDefault=(i == 10)) for i in range(page * 20, (page + 1) * 20)]
            self.transport.result('model/list', {'data': rows, 'nextCursor': str(page) if page < 4 else None})
        self.assertEqual(len(self.controller.snapshot['models']), 100)
        self.assertTrue(self.controller.snapshot['can_send'])
        self.assertTrue(self.controller.refresh_models())
        self.assertFalse(self.controller.snapshot['can_send'])
        self.assertEqual(self.controller.snapshot['selected_model'], 'chosen-99')
        self.transport.result('model/list', {'data': [self.model_row()]})
        self.assertFalse(self.controller.snapshot['can_send'])
        self.assertEqual(self.controller.snapshot['models_error']['code'], 'CHAT_MODEL_UNAVAILABLE')
        self.assertTrue(self.controller.select_model('fixture-model'))
        self.assertTrue(self.controller.snapshot['can_send'])
        self.controller.send('Selection is now locked before a thread exists')
        self.assertFalse(self.controller.select_model(None))
        self.assertFalse(self.controller.refresh_models())
        self.assertEqual(self.transport.messages[-1]['method'], 'config/read')
        self.assertEqual(self.controller.snapshot['phase'], 'sending')
        self.assertEqual(self.transport.stops, 0)

    def test_invalid_model_candidates_are_rejected_before_process_start(self):
        for value in ('bad\nmodel', ' padded ', 'x' * 257, 1, ['fixture-model']):
            with self.subTest(value=value):
                self.controller.connect('/opt/example/codex', '/var/example/home', self.cwd, model_id=value)
                self.assertEqual(self.controller.snapshot['error']['code'], 'CHAT_MODEL_INVALID')
        self.assertEqual(self.transport.starts, [])

    def test_connect_only_initializes_and_reads_dedicated_account_without_refresh(self):
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'must-not-inherit', 'NODE_OPTIONS': '--injected',
                                    'CODEX_AUTH_TOKEN': 'must-not-inherit', 'HTTP_PROXY': 'private-proxy'}):
            self.connect()
        self.assertEqual(self.transport.methods(), ['initialize', 'initialized', 'account/read'])
        self.assertEqual(self.transport.messages[-1]['params'], {'refreshToken': False})
        self.assertEqual(self.controller.snapshot['phase'], 'signed_out')
        self.assertFalse(self.controller.snapshot['can_send'])
        program, args, env, cwd, *_ = self.transport.starts[0]
        self.assertEqual(program, '/opt/example/codex')
        self.assertEqual(cwd, self.cwd)
        self.assertEqual(env['CODEX_HOME'], '/var/example/freecad-chat/config')
        for key in ('OPENAI_API_KEY', 'NODE_OPTIONS', 'CODEX_AUTH_TOKEN', 'HTTP_PROXY'):
            self.assertNotIn(key, env)
        self.assertIn('apply_patch_freeform', args)
        self.assertNotIn('apply_patch', args)
        self.assertEqual(self.urls, [])

    def test_unsupported_version_and_api_key_account_cannot_send(self):
        self.controller.connect('/opt/example/codex', '/var/example/home', self.cwd)
        self.transport.result('initialize', {'userAgent': 'codex_cli_rs/0.159.0'})
        self.assertEqual(self.controller.snapshot['error']['code'], 'CHAT_UNSUPPORTED_VERSION')
        self.assertNotIn('account/read', self.transport.methods())
        self.connect({'type': 'apiKey'})
        self.controller.send('Do not send')
        self.assertNotIn('thread/start', self.transport.methods())
        self.assertEqual(self.controller.snapshot['account'], 'unsupported')

    def test_default_home_relative_and_same_directory_are_refused_before_start(self):
        for program, home, cwd in (('codex', '/var/example/home', self.cwd),
            ('/opt/codex', os.path.expanduser('~/.codex'), self.cwd),
            ('/opt/codex', self.cwd, self.cwd), ('/opt/codex', '/var/\nexample', self.cwd)):
            with self.subTest(home=home): self.controller.connect(program, home, cwd)
        self.assertEqual(self.transport.starts, [])

    def test_official_login_is_user_triggered_and_missing_completion_id_is_supported(self):
        self.connect()
        self.controller.login()
        self.assertEqual(self.transport.messages[-1]['params'], {'type': 'chatgpt'})
        url = 'https://auth.openai.com/oauth/authorize?synthetic=fixture'
        self.transport.result('account/login/start', {'type': 'chatgpt', 'loginId': 'private-login', 'authUrl': url})
        self.assertEqual(self.urls, [url])
        self.assertEqual(self.controller.snapshot['phase'], 'logging_in')
        self.assertNotIn('private-login', json.dumps(self.controller.snapshot))
        self.transport.notify('account/login/completed', {'success': True})
        self.transport.result('account/read', {'account': {'type': 'chatgpt'}})
        self.assertTrue(self.controller.snapshot['can_send'])

    def test_login_completion_before_response_and_wrong_login_are_not_misbound(self):
        self.connect()
        self.controller.login()
        self.transport.notify('account/login/completed', {'success': True, 'loginId': 'current-login'})
        self.transport.result('account/login/start', {'type': 'chatgpt', 'loginId': 'current-login',
            'authUrl': 'https://auth.openai.com/authorize'})
        self.transport.result('account/read', {'account': {'type': 'chatgpt'}})
        self.assertEqual(self.urls, [])
        self.assertTrue(self.controller.snapshot['can_send'])
        self.controller.login()
        self.transport.result('account/login/start', {'type': 'chatgpt', 'loginId': 'next-login',
            'authUrl': 'https://auth.openai.com/authorize'})
        self.transport.notify('account/login/completed', {'success': True, 'loginId': 'previous-login'})
        self.assertEqual(self.controller.snapshot['phase'], 'logging_in')
        self.transport.notify('account/login/completed', {'success': False, 'loginId': 'next-login',
                                                        'error': '/private/native/cancelled'})
        self.assertEqual(self.controller.snapshot['phase'], 'signed_out')
        self.assertNotIn('/private/native', json.dumps(self.controller.snapshot))

    def test_login_rejects_unofficial_or_ambiguous_urls_without_opening(self):
        for url in ('http://auth.openai.com/login', 'https://auth.openai.com.evil.invalid/',
                    'https://evil.invalid@auth.openai.com/', 'https://auth.openai.com:444/',
                    'https://auth.openai.com/#fragment', 'file:///private/auth'):
            with self.subTest(url=url):
                self.connect()
                self.controller.login()
                self.transport.result('account/login/start', {'type': 'chatgpt', 'loginId': 'fixture', 'authUrl': url})
                self.assertEqual(self.controller.snapshot['error']['code'], 'CHAT_LOGIN_URL_REJECTED')
        self.assertEqual(self.urls, [])

    def test_early_completion_for_old_login_does_not_suppress_new_browser_login(self):
        self.connect()
        self.controller.login()
        self.transport.notify('account/login/completed', {'success': False, 'loginId': 'old-login'})
        url = 'https://auth.openai.com/authorize'
        self.transport.result('account/login/start', {'type': 'chatgpt', 'loginId': 'new-login', 'authUrl': url})
        self.assertEqual(self.urls, [url])
        self.assertEqual(self.controller.snapshot['phase'], 'logging_in')
        self.assertEqual(self.transport.methods().count('account/read'), 1)
        self.transport.notify('account/login/completed', {'success': True, 'loginId': 'new-login'})
        self.transport.result('account/read', {'account': {'type': 'chatgpt'}})
        self.assertTrue(self.controller.snapshot['can_send'])

    def test_rejected_browser_request_and_unfinished_login_do_not_start_chat(self):
        self.connect()
        self.controller.open_url = lambda _url: False
        self.controller.login()
        self.transport.result('account/login/start', {'type': 'chatgpt', 'loginId': 'private-login',
            'authUrl': 'https://auth.openai.com/authorize'})
        self.assertEqual(self.controller.snapshot['error']['code'], 'CHAT_BROWSER_OPEN_FAILED')
        self.assertEqual(self.transport.stops, 1)
        self.connect()
        self.controller.open_url = lambda _url: True
        self.controller.login()
        self.transport.result('account/login/start', {'type': 'chatgpt', 'loginId': 'another-private-login',
            'authUrl': 'https://auth.openai.com/authorize'})
        self.assertEqual(self.transport.timeouts[-1], 180000)
        self.transport.error(types.SimpleNamespace(code='CHAT_TIMEOUT'))
        self.assertFalse(self.controller.snapshot['can_send'])
        self.assertNotIn('thread/start', self.transport.methods())

    def test_unknown_and_duplicate_response_ids_never_advance_the_handshake(self):
        self.controller.connect('/opt/example/codex', '/var/example/home', self.cwd)
        initialize_id = self.transport.messages[-1]['id']
        before = len(self.transport.messages)
        self.transport.data({'id': initialize_id + 100, 'result': {'userAgent': 'codex_cli_rs/0.160.0'}})
        self.assertEqual(len(self.transport.messages), before)
        self.transport.result('initialize', {'userAgent': 'codex_cli_rs/0.160.0'})
        after = len(self.transport.messages)
        self.transport.data({'id': initialize_id, 'result': {'userAgent': 'codex_cli_rs/0.160.0'}})
        self.assertEqual(len(self.transport.messages), after)
        self.assertEqual(self.transport.methods().count('account/read'), 1)

    def test_send_checks_all_mcp_pages_and_thread_policy_before_text(self):
        self.ready()
        self.controller.send('Synthetic question')
        self.assertEqual(self.transport.messages[-1]['method'], 'config/read')
        self.assertEqual(self.transport.messages[-1]['params'], {'cwd': self.cwd, 'includeLayers': False})
        self.assertNotIn('mcpServerStatus/list', self.transport.methods())
        self.pass_config()
        self.transport.result('mcpServerStatus/list', {'data': [], 'nextCursor': 'next-fixture-page'})
        self.assertEqual(self.transport.messages[-1]['params']['cursor'], 'next-fixture-page')
        self.transport.result('mcpServerStatus/list', {'data': []})
        thread_request = self.transport.messages[-1]
        self.assertEqual(thread_request['method'], 'thread/start')
        self.assertTrue(thread_request['params']['ephemeral'])
        self.assertEqual(thread_request['params']['sandbox'], 'read-only')
        self.assertEqual(thread_request['params']['approvalPolicy'], 'never')
        self.transport.result('thread/start', self.thread_result())
        self.assertEqual(self.transport.messages[-1]['params']['threadId'], 'private-thread-fixture')
        self.assertNotIn('turn/start', self.transport.methods())
        self.transport.result('mcpServerStatus/list', {'data': []})
        turn = self.transport.messages[-1]
        self.assertEqual(turn['params']['sandboxPolicy'], {'type': 'readOnly', 'networkAccess': False})
        self.assertEqual(turn['params']['input'], [{'type': 'text', 'text': 'Synthetic question'}])
        self.assertNotIn('private-thread-fixture', json.dumps(self.controller.snapshot))

    def test_nonempty_inventory_and_incompatible_policy_fail_closed(self):
        self.ready()
        self.controller.send('No tools')
        self.pass_config()
        self.transport.result('mcpServerStatus/list', {'data': [{'name': 'unexpected-fixture'}]})
        self.assertEqual(self.controller.snapshot['error']['code'], 'CHAT_MCP_NOT_EMPTY')
        self.assertNotIn('thread/start', self.transport.methods())
        self.ready()
        self.controller.send('No write')
        self.pass_config()
        self.transport.result('mcpServerStatus/list', {'data': []})
        self.transport.result('thread/start', self.thread_result(sandbox={'type': 'workspaceWrite', 'networkAccess': False}))
        self.assertEqual(self.controller.snapshot['error']['code'], 'CHAT_POLICY_REJECTED')
        self.assertNotIn('turn/start', self.transport.methods())

    def test_thread_specific_inventory_is_also_required_empty(self):
        self.ready()
        self.controller.send('No tools')
        self.pass_config()
        self.transport.result('mcpServerStatus/list', {'data': []})
        self.transport.result('thread/start', self.thread_result())
        self.transport.result('mcpServerStatus/list', {'data': [{'name': 'thread-only-fixture'}]})
        self.assertEqual(self.controller.snapshot['error']['code'], 'CHAT_MCP_NOT_EMPTY')
        self.assertNotIn('turn/start', self.transport.methods())

    def test_stream_reconciles_final_item_without_duplicates_and_keeps_own_thread(self):
        self.ready()
        self.start_turn()
        self.transport.notify('item/agentMessage/delta', self.event(itemId='item-one', delta='partial'))
        final = {'id': 'item-one', 'type': 'agentMessage', 'text': 'Final reply'}
        self.transport.notify('item/completed', self.event(item=final, completedAtMs=0))
        self.transport.notify('item/agentMessage/delta', self.event(itemId='item-one', delta='late-ignored'))
        self.complete(items=[final])
        self.assertEqual([row['text'] for row in self.controller.snapshot['transcript']], ['Synthetic question', 'Final reply'])
        self.assertTrue(self.controller.snapshot['can_send'])
        self.controller.send('Next question')
        self.pass_config()
        self.transport.result('mcpServerStatus/list', {'data': []})
        self.transport.result('mcpServerStatus/list', {'data': []})
        self.assertEqual(self.transport.methods().count('thread/start'), 1)
        self.assertEqual(self.transport.methods().count('turn/start'), 2)

    def test_early_turn_notifications_are_bound_and_completed_before_rpc_response(self):
        self.ready()
        self.controller.send('Early events')
        self.pass_config()
        self.transport.result('mcpServerStatus/list', {'data': []})
        self.transport.result('thread/start', self.thread_result())
        self.transport.result('mcpServerStatus/list', {'data': []})
        self.transport.notify('item/agentMessage/delta', self.event(itemId='early-item', delta='Early answer'))
        self.complete()
        before = self.controller.snapshot
        self.transport.result('turn/start', {'turn': {'id': 'private-turn-fixture', 'items': [], 'status': 'inProgress'}})
        self.assertEqual(self.controller.snapshot, before)
        self.assertTrue(before['can_send'])

    def test_interrupt_waits_for_turn_completion_and_late_callbacks_cannot_reconnect(self):
        self.ready()
        self.start_turn()
        self.controller.interrupt()
        request = self.transport.messages[-1]
        self.assertEqual(request['method'], 'turn/interrupt')
        self.assertEqual(set(request['params']), {'threadId', 'turnId'})
        self.transport.result('turn/interrupt', {})
        self.assertEqual(self.controller.snapshot['phase'], 'interrupting')
        self.complete('interrupted')
        self.assertTrue(self.controller.snapshot['can_send'])
        old_data, old_error = self.transport.data, self.transport.error
        self.controller.disconnect()
        self.connect()
        before = self.controller.snapshot
        old_data({'method': 'account/updated', 'params': {'authMode': 'chatgpt'}})
        old_error(types.SimpleNamespace(code='CHAT_TIMEOUT'))
        self.assertEqual(self.controller.snapshot, before)

    def test_unknown_approval_is_explicitly_rejected_and_no_tool_is_executed(self):
        self.ready()
        self.transport.data({'id': 'server-private-request', 'method': 'item/commandExecution/requestApproval',
                             'params': {'command': 'never execute fixture'}})
        self.assertEqual(self.transport.messages[-1]['error']['code'], -32601)
        self.assertEqual(self.controller.snapshot['error']['code'], 'CHAT_REQUEST_REJECTED')
        self.assertEqual(self.transport.stops, 1)
        self.assertNotIn('never execute fixture', json.dumps(self.controller.snapshot))

    def test_input_output_single_flight_and_timeout_are_bounded_without_retry(self):
        self.ready()
        self.controller.send('x' * (16 * 1024 + 1))
        self.assertNotIn('mcpServerStatus/list', self.transport.methods())
        self.start_turn()
        count = len(self.transport.messages)
        self.controller.send('Overlapping send')
        self.assertEqual(len(self.transport.messages), count)
        self.transport.notify('item/agentMessage/delta', self.event(itemId='huge-item', delta='x' * (256 * 1024)))
        self.assertEqual(self.controller.snapshot['error']['code'], 'CHAT_OUTPUT_TOO_LARGE')
        self.assertLess(len(json.dumps(self.controller.snapshot)), 5000)
        self.ready()
        self.start_turn()
        count = len(self.transport.messages)
        self.transport.error(types.SimpleNamespace(code='CHAT_TIMEOUT'))
        self.assertEqual(self.controller.snapshot['error']['code'], 'CHAT_TIMEOUT')
        self.assertEqual(len(self.transport.messages), count)

    def test_unexpected_tool_item_and_server_error_do_not_expose_native_details(self):
        self.ready()
        self.start_turn()
        self.transport.notify('item/started', self.event(item={'id': 'tool-fixture', 'type': 'fileChange'}))
        self.assertEqual(self.controller.snapshot['error']['code'], 'CHAT_UNEXPECTED_TOOL')
        self.ready()
        self.controller.send('Failure')
        self.pass_config()
        identifier = self.transport.messages[-1]['id']
        self.transport.data({'id': identifier, 'error': {'code': -1, 'message': '/private/secret/provider-detail'}})
        self.assertEqual(self.controller.snapshot['error']['code'], 'CHAT_REQUEST_FAILED')
        self.assertNotIn('/private/secret', json.dumps(self.controller.snapshot))

    def test_mcp_and_plain_turns_have_fixed_total_budgets_and_commentary_is_not_final(self):
        for use_mcp, budget in ((False, 120000), (True, 300000)):
            with self.subTest(mcp=use_mcp), patch.object(chat.time, 'monotonic', return_value=100.0) as clock:
                self.setUp()
                self.ready(mcp=self.mcp_config() if use_mcp else None)
                self.start_turn()
                self.assertEqual(self.transport.timeouts[-1], budget)
                count, timeouts = len(self.transport.messages), list(self.transport.timeouts)
                clock.return_value = 160.0
                commentary = {'id': 'commentary', 'type': 'agentMessage', 'phase': 'commentary',
                              'text': 'I will check the permitted tool scope.'}
                self.transport.notify('item/completed', self.event(item=commentary, completedAtMs=0))
                if use_mcp:
                    self.transport.notify('item/completed', self.event(completedAtMs=0, item={
                        'id': 'capability-call', 'type': 'mcpToolCall', 'server': 'phoenix_freecad',
                        'tool': 'get_capabilities', 'status': 'completed', 'result': {'content': []}}))
                self.transport.notify('error', self.event(willRetry=True, error={'codexErrorInfo': 'serverOverloaded'}))
                self.assertEqual(self.controller.snapshot['phase'], 'sending')
                self.assertFalse(self.controller.snapshot['can_send'])
                self.assertEqual(len(self.transport.messages), count)
                self.assertEqual(self.transport.timeouts, timeouts)
                # Even a late interrupt RPC cannot restart the original budget.
                clock.return_value = 100.0 + budget / 1000 - 5
                self.controller.interrupt()
                self.assertEqual(self.transport.timeouts[-1], 5000)
                self.transport.result('turn/interrupt', {})
                self.assertEqual(self.transport.timeouts[-1], 5000)
                self.transport.error(types.SimpleNamespace(code='CHAT_TIMEOUT'))
                self.assertEqual(self.controller.snapshot['error']['code'], 'CHAT_TIMEOUT')
                self.assertEqual(self.transport.stops, 1)
                self.assertEqual(self.transport.methods().count('turn/start'), 1)

    def test_snapshot_is_detached_and_failed_turn_does_not_automatically_retry(self):
        self.ready()
        self.start_turn()
        snapshot = self.controller.snapshot
        snapshot['transcript'].clear()
        self.assertEqual(len(self.controller.snapshot['transcript']), 1)
        self.transport.notify('item/agentMessage/delta', self.event(itemId='partial', delta='Partial answer'))
        count = len(self.transport.messages)
        self.complete('failed')
        self.assertEqual(self.controller.snapshot['transcript'][-1]['status'], 'error')
        self.assertEqual(self.controller.snapshot['error']['code'], 'CHAT_TURN_FAILED')
        self.assertEqual(len(self.transport.messages), count)

    def test_effective_config_missing_conflicting_or_untyped_values_fail_before_mcp(self):
        cases = []
        for key in ('approval_policy', 'sandbox_mode', 'model_provider', 'forced_login_method', 'web_search', 'cli_auth_credentials_store'):
            missing = self.effective_config()
            del missing[key]
            cases.append(missing)
            conflicting = self.effective_config()
            conflicting[key] = 'unexpected-fixture-value'
            cases.append(conflicting)
        for key in self.effective_config()['features']:
            missing = self.effective_config()
            del missing['features'][key]
            cases.append(missing)
            conflicting = self.effective_config()
            conflicting['features'][key] = True
            cases.append(conflicting)
        for value in (None, {'view_image': True}, {'view_image': 'false'}, {'view_image': 0}, {'view_image': None}):
            wrong = self.effective_config()
            wrong['tools'] = value
            cases.append(wrong)
        wrong = self.effective_config()
        wrong['features']['shell_tool'] = 'false'
        cases.append(wrong)
        for config in cases:
            with self.subTest(config=config):
                self.ready()
                self.controller.send('Do not send without verified policy')
                self.transport.result('config/read', {'config': config, 'origins': {'private-origin': '/private/do-not-display'}})
                self.assertEqual(self.controller.snapshot['error']['code'], 'CHAT_EFFECTIVE_CONFIG_REJECTED')
                self.assertNotIn('/private', json.dumps(self.controller.snapshot))
                self.assertNotIn('mcpServerStatus/list', self.transport.methods())
                self.assertNotIn('thread/start', self.transport.methods())

    def test_unreported_view_image_is_unknown_and_does_not_claim_tool_removal(self):
        for tools in ('absent', {}):
            with self.subTest(tools=tools):
                self.ready()
                self.controller.send('Check only reported effective configuration')
                config = self.effective_config()
                if tools == 'absent':
                    config.pop('tools')
                else:
                    config['tools'] = tools
                self.transport.result('config/read', {'config': config, 'origins': {'private': '/private/native'}})
                self.assertEqual(self.transport.messages[-1]['method'], 'mcpServerStatus/list')
                self.assertEqual(self.controller.snapshot['phase'], 'sending')
                self.assertNotIn('view_image', json.dumps(self.controller.snapshot))
                self.assertNotIn('/private', json.dumps(self.controller.snapshot))
                self.controller.disconnect()

    def test_config_rpc_failure_and_old_response_do_not_send_or_retry(self):
        self.ready()
        self.controller.send('Inspect first')
        identifier = self.transport.messages[-1]['id']
        self.transport.data({'id': identifier, 'error': {'code': -32601, 'message': '/private/native-detail'}})
        self.assertEqual(self.controller.snapshot['error']['code'], 'CHAT_EFFECTIVE_CONFIG_REJECTED')
        self.assertNotIn('/private', json.dumps(self.controller.snapshot))
        self.assertNotIn('mcpServerStatus/list', self.transport.methods())
        self.ready()
        self.controller.send('Cancelled check')
        callback, before = self.transport.data, len(self.transport.messages)
        self.controller.interrupt()
        callback({'id': self.transport.messages[-1]['id'], 'result': {'config': self.effective_config(), 'origins': {}}})
        self.assertEqual(len(self.transport.messages), before)
        self.assertNotIn('mcpServerStatus/list', self.transport.methods())

    def test_service_reconnect_preserves_turn_and_deltas_can_finish_without_resend(self):
        self.ready()
        self.start_turn()
        self.transport.notify('item/agentMessage/delta', self.event(itemId='reply', delta='First '))
        count, timeouts = len(self.transport.messages), list(self.transport.timeouts)
        for _ in range(3):
            self.transport.notify('error', self.event(willRetry=True, error={
                'codexErrorInfo': {'responseStreamDisconnected': {'httpStatusCode': None}},
                'message': 'private-provider-details https://private.invalid/?credential=fixture'}))
        self.assertEqual(self.controller.snapshot['phase'], 'sending')
        self.assertEqual(self.controller.snapshot['error']['code'], 'CHAT_SERVICE_RECONNECTING')
        self.assertEqual(self.transport.stops, 0)
        self.assertEqual(len(self.transport.messages), count)
        self.assertEqual(self.transport.timeouts, timeouts)
        self.assertNotIn('private-provider-details', json.dumps(self.controller.snapshot))
        self.transport.notify('item/agentMessage/delta', self.event(itemId='reply', delta='last'))
        self.complete(items=[{'id': 'reply', 'type': 'agentMessage', 'text': 'First last'}])
        self.assertTrue(self.controller.snapshot['can_send'])
        self.assertIsNone(self.controller.snapshot['error'])
        self.assertEqual(self.controller.snapshot['transcript'][-1]['text'], 'First last')
        self.assertEqual(self.transport.methods().count('turn/start'), 1)

    def test_service_recovery_keeps_interrupting_and_original_timeout_still_terminates(self):
        self.ready()
        self.start_turn()
        self.controller.interrupt()
        self.transport.result('turn/interrupt', {})
        count, timeouts = len(self.transport.messages), list(self.transport.timeouts)
        self.transport.notify('error', self.event(willRetry=True, error={'codexErrorInfo': 'serverOverloaded'}))
        self.assertEqual(self.controller.snapshot['phase'], 'interrupting')
        self.assertEqual(self.transport.timeouts, timeouts)
        self.assertEqual(len(self.transport.messages), count)
        self.transport.error(types.SimpleNamespace(code='CHAT_TIMEOUT'))
        self.assertEqual(self.controller.snapshot['error']['code'], 'CHAT_TIMEOUT')
        self.assertEqual(self.transport.stops, 1)
        self.assertEqual(len(self.transport.messages), count)

    def test_terminal_provider_errors_use_fixed_typed_messages_without_raw_details(self):
        cases = [({'responseStreamDisconnected': {'httpStatusCode': None}}, 'CHAT_NETWORK_FAILED'),
                 ({'httpConnectionFailed': {'httpStatusCode': 503}}, 'CHAT_NETWORK_FAILED'),
                 ('unauthorized', 'CHAT_AUTH_FAILED'),
                 ({'responseStreamConnectionFailed': {'httpStatusCode': 401}}, 'CHAT_AUTH_FAILED'),
                 ('usageLimitExceeded', 'CHAT_LIMIT_REACHED'),
                 ({'httpConnectionFailed': {'httpStatusCode': 429}}, 'CHAT_LIMIT_REACHED'),
                 ('badRequest', 'CHAT_MODEL_REQUEST_FAILED'),
                 ({'httpConnectionFailed': {'httpStatusCode': 404}}, 'CHAT_MODEL_REQUEST_FAILED'),
                 ('other', 'CHAT_TURN_FAILED')]
        for index, (info, expected) in enumerate(cases):
            with self.subTest(info=info):
                self.ready()
                self.start_turn()
                params = self.event(error={'codexErrorInfo': info, 'message': '/private/provider?token=fixture'})
                if index % 2:
                    params['willRetry'] = False  # Missing also terminates conservatively.
                self.transport.notify('error', params)
                self.assertEqual(self.controller.snapshot['error']['code'], expected)
                self.assertNotIn('/private', json.dumps(self.controller.snapshot))
                self.assertFalse(self.controller.snapshot['can_send'])

    def test_invalid_retry_flags_fail_closed_and_old_turn_errors_are_ignored(self):
        for retry in (None, 0, 1, 'true', []):
            with self.subTest(retry=retry):
                self.ready()
                self.start_turn()
                self.transport.notify('error', self.event(willRetry=retry, error={'codexErrorInfo': 'other'}))
                self.assertEqual(self.controller.snapshot['error']['code'], 'CHAT_PROTOCOL_INVALID')
        self.ready()
        self.start_turn()
        before, count = self.controller.snapshot, len(self.transport.messages)
        self.transport.notify('error', self.event(turnId='old-turn-fixture', willRetry=False,
                                                error={'codexErrorInfo': 'unauthorized'}))
        self.assertEqual(self.controller.snapshot, before)
        self.assertEqual(len(self.transport.messages), count)

    def test_mcp_connect_uses_literal_toml_values_and_exact_scoped_tools(self):
        config = self.mcp_config()
        config['cli_entry'] = '/opt/example/`literal`/"quoted"/phoenix-cli.js'
        self.ready(mcp=config)
        arguments = self.transport.starts[-1][1]
        override = next(value for value in arguments if value.startswith('mcp_servers.phoenix_freecad='))
        parsed = tomllib.loads(override)['mcp_servers']['phoenix_freecad']
        self.assertEqual(parsed['command'], config['node_executable'])
        self.assertEqual(parsed['args'], [config['cli_entry']])
        self.assertEqual(parsed['env'], {'PHOENIX_FREECAD_TOOL_SCOPE': 'chat', 'PHOENIX_FREECAD_URL': config['bridge_url']})
        self.assertIs(parsed['required'], True)
        self.assertEqual(set(parsed['enabled_tools']), set(EXPECTED_CHAT_TOOLS))
        self.assertEqual(len(parsed['enabled_tools']), 19)
        self.assertEqual(len(set(EXPECTED_CHAT_TOOLS)), 19)
        self.assertNotIn('execute_script', parsed['enabled_tools'])
        self.assertTrue(self.controller.snapshot['mcp_enabled'])
        self.assertEqual(self.controller.snapshot['tool_calls'], [])

    def test_mcp_paths_and_bridge_origin_are_validated_before_any_child(self):
        invalid = [dict(self.mcp_config(), bridge_url=url) for url in (
            'http://localhost:9876', 'http://127.0.0.1', 'https://127.0.0.1:9876',
            'http://127.0.0.1:9876/path', 'http://user@127.0.0.1:9876',
            'http://127.0.0.1:9876/?token=fixture', 'http://127.0.0.1:9876/#fragment',
            'http://127.0.0.1:0', 'http://192.0.2.1:9876')]
        invalid.extend((dict(self.mcp_config(), node_executable='node'),
                        dict(self.mcp_config(), cli_entry='/opt/\ncli.js'),
                        dict(self.mcp_config(), extra='not allowed'), {}))
        for config in invalid:
            with self.subTest(config=config):
                self.controller.connect('/opt/codex', '/var/example/home', self.cwd, mcp=config)
                self.assertEqual(self.controller.snapshot['error']['code'], 'CHAT_MCP_INVALID_CONFIGURATION')
        self.assertEqual(self.transport.starts, [])

    def test_mcp_config_gate_rejects_extra_server_wrong_env_and_changed_tools(self):
        changes = ('extra_server', 'wrong_command', 'wrong_args', 'extra_env', 'wrong_scope',
                   'not_required', 'missing_tool', 'extra_tool', 'env_vars', 'disabled_tools')
        for change in changes:
            with self.subTest(change=change):
                self.ready(mcp=self.mcp_config())
                self.controller.send('Read CAD on explicit request')
                config = self.effective_config()
                server = config['mcp_servers']['phoenix_freecad']
                if change == 'extra_server': config['mcp_servers']['unapproved'] = {}
                elif change == 'wrong_command': server['command'] = '/opt/other-node'
                elif change == 'wrong_args': server['args'].append('--extra')
                elif change == 'extra_env': server['env']['SECRET_ENV'] = 'never-display'
                elif change == 'wrong_scope': server['env']['PHOENIX_FREECAD_TOOL_SCOPE'] = 'full'
                elif change == 'not_required': server['required'] = False
                elif change == 'missing_tool': server['enabled_tools'].pop()
                elif change == 'extra_tool': server['enabled_tools'].append('execute_script')
                elif change == 'env_vars': server['env_vars'] = ['OPENAI_API_KEY']
                elif change == 'disabled_tools': server['disabled_tools'] = ['get_connection_status']
                self.transport.result('config/read', {'config': config, 'origins': {}})
                self.assertEqual(self.controller.snapshot['error']['code'], 'CHAT_MCP_CONFIG_REJECTED')
                self.assertNotIn('mcpServerStatus/list', self.transport.methods())
                self.assertNotIn('never-display', json.dumps(self.controller.snapshot))

    def test_mcp_inventory_covers_all_pages_and_checks_runtime_and_descriptor_identity(self):
        for change in ('missing_tool', 'extra_tool', 'wrong_descriptor_name', 'unknown_auth', 'tools_error', 'extra_server'):
            with self.subTest(change=change):
                self.ready(mcp=self.mcp_config())
                self.controller.send('Read CAD on explicit request')
                self.pass_config()
                inventory = self.inventory()
                server = inventory['data'][0]
                if change == 'missing_tool': server['tools'].pop('get_document_info')
                elif change == 'extra_tool': server['tools']['execute_script'] = {'name': 'execute_script'}
                elif change == 'wrong_descriptor_name': server['tools']['get_document_info']['name'] = 'execute_script'
                elif change == 'unknown_auth': server['authStatus'] = 'unknown'
                elif change == 'tools_error': server['toolsError'] = '/private/native-detail'
                elif change == 'extra_server': inventory['data'].append(dict(server, name='unapproved'))
                self.transport.result('mcpServerStatus/list', inventory)
                self.assertEqual(self.controller.snapshot['error']['code'], 'CHAT_MCP_INVENTORY_REJECTED')
                self.assertNotIn('thread/start', self.transport.methods())
        self.ready(mcp=self.mcp_config())
        self.controller.send('Read CAD on explicit request')
        self.pass_config()
        first = self.inventory()
        first['nextCursor'] = 'empty-final-page'
        self.transport.result('mcpServerStatus/list', first)
        self.assertNotIn('thread/start', self.transport.methods())
        self.transport.result('mcpServerStatus/list', {'data': []})
        self.transport.result('thread/start', self.thread_result())
        self.transport.result('mcpServerStatus/list', self.inventory(False))
        self.assertEqual(self.controller.snapshot['error']['code'], 'CHAT_MCP_INVENTORY_REJECTED')
        self.assertNotIn('turn/start', self.transport.methods())

    def test_allowlisted_tool_progress_is_separate_bounded_and_result_details_are_private(self):
        self.ready(mcp=self.mcp_config())
        self.start_turn('Please read the document and change appearance only as requested')
        call = {'id': 'private-tool-call', 'type': 'mcpToolCall', 'server': 'phoenix_freecad',
                'tool': 'set_appearance', 'status': 'inProgress', 'arguments': {'private_document': 'never-display'}}
        self.transport.notify('item/started', self.event(item=call))
        self.assertEqual(self.controller.snapshot['tool_calls'], [{'server': 'phoenix_freecad',
            'name': 'set_appearance', 'status': 'in_progress'}])
        completed = dict(call, status='completed', result={'content': [{'type': 'text', 'text': 'private-result'}]})
        self.transport.notify('item/completed', self.event(item=completed, completedAtMs=0))
        self.transport.notify('item/started', self.event(item=call))  # Late progress cannot reopen it.
        self.complete(items=[completed, {'id': 'assistant-reply', 'type': 'agentMessage', 'text': 'The tool call completed.'}])
        snapshot = self.controller.snapshot
        self.assertEqual(snapshot['tool_calls'][0]['status'], 'completed')
        self.assertEqual({row['role'] for row in snapshot['transcript']}, {'user', 'assistant'})
        for private in ('private-tool-call', 'private_document', 'never-display', 'private-result'):
            self.assertNotIn(private, json.dumps(snapshot))
        self.assertTrue(snapshot['can_send'])

    def test_tool_failure_stays_failed_and_interrupted_calls_never_claim_completion(self):
        self.ready(mcp=self.mcp_config())
        self.start_turn()
        call = {'id': 'tool-failed', 'type': 'mcpToolCall', 'server': 'phoenix_freecad',
                'tool': 'get_document_info', 'status': 'completed', 'arguments': {},
                'error': {'message': '/private/provider-error'}}
        self.transport.notify('item/completed', self.event(item=call, completedAtMs=0))
        self.transport.notify('item/completed', self.event(item=dict(call, error=None), completedAtMs=0))
        self.assertEqual(self.controller.snapshot['tool_calls'][0]['status'], 'failed')
        pending = dict(call, id='tool-pending', status='inProgress', error=None)
        self.transport.notify('item/started', self.event(item=pending))
        self.complete('interrupted')
        self.assertTrue(all(row['status'] == 'failed' and set(row) == {'server', 'name', 'status'}
                            for row in self.controller.snapshot['tool_calls']))
        self.assertNotIn('/private', json.dumps(self.controller.snapshot))

    def test_design_diagnostics_is_required_in_effective_config_and_extra_read_tool_is_refused(self):
        for change in ('missing_design', 'extra_readonly', 'duplicate_design'):
            with self.subTest(change=change):
                self.ready(mcp=self.mcp_config())
                self.controller.send('Inspect the public design fixture')
                config = self.effective_config()
                tools = config['mcp_servers']['phoenix_freecad']['enabled_tools']
                if change == 'missing_design': tools.remove('inspect_design')
                elif change == 'extra_readonly': tools.append('capture_view')
                else: tools.append('inspect_design')
                self.transport.result('config/read', {'config': config, 'origins': {}})
                self.assertEqual(self.controller.snapshot['error']['code'], 'CHAT_MCP_CONFIG_REJECTED')
                self.assertNotIn('mcpServerStatus/list', self.transport.methods())
                self.assertNotIn('thread/start', self.transport.methods())

    def test_design_diagnostics_inventory_is_required_before_thread_and_before_turn(self):
        for phase in ('before_thread', 'before_turn'):
            for change in ('missing_design', 'extra_readonly', 'wrong_design_name'):
                with self.subTest(phase=phase, change=change):
                    self.ready(mcp=self.mcp_config())
                    self.controller.send('Inspect the public design fixture')
                    self.pass_config()
                    if phase == 'before_turn':
                        self.transport.result('mcpServerStatus/list', self.inventory())
                        thread_request = self.transport.messages[-1]
                        instructions = thread_request['params']['developerInstructions']
                        self.assertIn('inspect_design', instructions)
                        self.assertIn('cached constraints', instructions)
                        self.assertIn('never solve sketches or recompute automatically', instructions)
                        self.transport.result('thread/start', self.thread_result())
                    inventory = self.inventory(phase == 'before_turn')
                    tools = inventory['data'][0]['tools']
                    if change == 'missing_design': tools.pop('inspect_design')
                    elif change == 'extra_readonly': tools['capture_view'] = {'name': 'capture_view'}
                    else: tools['inspect_design']['name'] = 'get_document_info'
                    self.transport.result('mcpServerStatus/list', inventory)
                    self.assertEqual(self.controller.snapshot['error']['code'], 'CHAT_MCP_INVENTORY_REJECTED')
                    self.assertNotIn('turn/start', self.transport.methods())
                    if phase == 'before_thread': self.assertNotIn('thread/start', self.transport.methods())

    def test_native_approval_inventory_is_exact_before_thread_and_turn(self):
        approval_tools = ('prepare_part_changes', 'request_part_change_approval', 'get_part_change_status')
        for phase in ('before_thread', 'before_turn'):
            for change in (*approval_tools, 'old_sixteen', 'direct_apply'):
                with self.subTest(phase=phase, change=change):
                    self.ready(mcp=self.mcp_config())
                    self.controller.send('Preview a neutral Label change for native review')
                    self.pass_config()
                    if phase == 'before_turn':
                        self.transport.result('mcpServerStatus/list', self.inventory())
                        self.transport.result('thread/start', self.thread_result())
                    inventory = self.inventory(phase == 'before_turn')
                    tools = inventory['data'][0]['tools']
                    if change == 'old_sixteen':
                        for name in approval_tools:
                            tools.pop(name)
                    elif change == 'direct_apply':
                        tools['apply_part_changes'] = {'name': 'apply_part_changes'}
                    else:
                        tools.pop(change)
                    self.transport.result('mcpServerStatus/list', inventory)
                    self.assertEqual(self.controller.snapshot['error']['code'], 'CHAT_MCP_INVENTORY_REJECTED')
                    self.assertNotIn('turn/start', self.transport.methods())
                    if phase == 'before_thread':
                        self.assertNotIn('thread/start', self.transport.methods())

    def test_approval_tool_events_do_not_store_preview_or_imply_an_application(self):
        self.ready(mcp=self.mcp_config())
        self.start_turn('Preview a neutral attribute change and wait for native confirmation')
        methods = list(self.transport.methods())
        for name in ('prepare_part_changes', 'request_part_change_approval', 'get_part_change_status'):
            item = {'id': 'synthetic-' + name, 'type': 'mcpToolCall', 'server': 'phoenix_freecad',
                'tool': name, 'status': 'completed',
                'arguments': {'token': 'synthetic-token-not-a-real-request'},
                'result': {'content': [{'type': 'text', 'text': 'synthetic-sensitive-preview'}],
                    'structuredContent': {'state': 'awaiting_confirmation', 'native_confirmation': False,
                        'rows': [{'old': 'synthetic-old', 'new': 'synthetic-new'}]}}}
            self.transport.notify('item/completed', self.event(item=item))
        snapshot = self.controller.snapshot
        self.assertEqual(snapshot['phase'], 'sending')
        self.assertEqual(snapshot['tool_calls'], [{'server': 'phoenix_freecad', 'name': name, 'status': 'completed'}
            for name in ('prepare_part_changes', 'request_part_change_approval', 'get_part_change_status')])
        self.assertEqual(self.transport.methods(), methods)
        for state in self.events + [snapshot]:
            encoded = json.dumps(state)
            for excluded in ('synthetic-token-not-a-real-request', 'synthetic-sensitive-preview',
                             'synthetic-old', 'synthetic-new', 'awaiting_confirmation', 'native_confirmation'):
                self.assertNotIn(excluded, encoded)

    def test_design_tool_progress_exports_only_server_name_status_without_call_or_result_details(self):
        self.ready(mcp=self.mcp_config())
        self.assertNotIn('thread/start', self.transport.methods())
        self.assertEqual(self.controller.snapshot['tool_calls'], [])
        self.start_turn('Inspect cached design diagnostics only when requested')
        call = {'id': 'synthetic-design-call', 'type': 'mcpToolCall', 'server': 'phoenix_freecad',
                'tool': 'inspect_design', 'status': 'inProgress',
                'arguments': {'document': 'private-design-fixture', 'objects': ['private-object-fixture']}}
        self.transport.notify('item/started', self.event(item=call))
        self.assertEqual(self.controller.snapshot['tool_calls'], [
            {'server': 'phoenix_freecad', 'name': 'inspect_design', 'status': 'in_progress'}])
        completed = dict(call, status='completed', result={'content': [
            {'type': 'text', 'text': 'private-diagnostic-results'}],
            'structuredContent': {'diagnostics': 'private-structured-diagnostic'}})
        self.transport.notify('item/completed', self.event(item=completed, completedAtMs=0))
        self.complete(items=[completed])
        snapshot = self.controller.snapshot
        self.assertEqual(snapshot['tool_calls'], [
            {'server': 'phoenix_freecad', 'name': 'inspect_design', 'status': 'completed'}])
        for private in ('synthetic-design-call', 'private-design-fixture', 'private-object-fixture',
                        'private-diagnostic-results', 'private-structured-diagnostic'):
            self.assertNotIn(private, json.dumps(snapshot))
        self.assertTrue(snapshot['can_send'])

    def test_unapproved_mcp_tool_or_server_still_stops_before_client_action(self):
        for server, tool in (('unapproved', 'get_document_info'), ('phoenix_freecad', 'execute_script')):
            with self.subTest(server=server,tool=tool):
                self.ready(mcp=self.mcp_config())
                self.start_turn()
                self.transport.notify('item/started', self.event(item={'id': 'blocked', 'type': 'mcpToolCall',
                    'server': server, 'tool': tool, 'status': 'inProgress', 'arguments': {}}))
                self.assertEqual(self.controller.snapshot['error']['code'], 'CHAT_UNEXPECTED_TOOL')
                self.assertFalse(self.controller.snapshot['can_send'])



    def test_explicit_desk_option_freezes_exact_twenty_four_tools_and_scoped_environment(self):
        config = self.desk_mcp_config()
        config['desk_config']['state_dir'] = '/var/neutral/`literal`/"quoted"/state'
        self.ready(mcp=config)
        override = next(arg for arg in self.transport.starts[-1][1] if arg.startswith('mcp_servers.'))
        parsed = tomllib.loads(override)['mcp_servers']['phoenix_freecad']
        self.assertEqual(set(parsed['enabled_tools']), set(EXPECTED_CHAT_TOOLS + EXPECTED_DESK_TOOLS))
        self.assertEqual(len(parsed['enabled_tools']), 24)
        expected = {'PHOENIX_FREECAD_TOOL_SCOPE': 'chat', 'PHOENIX_FREECAD_URL': config['bridge_url'],
                    'PHOENIX_DESK_CHAT_READS': '1', 'PHOENIX_DESK_NODE': config['desk_config']['node_executable'],
                    'PHOENIX_DESK_CLI': config['desk_config']['cli_entry'],
                    'PHOENIX_DESK_STATE_DIR': config['desk_config']['state_dir']}
        self.assertEqual(parsed['env'], expected)
        self.assertTrue(self.controller.snapshot['desk_enabled'])
        config['desk_config']['state_dir'] = '/neutral/changed-after-connect'
        self.assertEqual(chat._mcp_environment(self.controller._mcp), expected)
        self.assertNotIn('PHOENIX_DESK_NODE', self.transport.starts[-1][2])

    def test_desk_paths_are_exact_and_never_accept_arbitrary_commands_or_extra_fields(self):
        for desk in (None, {}, {'command': 'sh'},
                     dict(self.desk_mcp_config()['desk_config'], state_dir='relative'),
                     dict(self.desk_mcp_config()['desk_config'], cli_entry='/opt/neutral/other.mjs'),
                     dict(self.desk_mcp_config()['desk_config'], extra='rejected')):
            with self.subTest(desk=desk):
                config = dict(self.mcp_config(), desk_config=desk)
                self.controller.connect('/opt/neutral/codex', '/var/neutral/home', self.cwd, mcp=config)
                self.assertEqual(self.controller.snapshot['error']['code'], 'CHAT_MCP_INVALID_CONFIGURATION')
        self.assertEqual(self.transport.starts, [])

    def test_desk_effective_environment_and_inventory_fail_closed_before_thread_or_turn(self):
        for change in ('flag', 'path', 'extra_env', 'missing_tool', 'extra_tool'):
            with self.subTest(change=change):
                self.ready(mcp=self.desk_mcp_config())
                self.controller.send('Read only an explicitly named neutral workspace')
                config = self.effective_config(); server = config['mcp_servers']['phoenix_freecad']
                if change == 'flag': server['env']['PHOENIX_DESK_CHAT_READS'] = '0'
                elif change == 'path': server['env']['PHOENIX_DESK_STATE_DIR'] = '/neutral/other-state'
                elif change == 'extra_env': server['env']['NODE_OPTIONS'] = '--rejected'
                elif change == 'missing_tool': server['enabled_tools'].remove('desk_search_index')
                else: server['enabled_tools'].append('execute_script')
                self.transport.result('config/read', {'config': config, 'origins': {}})
                self.assertEqual(self.controller.snapshot['error']['code'], 'CHAT_MCP_CONFIG_REJECTED')
                self.assertNotIn('thread/start', self.transport.methods())
        for stage in ('before_thread', 'before_turn'):
            for change in ('missing', 'extra', 'writable', 'outside_server'):
                with self.subTest(stage=stage, change=change):
                    self.ready(mcp=self.desk_mcp_config()); self.controller.send('Neutral explicit query'); self.pass_config()
                    if stage == 'before_turn':
                        self.transport.result('mcpServerStatus/list', self.inventory())
                        self.transport.result('thread/start', self.thread_result())
                    inventory = self.inventory(stage == 'before_turn'); tools = inventory['data'][0]['tools']
                    if change == 'missing': tools.pop('desk_search_index')
                    elif change == 'extra': tools['execute_script'] = {'name': 'execute_script'}
                    elif change == 'writable': tools['desk_search_index']['annotations']['readOnlyHint'] = False
                    else: inventory['data'][0]['name'] = 'external_desk'
                    self.transport.result('mcpServerStatus/list', inventory)
                    self.assertEqual(self.controller.snapshot['error']['code'], 'CHAT_MCP_INVENTORY_REJECTED')
                    self.assertNotIn('turn/start', self.transport.methods())

    def test_desk_progress_is_opt_in_only_and_keeps_data_out_of_status_snapshot(self):
        for enabled in (False, True):
            with self.subTest(enabled=enabled):
                self.ready(mcp=self.desk_mcp_config() if enabled else self.mcp_config())
                self.start_turn('Query a neutral saved index only')
                if enabled:
                    instructions = next(m['params']['developerInstructions'] for m in reversed(self.transport.messages) if m['method'] == 'thread/start')
                    self.assertIn('not live FreeCAD memory', instructions)
                    self.assertIn('not physical part quantities', instructions)
                    self.assertIn('includeRuntimeFeatures=true', instructions)
                    self.assertIn('idle still requires preflight', instructions)
                    self.assertIn('When the user asks', instructions)
                item = {'id': 'neutral-desk-call', 'type': 'mcpToolCall', 'server': 'phoenix_freecad',
                    'tool': 'desk_search_index', 'status': 'completed', 'arguments': {'workspace': '/neutral/not-displayed'},
                    'result': {'content': [{'type': 'text', 'text': 'neutral-private-result'}]}}
                self.transport.notify('item/completed', self.event(item=item, completedAtMs=0))
                if enabled:
                    self.assertEqual(self.controller.snapshot['tool_calls'][-1]['name'], 'desk_search_index')
                    self.assertNotIn('/neutral/not-displayed', json.dumps(self.controller.snapshot))
                    self.assertNotIn('neutral-private-result', json.dumps(self.controller.snapshot))
                else:
                    self.assertEqual(self.controller.snapshot['error']['code'], 'CHAT_UNEXPECTED_TOOL')


    def test_large_cached_image_completed_event_keeps_only_tool_status_in_public_and_internal_chat_state(self):
        from test_pft_chat_qprocess import synthetic_cached_thumbnail_event
        self.ready(mcp=self.desk_mcp_config())
        self.start_turn('Show the explicitly requested neutral cached thumbnail')
        event = synthetic_cached_thumbnail_event()
        event['params'].update(self.event())
        image = event['params']['item']['result']['content'][0]['data']
        self.assertGreater(len(image), 512 * 1024)
        self.transport.notify(event['method'], event['params'])
        self.assertEqual(self.controller.snapshot['tool_calls'], [{'server': 'phoenix_freecad',
            'name': 'desk_get_cached_thumbnail', 'status': 'completed'}])
        self.assertIsNone(self.controller.snapshot['error'])
        for snapshot in self.events + [self.controller.snapshot]:
            public = json.dumps(snapshot)
            self.assertNotIn(image, public)
            self.assertNotIn('/neutral/not-displayed', public)
            self.assertNotIn('image/png', public)
        for value in (self.controller._transcript, self.controller._tool_calls, self.controller._items):
            self.assertNotIn(image, json.dumps(value))
        self.complete()
        self.assertTrue(self.controller.snapshot['can_send'])
        self.assertNotIn(image, json.dumps(self.controller.snapshot))


if __name__ == '__main__': unittest.main()
