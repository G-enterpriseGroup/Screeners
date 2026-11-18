"""Run scoped security assessments and retain only sanitized, dated evidence.

Exit 0: assessed checks passed. Exit 1: remediation/review needed. Exit 2:
assessment incomplete or invalid. This does not certify deployed infrastructure
or developer-device security and never changes dependencies or production data.
"""

from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta, timezone
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:+/-]{0,239}\Z")
SAFE_PACKAGE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,159}\Z")
PRIORITIES = {"critical", "high", "medium", "low"}
SOURCE_TARGETS = ("streamlit_app.py", "src", "scripts/sync_networth.py", "scripts/security_audit.py", "scripts/setup_terminal_mfa.py")


class IncompleteAssessment(ValueError):
    """A scanner or evidence input is missing, malformed or contradictory."""


def _iso_date(value: object) -> date:
    if not isinstance(value, str):
        raise IncompleteAssessment("Policy dates must be ISO calendar dates.")
    try:
        result = date.fromisoformat(value)
    except ValueError:
        raise IncompleteAssessment("Policy dates must be ISO calendar dates.") from None
    if result.isoformat() != value:
        raise IncompleteAssessment("Policy dates must be ISO calendar dates.")
    return result


def load_policy(path: Path, today: date) -> dict:
    try:
        policy = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise IncompleteAssessment("Support and deadline policy could not be read.") from None
    if not isinstance(policy, dict) or policy.get("schema_version") != 1:
        raise IncompleteAssessment("Support and deadline policy schema is invalid.")
    reviewed = _iso_date(policy.get("reviewed_on"))
    due = _iso_date(policy.get("review_due"))
    if reviewed > today or due <= reviewed or due > reviewed + timedelta(days=100):
        raise IncompleteAssessment("Support policy review dates are invalid.")
    deadlines = policy.get("patch_sla_days")
    if not isinstance(deadlines, dict) or set(deadlines) != PRIORITIES:
        raise IncompleteAssessment("All four patch priorities require deadlines.")
    if any(type(v) is not int or not 1 <= v <= 90 for v in deadlines.values()):
        raise IncompleteAssessment("Patch deadlines must be between one and ninety days.")
    if type(policy.get("triage_sla_days")) is not int or not 1 <= policy["triage_sla_days"] <= 7:
        raise IncompleteAssessment("A bounded triage deadline is required.")
    cutoffs = policy.get("python_support_cutoffs")
    if not isinstance(cutoffs, dict) or not cutoffs:
        raise IncompleteAssessment("Python support cutoffs are missing.")
    for version, cutoff in cutoffs.items():
        if not isinstance(version, str) or not re.fullmatch(r"3\.\d{1,2}", version):
            raise IncompleteAssessment("Python support versions are invalid.")
        _iso_date(cutoff)
    records = policy.get("findings")
    if not isinstance(records, list):
        raise IncompleteAssessment("The persistent findings register is invalid.")
    ids = set()
    for record in records:
        if not isinstance(record, dict):
            raise IncompleteAssessment("A finding record is invalid.")
        ident = record.get("id")
        if not isinstance(ident, str) or not SAFE_ID.fullmatch(ident) or ident in ids:
            raise IncompleteAssessment("Finding identifiers must be unique and bounded.")
        ids.add(ident)
        if _iso_date(record.get("first_seen")) > today:
            raise IncompleteAssessment("A finding observation cannot be future dated.")
        if record.get("priority") not in PRIORITIES or record.get("status") not in {"open", "resolved"}:
            raise IncompleteAssessment("A finding needs a valid priority and status.")
    return policy


def _safe_text(value: object, pattern=SAFE_ID) -> str:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise IncompleteAssessment("Scanner identifiers are invalid.")
    return value


