# Terminal security implementation receipt

Prepared November 17, 2026 local / November 18 UTC.
Branch: `codex/security-remediation-20261117`.
Implementation commit: `5255550f0121fc4b23c8259b288b15f0b2bf3c13`.

## Result and operational boundary

The public login policies, mandatory PIN/passkey→TOTP gate, owner/auditor roles,
expiry/revocation, encrypted CAS security state, email PIN recovery, current-data
retention/deletion controls and scheduled security assessments are implemented.
Login setup uses a separate `login_pin_sha256`; the legacy broker/cache sealing
key is preserved. Missing setup or unavailable authentication state fails closed.

This is a tested implementation. Production MFA enrollment, secret configuration,
actual mail delivery, selected retention period, provider-account MFA and external
IAM/offboarding evidence remain separate activation steps. No Plaid attestation
has been checked based only on code or policy. Earlier encrypted Git revisions,
provider backups and owner exports have not been erased. No real data deletion,
broker transaction or actual reset email was performed.

## Validation

- Fresh isolated installation of production requirements: PASS, including
  cryptography 50.0.2; `pip check`: no broken requirements.
- Complete clean-environment `unittest discover -s tests`: **683 tests PASS**.
- Focused MFA/store/recovery/login/public-policy/audit suite: **76 tests PASS**.
- Sixteen enabled-tab/repeated/second-session AppTest renders: PASS, using real
  AuthService with synthetic credentials and an isolated CAS ledger.
- Existing Risk production interaction/intent, connected Rebalance, Performance,
  Protective Puts and Touch ID persistence regressions: PASS.
- All 46 affected Python files compile; keypad JavaScript syntax: PASS.
- Architecture guard: PASS. Workflow YAML: PASS, four files.
- Full diff/whitespace and explicit scope audit: PASS.
- CUA verified a logged-out public privacy page and the missing-configuration
  locked keypad. This did not establish live production MFA or reset delivery.

The [saved security scan](evidence/2026-11-18/security-audit-summary.md) observed
53 resolved dependencies with zero known advisories and 84 Python files / 48,040
lines. All 76 source warnings remain in the JSON: seventeen individually reviewed
contexts and fifty-nine below the configured threshold. The scan identifies the
precommit working tree and includes file digests; it is not a scan of deployed
packages, developer endpoints or a provider's internal infrastructure.

## Preserved scope and dependency justification

Global theme/CSS/fonts, Streamlit theme configuration, navigation/layout,
financial/risk math, account selection, pricing, broker transports, order review
and OAuth implementation are unchanged. Fragment/dialog/callback owner files
needed authorization wrappers because those execution paths skip the entrypoint.
The Risk V2 helper is a real dependency of production V10 through V9. Tests mock
authorization only around isolated synthetic financial UI fixtures; real-guard
integration tests verify the corresponding denials. The Networth worker uses the
same retention implementation and patched encryption dependency as the app.

`src/theme.py`, `.streamlit/config.toml`, `src/tab_bar_v4.py`, navigation components,
`src/etrade_client.py`, `src/session_persistence.py`, `src/passkey_auth.py` and
canonical trade/risk math owners are unchanged. The preexisting untracked
`.gex_snapshot_cache/` is excluded.

## Exact changed-file manifest

