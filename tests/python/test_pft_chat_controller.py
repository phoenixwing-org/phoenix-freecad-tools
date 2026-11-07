# SPDX-License-Identifier: Apache-2.0
"""Synthetic RPC lifecycle tests; never launches Codex or reads authentication."""
import importlib.util
from copy import deepcopy
import json
import os
import sys
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
    'prepare_part_changes', 'request_part_change_approval', 'get_part_change_status',
    'prepare_drawing_export', 'request_drawing_export_approval', 'get_drawing_export_status')

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


class PftSessionStore:
    """Private-store contract fake; no directories, credentials or files."""
    def __init__(self, home, cwd, records=()):
        self.home, self.cwd = home, cwd
        self.records = {row['thread_id']: dict(row) for row in records}
        self.gets, self.registrations = [], []
        self.register_error = None

    def get_session(self, thread_id):
        self.gets.append(thread_id)
        return dict(self.records[thread_id])

    def register_thread(self, thread_id, created_at, model, scope_fingerprint, history_mode='paginated', *, cli_version='0.160.0'):
        self.registrations.append((thread_id, created_at, model, scope_fingerprint, history_mode))
        if self.register_error is not None:
            raise self.register_error
        row = {'thread_id': thread_id, 'created_at': created_at, 'model': model,
               'scope_fingerprint': scope_fingerprint, 'history_mode': history_mode,
               'cli_version': cli_version, 'provider': 'openai'}
        self.records[thread_id] = row
        return dict(row)