def normalize_dependencies(raw: object, returncode: int) -> tuple[list[dict], list[dict]]:
    if returncode not in (0, 1) or not isinstance(raw, dict) or not isinstance(raw.get("dependencies"), list):
        raise IncompleteAssessment("Dependency assessment did not complete.")
    inventory = []
    findings = []
    seen = set()
    if not raw["dependencies"]:
        raise IncompleteAssessment("Dependency assessment returned an empty inventory.")
    for package in raw["dependencies"]:
        if not isinstance(package, dict) or package.get("skip_reason"):
            raise IncompleteAssessment("Dependency coverage is incomplete.")
        name = _safe_text(package.get("name"), SAFE_PACKAGE)
        version = _safe_text(package.get("version"))
        vulns = package.get("vulns")
        if not isinstance(vulns, list):
            raise IncompleteAssessment("Dependency vulnerability results are invalid.")
        inventory.append({"name": name, "version": version})
        for vuln in vulns:
            if not isinstance(vuln, dict):
                raise IncompleteAssessment("Dependency vulnerability results are invalid.")
            ident = _safe_text(vuln.get("id"))
            fixed = vuln.get("fix_versions", [])
            if not isinstance(fixed, list):
                raise IncompleteAssessment("Dependency fix versions are invalid.")
            finding_id = "dependency:" + name + ":" + ident
            if finding_id in seen:
                continue
            seen.add(finding_id)
            findings.append({
                "id": finding_id,
                "tool": "pip-audit", "package": name, "version": version,
                "advisory": ident, "fix_versions": [_safe_text(v) for v in fixed],
                "priority": "high", "priority_basis": "default_pending_owner_triage",
                "requires_action": True,
            })
    if bool(findings) != (returncode == 1):
        raise IncompleteAssessment("Dependency result and tool exit status disagree.")
    return sorted(inventory, key=lambda row: row["name"].lower()), findings


def normalize_source(raw: object, returncode: int, root: Path) -> tuple[dict, list[dict]]:
    if returncode not in (0, 1) or not isinstance(raw, dict) or raw.get("errors"):
        raise IncompleteAssessment("Source assessment did not complete.")
    results = raw.get("results")
    metrics = raw.get("metrics")
    if not isinstance(results, list) or not isinstance(metrics, dict):
        raise IncompleteAssessment("Source assessment results are invalid.")
    totals = metrics.get("_totals")
    if not isinstance(totals, dict) or type(totals.get("loc")) is not int or totals["loc"] <= 0:
        raise IncompleteAssessment("Source assessment returned no coverage.")
    findings = []
    for issue in results:
        if not isinstance(issue, dict):
            raise IncompleteAssessment("Source finding is invalid.")
        filename = issue.get("filename")
        if not isinstance(filename, str):
            raise IncompleteAssessment("Source finding path is invalid.")
        path = Path(filename)
        path = path if path.is_absolute() else root / path
        try:
            relative = path.resolve().relative_to(root.resolve()).as_posix()
        except ValueError:
            raise IncompleteAssessment("Source finding is outside the assessed repository.") from None
        if not re.fullmatch(r"[A-Za-z0-9_./-]{1,220}", relative) or ".." in relative.split("/"):
            raise IncompleteAssessment("Source finding path is invalid.")
        ident = issue.get("test_id")
        severity = issue.get("issue_severity")
        confidence = issue.get("issue_confidence")
        line = issue.get("line_number")
        if (not isinstance(ident, str) or not re.fullmatch(r"B\d{3}", ident)
                or severity not in {"LOW", "MEDIUM", "HIGH"}
                or confidence not in {"LOW", "MEDIUM", "HIGH"}
                or type(line) is not int or line <= 0):
            raise IncompleteAssessment("Source finding metadata is invalid.")
        findings.append({
            "id": f"source:{relative}:{ident}:{line}", "tool": "bandit", "rule": ident,
            "path": relative, "line": line, "priority": severity.lower(),
            "confidence": confidence.lower(), "priority_basis": "scanner_severity",
            "requires_action": severity in {"MEDIUM", "HIGH"} and confidence in {"MEDIUM", "HIGH"},
        })
    if bool(results) != (returncode == 1):
        raise IncompleteAssessment("Source result and tool exit status disagree.")
    return {"lines_assessed": totals["loc"], "file_metric_count": len(metrics) - 1}, findings


