"""Unit tests for opensquilla.contrib.codetask.verification."""

import json

from opensquilla.contrib.codetask import verification
from opensquilla.contrib.codetask.config import VERIFICATION_MANIFEST_NAME
from opensquilla.contrib.codetask.types import (
    AcceptanceCheck,
    RegressionResult,
    TaskState,
)


class TestManifestLoading:
    def test_missing_manifest(self, tmp_path):
        assert verification.load_manifest(tmp_path) is None

    def test_malformed_manifest(self, tmp_path):
        (tmp_path / VERIFICATION_MANIFEST_NAME).write_text("not json{")
        assert verification.load_manifest(tmp_path) is None

    def test_valid_manifest(self, tmp_path):
        (tmp_path / VERIFICATION_MANIFEST_NAME).write_text(json.dumps({"testable": True}))
        assert verification.load_manifest(tmp_path) == {"testable": True}


class TestStateDecision:
    def _green(self, before):
        return AcceptanceCheck(name="t", command="c", before=before, after="pass")

    def test_red_then_green_is_verified(self):
        state = verification._decide_state([self._green("fail")], None, red_known=True)
        assert state == TaskState.VERIFIED

    def test_green_on_base_is_already_satisfied(self):
        state = verification._decide_state([self._green("pass")], None, red_known=True)
        assert state == TaskState.ALREADY_SATISFIED

    def test_after_fail_is_failed(self):
        check = AcceptanceCheck(name="t", command="c", before="fail", after="fail")
        state = verification._decide_state([check], None, red_known=True)
        assert state == TaskState.FAILED

    def test_regression_new_failures_is_failed(self):
        reg = RegressionResult(command="pytest", ran=True, new_failures=2)
        state = verification._decide_state([self._green("fail")], reg, red_known=True)
        assert state == TaskState.FAILED

    def test_regression_clean_keeps_verified(self):
        reg = RegressionResult(command="pytest", ran=True, new_failures=0)
        state = verification._decide_state([self._green("fail")], reg, red_known=True)
        assert state == TaskState.VERIFIED


class TestParseHelpers:
    def test_parse_pytest_failures(self):
        assert verification._parse_failures("3 passed, 2 failed", 1) == 2

    def test_parse_failures_returncode_zero(self):
        assert verification._parse_failures("all good", 0) == 0

    def test_parse_failures_unparseable_nonzero(self):
        assert verification._parse_failures("boom", 1) is None

    def test_parse_passes(self):
        assert verification._parse_passes("10 passed, 0 failed") == 10


class TestVerifyEndToEnd:
    def test_no_manifest_is_invalid(self, tmp_path):
        out = verification.verify(repo=tmp_path, base_commit="x", scratch_dir=tmp_path)
        assert out.state == TaskState.INVALID_ACCEPTANCE_TEST

    def test_not_testable(self, tmp_path):
        (tmp_path / VERIFICATION_MANIFEST_NAME).write_text(
            json.dumps({"testable": False, "not_testable_reason": "docs only"})
        )
        out = verification.verify(repo=tmp_path, base_commit="x", scratch_dir=tmp_path)
        assert out.state == TaskState.NOT_TESTABLE
        assert "docs only" in out.detail

    def test_testable_but_no_tests_is_invalid(self, tmp_path):
        (tmp_path / VERIFICATION_MANIFEST_NAME).write_text(
            json.dumps({"testable": True, "acceptance_tests": []})
        )
        out = verification.verify(repo=tmp_path, base_commit="x", scratch_dir=tmp_path)
        assert out.state == TaskState.INVALID_ACCEPTANCE_TEST
