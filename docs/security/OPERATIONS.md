# Security operations for Raj's Terminal

Effective November 17, 2026. Owner: Balraj Singh. Contact: enterpriseghotra@gmail.com.

These procedures support the personal, single-owner Terminal and its Plaid data flow. A policy or scheduled workflow is not evidence that every control is operating. Preserve actual run receipts and record incomplete coverage. Do not attest to an unimplemented control.

## Vulnerability assessments and evidence

`Terminal Security Audit` runs daily at 10:23 UTC, on relevant pull requests/pushes, and on manual dispatch after this workflow is merged to the default branch. It uses pip-audit for the current resolution of `requirements.txt`, including transitive packages, and Bandit for `streamlit_app.py`, `src/` (including security modules), `scripts/sync_networth.py`, the MFA setup helper, and the assessment runner itself. It does not change dependencies, source, credentials or financial records. The job receives no application secrets, has read-only repository permissions, and does not retain checkout credentials.

Each actual run retains sanitized JSON and a short summary for 90 days. Evidence includes the observation time, source commit when available, requirements digest, audit-tool versions, assessed Python version, resolved package versions, scoped source coverage, findings, support-review dates and remediation deadlines. Raw scanner logs, source snippets and arbitrary vulnerability descriptions are not uploaded. Missing tools, incomplete resolution, parse failures and contradictory results produce **INCOMPLETE**, never a clean result.

The source gate is medium/high severity with medium/high confidence. Lower findings remain visible for review. Dependency findings initially receive a conservative high policy priority until owner triage; this is not a claim of an upstream CVSS rating. Every actionable finding fails the audit, including findings still inside their patch deadline. A failed audit is a request for review, not an automatic production change.

`scripts/security_reviewed_findings.json` records individual source-context reviews. Each names one exact warning, its complete source-file digest, reviewer, rationale, review date and an expiry within ninety days. A matching, unchanged, unexpired context is reported as a retained reviewed warning rather than a patched vulnerability. A changed file, shifted warning or expired review requires fresh triage and fails the gate. There are no blanket ignored rule classes. The initial reviews trace fixed HTTPS endpoints, repository-local trusted code loaders, allowlisted/parameterized SQLite construction, and Git blob SHA1 identity separately from Fernet authentication. They do not certify upstream redirect behavior or replace infrastructure review. Reviewers must inspect the full data flow again before refreshing a digest or expiry.

Run an assessment locally from an isolated environment containing `pip-audit==2.10.1` and `bandit==1.9.4`:

```sh
python scripts/security_audit.py --output-dir /private/tmp/terminal-security-evidence
```

The dependency result describes a fresh requirements resolution, not the versions already installed in Streamlit production. Capture and assess the deployed installed-version inventory separately after deployment. Do not report the CI result as a scan of the owner's Mac or a managed provider's internal hosts. Use authorized device/OS assessment tools and provider evidence for those assets; do not probe hosted infrastructure without permission.

Initial implementation evidence (November 18, 2026 UTC; November 17 local): the first complete scanner invocation collected 53 resolved packages and Bandit coverage of 80 files / 46,994 lines, but correctly returned **INCOMPLETE** because source changed during concurrent implementation. It is not a clean source-scan receipt. A subsequent dependency-only retest of the fixed constraint resolved 53 packages, including cryptography 50.0.2, with zero known advisories. With that installed version, 100 existing encrypted Acquisition/Networth-store regression tests passed, alongside Fernet, WebAuthn option-generation and P-256 smoke checks. The separate security-audit regression suite passed 16 tests. Final source evidence must be regenerated after the implementation becomes stable, and deployed-runtime evidence must still follow deployment.

The final stable-source assessment completed at `2026-11-18T04:46:50.279207Z` with **PASS** for its documented scope: 53 resolved packages with zero known dependency advisories; 84 Python files / 48,040 lines assessed, including the enrollment helper and audit runner. All 76 source warnings remain in evidence: 17 individual reviewed-context warnings and 59 below the gate threshold (including seven medium-severity/low-confidence warnings). None is described as a patched source vulnerability. The JSON receipt SHA256 is `16927016207e830e328e082c563527bcc3af2fdc5cb1e05fc2ebbf4972b6de9e`. This assessed an uncommitted working tree with a complete per-file digest manifest, not an already-deployed release; obtain fresh CI and deployment receipts after publication.

## Triage patching and fixed deadlines