- `.github/dependabot.yml`
- `.github/workflows/architecture-guard.yml`
- `.github/workflows/networth-sync.yml`
- `.github/workflows/security-audit.yml`
- `docs/policies/privacy.md`
- `docs/policies/retention.md`
- `docs/policies/security.md`
- `docs/policies/vulnerability-management.md`
- `docs/security/CONTROL_REGISTER.md`
- `docs/security/IMPLEMENTATION_RECEIPT.md`
- `docs/security/MFA_SETUP.md`
- `docs/security/OPERATIONS.md`
- `docs/security/evidence/2026-11-18/security-audit-summary.md`
- `docs/security/evidence/2026-11-18/security-audit.json`
- `requirements.txt`
- `scripts/security_audit.py`
- `scripts/security_reviewed_findings.json`
- `scripts/security_support_matrix.json`
- `scripts/setup_terminal_mfa.py`
- `scripts/sync_networth.py`
- `scripts/test_performance_ui.py`
- `scripts/test_protective_puts.py`
- `scripts/test_protective_puts_paging_browser.py`
- `scripts/test_rebalance_connected_ui.py`
- `scripts/test_risk_intent_override.py`
- `scripts/test_risk_production_ui.py`
- `scripts/test_tab_layout.py`
- `scripts/test_touch_id_persistence.py`
- `scripts/test_vertical_options_scanner_browser.py`
- `scripts/validate_architecture.py`
- `src/ARCHITECTURE.md`
- `src/NETWORTH_SETUP.md`
- `src/TERMINAL_CHANGELOG.md`
- `src/acquisition_ui.py`
- `src/components/lock_keypad_v2/index.html`
- `src/gex_ui_v3.py`
- `src/heatmaps_ui.py`
- `src/lock_screen_v2.py`
- `src/networth.py`
- `src/networth_store.py`
- `src/networth_ui.py`
- `src/performance_ui.py`
- `src/pin_recovery.py`
- `src/production_manifest.py`
- `src/protective_puts_portfolio_ui.py`
- `src/protective_puts_ui.py`
- `src/public_policies.py`
- `src/rebalance_portfolio_ui.py`
- `src/risk_sizing_ui_v10.py`
- `src/risk_sizing_ui_v2.py`
- `src/security_runtime.py`
- `src/security_store.py`
- `src/terminal_core.py`
- `src/terminal_security.py`
- `src/verti_phone_ui.py`
- `src/vertical_options_ui.py`
- `streamlit_app.py`
- `tests/test_acquisition.py`
- `tests/test_networth.py`
- `tests/test_networth_store.py`
- `tests/test_networth_ui.py`
- `tests/test_pin_recovery.py`
- `tests/test_public_policies.py`
- `tests/test_security_audit.py`
- `tests/test_security_login.py`
- `tests/test_security_store.py`
- `tests/test_terminal_security.py`
- `tests/test_verti_phone.py`
- `tests/test_vertical_options.py`

## Activation and attestations

Follow [MFA and recovery setup](MFA_SETUP.md) and the
[thirteen-item control register](CONTROL_REGISTER.md). VIP Access uses its
standard-authenticator QR account mode; an existing VIP Credential ID is not the
Terminal setup key. Owner entry is required for new credentials. Merge only after
preserving existing server secrets and completing verified enrollment.

Production fresh-process reboot, health endpoint and rendered frontend are not
claimed here until actual publication is observed. Commit/deployment receipts
will be appended after those actions occur.

## Commit and reboot receipt

Committed the exact reviewed 67-file implementation as
`5255550f0121fc4b23c8259b288b15f0b2bf3c13`. The working tree was then clean except
the preexisting excluded cache. Booted a separate fresh process using the clean
production-requirements environment on local port 8519. Its Streamlit health
endpoint returned HTTP 200 / `ok`; CUA observed the committed public security
policy, correct links and no financial imports/rendering. This is a **local**
fresh-process receipt. Managed production publication/reboot, deployed health,
actual MFA login and mail delivery remain pending owner credential activation.

The published review commit `74bd9e1c628467302381fa4c45b1df506e52b03b` in
[draft PR #210](https://github.com/G-enterpriseGroup/Screeners/pull/210) has tree
`6401d014afc42957eabcdda5aee943187b330297`, identical to tested local receipt
commit `a390ad7`. Remote security assessment passed. Every architecture-job unit,
syntax, tab and workflow step passed; its standalone scanner browser fixture
initially lacked the synthetic authorization required by the new guards. Two
generated browser-test apps now mock authorization only inside their isolated
synthetic processes, including early callbacks. Their compilation and extracted
AppTest rendering/callback/rerun checks pass. The complete manifest now contains
69 files. No production authorization code changed in this CI fixture follow-up;
remote browser verification is rerun on the follow-up commit.
