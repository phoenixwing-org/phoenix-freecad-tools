# SPDX-License-Identifier: Apache-2.0
"""Regression checks for the blank-document basic part approval branch."""

from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from test_pft_business import Error, business


class BasicPartApprovalTests(unittest.TestCase):
    def setUp(self):
        with business._lock:
            business._plans.clear()
        business.begin_approval_lifecycle()
        self.addCleanup(business.cancel_pending_approvals)
        self.docs = {}
        self.app = SimpleNamespace(ActiveDocument=None, listDocuments=lambda: dict(self.docs),
                                   getActiveTransaction=lambda: None, newDocument=Mock())
        self.gui = SimpleNamespace(activeDocument=lambda: self.app.ActiveDocument)
        self.request = {"operation": "create_basic_part", "newPart": {
            "name": "FixtureAnnulus", "profile": "annulus", "innerDiameterMm": 4,
            "outerDiameterMm": 12, "thicknessMm": 2,
        }}

    def call(self, method, **params):
        return business.perform(method, params, self.app, self.gui, Error)

    def test_new_part_has_an_independent_bounded_input_schema(self):
        for override in ({"innerDiameterMm": 12}, {"thicknessMm": True}, {"thicknessMm": float("inf")},
                         {"widthMm": 7}, {"name": "part/name"}, {"profile": "unrestricted"}):
            with self.subTest(override=override):
                bad = {**self.request, "newPart": {**self.request["newPart"], **override}}
                with self.assertRaises(Error) as caught:
                    self.call("prepare_part_changes", **bad)
                self.assertEqual(caught.exception.code, "BASIC_PART_INVALID")
        with self.assertRaises(Error) as caught:
            self.call("prepare_part_changes", **self.request, docName="Existing")
        self.assertEqual(caught.exception.code, "INVALID_PARAMS")
        self.assertFalse(business._plans)
        self.app.newDocument.assert_not_called()

    def test_late_document_open_invalidates_native_grant(self):
        preview = self.call("prepare_part_changes", **self.request)
        token = preview["token"]
        self.assertEqual(preview["document"], "FixtureAnnulus")
        self.assertEqual(len(preview["rows"]), 7)
        with self.assertRaises(Error) as caught:
            self.call("apply_part_changes", token=token)
        self.assertEqual(caught.exception.code, "BASIC_PART_NATIVE_CONFIRMATION_REQUIRED")
        with patch.object(business, "_open_approval", return_value=Mock()) as window:
            state = self.call("request_part_change_approval", token=token)
        self.assertEqual(state["state"], "awaiting_confirmation")
        self.assertFalse(state["native_confirmation"])
        confirm = window.call_args.args[2]
        self.app.ActiveDocument = self.docs["Unrelated"] = object()
        failed = confirm()
        self.assertEqual(failed["state"], "failed")
        self.assertEqual(failed["error_code"], "BASIC_PART_CONTEXT_CHANGED")
        self.assertIs(self.app.ActiveDocument, self.docs["Unrelated"])
        self.docs.clear()
        self.app.ActiveDocument = None
        self.assertEqual(confirm(), failed)
        self.app.newDocument.assert_not_called()