def apply_source_reviews(path: Path, findings: list[dict], root: Path, today: date) -> list[dict]:
    """Apply expiring reviews to individual warnings only while their file is unchanged."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise IncompleteAssessment("The source-review register could not be read.") from None
    if not isinstance(raw, dict) or raw.get("schema_version") != 1 or not isinstance(raw.get("reviews"), list):
        raise IncompleteAssessment("The source-review register is invalid.")
    reviews = {}
    for record in raw["reviews"]:
        if not isinstance(record, dict):
            raise IncompleteAssessment("A source-review record is invalid.")
        ident = record.get("id")
        digest = record.get("source_sha256")
        if (not isinstance(ident, str) or not SAFE_ID.fullmatch(ident) or not ident.startswith("source:")
                or ident in reviews or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest)):
            raise IncompleteAssessment("Source reviews require unique exact finding IDs and file digests.")
        reviewed = _iso_date(record.get("reviewed_on"))
        expiry = _iso_date(record.get("expires_on"))
        if reviewed > today or not reviewed < expiry <= reviewed + timedelta(days=90):
            raise IncompleteAssessment("Source-review dates are invalid or unbounded.")
        for field, limit in (("reviewer", 120), ("rationale", 1800)):
            value = record.get(field)
            if not isinstance(value, str) or not value.strip() or len(value) > limit or any(ord(c) < 32 for c in value):
                raise IncompleteAssessment("Source reviews require bounded reviewer and rationale text.")
        reviews[ident] = record
    evaluated = []
    for finding in findings:
        item = dict(finding)
        record = reviews.get(item["id"])
        if item["tool"] == "bandit" and item["requires_action"] and record:
            item["reviewed_on"] = record["reviewed_on"]
            item["review_expires_on"] = record["expires_on"]
            item["reviewer"] = record["reviewer"]
            item["review_rationale"] = record["rationale"]
            digest = hashlib.sha256((root / item["path"]).read_bytes()).hexdigest()
            item["source_sha256"] = digest
            if digest != record["source_sha256"]:
                item["review_status"] = "invalidated_by_source_change"
            elif today >= _iso_date(record["expires_on"]):
                item["review_status"] = "expired"
            else:
                item["review_status"] = "reviewed_not_exploitable_in_current_context"
                item["scanner_requires_review"] = True
                item["requires_action"] = False
        evaluated.append(item)
    return evaluated


def evaluate(policy: dict, findings: list[dict], today: date, python_minor: str) -> tuple[list[dict], list[dict]]:
    checks = []
    checks.append({"control": "support_policy_review", "status": "pass" if today <= _iso_date(policy["review_due"]) else "fail",
                   "reviewed_on": policy["reviewed_on"], "review_due": policy["review_due"]})
    cutoff = policy["python_support_cutoffs"].get(python_minor)
    checks.append({"control": "assessment_python_support", "status": "pass" if cutoff and today < _iso_date(cutoff) else "fail",
                   "python_minor": python_minor, "policy_cutoff": cutoff})
    records = {row["id"]: row for row in policy["findings"]}
    evaluated = []
    for finding in findings:
        item = dict(finding)
        if not item["requires_action"]:
            item["tracking_status"] = ("reviewed_context_warning_retained" if item.get("review_status")
                                       else "reported_below_gate_threshold")
            evaluated.append(item)
            continue
        record = records.get(item["id"])
        if record is None:
            item.update({"tracking_status": "untriaged", "observed_on": today.isoformat(),
                         "triage_due": (today + timedelta(days=policy["triage_sla_days"])).isoformat()})
        else:
            first = _iso_date(record["first_seen"])
            deadline = first + timedelta(days=policy["patch_sla_days"][record["priority"]])
            item.update({"priority": record["priority"], "priority_basis": "owner_register",
                         "first_seen": first.isoformat(), "patch_due": deadline.isoformat(),
                         "tracking_status": "reopened" if record["status"] == "resolved" else
                         "overdue" if today > deadline else "within_sla"})
        evaluated.append(item)
    checks.append({"control": "actionable_findings", "status": "fail" if any(x["requires_action"] for x in findings) else "pass",
                   "actionable_count": sum(x["requires_action"] for x in findings),
                   "reviewed_context_count": sum(bool(x.get("scanner_requires_review")) for x in findings),
                   "reported_below_gate_count": sum(not x["requires_action"] and not x.get("scanner_requires_review") for x in findings)})
    return checks, evaluated


def _scan(module: str, arguments: list[str], output: Path, root: Path) -> tuple[object, int]:
    try:
        result = subprocess.run([sys.executable, "-m", module, *arguments, "--output", str(output)],
                                cwd=root, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                timeout=1200, check=False)
        raw = json.loads(output.read_text(encoding="utf-8"))
        return raw, result.returncode
    except (OSError, ValueError, subprocess.TimeoutExpired):
        raise IncompleteAssessment("A required scanner failed or did not produce valid JSON.") from None


def _version(distribution: str) -> str | None:
    try:
        return importlib.metadata.version(distribution)
    except importlib.metadata.PackageNotFoundError:
        return None


def source_manifest(root: Path) -> list[dict]:
    paths = [root / target for target in SOURCE_TARGETS if target != "src"]
    paths.extend((root / "src").rglob("*.py"))
    if any(path.is_symlink() or not path.resolve().is_relative_to(root.resolve()) for path in paths):
        raise IncompleteAssessment("Source coverage must not follow links outside the assessed repository.")
    return [{"path": path.relative_to(root).as_posix(), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
            for path in sorted(set(paths))]


def run_assessment(root: Path, policy_path: Path, output_dir: Path) -> dict:
    now = datetime.now(timezone.utc)
    today = now.date()
    output_dir.mkdir(parents=True, exist_ok=True)
    report = {
        "schema_version": 1, "observed_at": now.isoformat().replace("+00:00", "Z"),
        "status": "incomplete", "scope": {
            "dependency_scope": "current requirements resolution, including transitive packages; not a deployed-runtime inventory",
            "source_targets": list(SOURCE_TARGETS),
            "not_assessed": ["developer_device_OS", "managed_host_internals", "deployed_installed_versions", "provider_account_permissions_or_MFA", "vendor_EOL_of_individual_Python_packages"],
            "source_gate": "medium/high severity and medium/high confidence; individual expiring file-digest reviews and all lower findings retained",
        },
        "tool_versions": {"pip-audit": _version("pip-audit"), "bandit": _version("bandit")},
        "assessment_python": ".".join(str(v) for v in sys.version_info[:3]),
        "findings": [], "checks": [], "assessment_errors": [],
    }
    commit = os.environ.get("GITHUB_SHA", "")
    if re.fullmatch(r"[0-9a-f]{40}", commit):
        report["source_commit"] = commit
    else:
        try:
            sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, stdout=subprocess.PIPE,
                                 stderr=subprocess.DEVNULL, text=True, timeout=5, check=False).stdout.strip()
            if re.fullmatch(r"[0-9a-f]{40}", sha):
                report["source_commit"] = sha
        except (OSError, subprocess.TimeoutExpired):
            pass
    try:
        policy = load_policy(policy_path, today)
        requirements = root / "requirements.txt"
        report["requirements_sha256"] = hashlib.sha256(requirements.read_bytes()).hexdigest()
        if not all(report["tool_versions"].values()):
            raise IncompleteAssessment("Install the pinned pip-audit and Bandit assessment tools first.")
        if not all((root / target).exists() for target in SOURCE_TARGETS):
            raise IncompleteAssessment("One or more required source targets are missing.")
        manifest = source_manifest(root)
        report["source_manifest"] = manifest
        try:
            changed = subprocess.run(["git", "status", "--porcelain", "--untracked-files=all", "--", *SOURCE_TARGETS],
                                     cwd=root, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                     timeout=5, check=False)
            report["assessed_source_checkout_clean"] = changed.returncode == 0 and not bool(changed.stdout)
        except (OSError, subprocess.TimeoutExpired):
            report["assessed_source_checkout_clean"] = None
        inventory = []
        findings = []
        with tempfile.TemporaryDirectory(prefix="terminal-security-raw-") as temp:
            try:
                dependency_raw, dependency_exit = _scan("pip_audit", ["--requirement", str(requirements), "--strict", "--format", "json", "--progress-spinner", "off"], Path(temp) / "dependencies.json", root)
                inventory, dependency_findings = normalize_dependencies(dependency_raw, dependency_exit)
                findings.extend(dependency_findings)
            except IncompleteAssessment:
                report["assessment_errors"].append("Dependency assessment is incomplete; no clean dependency result is asserted.")
            try:
                source_raw, source_exit = _scan("bandit", ["--recursive", *SOURCE_TARGETS, "--format", "json", "--quiet"], Path(temp) / "source.json", root)
                coverage, source_findings = normalize_source(source_raw, source_exit, root)
                findings.extend(source_findings)
                report["source_coverage"] = coverage
            except IncompleteAssessment:
                report["assessment_errors"].append("Source assessment is incomplete; no clean source result is asserted.")
        report["dependency_inventory"] = inventory
        findings = apply_source_reviews(root / "scripts/security_reviewed_findings.json", findings, root, today)
        checks, findings = evaluate(policy, findings, today,
                                    ".".join(str(v) for v in sys.version_info[:2]))
        report["checks"] = checks
        report["findings"] = findings
        if manifest != source_manifest(root) or report["requirements_sha256"] != hashlib.sha256(requirements.read_bytes()).hexdigest():
            report["assessment_errors"].append("Source or requirements changed during assessment; run again on a stable revision.")
        if not report["assessment_errors"]:
            report["status"] = "fail" if any(c["status"] == "fail" for c in checks) else "pass"
    except (IncompleteAssessment, OSError) as exc:
        # Never serialize raw scanner output, exception payloads or source snippets.
        message = str(exc) if isinstance(exc, IncompleteAssessment) else "A required assessment file could not be read."
        report["assessment_errors"].append(message)
    (output_dir / "security-audit.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    actionable = sum(bool(row.get("requires_action")) for row in report["findings"])
    summary = ("# Terminal security assessment\n\n"
               + "Observed: " + report["observed_at"] + "\n\n"
               + "Result: **" + report["status"].upper() + "**\n\n"
               + "Actionable findings: " + str(actionable) + ".\n\n"
               + "Scope: repository Python source and current requirements resolution. This is not a deployed-runtime or developer-device certification.\n\n"
               + "Raw source snippets, secrets and raw scanner logs are excluded. Review the JSON evidence and persist newly triaged first-seen dates in scripts/security_support_matrix.json. An untracked finding always fails, so repeat scans cannot silently reset its deadline.\n")
    (output_dir / "security-audit-summary.md").write_text(summary, encoding="utf-8")
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    report = run_assessment(ROOT, ROOT / "scripts/security_support_matrix.json", args.output_dir)
    print("Terminal security assessment: " + report["status"].upper())
    print("Sanitized evidence: " + str(args.output_dir / "security-audit.json"))
    return {"pass": 0, "fail": 1, "incomplete": 2}[report["status"]]


if __name__ == "__main__":
    raise SystemExit(main())