class PftChatControllerTests(unittest.TestCase):
    def setUp(self):
        self.transport, self.events, self.urls = PftTransport(), [], []
        self.controller = chat.PftChatController(self.transport, self.events.append, lambda url: self.urls.append(url) or True,
                                                 network_environment=lambda: {})
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
        result['features'].update(respect_system_proxy=self.controller._respect_system_proxy, system_proxy_fallback=False)
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

    def test_public_connection_generation_changes_on_connect_and_disconnect(self):
        before = self.controller.snapshot['connection_generation']
        self.assertIs(type(before), int)
        self.ready()
        connected = self.controller.snapshot['connection_generation']
        self.assertGreater(connected, before)
        self.controller.disconnect()
        self.assertGreater(self.controller.snapshot['connection_generation'], connected)

    def test_verified_current_cli_completes_login_and_turn_with_unchanged_policy_checks(self):
        version = '0.167.0-alpha.3.2'
        self.controller.connect('/opt/example/codex', '/var/example/freecad-chat/config', self.cwd)
        self.transport.result('initialize', {'userAgent': 'codex_cli_rs/' + version + ' (fixture)'})
        self.transport.result('account/read', {'requiresOpenaiAuth': True})
        self.assertEqual(self.controller.snapshot['phase'], 'signed_out')
        self.controller.login()
        self.transport.result('account/login/start', {'type': 'chatgpt', 'loginId': 'neutral-login',
            'authUrl': 'https://auth.openai.com/authorize'})
        self.transport.notify('account/login/completed', {'loginId': 'neutral-login', 'success': True})
        self.transport.result('account/read', {'requiresOpenaiAuth': True, 'account': {'type': 'chatgpt'}})
        self.transport.result('model/list', {'data': [self.model_row()]})
        self.controller.send('Neutral compatible request')
        self.pass_config()
        self.transport.result('mcpServerStatus/list', self.inventory())
        result = self.thread_result()
        result['thread']['cliVersion'] = version
        self.transport.result('thread/start', result)
        self.transport.result('mcpServerStatus/list', self.inventory(True))
        self.transport.result('turn/start', {'turn': {'id': 'private-turn-fixture', 'items': [], 'status': 'inProgress'}})
        self.transport.notify('item/agentMessage/delta', self.event(itemId='neutral-answer', delta='Neutral reply'))
        self.complete()
        self.assertEqual(self.controller.snapshot['phase'], 'ready')
        self.assertEqual(self.controller.snapshot['transcript'][-1]['text'], 'Neutral reply')

    def test_unverified_cli_versions_and_current_cli_policy_drift_never_send(self):
        for version in ('0.167.0', '0.167.0-alpha.3.20', '0.167.0-alpha.3.2+unknown', '0.160.0-alpha.1'):
            with self.subTest(version=version):
                self.setUp()
                self.controller.connect('/opt/example/codex', '/var/example/freecad-chat/config', self.cwd)
                self.transport.result('initialize', {'userAgent': 'codex_cli_rs/' + version + ' (fixture)'})
                self.assertEqual(self.controller.snapshot['error']['code'], 'CHAT_UNSUPPORTED_VERSION')
                self.assertNotIn('account/read', self.transport.methods())
        for change in ('version', 'sandbox', 'network', 'missing_network', 'approval', 'provider', 'cwd'):
            with self.subTest(change=change):
                self.setUp()
                self.controller.connect('/opt/example/codex', '/var/example/freecad-chat/config', self.cwd)
                self.transport.result('initialize', {'userAgent': 'codex_cli_rs/0.167.0-alpha.3.2 (fixture)'})
                self.transport.result('account/read', {'account': {'type': 'chatgpt'}, 'requiresOpenaiAuth': True})
                self.controller.send('Neutral rejected request')
                self.pass_config()
                self.transport.result('mcpServerStatus/list', self.inventory())
                result = self.thread_result()
                result['thread']['cliVersion'] = '0.167.0-alpha.3.2'
                if change == 'version': result['thread']['cliVersion'] = '0.160.0'
                elif change == 'sandbox': result['sandbox']['type'] = 'workspaceWrite'
                elif change == 'network': result['sandbox']['networkAccess'] = True
                elif change == 'missing_network': del result['sandbox']['networkAccess']
                elif change == 'approval': result['approvalPolicy'] = 'on-request'
                elif change == 'provider': result['modelProvider'] = 'unverified'
                elif change == 'cwd': result['cwd'] = '/var/example/other-work'
                self.transport.result('thread/start', result)
                self.assertEqual(self.controller.snapshot['error']['code'], 'CHAT_POLICY_REJECTED')
                self.assertNotIn('turn/start', self.transport.methods())

    def test_initial_connect_publication_can_disconnect_without_spawning_a_child(self):
        disconnected = []
        def changed(snapshot):
            self.events.append(snapshot)
            if snapshot['phase'] == 'connecting' and not disconnected:
                disconnected.append(True)
                self.controller.disconnect()
        self.controller.changed = changed
        self.controller.connect('/opt/example/codex', '/var/example/freecad-chat/config', self.cwd)
        self.assertEqual(self.controller.snapshot['phase'], 'disconnected')
        self.assertFalse(self.controller.snapshot['can_send'])
        self.assertEqual(self.transport.starts, [])
        self.assertEqual(self.transport.messages, [])
        self.assertEqual(self.transport.stops, 1)
        self.assertIsNone(self.transport.timeouts[-1])
        # A later explicit connect still owns its normal initialization.
        self.controller.connect('/opt/example/codex', '/var/example/freecad-chat/config', self.cwd)
        self.assertEqual(len(self.transport.starts), 1)
        self.assertEqual(self.transport.methods(), ['initialize'])

    def test_initial_publication_reconnect_only_starts_the_replacement_generation(self):
        replaced = []
        def changed(snapshot):
            self.events.append(snapshot)
            if snapshot['phase'] == 'connecting' and not replaced:
                replaced.append(True)
                self.controller.disconnect()
                self.controller.connect('/opt/example/replacement-codex',
                    '/var/example/replacement-config', '/var/example/replacement-clean')
        self.controller.changed = changed
        self.controller.connect('/opt/example/codex', '/var/example/freecad-chat/config', self.cwd)
        self.assertEqual(len(self.transport.starts), 1)
        program, _, environment, cwd, *_ = self.transport.starts[0]
        self.assertEqual(program, '/opt/example/replacement-codex')
        self.assertEqual(environment['CODEX_HOME'], '/var/example/replacement-config')
        self.assertEqual(cwd, '/var/example/replacement-clean')
        self.assertEqual(self.controller.snapshot['phase'], 'connecting')
        self.assertEqual(self.transport.methods(), ['initialize'])
        self.transport.result('initialize', {'userAgent': 'codex_cli_rs/0.160.0 (fixture)'})
        self.transport.result('account/read', {'account': None, 'requiresOpenaiAuth': True})
        self.assertEqual(self.controller.snapshot['phase'], 'signed_out')
        self.assertIsNone(self.controller.snapshot['error'])

    def test_transport_failures_keep_only_known_sanitized_diagnostic_codes(self):
        known = ('CHAT_STDERR_TOO_LARGE', 'CHAT_PROTOCOL_INVALID', 'CHAT_PROCESS_BUSY',
                 'CHAT_NOT_CONNECTED', 'CHAT_WRITE_TOO_LARGE', 'CHAT_WRITE_FAILED',
                 'CHAT_TIMEOUT_CALLBACK_FAILED')
        for code in (*known, 'UNKNOWN_FAILURE', None, {'untrusted': 'code'}):
            with self.subTest(code=code):
                self.setUp()
                self.controller.connect('/opt/example/codex', '/var/example/freecad-chat/config', self.cwd)
                failure = type('TransportFailure', (RuntimeError,), {'code': code})(
                    'synthetic-private-process-details')
                self.transport.error(failure)
                snapshot = self.controller.snapshot
                self.assertEqual(snapshot['phase'], 'error')
                expected = code if isinstance(code, str) and code in known else 'CHAT_PROCESS_FAILED'
                self.assertEqual(snapshot['error']['code'], expected)
                self.assertNotIn('synthetic-private-process-details', json.dumps(snapshot))
                self.assertEqual(self.transport.stops, 1)
                self.assertEqual(len(self.transport.starts), 1)
                self.assertEqual(self.transport.methods(), ['initialize'])

    def test_lease_acquisition_errors_preserve_safe_codes_without_initializing_or_retrying(self):
        for suffix in ('BUSY', 'INVALID', 'UNSAFE', 'CHANGED', 'PROFILE_INVALID', 'UNSUPPORTED',
                       'IO_FAILED', 'UPDATE_UNVERIFIED', 'RELEASE_UNVERIFIED', 'STATE_INVALID'):
            with self.subTest(suffix=suffix):
                self.setUp()
                code = 'CHAT_PROCESS_LEASE_' + suffix
                error = type('LeaseFailure', (RuntimeError,), {'code': code})('synthetic-private-process-path')
                with patch.object(self.transport, 'start', side_effect=error):
                    self.controller.connect('/opt/example/codex', '/var/example/freecad-chat/config', self.cwd)
                snapshot = self.controller.snapshot
                self.assertEqual(snapshot['error']['code'], code)
                self.assertNotIn('synthetic-private-process-path', json.dumps(snapshot))
                self.assertNotIn('initialize', self.transport.methods())
                self.assertFalse(snapshot['can_send'])

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

    def test_network_selection_runs_only_for_explicit_valid_connect_and_stays_out_of_public_state(self):
        calls = []
        proxy = "http://neutral-user:neutral-secret@proxy.example.invalid:8080"
        def resolve():
            calls.append("resolved")
            return {"HTTPS_PROXY": proxy, "https_proxy": proxy,
                    "NO_PROXY": "localhost,127.0.0.1,::1", "no_proxy": "localhost,127.0.0.1,::1"}
        self.controller = chat.PftChatController(self.transport, self.events.append, lambda _url: True,
                                                 network_environment=resolve)
        self.assertEqual(calls, [])
        self.controller.connect("relative", "/var/example/home", self.cwd)
        self.assertEqual(calls, [])
        self.controller.connect("/opt/example/codex", "/var/example/home", self.cwd)
        self.assertEqual(calls, ["resolved"])
        _, arguments, environment, *_ = self.transport.starts[-1]
        self.assertEqual(environment["HTTPS_PROXY"], proxy)
        self.assertEqual(environment["https_proxy"], proxy)
        self.assertNotIn(proxy, repr(arguments))
        self.assertNotIn(proxy, json.dumps(self.events))
        self.assertEqual(self.transport.methods(), ["initialize"])
        self.controller.connect("/opt/example/codex", "/var/example/home", self.cwd)
        self.assertEqual(calls, ["resolved"])

    def test_default_network_resolver_is_lazy_and_uses_the_current_environment(self):
        seen = []
        def build(environment):
            seen.append({key: environment[key] for key in ("HTTPS_PROXY", "NO_PROXY")})
            return {"HTTPS_PROXY": environment["HTTPS_PROXY"], "NO_PROXY": "localhost,127.0.0.1,::1"}
        self.controller = chat.PftChatController(self.transport, self.events.append, lambda _url: True)
        module = types.SimpleNamespace(build_chat_proxy_environment=build)
        import sys
        with patch.dict(sys.modules, {"pft_chat_network": module}), patch.dict(os.environ,
                {"HTTPS_PROXY": "http://proxy.example.invalid:8080", "NO_PROXY": "example.invalid"}, clear=True):
            self.assertEqual(seen, [])
            self.controller.connect("/opt/example/codex", "/var/example/home", self.cwd)
        self.assertEqual(seen, [{"HTTPS_PROXY": "http://proxy.example.invalid:8080", "NO_PROXY": "example.invalid"}])
        self.assertEqual(self.transport.starts[-1][2]["HTTPS_PROXY"], "http://proxy.example.invalid:8080")

    def test_default_connection_enables_official_system_resolution_and_disables_auth_fallback(self):
        self.ready()
        arguments = self.transport.starts[-1][1]
        self.assertIn(("--enable", "respect_system_proxy"), list(zip(arguments, arguments[1:])))
        self.assertIn(("--disable", "system_proxy_fallback"), list(zip(arguments, arguments[1:])))
        self.start_turn()
        params = next(item['params'] for item in self.transport.messages if item.get('method') == 'thread/start')
        self.assertIs(params['config']['features']['respect_system_proxy'], True)
        self.assertIs(params['config']['features']['system_proxy_fallback'], False)

    def test_explicit_proxy_connection_and_thread_keep_the_official_environment_policy(self):
        for value in ('http://proxy.example.invalid:8080', ''):
            with self.subTest(value=value):
                self.controller.disconnect()
                self.controller._network_environment = lambda: {'HTTPS_PROXY': value}
                self.ready()
                arguments = self.transport.starts[-1][1]
                self.assertIn(("--disable", "respect_system_proxy"), list(zip(arguments, arguments[1:])))
                self.assertIn(("--disable", "system_proxy_fallback"), list(zip(arguments, arguments[1:])))
                self.assertEqual(self.transport.starts[-1][2]['HTTPS_PROXY'], value)
                self.start_turn()
                params = next(item['params'] for item in reversed(self.transport.messages) if item.get('method') == 'thread/start')
                self.assertIs(params['config']['features']['respect_system_proxy'], False)
                self.assertIs(params['config']['features']['system_proxy_fallback'], False)

    def test_network_mode_is_bound_to_the_owned_history_scope(self):
        self.assertNotEqual(chat.chat_scope_fingerprint(respect_system_proxy=True),
                            chat.chat_scope_fingerprint(respect_system_proxy=False))

    def test_changed_network_policy_refuses_owned_history_before_spawning(self):
        record = self.persistent_record()
        self.controller._network_environment = lambda: {'HTTPS_PROXY': 'http://proxy.example.invalid:8080'}
        self.connect_persistent(record)
        self.assertEqual(self.controller.snapshot['error']['code'], 'CHAT_HISTORY_CONFIGURATION_INVALID')
        self.assertEqual(self.transport.starts, [])
        self.assertEqual(self.transport.messages, [])

    def test_network_configuration_error_is_sanitized_and_does_not_start_or_retry(self):
        for code in ("CHAT_PROXY_READ_FAILED", "CHAT_PROXY_INVALID", "CHAT_PROXY_UNSUPPORTED", "untrusted"):
            with self.subTest(code=code):
                def fail():
                    error = RuntimeError("neutral-secret@proxy.example.invalid")
                    error.code = code
                    raise error
                self.controller._network_environment = fail
                self.controller.connect("/opt/example/codex", "/var/example/home", self.cwd)
                expected = code if code.startswith("CHAT_PROXY_") else "CHAT_PROXY_READ_FAILED"
                self.assertEqual(self.controller.snapshot["error"]["code"], expected)
                self.assertEqual(self.controller.snapshot["phase"], "disconnected")
                self.assertNotIn("neutral-secret", json.dumps(self.events))
                self.assertNotIn("example.invalid", json.dumps(self.events))
                self.assertEqual(self.transport.starts, [])
                self.assertEqual(self.transport.messages, [])
                self.assertEqual(self.transport.stops, 0)
        self.controller._network_environment = lambda: {"OPENAI_API_KEY": "neutral-secret"}
        self.controller.connect("/opt/example/codex", "/var/example/home", self.cwd)
        self.assertEqual(self.controller.snapshot["error"]["code"], "CHAT_PROXY_READ_FAILED")
        self.assertEqual(self.transport.starts, [])

    def test_connect_only_initializes_and_reads_dedicated_account_without_refresh(self):
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'must-not-inherit', 'NODE_OPTIONS': '--injected',
                                    'CODEX_AUTH_TOKEN': 'must-not-inherit'}):
            self.connect()
        self.assertEqual(self.transport.methods(), ['initialize', 'initialized', 'account/read'])
        self.assertEqual(self.transport.messages[-1]['params'], {'refreshToken': False})
        self.assertEqual(self.controller.snapshot['phase'], 'signed_out')
        self.assertFalse(self.controller.snapshot['can_send'])
        program, args, env, cwd, *_ = self.transport.starts[0]
        self.assertEqual(program, '/opt/example/codex')
        self.assertEqual(cwd, self.cwd)
        self.assertEqual(env['CODEX_HOME'], '/var/example/freecad-chat/config')
        for key in ('OPENAI_API_KEY', 'NODE_OPTIONS', 'CODEX_AUTH_TOKEN'):
            self.assertNotIn(key, env)
        self.assertIn('apply_patch_freeform', args)
        self.assertNotIn('apply_patch', args)
        self.assertEqual(self.urls, [])

    def test_optional_account_omission_needs_explicit_login_without_claiming_authentication(self):
        for requires_auth in (True, False):
            with self.subTest(requires_auth=requires_auth):
                self.setUp()
                self.controller.connect('/opt/example/codex', '/var/example/freecad-chat/config', self.cwd)
                self.transport.result('initialize', {'userAgent': 'codex_cli_rs/0.160.0 (fixture)'})
                self.transport.result('account/read', {'requiresOpenaiAuth': requires_auth})
                snapshot = self.controller.snapshot
                self.assertEqual(snapshot['phase'], 'signed_out')
                self.assertEqual(snapshot['account'], 'none')
                self.assertIsNone(snapshot['error'])
                self.assertFalse(snapshot['can_send'])
                self.assertEqual(self.urls, [])
                self.assertEqual(self.transport.methods(), ['initialize', 'initialized', 'account/read'])
                self.assertEqual(self.transport.stops, 0)
                self.controller.login()
                self.assertEqual(self.transport.methods()[-1], 'account/login/start')
                self.assertEqual(self.controller.snapshot['phase'], 'logging_in')

    def test_omitted_account_requires_the_schema_boolean_auth_requirement(self):
        invalid = ({}, {'requiresOpenaiAuth': 'true'}, {'requiresOpenaiAuth': None},
                   {'requiresOpenaiAuth': 0}, {'requiresOpenaiAuth': 1},
                   {'requiresOpenaiAuth': 0.0})
        for result in invalid:
            with self.subTest(result=result):
                self.setUp()
                self.controller.connect('/opt/example/codex', '/var/example/freecad-chat/config', self.cwd)
                self.transport.result('initialize', {'userAgent': 'codex_cli_rs/0.160.0 (fixture)'})
                self.transport.result('account/read', result)
                snapshot = self.controller.snapshot
                self.assertEqual(snapshot['phase'], 'error')
                self.assertEqual(snapshot['error']['code'], 'CHAT_PROTOCOL_INVALID')
                self.assertFalse(snapshot['can_send'])
                self.assertEqual(self.transport.stops, 1)
                self.assertNotIn('account/login/start', self.transport.methods())
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
        self.assertEqual(self.transport.stops, 0)
        self.assertEqual(self.controller.snapshot['phase'], 'logging_in')
        self.controller.disconnect()
        self.connect()
        self.controller.open_url = lambda _url: True
        self.controller.login()
        self.transport.result('account/login/start', {'type': 'chatgpt', 'loginId': 'another-private-login',
            'authUrl': 'https://auth.openai.com/authorize'})
        self.assertEqual(self.transport.timeouts[-1], 180000)
        self.transport.error(types.SimpleNamespace(code='CHAT_TIMEOUT'))
        self.assertFalse(self.controller.snapshot['can_send'])
        self.assertNotIn('thread/start', self.transport.methods())

    def test_login_link_stages_and_recovery_reuse_one_request_without_exposing_url(self):
        self.connect()
        self.controller.login()
        self.assertEqual(self.controller.snapshot['login_stage'], 'requesting_link')
        self.assertFalse(self.controller.snapshot['can_use_login_link'])
        self.assertEqual(self.urls, [])
        copied = []
        self.assertFalse(self.controller.reopen_login())
        self.assertFalse(self.controller.copy_login_link(copied.append))
        url = 'https://auth.openai.com/authorize?fixture=neutral-login'
        self.transport.result('account/login/start', {'type': 'chatgpt', 'loginId': 'neutral-login', 'authUrl': url})
        self.assertEqual(self.controller.snapshot['login_stage'], 'browser_opened')
        self.assertTrue(self.controller.snapshot['can_use_login_link'])
        before_timeout = list(self.transport.timeouts)
        self.assertTrue(self.controller.reopen_login())
        self.assertTrue(self.controller.copy_login_link(copied.append))
        self.assertEqual(copied, [url])
        self.assertEqual(self.urls, [url, url])
        self.assertEqual(self.transport.methods().count('account/login/start'), 1)
        self.assertEqual(self.transport.timeouts, before_timeout)
        self.assertNotIn(url, json.dumps(self.controller.snapshot))
        self.transport.notify('account/login/completed', {'loginId': 'neutral-login', 'success': True})
        self.assertEqual(self.controller.snapshot['phase'], 'connecting')
        self.assertIsNone(self.controller.snapshot['login_stage'])
        self.assertFalse(self.controller.snapshot['can_use_login_link'])
        self.assertFalse(self.controller.reopen_login())
        self.assertFalse(self.controller.copy_login_link(copied.append))
        self.assertEqual(copied, [url])

    def test_browser_rejection_or_exception_keeps_pending_link_and_fixed_recoverable_error(self):
        for outcome in ('false', 'exception'):
            with self.subTest(outcome=outcome):
                self.setUp()
                self.connect()
                calls = []
                def open_browser(url):
                    calls.append(url)
                    if outcome == 'exception': raise RuntimeError('Neutral browser diagnostic')
                    return False
                self.controller.open_url = open_browser
                self.controller.login()
                url = 'https://chatgpt.com/authorize?fixture=neutral-browser'
                self.transport.result('account/login/start', {'type': 'chatgpt', 'loginId': 'neutral-login', 'authUrl': url})
                snapshot = self.controller.snapshot
                self.assertEqual(snapshot['phase'], 'logging_in')
                self.assertEqual(snapshot['login_stage'], 'browser_open_failed')
                self.assertEqual(snapshot['error']['code'], 'CHAT_BROWSER_OPEN_FAILED')
                self.assertTrue(snapshot['can_use_login_link'])
                self.assertNotIn(url, json.dumps(snapshot))
                self.assertNotIn('Neutral browser diagnostic', json.dumps(snapshot))
                self.assertEqual(self.transport.stops, 0)
                self.controller.open_url = lambda value: calls.append(value) or True
                self.assertTrue(self.controller.reopen_login())
                self.assertEqual(calls, [url, url])
                self.assertEqual(self.controller.snapshot['login_stage'], 'browser_opened')
                self.assertIsNone(self.controller.snapshot['error'])
                self.assertEqual(self.transport.methods().count('account/login/start'), 1)

    def test_login_link_clears_on_cancel_disconnect_and_timeout_and_copy_failure_is_sanitized(self):
        for ending in ('cancel', 'disconnect', 'timeout'):
            with self.subTest(ending=ending):
                self.setUp()
                self.connect()
                self.controller.login()
                url = 'https://auth.openai.com/authorize?fixture=neutral-expiry'
                self.transport.result('account/login/start', {'type': 'chatgpt', 'loginId': 'neutral-login', 'authUrl': url})
                def copy_failure(_url): raise RuntimeError('Neutral clipboard diagnostic')
                self.assertFalse(self.controller.copy_login_link(copy_failure))
                self.assertEqual(self.controller.snapshot['error']['code'], 'CHAT_LOGIN_LINK_COPY_FAILED')
                self.assertTrue(self.controller.snapshot['can_use_login_link'])
                if ending == 'cancel':
                    self.transport.notify('account/login/completed', {'loginId': 'neutral-login', 'success': False})
                elif ending == 'disconnect': self.controller.disconnect()
                else: self.transport.error(types.SimpleNamespace(code='CHAT_TIMEOUT'))
                self.assertIsNone(self.controller.snapshot['login_stage'])
                self.assertFalse(self.controller.snapshot['can_use_login_link'])
                self.assertFalse(self.controller.reopen_login())
                copied = []
                self.assertFalse(self.controller.copy_login_link(copied.append))
                self.assertEqual(copied, [])
                self.assertNotIn(url, json.dumps(self.controller.snapshot))

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

    @patch.object(chat.time, 'monotonic', return_value=100.0)
    def test_terminal_turn_retires_unanswered_requests_and_ignores_late_errors(self, _clock):
        for method in ('turn/start', 'turn/interrupt'):
            with self.subTest(method=method):
                self.setUp()
                self.ready()
                self.controller.send('Neutral completion example')
                self.pass_config()
                self.transport.result('mcpServerStatus/list', self.inventory())
                self.transport.result('thread/start', self.thread_result())
                self.transport.result('mcpServerStatus/list', self.inventory(True))
                if method == 'turn/interrupt':
                    self.transport.result('turn/start', {'turn': {
                        'id': self.event()['turnId'], 'items': [], 'status': 'inProgress'}})
                    self.controller.interrupt()
                request = self.transport.messages[-1]
                self.assertEqual(request['method'], method)
                self.complete('interrupted' if method == 'turn/interrupt' else 'completed')
                before = self.controller.snapshot
                self.assertTrue(before['can_send'])
                self.assertIsNone(self.transport.timeouts[-1])
                self.assertEqual(self.controller._pending, {})
                late_responses = (
                    {'id': request['id'], 'error': {
                        'code': -32000, 'message': 'Neutral stale response'}},
                    {'id': request['id'], 'result': {}})
                for response in late_responses:
                    self.transport.data(response)
                    self.assertEqual(self.controller.snapshot, before)
                self.controller.send('Neutral follow-up')
                self.assertEqual(self.transport.messages[-1]['method'], 'config/read')
                next_turn = self.controller.snapshot
                for response in late_responses:
                    self.transport.data(response)
                    self.assertEqual(self.controller.snapshot, next_turn)
                self.assertEqual(self.transport.stops, 0)

    @patch.object(chat.time, 'monotonic', return_value=100.0)
    def test_old_turn_notifications_cannot_claim_a_new_pending_turn(self, _clock):
        for method in ('turn/started', 'turn/completed'):
            with self.subTest(method=method):
                self.setUp()
                self.ready()
                self.start_turn('Neutral first question')
                self.complete()
                self.controller.send('Neutral second question')
                self.pass_config()
                self.transport.result('mcpServerStatus/list', self.inventory())
                self.transport.result('mcpServerStatus/list', self.inventory(True))
                before = self.controller.snapshot
                pending, timeouts = dict(self.controller._pending), list(self.transport.timeouts)
                self.assertIsNone(self.controller._turn_id)
                self.transport.notify(method, {'threadId': self.event()['threadId'], 'turn': {
                    'id': self.event()['turnId'], 'items': [],
                    'status': 'inProgress' if method == 'turn/started' else 'completed'}})
                self.assertEqual(self.controller.snapshot, before)
                self.assertIsNone(self.controller._turn_id)
                self.assertEqual(self.controller._pending, pending)
                self.assertEqual(self.transport.timeouts, timeouts)
                self.transport.result('turn/start', {'turn': {
                    'id': 'neutral-next-turn', 'items': [], 'status': 'inProgress'}})
                self.transport.notify('item/agentMessage/delta', self.event(
                    turnId='neutral-next-turn', itemId='neutral-next-reply', delta='Neutral answer'))
                self.transport.notify('turn/completed', {'threadId': self.event()['threadId'],
                    'turn': {'id': 'neutral-next-turn', 'items': [], 'status': 'completed'}})
                self.assertTrue(self.controller.snapshot['can_send'])
                self.assertEqual(self.controller.snapshot['transcript'][-1]['text'], 'Neutral answer')
                self.assertEqual(self.transport.stops, 0)
                self.controller.disconnect()
                self.assertEqual(self.controller._completed_turns, set())
                self.ready()
                self.start_turn('Neutral question after reconnect')
                self.complete()
                self.assertTrue(self.controller.snapshot['can_send'])
                self.assertIsNone(self.controller.snapshot['error'])

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
            conflicting['features'][key] = not conflicting['features'][key]
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

    @patch.object(chat.time, 'monotonic', return_value=100.0)
    def test_invalid_retry_flags_fail_closed_and_old_turn_errors_are_ignored(self, _clock):
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
        self.assertEqual(len(parsed['enabled_tools']), 22)
        self.assertEqual(len(set(EXPECTED_CHAT_TOOLS)), 22)
        self.assertNotIn('execute_script', parsed['enabled_tools'])

    def test_drawing_export_tools_share_only_the_explicit_native_confirmation_scope(self):
        export_tools = {'prepare_drawing_export', 'request_drawing_export_approval', 'get_drawing_export_status'}
        self.assertEqual(chat._isolated_config(None)['mcp_servers'], {})
        self.assertNotIn('prepare_drawing_export', chat._developer_instructions(None))
        configured = chat._isolated_config(self.mcp_config())['mcp_servers']['phoenix_freecad']
        self.assertEqual(len(configured['enabled_tools']), 22)
        self.assertTrue(export_tools.issubset(configured['enabled_tools']))
        instructions = chat._developer_instructions(self.mcp_config())
        for text in ('one stable requestId', 'ORIGINAL token', 'native_publication_confirmation',
                     'not current filesystem checks', 'CAD Undo cannot roll back', 'Never provide a file path'):
            self.assertIn(text, instructions)
        for name in ('apply_part_changes', 'execute_script', 'recompute_document'):
            self.assertNotIn(name, configured['enabled_tools'])

    def test_legacy_nineteen_tool_effective_config_or_inventory_is_rejected_without_expansion(self):
        export_tools = {'prepare_drawing_export', 'request_drawing_export_approval', 'get_drawing_export_status'}
        for phase in ('effective_config', 'inventory'):
            with self.subTest(phase=phase):
                self.setUp(); self.ready(mcp=self.mcp_config())
                self.controller.send('Prepare a neutral PDF export for native review')
                if phase == 'effective_config':
                    config = self.effective_config()
                    config['mcp_servers']['phoenix_freecad']['enabled_tools'] = [name for name in EXPECTED_CHAT_TOOLS if name not in export_tools]
                    self.assertEqual(len(config['mcp_servers']['phoenix_freecad']['enabled_tools']), 19)
                    before = deepcopy(config)
                    self.transport.result('config/read', {'config': config, 'origins': {}})
                    self.assertEqual(config, before)
                    self.assertEqual(self.controller.snapshot['error']['code'], 'CHAT_MCP_CONFIG_REJECTED')
                    self.assertNotIn('mcpServerStatus/list', self.transport.methods())
                else:
                    self.pass_config(); inventory = self.inventory()
                    for name in export_tools: inventory['data'][0]['tools'].pop(name)
                    self.assertEqual(len(inventory['data'][0]['tools']), 19)
                    before = deepcopy(inventory)
                    self.transport.result('mcpServerStatus/list', inventory)
                    self.assertEqual(inventory, before)
                    self.assertEqual(self.controller.snapshot['error']['code'], 'CHAT_MCP_INVENTORY_REJECTED')
                self.assertNotIn('thread/start', self.transport.methods())
                self.assertNotIn('thread/resume', self.transport.methods())
                self.assertNotIn('turn/start', self.transport.methods())
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
                        self.assertIn('add_drawing_dimensions', instructions)
                        self.assertIn('Edge0-based', instructions)
                        self.assertIn('native_reference_and_value_checked', instructions)
                        self.assertIn('never supply measurement overrides', instructions)
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
        self.assertEqual(len(parsed['enabled_tools']), 27)
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


    def test_turn_timing_starts_at_accepted_send_and_includes_preflight_from_zero(self):
        with patch.object(chat.time, 'monotonic', return_value=0.0) as clock:
            self.ready()
            self.assertIsNone(self.controller.snapshot['turn_timing'])
            self.assertEqual(self.controller.snapshot['message_times'], [])
            self.controller.send('Synthetic timing question')
            self.assertEqual(self.controller.snapshot['turn_timing'], {
                'elapsed_seconds': 0.0, 'first_response_seconds': None,
                'running': True, 'finished': False})
            clock.return_value = 2.25
            self.assertEqual(self.controller.snapshot['transcript'], [])
            self.assertEqual(self.controller.snapshot['turn_timing']['elapsed_seconds'], 2.25)
            self.pass_config()
            self.transport.result('mcpServerStatus/list', self.inventory())
            self.transport.result('thread/start', self.thread_result())
            clock.return_value = 6.5
            self.transport.result('mcpServerStatus/list', self.inventory(True))
            self.transport.result('turn/start', {'turn': {
                'id': self.event()['turnId'], 'items': [], 'status': 'inProgress'}})
            self.assertEqual(self.controller.snapshot['turn_timing']['elapsed_seconds'], 6.5)
            clock.return_value = 8.125
            self.transport.notify('item/agentMessage/delta', self.event(itemId='clock-reply', delta='Answer'))
            self.assertEqual(self.controller.snapshot['turn_timing']['first_response_seconds'], 8.125)
            clock.return_value = 11.0
            self.complete()
            frozen = self.controller.snapshot['turn_timing']
            clock.return_value = 80.0
            self.assertEqual(self.controller.snapshot['turn_timing'], frozen)
            self.assertEqual(frozen, {'elapsed_seconds': 11.0, 'first_response_seconds': 8.125,
                                      'running': False, 'finished': True})

    def test_message_times_track_row_creation_and_survive_stream_updates_until_connect(self):
        with patch.object(chat.time, 'monotonic', return_value=10.0), \
                patch.object(chat.time, 'strftime', side_effect=['08:20', '08:21', '08:22']) as wall_clock:
            self.ready()
            self.start_turn('Neutral timestamp question')
            self.transport.notify('item/agentMessage/delta', self.event(itemId='timestamp-reply', delta=''))
            self.transport.notify('item/agentMessage/delta', self.event(itemId='timestamp-reply', delta='Draft'))
            final = {'id': 'timestamp-reply', 'type': 'agentMessage', 'text': 'Completed answer'}
            self.transport.notify('item/completed', self.event(item=final))
            self.transport.notify('item/agentMessage/delta', self.event(itemId='timestamp-reply', delta='Ignored'))
            second = {'id': 'timestamp-next', 'type': 'agentMessage', 'text': 'Another answer item'}
            self.complete(items=[final, second])
            snapshot = self.controller.snapshot
            self.assertEqual(snapshot['message_times'], ['08:20', '08:21', '08:22'])
            self.assertEqual(len(snapshot['message_times']), len(snapshot['transcript']))
            self.assertTrue(all(set(row) == {'role', 'text', 'status'} for row in snapshot['transcript']))
            self.assertEqual(wall_clock.call_count, 3)
            self.assertTrue(all(call.args == ('%H:%M',) for call in wall_clock.call_args_list))
            snapshot['message_times'].clear()
            snapshot['turn_timing']['elapsed_seconds'] = 900.0
            self.assertEqual(len(self.controller.snapshot['message_times']), 3)
            self.assertEqual(self.controller.snapshot['turn_timing']['elapsed_seconds'], 0.0)
            self.controller.disconnect()
            self.assertEqual(self.controller.snapshot['message_times'], ['08:20', '08:21', '08:22'])
            self.ready()
            self.assertEqual(self.controller.snapshot['message_times'], [])
            self.assertEqual(self.controller.snapshot['transcript'], [])
            self.assertIsNone(self.controller.snapshot['turn_timing'])

    def test_first_response_excludes_tools_recovery_reasoning_and_blank_text(self):
        with patch.object(chat.time, 'monotonic', return_value=100.0) as clock:
            self.ready(mcp=self.mcp_config())
            self.start_turn()
            count = len(self.transport.messages)
            clock.return_value = 105.0
            tool = {'id': 'timing-tool', 'type': 'mcpToolCall', 'server': 'phoenix_freecad',
                    'tool': 'get_capabilities', 'status': 'inProgress'}
            self.transport.notify('item/started', self.event(item=tool))
            self.transport.notify('error', self.event(willRetry=True, error={'codexErrorInfo': 'serverOverloaded'}))
            self.transport.notify('item/started', self.event(item={'id': 'timing-reasoning', 'type': 'reasoning'}))
            self.transport.notify('item/agentMessage/delta', self.event(itemId='timing-blank', delta=''))
            self.transport.notify('item/agentMessage/delta', self.event(itemId='timing-blank', delta=' \n\t'))
            self.assertIsNone(self.controller.snapshot['turn_timing']['first_response_seconds'])
            self.assertEqual(len(self.transport.messages), count)
            clock.return_value = 108.25
            answer = {'id': 'timing-blank', 'type': 'agentMessage', 'text': 'Visible answer'}
            self.transport.notify('item/completed', self.event(item=answer))
            clock.return_value = 110.0
            self.complete(items=[answer])
            self.assertEqual(self.controller.snapshot['turn_timing'], {
                'elapsed_seconds': 10.0, 'first_response_seconds': 8.25,
                'running': False, 'finished': True})
            self.assertEqual(self.transport.methods().count('turn/start'), 1)

    def test_all_terminal_turn_statuses_freeze_timing_without_late_changes(self):
        for status in ('completed', 'interrupted', 'failed'):
            with self.subTest(status=status), patch.object(chat.time, 'monotonic', return_value=100.0) as clock:
                self.setUp()
                self.ready()
                self.start_turn()
                clock.return_value = 105.0
                self.transport.notify('item/agentMessage/delta', self.event(itemId='terminal-clock', delta='Answer'))
                if status == 'interrupted':
                    self.controller.interrupt()
                    self.assertTrue(self.controller.snapshot['turn_timing']['running'])
                clock.return_value = 108.0
                self.complete(status)
                frozen = self.controller.snapshot['turn_timing']
                self.assertEqual(frozen, {'elapsed_seconds': 8.0, 'first_response_seconds': 5.0,
                                          'running': False, 'finished': True})
                clock.return_value = 140.0
                self.transport.notify('item/agentMessage/delta', self.event(itemId='terminal-late', delta='Ignored'))
                self.assertEqual(self.controller.snapshot['turn_timing'], frozen)
                self.assertEqual(len(self.controller.snapshot['message_times']), 2)

    def test_stop_and_preflight_failures_freeze_elapsed_without_a_transcript_row(self):
        for ending in ('disconnect', 'timeout', 'process_exit', 'preparation_cancel', 'config_failure'):
            with self.subTest(ending=ending), patch.object(chat.time, 'monotonic', return_value=0.0) as clock:
                self.setUp()
                self.ready()
                self.controller.send('Neutral preflight timing')
                clock.return_value = 2.5
                if ending == 'disconnect':
                    self.controller.disconnect()
                elif ending == 'timeout':
                    self.transport.error(types.SimpleNamespace(code='CHAT_TIMEOUT'))
                elif ending == 'process_exit':
                    self.transport.exit(1)
                elif ending == 'preparation_cancel':
                    self.controller.interrupt()
                else:
                    self.transport.result('config/read', {'config': {}})
                self.assertEqual(self.controller.snapshot['transcript'], [])
                self.assertEqual(self.controller.snapshot['message_times'], [])
                frozen = self.controller.snapshot['turn_timing']
                self.assertEqual(frozen, {'elapsed_seconds': 2.5, 'first_response_seconds': None,
                                          'running': False, 'finished': True})
                clock.return_value = 1000.0
                self.assertEqual(self.controller.snapshot['turn_timing'], frozen)

    def test_rejected_sends_preserve_timing_and_next_accepted_send_starts_a_new_clock(self):
        with patch.object(chat.time, 'monotonic', return_value=10.0) as clock:
            self.ready()
            self.controller.send(' ')
            self.assertIsNone(self.controller.snapshot['turn_timing'])
            self.start_turn()
            clock.return_value = 12.0
            self.controller.send('Overlapping neutral question')
            self.assertEqual(self.controller.snapshot['turn_timing']['elapsed_seconds'], 2.0)
            clock.return_value = 14.0
            self.complete()
            first = self.controller.snapshot['turn_timing']
            clock.return_value = 20.0
            self.controller.send('')
            self.assertEqual(self.controller.snapshot['turn_timing'], first)
            self.controller.send('Next neutral question')
            self.assertEqual(self.controller.snapshot['turn_timing'], {
                'elapsed_seconds': 0.0, 'first_response_seconds': None,
                'running': True, 'finished': False})
            self.assertEqual(len(self.controller.snapshot['message_times']), 1)
            self.assertEqual(len(self.controller.snapshot['transcript']), 1)
            clock.return_value = 23.0
            self.transport.result('config/read', {'config': {}})
            self.assertEqual(self.controller.snapshot['turn_timing']['elapsed_seconds'], 3.0)

    def test_budget_rejection_never_appends_timestamp_or_counts_a_failed_first_response(self):
        for failing_row in ('user', 'assistant'):
            with self.subTest(failing_row=failing_row), patch.object(chat.time, 'monotonic', return_value=10.0) as clock:
                self.setUp()
                self.ready()
                if failing_row == 'assistant':
                    self.start_turn('Q')
                    clock.return_value = 15.0
                    with patch.object(chat, '_PFT_OUTPUT_LIMIT', 4):
                        self.transport.notify('item/agentMessage/delta', self.event(itemId='too-large', delta='Large'))
                    expected = 1
                else:
                    self.controller.send('Large')
                    self.pass_config()
                    self.transport.result('mcpServerStatus/list', self.inventory())
                    self.transport.result('thread/start', self.thread_result())
                    clock.return_value = 15.0
                    with patch.object(chat, '_PFT_OUTPUT_LIMIT', 4):
                        self.transport.result('mcpServerStatus/list', self.inventory(True))
                    expected = 0
                snapshot = self.controller.snapshot
                self.assertEqual(snapshot['error']['code'], 'CHAT_OUTPUT_TOO_LARGE')
                self.assertEqual(len(snapshot['transcript']), expected)
                self.assertEqual(len(snapshot['message_times']), expected)
                self.assertEqual(snapshot['turn_timing'], {'elapsed_seconds': 5.0,
                    'first_response_seconds': None, 'running': False, 'finished': True})

    def test_final_only_zero_second_response_and_early_terminal_rpc_keep_frozen_timing(self):
        with patch.object(chat.time, 'monotonic', return_value=0.0) as clock:
            self.ready()
            self.controller.send('Neutral final-only question')
            self.pass_config()
            self.transport.result('mcpServerStatus/list', self.inventory())
            self.transport.result('thread/start', self.thread_result())
            self.transport.result('mcpServerStatus/list', self.inventory(True))
            answer = {'id': 'final-clock-answer', 'type': 'agentMessage', 'text': 'Final-only answer'}
            self.complete(items=[answer])
            expected = {'elapsed_seconds': 0.0, 'first_response_seconds': 0.0,
                        'running': False, 'finished': True}
            self.assertEqual(self.controller.snapshot['turn_timing'], expected)
            clock.return_value = 20.0
            self.transport.result('turn/start', {'turn': {
                'id': self.event()['turnId'], 'items': [], 'status': 'inProgress'}})
            self.assertEqual(self.controller.snapshot['turn_timing'], expected)
            self.assertEqual(len(self.controller.snapshot['message_times']), 2)


    def test_failed_terminal_payload_alone_classifies_typed_errors_and_stops_without_resend(self):
        cases = [
            ('unauthorized', 'CHAT_AUTH_FAILED'),
            ({'responseStreamConnectionFailed': {'httpStatusCode': 403}}, 'CHAT_AUTH_FAILED'),
            ('usageLimitExceeded', 'CHAT_LIMIT_REACHED'),
            ({'httpConnectionFailed': {'httpStatusCode': 429}}, 'CHAT_LIMIT_REACHED'),
            ('badRequest', 'CHAT_MODEL_REQUEST_FAILED'),
            ({'httpConnectionFailed': {'httpStatusCode': 422}}, 'CHAT_MODEL_REQUEST_FAILED'),
            ({'responseStreamDisconnected': {'httpStatusCode': None}}, 'CHAT_NETWORK_FAILED'),
            ({'httpConnectionFailed': {'httpStatusCode': 503}}, 'CHAT_NETWORK_FAILED'),
            ('other', 'CHAT_TURN_FAILED'),
            (None, 'CHAT_TURN_FAILED'),
        ]
        for info, expected in cases:
            with self.subTest(info=info):
                self.setUp()
                self.ready()
                self.start_turn('Neutral terminal failure')
                self.transport.notify('item/agentMessage/delta', self.event(itemId='partial', delta='Draft answer'))
                before_count = len(self.transport.messages)
                self.transport.notify('turn/completed', {'threadId': self.event()['threadId'], 'turn': {
                    'id': self.event()['turnId'], 'status': 'failed', 'items': [],
                    'error': {'codexErrorInfo': info, 'message': 'synthetic-private-provider-text',
                              'additionalDetails': 'https://example.invalid/synthetic-private-details'}}})
                snapshot = self.controller.snapshot
                self.assertEqual(snapshot['error']['code'], expected)
                self.assertEqual(snapshot['phase'], 'error')
                self.assertFalse(snapshot['can_send'])
                self.assertEqual(snapshot['transcript'][-1]['status'], 'error')
                self.assertEqual(self.transport.stops, 1)
                self.assertEqual(len(self.transport.messages), before_count)
                self.assertEqual(self.transport.methods().count('turn/start'), 1)
                self.assertEqual(self.controller._pending, {})
                self.assertIsNone(self.transport.timeouts[-1])
                for event in self.events + [snapshot]:
                    self.assertNotIn('synthetic-private-provider', json.dumps(event))
                    self.assertNotIn('synthetic-private-details', json.dumps(event))

    def test_terminal_failure_missing_empty_or_unknown_error_remains_generic_and_stopped(self):
        for error in (None, {}, 'synthetic-private-error', [],
                      {'message': 'synthetic-private-error'},
                      {'codexErrorInfo': {'unknown': {}}, 'message': 'synthetic-private-error'}):
            with self.subTest(error=error):
                self.setUp()
                self.ready()
                self.start_turn('Neutral fallback failure')
                self.transport.notify('turn/completed', {'threadId': self.event()['threadId'], 'turn': {
                    'id': self.event()['turnId'], 'status': 'failed', 'items': [], 'error': error}})
                snapshot = self.controller.snapshot
                self.assertEqual(snapshot['error']['code'], 'CHAT_TURN_FAILED')
                self.assertEqual(snapshot['phase'], 'error')
                self.assertFalse(snapshot['can_send'])
                self.assertEqual(self.transport.stops, 1)
                self.assertNotIn('synthetic-private-error', json.dumps(snapshot))

    def test_failed_turn_start_response_uses_same_terminal_classification(self):
        self.ready()
        self.controller.send('Neutral immediate failure')
        self.pass_config()
        self.transport.result('mcpServerStatus/list', self.inventory())
        self.transport.result('thread/start', self.thread_result())
        self.transport.result('mcpServerStatus/list', self.inventory(True))
        self.transport.result('turn/start', {'turn': {
            'id': self.event()['turnId'], 'status': 'failed', 'items': [],
            'error': {'codexErrorInfo': 'unauthorized', 'message': 'synthetic-private-auth'}}})
        self.assertEqual(self.controller.snapshot['phase'], 'error')
        self.assertEqual(self.controller.snapshot['error']['code'], 'CHAT_AUTH_FAILED')
        self.assertEqual(self.transport.stops, 1)
        self.assertEqual(self.transport.methods().count('turn/start'), 1)
        self.assertNotIn('synthetic-private-auth', json.dumps(self.controller.snapshot))

    def test_interrupt_ack_keeps_streaming_until_terminal_then_marks_completed_item_incomplete(self):
        self.ready()
        self.start_turn('Neutral interrupted answer')
        self.transport.notify('item/agentMessage/delta', self.event(itemId='reply', delta='Partial answer'))
        self.controller.interrupt()
        self.transport.result('turn/interrupt', {})
        self.assertEqual(self.controller.snapshot['phase'], 'interrupting')
        self.assertEqual(self.controller.snapshot['transcript'][-1]['status'], 'streaming')
        final_item = {'id': 'reply', 'type': 'agentMessage', 'text': 'Partial answer'}
        self.complete('interrupted', items=[final_item])
        ended = self.controller.snapshot
        self.assertEqual(ended['phase'], 'ready')
        self.assertEqual(ended['transcript'][-1]['status'], 'interrupted')
        self.assertTrue(ended['can_send'])
        self.assertEqual(self.transport.stops, 0)
        self.transport.notify('item/completed', self.event(item=final_item))
        self.complete()
        self.assertEqual(self.controller.snapshot, ended)

    def test_failed_terminal_marks_last_completed_agent_item_without_rewriting_earlier_items(self):
        self.ready()
        self.start_turn('Neutral failed answer')
        commentary = {'id': 'commentary', 'type': 'agentMessage', 'phase': 'commentary', 'text': 'Checking.'}
        answer = {'id': 'answer', 'type': 'agentMessage', 'phase': 'final_answer', 'text': 'Partial final answer'}
        self.transport.notify('item/completed', self.event(item=commentary))
        self.transport.notify('item/completed', self.event(item=answer))
        self.complete('failed', items=[commentary, answer])
        rows = self.controller.snapshot['transcript']
        self.assertEqual([row['status'] for row in rows], ['complete', 'complete', 'error'])
        self.assertEqual(rows[-1]['text'], 'Partial final answer')
        self.assertEqual(self.controller.snapshot['phase'], 'error')

    def test_interrupted_message_marker_survives_next_turn_and_old_terminal_is_ignored(self):
        self.ready()
        self.start_turn('Neutral first turn')
        self.transport.notify('item/agentMessage/delta', self.event(itemId='old-answer', delta='Old partial answer'))
        self.complete('interrupted')
        first = self.controller.snapshot['transcript'][-1]
        self.controller.send('Neutral follow-up turn')
        self.pass_config()
        self.transport.result('mcpServerStatus/list', self.inventory())
        self.transport.result('mcpServerStatus/list', self.inventory(True))
        self.transport.result('turn/start', {'turn': {'id': 'neutral-next-turn', 'items': [], 'status': 'inProgress'}})
        self.transport.notify('item/agentMessage/delta', self.event(
            turnId='neutral-next-turn', itemId='new-answer', delta='New complete answer'))
        before = self.controller.snapshot
        self.transport.notify('turn/completed', {'threadId': self.event()['threadId'], 'turn': {
            'id': self.event()['turnId'], 'status': 'failed', 'items': [],
            'error': {'codexErrorInfo': 'unauthorized', 'message': 'synthetic-private-old-error'}}})
        self.assertEqual(self.controller.snapshot, before)
        self.transport.notify('turn/completed', {'threadId': self.event()['threadId'], 'turn': {
            'id': 'neutral-next-turn', 'status': 'completed', 'items': []}})
        rows = self.controller.snapshot['transcript']
        self.assertEqual(rows[1], first)
        self.assertEqual(rows[1]['status'], 'interrupted')
        self.assertEqual(rows[-1]['status'], 'complete')
        self.assertTrue(self.controller.snapshot['can_send'])
        self.assertEqual(self.transport.stops, 0)

    def test_disconnect_after_success_or_next_preflight_failure_never_marks_previous_success_incomplete(self):
        for ending in ('disconnect', 'preflight_failure'):
            with self.subTest(ending=ending):
                self.setUp()
                self.ready()
                self.start_turn('Neutral successful turn')
                self.transport.notify('item/agentMessage/delta', self.event(itemId='finished', delta='Completed answer'))
                self.complete()
                before = self.controller.snapshot['transcript']
                if ending == 'disconnect':
                    self.controller.disconnect()
                else:
                    self.controller.send('Neutral next preflight')
                    self.transport.result('config/read', {'config': {}})
                self.assertEqual(self.controller.snapshot['transcript'], before)
                self.assertEqual(before[-1]['status'], 'complete')

    def test_disconnect_during_completed_item_before_turn_terminal_marks_only_current_output(self):
        self.ready()
        self.start_turn('Neutral disconnect')
        self.transport.notify('item/completed', self.event(item={
            'id': 'not-terminal', 'type': 'agentMessage', 'text': 'Text before terminal'}))
        self.controller.disconnect()
        self.assertEqual(self.controller.snapshot['transcript'][-1]['status'], 'interrupted')
        self.assertEqual(self.controller.snapshot['phase'], 'disconnected')


    def persistent_record(self, mcp=None, **changes):
        row = {'thread_id': 'neutral-persistent-thread', 'created_at': 1740000000,
               'model': 'fixture-model', 'scope_fingerprint': chat.chat_scope_fingerprint(mcp),
               'history_mode': 'paginated', 'cli_version': '0.160.0', 'provider': 'openai'}
        row.update(changes)
        return row

    def persistent_store(self, records=()):
        return PftSessionStore('/var/example/freecad-chat/config', self.cwd, records)

    def persistent_metadata(self, runtime='idle', **changes):
        row = {'id': 'neutral-persistent-thread', 'createdAt': 1740000000,
               'cliVersion': '0.160.0', 'model': 'fixture-model', 'modelProvider': 'openai',
               'ephemeral': False, 'historyMode': 'paginated', 'cwd': self.cwd,
               'status': {'type': runtime}, 'turns': []}
        row.update(changes)
        return row

    def persistent_result(self, runtime='idle', **changes):
        value = self.thread_result()
        value['thread'] = self.persistent_metadata(runtime)
        value['instructionSources'] = []
        value.update(changes)
        return value

    def connect_persistent(self, record=None, mcp=None, retain=True, model_id=None, through_models=True):
        if record is None:
            record = self.persistent_record(mcp)
        self.store = self.persistent_store([record])
        self.configured_mcp = mcp
        self.controller.connect('/opt/example/codex', self.store.home, self.cwd, mcp=mcp,
                                model_id=model_id, retain_history=retain,
                                resume_session=record, session_store=self.store)
        if not self.transport.starts:
            return
        self.transport.result('initialize', {'userAgent': 'codex_cli_rs/0.160.0 (fixture)'})
        self.transport.result('account/read', {'account': {'type': 'chatgpt'}})
        if through_models:
            self.transport.result('model/list', {'data': [self.model_row()]})

    def install_history_projection(self):
        module_spec = importlib.util.spec_from_file_location('pft_chat_history', ADDON / 'pft_chat_history.py')
        module = importlib.util.module_from_spec(module_spec)
        module_spec.loader.exec_module(module)
        handle = patch.dict(sys.modules, {'pft_chat_history': module})
        handle.start()
        self.addCleanup(handle.stop)

    def resume_until_history(self, mcp=None, runtime='idle'):
        self.install_history_projection()
        self.connect_persistent(mcp=mcp)
        self.pass_config()
        self.transport.result('mcpServerStatus/list', self.inventory())
        self.transport.result('thread/read', {'thread': self.persistent_metadata('notLoaded')})
        self._resume_result_runtime = runtime
        self.assertEqual(self.transport.messages[-1]['method'], 'thread/turns/list')
        self.assertNotIn('thread/resume', self.transport.methods())

    def history_turn(self, identifier='old-turn', status='completed'):
        return {'id': identifier, 'status': status, 'items': [], 'itemsView': 'notLoaded'}

    def history_entry(self, identifier='old-answer', text='Neutral stored answer', turn_id='old-turn', role='assistant', **changes):
        item = {'id': identifier, 'type': 'agentMessage', 'text': text}
        if role == 'user':
            item = {'id': identifier, 'type': 'userMessage', 'content': [{'type': 'text', 'text': text}]}
        result = {'turnId': turn_id, 'item': item}
        result.update(changes)
        return result

    def finish_one_history_turn(self, status='completed', runtime='idle', entries=None):
        self.transport.result('thread/turns/list', {'data': [self.history_turn(status=status)]})
        self.assertEqual(self.transport.messages[-1]['method'], 'thread/items/list')
        self.transport.result('thread/items/list', {'data': entries if entries is not None else [self.history_entry()]})
        self.assertEqual(self.transport.messages[-1]['method'], 'thread/read')
        self.assertFalse(self.controller.snapshot['can_send'])
        self.transport.result('thread/read', {'thread': self.persistent_metadata()})
        self.finish_history_resume(runtime)

    def finish_history_resume(self, runtime='idle'):
        if self.transport.messages[-1]['method'] != 'thread/resume':
            return
        self.assertFalse(self.controller.snapshot['can_send'])
        self.transport.result('thread/resume', self.persistent_result(getattr(self, '_resume_result_runtime', 'idle')))
        self.transport.result('mcpServerStatus/list', self.inventory(True))
        self.assertEqual(self.transport.messages[-1]['method'], 'thread/read')
        self.transport.result('thread/read', {'thread': self.persistent_metadata(runtime)})

    def test_scope_fingerprint_is_deterministic_and_binds_exact_tool_configuration_without_plaintext(self):
        plain = chat.chat_scope_fingerprint()
        mcp = self.mcp_config()
        scoped = chat.chat_scope_fingerprint(mcp)
        self.assertRegex(plain, r'^[0-9a-f]{64}$')
        self.assertNotEqual(plain, scoped)
        self.assertEqual(scoped, chat.chat_scope_fingerprint(dict(reversed(list(mcp.items())))))
        self.assertNotIn(mcp['cli_entry'], scoped)
        changed = dict(mcp, bridge_url='http://127.0.0.1:9877')
        self.assertNotEqual(scoped, chat.chat_scope_fingerprint(changed))
        with patch.object(chat, '_PFT_CHAT_TOOLS', chat._PFT_CHAT_TOOLS + ('synthetic-extra-tool',)):
            self.assertNotEqual(scoped, chat.chat_scope_fingerprint(mcp))

    def test_persisted_nineteen_tool_scope_is_not_upgraded_or_resumed(self):
        mcp = self.mcp_config()
        export_tools = {'prepare_drawing_export', 'request_drawing_export_approval', 'get_drawing_export_status'}
        legacy = tuple(name for name in chat._PFT_CHAT_TOOLS if name not in export_tools)
        self.assertEqual(len(legacy), 19)
        with patch.object(chat, '_PFT_CHAT_TOOLS', legacy):
            old_fingerprint = chat.chat_scope_fingerprint(mcp)
        self.assertNotEqual(old_fingerprint, chat.chat_scope_fingerprint(mcp))
        record = self.persistent_record(mcp, scope_fingerprint=old_fingerprint)
        store = self.persistent_store([record]); before = deepcopy(store.records)
        self.controller.connect('/opt/example/codex', store.home, self.cwd, mcp=mcp,
            retain_history=True, resume_session=record, session_store=store)
        self.assertEqual(self.controller.snapshot['error']['code'], 'CHAT_HISTORY_CONFIGURATION_INVALID')
        self.assertEqual(self.transport.starts, [])
        self.assertEqual(self.transport.messages, [])
        self.assertEqual(store.registrations, [])
        self.assertEqual(store.records, before)

    def test_default_temporary_connection_never_touches_an_injected_store(self):
        class UnreadableStore:
            def __getattribute__(self, _name): raise AssertionError('must not access store')
        self.controller = chat.PftChatController(self.transport, self.events.append, lambda _url: True,
            network_environment=lambda: {}, session_store=UnreadableStore())
        self.ready()
        self.start_turn('Neutral temporary request')
        request = next(row for row in self.transport.messages if row.get('method') == 'thread/start')
        self.assertIs(request['params']['ephemeral'], True)
        self.assertEqual(self.controller.snapshot['history_status'], 'inactive')
        self.assertIsNone(self.controller.snapshot['registered_session'])
        self.assertNotIn('thread/read', self.transport.methods())
        self.assertNotIn('thread/resume', self.transport.methods())

    def test_new_persistent_thread_registers_only_after_thread_inventory_and_before_first_turn(self):
        self.store = self.persistent_store()
        self.controller.connect('/opt/example/codex', self.store.home, self.cwd, retain_history=True, session_store=self.store)
        self.transport.result('initialize', {'userAgent': 'codex_cli_rs/0.160.0 (fixture)'})
        self.transport.result('account/read', {'account': {'type': 'chatgpt'}})
        self.transport.result('model/list', {'data': [self.model_row()]})
        self.controller.send('Neutral retained request')
        self.pass_config()
        self.transport.result('mcpServerStatus/list', self.inventory())
        params = self.transport.messages[-1]['params']
        self.assertIs(params['ephemeral'], False)
        self.assertNotIn('historyMode', params)
        self.assertNotIn('persistExtendedHistory', params)
        self.transport.result('thread/start', self.persistent_result())
        self.assertEqual(self.store.registrations, [])
        self.assertNotIn('turn/start', self.transport.methods())
        self.transport.result('mcpServerStatus/list', self.inventory(True))
        self.assertEqual(len(self.store.registrations), 1)
        self.assertEqual(self.transport.messages[-1]['method'], 'turn/start')
        self.assertEqual(self.controller.snapshot['registered_session'], self.persistent_record())
        self.assertEqual(self.transport.messages[-1]['params']['input'], [{'type': 'text', 'text': 'Neutral retained request'}])

    def test_current_cli_registration_uses_runtime_version_and_scope(self):
        version = '0.167.0-alpha.3.2'
        self.store = self.persistent_store()
        self.controller.connect('/opt/example/codex', self.store.home, self.cwd, retain_history=True, session_store=self.store)
        self.transport.result('initialize', {'userAgent': 'codex_cli_rs/' + version + ' (fixture)'})
        self.transport.result('account/read', {'account': {'type': 'chatgpt'}, 'requiresOpenaiAuth': True})
        self.controller.send('Neutral retained compatible request')
        self.pass_config()
        self.transport.result('mcpServerStatus/list', self.inventory())
        result = self.persistent_result()
        result['thread']['cliVersion'] = version
        self.transport.result('thread/start', result)
        self.assertEqual(self.store.registrations, [])
        self.transport.result('mcpServerStatus/list', self.inventory(True))
        registered = self.controller.snapshot['registered_session']
        self.assertEqual(registered['cli_version'], version)
        self.assertEqual(registered['scope_fingerprint'], chat.chat_scope_fingerprint(cli_version=version))
        self.assertNotEqual(registered['scope_fingerprint'], chat.chat_scope_fingerprint())
        self.assertEqual(self.store.records[registered['thread_id']], registered)
        self.assertEqual(self.transport.messages[-1]['method'], 'turn/start')

    def test_cross_version_restore_stops_before_account_or_history_read_without_changing_registration(self):
        for original, runtime in (('0.160.0', '0.167.0-alpha.3.2'), ('0.167.0-alpha.3.2', '0.160.0')):
            with self.subTest(original=original, runtime=runtime):
                self.setUp()
                record = self.persistent_record(cli_version=original,
                    scope_fingerprint=chat.chat_scope_fingerprint(cli_version=original))
                store = self.persistent_store([record])
                self.controller.connect('/opt/example/codex', store.home, self.cwd, retain_history=True,
                    resume_session=record, session_store=store)
                self.assertEqual(len(self.transport.starts), 1)
                self.transport.result('initialize', {'userAgent': 'codex_cli_rs/' + runtime + ' (fixture)'})
                self.assertEqual(self.controller.snapshot['error']['code'], 'CHAT_HISTORY_VERSION_MISMATCH')
                self.assertEqual(self.controller.snapshot['phase'], 'error')
                self.assertEqual(self.transport.methods(), ['initialize'])
                self.assertEqual(store.records, {record['thread_id']: record})
                self.assertEqual(store.registrations, [])

    def test_current_cli_restore_uses_matching_registered_version_before_history_read(self):
        version = '0.167.0-alpha.3.2'
        record = self.persistent_record(cli_version=version,
            scope_fingerprint=chat.chat_scope_fingerprint(cli_version=version))
        store = self.persistent_store([record])
        self.controller.connect('/opt/example/codex', store.home, self.cwd, retain_history=True,
            resume_session=record, session_store=store)
        self.transport.result('initialize', {'userAgent': 'codex_cli_rs/' + version + ' (fixture)'})
        self.transport.result('account/read', {'account': {'type': 'chatgpt'}, 'requiresOpenaiAuth': True})
        self.transport.result('model/list', {'data': [self.model_row()]})
        self.pass_config()
        self.transport.result('mcpServerStatus/list', self.inventory())
        self.assertEqual(self.transport.messages[-1]['method'], 'thread/read')
        self.assertEqual(self.transport.messages[-1]['params'], {'threadId': record['thread_id'], 'includeTurns': False})
        self.assertIsNone(self.controller.snapshot['error'])
        self.assertNotIn('turn/start', self.transport.methods())

    def test_persistent_registration_failure_or_bad_metadata_never_sends_first_user_text(self):
        for failure in ('store', 'legacy', 'timestamp', 'provider', 'reviewer'):
            with self.subTest(failure=failure):
                self.setUp()
                store = self.persistent_store()
                if failure == 'store': store.register_error = RuntimeError('synthetic-private-register-error')
                self.controller.connect('/opt/example/codex', store.home, self.cwd, retain_history=True, session_store=store)
                self.transport.result('initialize', {'userAgent': 'codex_cli_rs/0.160.0 (fixture)'})
                self.transport.result('account/read', {'account': {'type': 'chatgpt'}})
                self.transport.result('model/list', {'data': [self.model_row()]})
                self.controller.send('Neutral not-sent retained request')
                self.pass_config()
                self.transport.result('mcpServerStatus/list', self.inventory())
                result = self.persistent_result()
                if failure == 'legacy': result['thread']['historyMode'] = 'legacy'
                elif failure == 'timestamp': result['thread']['createdAt'] = True
                elif failure == 'provider': result['thread']['modelProvider'] = 'unknown-provider'
                elif failure == 'reviewer': result['approvalsReviewer'] = 'unknown-reviewer'
                self.transport.result('thread/start', result)
                if failure == 'store': self.transport.result('mcpServerStatus/list', self.inventory(True))
                self.assertEqual(self.controller.snapshot['phase'], 'error')
                self.assertNotIn('turn/start', self.transport.methods())
                self.assertEqual(self.controller.snapshot['transcript'], [])
                self.assertIsNone(self.controller.snapshot['registered_session'])
                self.assertNotIn('synthetic-private', json.dumps(self.controller.snapshot))
                self.assertEqual(len(store.registrations), 1 if failure == 'store' else 0)

    def test_resume_rejects_unregistered_forged_scope_model_or_cross_profile_before_starting(self):
        for failure in ('missing', 'forged', 'scope', 'model', 'profile', 'retain_off'):
            with self.subTest(failure=failure):
                self.setUp()
                record = self.persistent_record()
                store = self.persistent_store([record])
                candidate, model_id, retain = dict(record), None, True
                if failure == 'missing': store.records.clear()
                elif failure == 'forged': candidate['created_at'] += 1
                elif failure == 'scope': store.records[record['thread_id']]['scope_fingerprint'] = 'a' * 64; candidate['scope_fingerprint'] = 'a' * 64
                elif failure == 'model': model_id = 'other-model'
                elif failure == 'profile': store.cwd = '/var/example/other-work'
                elif failure == 'retain_off': retain = False
                self.controller.connect('/opt/example/codex', '/var/example/freecad-chat/config', self.cwd,
                    model_id=model_id, retain_history=retain, resume_session=candidate, session_store=store)
                self.assertEqual(self.transport.starts, [])
                self.assertEqual(self.transport.messages, [])
                self.assertEqual(self.controller.snapshot['error']['code'], 'CHAT_HISTORY_CONFIGURATION_INVALID')

    def test_resume_waits_for_authentication_and_verified_original_model_then_starts_without_send(self):
        self.connect_persistent(through_models=False)
        self.assertEqual(self.controller.snapshot['phase'], 'restoring')
        self.assertFalse(self.controller.snapshot['can_send'])
        self.assertNotIn('config/read', self.transport.methods())
        self.transport.result('model/list', {'data': [self.model_row()]})
        self.assertEqual(self.transport.messages[-1]['method'], 'config/read')
        self.assertEqual(self.controller.snapshot['selected_model'], 'fixture-model')
        self.assertNotIn('turn/start', self.transport.methods())
        self.assertNotIn('thread/start', self.transport.methods())
        self.assertEqual(self.controller.snapshot['transcript'], [])

    def test_resume_catalog_failure_missing_model_or_changed_registry_stops_before_history(self):
        for failure in ('rpc', 'missing', 'changed', 'timeout'):
            with self.subTest(failure=failure):
                self.setUp()
                self.connect_persistent(through_models=False)
                if failure == 'changed': self.store.records['neutral-persistent-thread']['created_at'] += 1
                if failure == 'rpc':
                    request = self.transport.messages[-1]
                    self.transport.data({'id': request['id'], 'error': {'message': 'synthetic-private-catalog'}})
                elif failure == 'timeout': self.transport.expire_timeout()
                else:
                    self.transport.result('model/list', {'data': [] if failure == 'missing' else [self.model_row()]})
                self.assertEqual(self.controller.snapshot['phase'], 'error')
                self.assertFalse(self.controller.snapshot['can_send'])
                self.assertNotIn('thread/read', self.transport.methods())
                self.assertNotIn('turn/start', self.transport.methods())
                self.assertNotIn('synthetic-private', json.dumps(self.controller.snapshot))

    def test_resume_uses_shared_explicit_policy_params_then_requires_final_idle_read_before_send(self):
        self.resume_until_history()
        self.finish_one_history_turn(entries=[self.history_entry('old-user', 'Neutral stored request', role='user', startedAtMs=1740000000000),
                                             self.history_entry(startedAtMs=1740000001000)])
        request = next(row for row in self.transport.messages if row.get('method') == 'thread/resume')
        params = request['params']
        self.assertEqual(params['threadId'], 'neutral-persistent-thread')
        self.assertIs(params['excludeTurns'], True)
        self.assertEqual(params['model'], 'fixture-model')
        self.assertEqual(params['modelProvider'], 'openai')
        self.assertEqual(params['cwd'], self.cwd)
        self.assertEqual(params['sandbox'], 'read-only')
        self.assertEqual(params['approvalPolicy'], 'never')
        self.assertEqual(params['approvalsReviewer'], 'user')
        self.assertEqual(params['config']['mcp_servers'], {})
        self.assertTrue(all(value is False for key, value in params['config']['features'].items() if key != 'respect_system_proxy'))
        self.assertIs(params['config']['features']['respect_system_proxy'], True)
        self.assertNotIn('ephemeral', params)
        self.assertNotIn('persistExtendedHistory', params)
        self.assertNotIn('turn/start', self.transport.methods())
        snapshot = self.controller.snapshot
        self.assertEqual(snapshot['phase'], 'ready')
        self.assertTrue(snapshot['can_send'])
        self.assertEqual(snapshot['history_status'], 'ready')
        self.assertEqual([row['text'] for row in snapshot['transcript']], ['Neutral stored request', 'Neutral stored answer'])
        self.assertTrue(all(row['history'] for row in snapshot['transcript']))
        self.assertTrue(all(value.endswith(' UTC') for value in snapshot['message_times']))
        self.assertIsNone(snapshot['turn_timing'])
        self.assertNotIn('turn/start', self.transport.methods())
        self.assertEqual(self.transport.methods().count('thread/read'), 3)
        self.assertEqual(self.store.registrations, [])

    def test_old_failed_turn_is_history_only_and_user_followup_sends_exactly_once_to_same_thread(self):
        self.resume_until_history()
        self.finish_one_history_turn('failed')
        snapshot = self.controller.snapshot
        self.assertEqual(snapshot['transcript'][-1]['status'], 'error')
        self.assertIsNone(snapshot['error'])
        self.assertEqual(snapshot['phase'], 'ready')
        self.assertEqual(self.transport.stops, 0)
        self.controller.send('Neutral explicit follow-up')
        self.pass_config()
        self.transport.result('mcpServerStatus/list', self.inventory())
        self.transport.result('mcpServerStatus/list', self.inventory(True))
        self.assertEqual(self.transport.messages[-1]['method'], 'turn/start')
        self.assertEqual(self.transport.messages[-1]['params']['threadId'], 'neutral-persistent-thread')
        self.assertEqual(self.transport.messages[-1]['params']['input'], [{'type': 'text', 'text': 'Neutral explicit follow-up'}])
        self.assertEqual(self.transport.methods().count('turn/start'), 1)
        self.assertNotIn('thread/start', self.transport.methods())
        self.assertEqual(self.controller.snapshot['transcript'][0]['status'], 'error')

    def test_metadata_mismatch_rejects_resume_before_any_history_request(self):
        changes = ({'id': 'unregistered-thread'}, {'createdAt': 1}, {'createdAt': True},
                   {'historyMode': 'legacy'}, {'modelProvider': 'other-provider'}, {'ephemeral': True},
                   {'cwd': '/var/example/other-work'}, {'model': 'other-model'}, {'status': None})
        for change in changes:
            with self.subTest(change=change):
                self.setUp()
                self.connect_persistent()
                self.pass_config()
                self.transport.result('mcpServerStatus/list', self.inventory())
                self.transport.result('thread/read', {'thread': self.persistent_metadata(**change)})
                self.assertEqual(self.controller.snapshot['error']['code'], 'CHAT_HISTORY_METADATA_INVALID')
                self.assertNotIn('thread/resume', self.transport.methods())
                self.assertNotIn('thread/turns/list', self.transport.methods())
                self.assertNotIn('turn/start', self.transport.methods())

    def test_resume_response_model_policy_or_metadata_mismatch_never_loads_history(self):
        changes = ({'model': 'other-model'}, {'modelProvider': 'other-provider'},
                   {'approvalPolicy': 'on-request'}, {'approvalsReviewer': 'other-reviewer'},
                   {'cwd': '/var/example/other-work'}, {'sandbox': {'type': 'workspaceWrite', 'networkAccess': False}},
                   {'sandbox': {'type': 'readOnly', 'networkAccess': True}}, {'instructionSources': ['/fixture/unapproved-instructions']})
        for change in changes:
            with self.subTest(change=change):
                self.setUp()
                self.resume_until_history()
                self.transport.result('thread/turns/list', {'data': []})
                self.transport.result('thread/read', {'thread': self.persistent_metadata('notLoaded')})
                self.transport.result('thread/resume', self.persistent_result(**change))
                self.assertEqual(self.controller.snapshot['error']['code'], 'CHAT_HISTORY_POLICY_REJECTED')
                self.assertEqual(self.transport.methods().count('thread/turns/list'), 1)
                self.assertNotIn('turn/start', self.transport.methods())

    def test_resume_thread_mcp_inventory_is_checked_after_read_only_hydration_before_send(self):
        self.resume_until_history(mcp=self.mcp_config())
        self.transport.result('thread/turns/list', {'data': []})
        self.transport.result('thread/read', {'thread': self.persistent_metadata('notLoaded')})
        self.transport.result('thread/resume', self.persistent_result())
        inventory = self.inventory(True)
        del inventory['data'][0]['tools']['set_appearance']
        self.transport.result('mcpServerStatus/list', inventory)
        self.assertEqual(self.controller.snapshot['error']['code'], 'CHAT_MCP_INVENTORY_REJECTED')
        self.assertEqual(self.transport.methods().count('thread/turns/list'), 1)
        self.assertNotIn('turn/start', self.transport.methods())

    def test_active_unknown_or_unfinished_recovered_turn_never_enables_send(self):
        for source in ('resume_active', 'final_active', 'final_unknown', 'in_progress', 'unknown_turn'):
            with self.subTest(source=source):
                self.setUp()
                self.resume_until_history(runtime='active' if source == 'resume_active' else 'idle')
                self.finish_one_history_turn('inProgress' if source == 'in_progress' else
                    'future-status' if source == 'unknown_turn' else 'completed',
                    'active' if source == 'final_active' else 'notLoaded' if source == 'final_unknown' else 'idle')
                self.assertEqual(self.controller.snapshot['history_status'], 'read_only')
                self.assertFalse(self.controller.snapshot['can_send'])
                self.assertEqual(self.controller.snapshot['phase'], 'history_read_only')
                count = len(self.transport.messages)
                self.controller.send('Must not send while read-only')
                self.assertEqual(len(self.transport.messages), count)
                self.assertNotIn('turn/start', self.transport.methods())

    def test_snapshot_overlap_and_duplicate_old_notifications_do_not_replay_or_duplicate_text(self):
        self.resume_until_history()
        event = {'threadId': 'neutral-persistent-thread', 'turnId': 'old-turn', 'itemId': 'old-answer', 'delta': 'duplicate'}
        self.transport.notify('item/agentMessage/delta', event)
        self.transport.notify('turn/completed', {'threadId': 'neutral-persistent-thread', 'turn': self.history_turn()})
        self.transport.result('thread/turns/list', {'data': [self.history_turn()], 'nextCursor': 'older-page'})
        self.transport.result('thread/turns/list', {'data': [self.history_turn()], 'nextCursor': None})
        self.transport.result('thread/items/list', {'data': [self.history_entry(text='Repeat repeat')], 'nextCursor': 'item-page'})
        self.transport.result('thread/items/list', {'data': [self.history_entry(text='Repeat repeat')], 'nextCursor': None})
        self.transport.result('thread/read', {'thread': self.persistent_metadata()})
        self.finish_history_resume()
        snapshot = self.controller.snapshot
        self.assertEqual(snapshot['transcript'][0]['text'], 'Repeat repeat')
        self.assertEqual(len(snapshot['transcript']), 1)
        self.assertTrue(snapshot['can_send'])
        before = deepcopy(snapshot)
        self.transport.notify('item/agentMessage/delta', event)
        self.transport.notify('turn/completed', {'threadId': 'neutral-persistent-thread', 'turn': self.history_turn(status='failed')})
        self.assertEqual(self.controller.snapshot, before)
        self.assertNotIn('turn/start', self.transport.methods())

    def test_new_activity_observed_while_hydrating_is_not_replayed_and_stays_read_only(self):
        self.resume_until_history()
        self.transport.notify('item/agentMessage/delta', {'threadId': 'neutral-persistent-thread',
            'turnId': 'new-unloaded-turn', 'itemId': 'unloaded-item', 'delta': 'synthetic-unapplied-delta'})
        self.finish_one_history_turn()
        self.assertEqual(self.controller.snapshot['history_status'], 'read_only')
        self.assertNotIn('synthetic-unapplied', json.dumps(self.controller.snapshot))
        self.assertNotIn('turn/start', self.transport.methods())

    def test_cursor_cycle_and_history_rpc_errors_stop_without_retry_or_raw_provider_text(self):
        self.resume_until_history()
        self.transport.result('thread/turns/list', {'data': [self.history_turn()], 'nextCursor': 'repeated-cursor'})
        self.transport.result('thread/turns/list', {'data': [self.history_turn()], 'nextCursor': 'repeated-cursor'})
        self.assertEqual(self.controller.snapshot['error']['code'], 'CHAT_HISTORY_INVALID')
        self.assertEqual(self.transport.methods().count('thread/turns/list'), 2)
        self.assertNotIn('thread/items/list', self.transport.methods())
        self.setUp()
        self.resume_until_history()
        request = self.transport.messages[-1]
        count = len(self.transport.messages)
        self.transport.data({'id': request['id'], 'error': {'code': -32601, 'message': 'synthetic-private-history-error'}})
        self.assertEqual(self.controller.snapshot['error']['code'], 'CHAT_HISTORY_REQUEST_FAILED')
        self.assertEqual(len(self.transport.messages), count)
        self.assertNotIn('synthetic-private', json.dumps(self.controller.snapshot))

    def test_shared_twenty_page_budget_leaves_partial_history_read_only_without_extra_item_requests(self):
        self.resume_until_history()
        turns = [self.history_turn('turn-' + str(index)) for index in range(20)]
        self.transport.result('thread/turns/list', {'data': turns})
        for _index in range(19):
            self.assertEqual(self.transport.messages[-1]['method'], 'thread/items/list')
            turn_id = self.transport.messages[-1]['params']['turnId']
            self.transport.result('thread/items/list', {'data': [self.history_entry(turn_id=turn_id)]})
        self.assertEqual(self.transport.messages[-1]['method'], 'thread/read')
        self.assertEqual(self.transport.methods().count('thread/items/list'), 19)
        self.transport.result('thread/read', {'thread': self.persistent_metadata()})
        self.assertEqual(self.controller.snapshot['history_status'], 'read_only')
        self.assertIn('部分载入', self.controller.snapshot['history_notice'])
        self.assertNotIn('turn/start', self.transport.methods())

    def test_disconnect_and_old_generation_responses_cannot_continue_hydration(self):
        self.resume_until_history()
        request, old_data = self.transport.messages[-1], self.transport.data
        self.controller.disconnect()
        before, count = deepcopy(self.controller.snapshot), len(self.transport.messages)
        old_data({'id': request['id'], 'result': {'data': [self.history_turn()]}})
        self.assertEqual(self.controller.snapshot, before)
        self.assertEqual(len(self.transport.messages), count)
        self.assertEqual(before['phase'], 'disconnected')
        self.assertEqual(before['history_status'], 'error')
        self.assertNotIn('turn/start', self.transport.methods())

    def test_history_has_one_total_deadline_and_late_page_does_not_extend_or_start_work(self):
        with patch.object(chat.time, 'monotonic', return_value=100.0) as clock:
            self.resume_until_history()
            self.assertLessEqual(self.transport.timeouts[-1], 30000)
            clock.return_value = 159.0
            self.transport.result('thread/turns/list', {'data': [self.history_turn()]})
            self.assertEqual(self.transport.timeouts[-1], 1000)
            clock.return_value = 161.0
            self.transport.result('thread/items/list', {'data': [self.history_entry()]})
            self.assertEqual(self.controller.snapshot['error']['code'], 'CHAT_HISTORY_TIMEOUT')
            self.assertEqual(self.transport.methods().count('thread/read'), 1)
            self.assertNotIn('turn/start', self.transport.methods())

    def test_runtime_activity_after_successful_restore_blocks_an_unsolicited_new_turn(self):
        self.resume_until_history()
        self.finish_one_history_turn()
        self.transport.notify('thread/status/changed', {'threadId': 'neutral-persistent-thread',
                                                     'status': {'type': 'active', 'activeFlags': []}})
        self.assertEqual(self.controller.snapshot['phase'], 'history_read_only')
        count = len(self.transport.messages)
        self.controller.send('Must remain explicit and idle')
        self.assertEqual(len(self.transport.messages), count)
        self.assertNotIn('turn/start', self.transport.methods())


    def test_resume_occurs_only_after_all_history_pages_and_second_metadata_then_final_idle(self):
        self.resume_until_history()
        self.transport.result('thread/turns/list', {'data': [self.history_turn()], 'nextCursor': 'older-turns'})
        self.assertNotIn('thread/resume', self.transport.methods())
        self.transport.result('thread/turns/list', {'data': [], 'nextCursor': None})
        self.transport.result('thread/items/list', {'data': [self.history_entry()], 'nextCursor': 'older-items'})
        self.assertNotIn('thread/resume', self.transport.methods())
        self.transport.result('thread/items/list', {'data': [], 'nextCursor': None})
        self.assertEqual(self.transport.messages[-1]['method'], 'thread/read')
        self.assertNotIn('thread/resume', self.transport.methods())
        self.transport.result('thread/read', {'thread': self.persistent_metadata('notLoaded')})
        self.assertEqual(self.transport.messages[-1]['method'], 'thread/resume')
        self.assertFalse(self.controller.snapshot['can_send'])
        self.transport.result('thread/resume', self.persistent_result())
        self.assertEqual(self.transport.messages[-1]['method'], 'mcpServerStatus/list')
        self.assertFalse(self.controller.snapshot['can_send'])
        self.transport.result('mcpServerStatus/list', self.inventory(True))
        self.assertEqual(self.transport.messages[-1]['method'], 'thread/read')
        self.assertFalse(self.controller.snapshot['can_send'])
        self.transport.result('thread/read', {'thread': self.persistent_metadata()})
        self.assertTrue(self.controller.snapshot['can_send'])
        self.assertNotIn('turn/start', self.transport.methods())
        self.assertEqual(self.transport.methods().count('thread/resume'), 1)

    def test_nonterminal_or_partial_history_is_displayed_without_ever_resuming_thread(self):
        for source in ('initial_active', 'changed_active', 'in_progress', 'unknown', 'truncated'):
            with self.subTest(source=source):
                self.setUp()
                self.install_history_projection()
                self.connect_persistent()
                self.pass_config()
                self.transport.result('mcpServerStatus/list', self.inventory())
                self.transport.result('thread/read', {'thread': self.persistent_metadata(
                    'active' if source == 'initial_active' else 'notLoaded')})
                if source == 'truncated':
                    for page in range(20):
                        self.transport.result('thread/turns/list', {'data': [self.history_turn()],
                            'nextCursor': 'history-cursor-' + str(page)})
                else:
                    self.transport.result('thread/turns/list', {'data': [self.history_turn(status=
                        'inProgress' if source == 'in_progress' else 'future-status' if source == 'unknown' else 'completed')]})
                    self.transport.result('thread/items/list', {'data': [self.history_entry()]})
                self.transport.result('thread/read', {'thread': self.persistent_metadata(
                    'active' if source == 'changed_active' else 'notLoaded')})
                self.assertEqual(self.controller.snapshot['history_status'], 'read_only')
                self.assertFalse(self.controller.snapshot['can_send'])
                self.assertNotIn('thread/resume', self.transport.methods())
                self.assertNotIn('turn/start', self.transport.methods())
                self.assertEqual(self.transport.methods().count('mcpServerStatus/list'), 1)
                if source != 'truncated':
                    self.assertEqual(self.controller.snapshot['transcript'][0]['text'], 'Neutral stored answer')

    def test_history_item_fetch_prioritizes_newest_turn_and_still_renders_oldest_first(self):
        self.resume_until_history()
        self.transport.result('thread/turns/list', {'data': [self.history_turn('newest'), self.history_turn('oldest')]})
        self.assertEqual(self.transport.messages[-1]['params']['turnId'], 'newest')
        self.transport.result('thread/items/list', {'data': [self.history_entry('new-answer', 'Newest answer', 'newest')]})
        self.assertEqual(self.transport.messages[-1]['params']['turnId'], 'oldest')
        self.transport.result('thread/items/list', {'data': [self.history_entry('old-answer', 'Oldest answer', 'oldest')]})
        self.transport.result('thread/read', {'thread': self.persistent_metadata()})
        self.finish_history_resume()
        self.assertEqual([row['text'] for row in self.controller.snapshot['transcript']], ['Oldest answer', 'Newest answer'])

    def test_hydration_activity_ids_are_bounded_and_malformed_ids_never_enter_live_reducer(self):
        self.resume_until_history()
        for index in range(101):
            self.transport.notify('item/agentMessage/delta', {'threadId': 'neutral-persistent-thread',
                'turnId': 'unloaded-' + str(index), 'itemId': 'unloaded-item', 'delta': 'synthetic-private-unapplied'})
        self.transport.notify('item/agentMessage/delta', {'threadId': 'neutral-persistent-thread',
            'turnId': {'synthetic': 'malformed'}, 'itemId': 'unloaded-item', 'delta': 'synthetic-private-unapplied'})
        self.assertEqual(len(self.controller._history_observed_turns), 100)
        self.finish_one_history_turn()
        self.assertEqual(self.controller.snapshot['history_status'], 'read_only')
        self.assertNotIn('thread/resume', self.transport.methods())
        self.assertNotIn('synthetic-private', json.dumps(self.controller.snapshot))

    def test_persistent_timestamp_outside_supported_calendar_is_rejected_before_registration(self):
        with self.assertRaises(ValueError):
            chat._session_record(self.persistent_record(created_at=253402300800))


    def test_restore_publish_disconnect_cannot_queue_another_request_or_touch_new_connection(self):
        for stage in ('discover', 'catalog_complete', 'begin_restore'):
            with self.subTest(stage=stage):
                self.setUp()
                self.connect_persistent(through_models=False)
                if stage == 'discover':
                    self.controller.disconnect()
                    trigger = lambda: self.controller._discover_models()
                else:
                    trigger = lambda: self.transport.result('model/list', {'data': [self.model_row()]})
                disconnected = []
                def changed(snapshot):
                    self.events.append(snapshot)
                    target = (snapshot['models_status'] == 'loading' if stage == 'discover' else
                              snapshot['models_status'] == 'ready' and
                              (self.controller._send_attempt is not None if stage == 'begin_restore' else self.controller._send_attempt is None))
                    if not disconnected and target:
                        disconnected.append(True)
                        self.controller.disconnect()
                self.controller.changed = changed
                before = len(self.transport.messages)
                trigger()
                self.assertTrue(disconnected)
                self.assertEqual(len(self.transport.messages), before)
                self.assertEqual(self.controller.snapshot['phase'], 'disconnected')
                self.assertNotIn('thread/resume', self.transport.methods())

    def test_restore_activity_before_first_metadata_remains_read_only_after_idle_snapshot(self):
        self.install_history_projection()
        self.connect_persistent()
        self.transport.notify('thread/status/changed', {'threadId': 'neutral-persistent-thread',
                                                     'status': {'type': 'active', 'activeFlags': []}})
        self.pass_config()
        self.transport.result('mcpServerStatus/list', self.inventory())
        self.transport.result('thread/read', {'thread': self.persistent_metadata('idle')})
        self.finish_one_history_turn()
        self.assertEqual(self.controller.snapshot['history_status'], 'read_only')
        self.assertNotIn('thread/resume', self.transport.methods())


    def test_missing_required_metadata_turns_is_not_accepted_as_empty_history(self):
        self.connect_persistent()
        self.pass_config()
        self.transport.result('mcpServerStatus/list', self.inventory())
        metadata = self.persistent_metadata('notLoaded')
        del metadata['turns']
        self.transport.result('thread/read', {'thread': metadata})
        self.assertEqual(self.controller.snapshot['error']['code'], 'CHAT_HISTORY_METADATA_INVALID')
        self.assertNotIn('thread/turns/list', self.transport.methods())
        self.assertNotIn('thread/resume', self.transport.methods())

    def test_unknown_activity_after_restore_is_read_only_without_replaying_or_fatal_old_errors(self):
        for method, payload in (
                ('item/agentMessage/delta', {'turnId': 'unknown-turn', 'itemId': 'unknown-item', 'delta': 'synthetic-private-late'}),
                ('turn/completed', {'turn': self.history_turn('unknown-turn')}),
                ('error', {'turnId': 'unknown-turn', 'message': 'synthetic-private-late'}),
                ('item/started', {'turnId': {'synthetic': 'invalid'}, 'item': {}})):
            with self.subTest(method=method):
                self.setUp()
                self.resume_until_history()
                self.finish_one_history_turn()
                self.transport.notify(method, dict(payload, threadId='neutral-persistent-thread'))
                self.assertEqual(self.controller.snapshot['phase'], 'history_read_only')
                self.assertFalse(self.controller.snapshot['can_send'])
                self.assertEqual(self.controller.snapshot['transcript'][0]['text'], 'Neutral stored answer')
                self.assertIsNone(self.controller.snapshot['error'])
                self.assertNotIn('synthetic-private', json.dumps(self.controller.snapshot))
                self.assertNotIn('turn/start', self.transport.methods())


    def test_final_history_read_after_total_deadline_cannot_enable_send_before_timer_event(self):
        with patch.object(chat.time, 'monotonic', return_value=100.0) as clock:
            self.resume_until_history()
            self.transport.result('thread/turns/list', {'data': []})
            self.transport.result('thread/read', {'thread': self.persistent_metadata('notLoaded')})
            self.transport.result('thread/resume', self.persistent_result())
            self.transport.result('mcpServerStatus/list', self.inventory(True))
            before = len(self.transport.messages)
            clock.return_value = 161.0
            self.transport.result('thread/read', {'thread': self.persistent_metadata()})
            snapshot = self.controller.snapshot
            self.assertEqual(snapshot['error']['code'], 'CHAT_HISTORY_TIMEOUT')
            self.assertEqual(snapshot['phase'], 'error')
            self.assertEqual(snapshot['history_status'], 'error')
            self.assertFalse(snapshot['can_send'])
            self.assertEqual(len(self.transport.messages), before)
            self.assertNotIn('turn/start', self.transport.methods())

    def test_late_resume_response_cannot_queue_thread_inventory_or_recover_after_timer_race(self):
        with patch.object(chat.time, 'monotonic', return_value=100.0) as clock:
            self.resume_until_history()
            self.transport.result('thread/turns/list', {'data': []})
            self.transport.result('thread/read', {'thread': self.persistent_metadata('notLoaded')})
            before = len(self.transport.messages)
            clock.return_value = 161.0
            self.transport.result('thread/resume', self.persistent_result())
            self.assertEqual(self.controller.snapshot['error']['code'], 'CHAT_HISTORY_TIMEOUT')
            self.assertFalse(self.controller.snapshot['can_send'])
            self.assertEqual(len(self.transport.messages), before)
            self.assertEqual(self.transport.methods().count('mcpServerStatus/list'), 1)


if __name__ == '__main__': unittest.main()
