import copy
from datetime import date
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.security_audit import (
    IncompleteAssessment, apply_source_reviews, evaluate, load_policy, normalize_dependencies,
    normalize_source, run_assessment,
)


ROOT = Path(__file__).resolve().parents[1]
TODAY = date(2026, 11, 17)


class SecurityAuditTests(unittest.TestCase):
    def policy(self):
        # A historical fixture must not inherit future observations from the live register.
        return {"schema_version": 1, "reviewed_on": "2026-11-17", "review_due": "2027-02-17",
                "patch_sla_days": {"critical": 7, "high": 14, "medium": 30, "low": 90},
                "triage_sla_days": 1, "findings": [],
                "python_support_cutoffs": {"3.10": "2026-10-01", "3.12": "2028-10-01"}}

    def dependency(self, vulns=None):
        return {"dependencies": [{"name": "example", "version": "1.0", "vulns": vulns or []}]}

    def source(self, results=None):
        return {"errors": [], "results": results or [], "metrics": {"_totals": {"loc": 25}, "src/example.py": {"loc": 25}}}

    def issue(self, **overrides):
        data = {"filename": "src/example.py", "line_number": 12, "test_id": "B105",
                "issue_severity": "MEDIUM", "issue_confidence": "HIGH",
                "code": "password = 'DO-NOT-PUBLISH'", "issue_text": "SECRET DO-NOT-PUBLISH"}
        data.update(overrides)
        return data

    def test_dependency_inventory_and_safe_advisory_metadata(self):
        inventory, findings = normalize_dependencies(self.dependency([{"id": "CVE-2026-1234", "fix_versions": ["1.1"], "description": "DO-NOT-PUBLISH"}]), 1)
        self.assertEqual(inventory, [{"name": "example", "version": "1.0"}])
        self.assertEqual(findings[0]["id"], "dependency:example:CVE-2026-1234")
        self.assertNotIn("DO-NOT-PUBLISH", json.dumps(findings))

    def test_skipped_dependency_and_tool_failure_are_incomplete(self):
        for raw, code in [({"dependencies": [{"name": "example", "skip_reason": "unavailable"}]}, 0), (self.dependency(), 2), ({"dependencies": []}, 0)]:
            with self.subTest(code=code, raw=raw), self.assertRaises(IncompleteAssessment):
                normalize_dependencies(raw, code)

    def test_dependency_exit_status_must_agree_with_findings(self):
        with self.assertRaises(IncompleteAssessment):
            normalize_dependencies(self.dependency(), 1)
        with self.assertRaises(IncompleteAssessment):
            normalize_dependencies(self.dependency([{"id": "CVE-2026-1234"}]), 0)

    def test_duplicate_feed_advisories_are_one_finding(self):
        vuln = {"id": "CVE-2026-1234", "fix_versions": ["1.1"]}
        _, findings = normalize_dependencies(self.dependency([vuln, vuln]), 1)
        self.assertEqual(len(findings), 1)

    def test_source_reports_do_not_copy_source_or_secret_text(self):
        coverage, findings = normalize_source(self.source([self.issue()]), 1, ROOT)
        self.assertEqual(coverage["lines_assessed"], 25)
        self.assertTrue(findings[0]["requires_action"])
        self.assertNotIn("DO-NOT-PUBLISH", json.dumps(findings))

    def test_low_findings_are_retained_but_gate_is_explicit(self):
        _, findings = normalize_source(self.source([self.issue(issue_severity="LOW")]), 1, ROOT)
        self.assertFalse(findings[0]["requires_action"])
        checks, evaluated = evaluate(self.policy(), findings, TODAY, "3.12")
        self.assertEqual(evaluated[0]["tracking_status"], "reported_below_gate_threshold")
        self.assertTrue(all(c["status"] == "pass" for c in checks))

    def test_source_parse_error_missing_coverage_or_external_path_is_incomplete(self):
        broken = self.source()
        broken["errors"] = [{"reason": "parse error"}]
        cases = [broken, {"errors": [], "results": [], "metrics": {}}, self.source([self.issue(filename="/private/outside.py")])]
        for raw in cases:
            with self.subTest(raw=raw), self.assertRaises(IncompleteAssessment):
                normalize_source(raw, 1 if raw.get("results") else 0, ROOT)

    def test_untriaged_findings_always_fail_and_cannot_reset_to_pass(self):
        _, findings = normalize_dependencies(self.dependency([{"id": "CVE-2026-1234"}]), 1)
        checks, evaluated = evaluate(self.policy(), findings, TODAY, "3.12")
        self.assertEqual(evaluated[0]["tracking_status"], "untriaged")
        self.assertEqual(evaluated[0]["triage_due"], "2026-11-18")
        self.assertEqual(checks[-1]["status"], "fail")

    def test_deadline_and_reopened_findings_use_persistent_first_seen(self):
        _, findings = normalize_dependencies(self.dependency([{"id": "CVE-2026-1234"}]), 1)
        policy = self.policy()
        policy["findings"] = [{"id": findings[0]["id"], "first_seen": "2026-11-01", "priority": "high", "status": "open"}]
        _, evaluated = evaluate(policy, findings, TODAY, "3.12")
        self.assertEqual(evaluated[0]["patch_due"], "2026-11-15")
        self.assertEqual(evaluated[0]["tracking_status"], "overdue")
        policy["findings"][0]["status"] = "resolved"
        _, evaluated = evaluate(policy, findings, TODAY, "3.12")
        self.assertEqual(evaluated[0]["tracking_status"], "reopened")

    def test_unsupported_or_unknown_runtime_and_stale_review_fail(self):
        checks, _ = evaluate(self.policy(), [], TODAY, "3.10")
        self.assertEqual(checks[1]["status"], "fail")
        checks, _ = evaluate(self.policy(), [], TODAY, "9.99")
        self.assertEqual(checks[1]["status"], "fail")
        checks, _ = evaluate(self.policy(), [], date(2027, 2, 18), "3.12")
        self.assertEqual(checks[0]["status"], "fail")

    def test_policy_rejects_future_observations_duplicate_ids_and_unbounded_slas(self):
        base = self.policy()
        invalids = []
        future = copy.deepcopy(base)
        future["reviewed_on"] = "2027-01-01"
        invalids.append(future)
        sla = copy.deepcopy(base)
        sla["patch_sla_days"]["high"] = 9999
        invalids.append(sla)
        duplicate = copy.deepcopy(base)
        record = {"id": "dependency:example:CVE-2026-1234", "first_seen": "2026-11-01", "priority": "high", "status": "open"}
        duplicate["findings"] = [record, record]
        invalids.append(duplicate)
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "policy.json"
            for value in invalids:
                path.write_text(json.dumps(value))
                with self.subTest(value=value), self.assertRaises(IncompleteAssessment):
                    load_policy(path, TODAY)

    def test_incomplete_runs_still_write_sanitized_failure_evidence(self):
        with tempfile.TemporaryDirectory() as temp, patch("scripts.security_audit._version", return_value=None):
            output = Path(temp)
            report = run_assessment(ROOT, ROOT / "scripts/security_support_matrix.json", output)
            self.assertEqual(report["status"], "incomplete")
            self.assertTrue((output / "security-audit.json").exists())
            self.assertTrue((output / "security-audit-summary.md").exists())
            self.assertNotIn("Traceback", (output / "security-audit.json").read_text())

    def test_partial_source_results_survive_a_dependency_failure(self):
        results = [IncompleteAssessment("DO-NOT-PUBLISH"), (self.source([self.issue()]), 1)]
        with tempfile.TemporaryDirectory() as temp, patch("scripts.security_audit._version", return_value="1.0"), patch("scripts.security_audit._scan", side_effect=results):
            report = run_assessment(ROOT, ROOT / "scripts/security_support_matrix.json", Path(temp))
            self.assertEqual(report["status"], "incomplete")
            self.assertEqual(report["findings"][0]["rule"], "B105")
            self.assertNotIn("DO-NOT-PUBLISH", json.dumps(report))

    def test_source_changes_during_scan_invalidate_the_result(self):
        results = [(self.dependency(), 0), (self.source(), 0)]
        with tempfile.TemporaryDirectory() as temp, patch("scripts.security_audit._version", return_value="1.0"), patch("scripts.security_audit._scan", side_effect=results), patch("scripts.security_audit.source_manifest", side_effect=[[{"path": "src/a.py", "sha256": "a"}], [{"path": "src/a.py", "sha256": "b"}]]):
            report = run_assessment(ROOT, ROOT / "scripts/security_support_matrix.json", Path(temp))
            self.assertEqual(report["status"], "incomplete")
            self.assertIn("changed during assessment", report["assessment_errors"][0])

    def test_source_review_requires_unchanged_context_and_unexpired_date(self):
        import hashlib
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            target = root / "src/example.py"
            target.parent.mkdir()
            target.write_text("trusted_source = True\n")
            _, findings = normalize_source(self.source([self.issue()]), 1, root)
            record = {"id": findings[0]["id"], "source_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
                      "reviewed_on": "2026-11-17", "expires_on": "2026-12-17",
                      "reviewer": "Test source review", "rationale": "Fixed test context; warning retained."}
            path = root / "reviews.json"
            path.write_text(json.dumps({"schema_version": 1, "reviews": [record]}))
            reviewed = apply_source_reviews(path, findings, root, TODAY)
            self.assertFalse(reviewed[0]["requires_action"])
            self.assertTrue(reviewed[0]["scanner_requires_review"])
            self.assertEqual(evaluate(self.policy(), reviewed, TODAY, "3.12")[0][-1]["reviewed_context_count"], 1)
            expired = apply_source_reviews(path, findings, root, date(2026, 12, 17))
            self.assertTrue(expired[0]["requires_action"])
            self.assertEqual(expired[0]["review_status"], "expired")
            target.write_text("changed_source = True\n")
            changed = apply_source_reviews(path, findings, root, TODAY)
            self.assertTrue(changed[0]["requires_action"])
            self.assertEqual(changed[0]["review_status"], "invalidated_by_source_change")

    def test_source_review_cannot_use_duplicate_or_unbounded_records(self):
        record = {"id": "source:src/example.py:B105:12", "source_sha256": "a" * 64,
                  "reviewed_on": "2026-11-17", "expires_on": "2027-11-17",
                  "reviewer": "Test", "rationale": "Test only."}
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "reviews.json"
            for records in ([record], [dict(record, expires_on="2026-12-17")] * 2):
                path.write_text(json.dumps({"schema_version": 1, "reviews": records}))
                with self.assertRaises(IncompleteAssessment):
                    apply_source_reviews(path, [], ROOT, TODAY)


if __name__ == "__main__":
    unittest.main()