Review failed audit notifications within one day. Record each actionable finding in `scripts/security_support_matrix.json` using its exact evidence `id`, actual `first_seen` date, `priority` (`critical`, `high`, `medium`, `low`), and `status` (`open` or `resolved`). The initial observation date must not be moved forward on repeat scans. An untracked finding always fails, so a repeat assessment cannot silently reset an unresolved deadline to a passing state. A finding marked resolved that is still detected is reported as reopened.

Policy patch targets from first observation are seven days for critical, fourteen for high, thirty for medium and ninety for low. Contain active exploitation immediately where practical. An owner may shorten a deadline based on exposure. Record the affected asset, assessment/triage evidence, verified fix, validation and deployed receipt. Mark resolved only after the affected asset has been retested. New targets do not establish that historical deadlines were met, and the tool does not automatically patch or merge updates.

Dependabot proposes weekly pip and GitHub Actions updates. Review and test each proposal; automatic merging is not enabled by this configuration. Repository dependency graph, Dependabot alerts and security-update settings require separate verification in GitHub. Do not claim these settings are enabled merely because `.github/dependabot.yml` exists.

## Supported software and EOL review

The support matrix tracks explicit Python support cutoffs from the Python Developer's Guide. For a vendor-published EOL month without a precise day, the policy conservatively uses the first day of that month. An unknown or expired assessed Python line fails. The evidence concerns the assessment interpreter; verify the deployed hosting interpreter separately.

The first local baseline found that the previous `cryptography>=43,<47` constraint resolved a release with four distinct advisories. The app and independently installed Networth worker now require `cryptography>=50.0.0,<51`, the release line that includes the highest necessary upstream fix. A fresh install and encrypted-storage compatibility checks are required before deployment, followed by a deployed-runtime receipt. Changing the version bound alone is not evidence that an already-running host was patched. The upstream [50.0.0 fix](https://github.com/pyca/cryptography/security/advisories/GHSA-g6cj-pr64-35w5) and [release history](https://cryptography.io/en/stable/changelog/) document the patched line.

The owner reviews the support matrix, runtime choices, dependency vendor advisories and maintained release lines at least quarterly and before a support cutoff. The first review is recorded November 17, 2026 and is due again February 17, 2027. An overdue matrix review fails the audit. A package being outdated is not automatically proof of EOL; record actual vendor support guidance. Package support notices without a machine-readable lifecycle still need owner review. Update the review dates only after performing and recording that review.

## Access reviews and offboarding

Maintain an owner-controlled inventory of people, service identities, roles, repositories, Streamlit administrators, Plaid team members, credential scopes and expiry/revocation controls. Never put token values or recovery codes in the inventory. Verify that the intended private data repository remains private and that only required owners/services have access.

At least quarterly and after a role change, the owner reviews actual GitHub collaborators/invitations, hosting administrators, Plaid members, app identities and service permissions. A review record must include date, reviewer, systems checked, identities/roles verified, discrepancies, corrective actions and a completed receipt. A future schedule is not evidence of a completed periodic review. Keep sensitive account lists and screenshots private; publish only policy-level descriptions.

For an access removal, revoke the person in the controlling identity directory, remove administrative memberships/invitations, revoke affected credentials and expire app authorization. Verify a fresh denied request. Record when removal was detected and when each system denied access. GitHub/hosting/Plaid offboarding are separate from app-local user expiry. Do not claim centralized IAM or automatic employee de-provisioning across providers until the actual provider controls and revocation tests support that claim.

## Evidence required before Plaid attestations

Actual successful source/dependency scans can support the scoped vulnerability-assessment control. They do not alone establish full developer-device coverage. A documented and enforced deadline process plus dated remediation receipts supports patch-SLA practice; defining a deadline alone does not prove compliance with it. The support matrix, actual review and runtime/version evidence support EOL monitoring.

App policies, MFA, access/role enforcement, published privacy content and retention/deletion each need their own implementation and tests. Internal-account MFA requires actual account evidence. Single-owner context may explain employee-specific requirements to Plaid, but do not invent employee accounts, enterprise zero-trust infrastructure or centralized provider IAM to clear a checklist.

References: [pip-audit](https://github.com/pypa/pip-audit), [Bandit](https://bandit.readthedocs.io/en/latest/man/bandit.html), [Dependabot security settings](https://docs.github.com/en/code-security/how-tos/secure-your-supply-chain/secure-your-dependencies/configure-security-updates), [Python support status](https://devguide.python.org/versions/).
