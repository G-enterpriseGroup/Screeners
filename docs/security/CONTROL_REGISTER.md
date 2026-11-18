# Plaid remediation control register

Owner: Balraj Singh · enterpriseghotra@gmail.com

Prepared November 17, 2026 · Plaid due date May 19, 2027

This register is an implementation record, not a certification. “Implemented in
code” does not mean enabled or tested in production. Keep Plaid attestations open
until the evidence in the last column exists. Never upload credentials, QR codes,
PIN hashes, private identity inventories or consumer records as evidence.

| Plaid item | Work implemented | Evidence still required before attesting |
| --- | --- | --- |
| MFA on internal systems | Written access/MFA policy and owner setup procedure | Enable and verify MFA on GitHub, Streamlit/its identity provider, Plaid and every other critical administrator account. GitHub 2FA was observed disabled during the earlier review. Application MFA does not fix provider MFA. |
| Patching within a defined SLA | Critical 7 / high 14 / medium 30 / low 90 day targets, finding first-seen records, audit gate and dependency update proposals. Vulnerable cryptography constraint replaced in app and worker. | Merge, verify installed production versions and retain dated finding/fix/deployment receipts. Repeat the process for all assets; a policy does not establish historical performance. |
| Documented access control | Public security/access policy, explicit owner/auditor roles, least privilege, expiry, revocation and credential rotation procedures | Publish the URL and adopt the policy in actual operations; verify provider permissions. |
| Periodic access reviews/audits | Quarterly review procedure covering app identities and all provider accounts | Complete and retain a dated owner review of actual memberships, permissions and service scopes, then repeat quarterly. No completed enterprise review is implied. |
| Zero trust access architecture | Server-side identity/role checks before finance, mandatory MFA, fragment rerun guards, short sessions and fail-closed durable verification | App controls are limited to this application. Review hosting/network/device/service boundaries and ask Plaid whether this single-owner architecture or compensating controls meet its requirement. |
| EOL monitoring | Version-support matrix, quarterly review deadline, interpreter-support audit gate and vendor review procedure | Verify deployed interpreter, developer device and other asset inventories; maintain actual quarterly review receipts. |
| Automated employee access changes | App registry supports enabled flags, credential versions and automatic timezone-aware access expiry; claims are rechecked | There are no asserted employee/HR provisioning integrations. Document the actual single-owner context and get Plaid acceptance or implement/test a directory-driven workflow across relevant providers. |
| Vulnerability scanning | Actual dependency and source scanning, CI schedule, immutable action pins and sanitized receipts | Activate scheduled workflow on main; review each actual run. Scan authorized developer endpoints and other production assets separately; CI source scanning is not endpoint scanning. |
| Published privacy policy | Public policy route `/?policy=privacy`, linked from login without authentication | Verify the deployed URL in a logged-out browser. |
| RBAC | Owner financial access and restricted auditor role; disabled/expired users denied; policy changes revoke claims | Configure real identities, verify each role against production and review provider roles. |
| Consumer application MFA before Plaid Link | PIN or newly MFA-enrolled Touch ID followed by server-verified TOTP; durable replay/guessing protection | Owner enrolls VIP Access standard-authenticator account or another compatible app, saves server secrets and verifies real production login. Neither email recovery nor passkey memory bypasses MFA. |
| Retention/deletion policy | Public policy; opt-in retention; confirmed deletion/revocation UI; worker cleanup; generation/tombstone race defenses and readback receipts | Choose and enable the real retention period. Verify operation on synthetic data. Prior encrypted Git history/backups/exports require separate provider cleanup/key-retirement evidence or explicit Plaid acceptance; current-view deletion is not all-copy erasure. |
| Centralized identity/access management | One server-controlled app registry, per-user factors/roles/expiry, fail-closed policy revalidation | This does not centralize GitHub, hosting and Plaid in an enterprise identity provider. Verify actual provider directory integration or obtain Plaid acceptance of the single-owner arrangement. |

Public URLs after deployment:

- `https://terminal8.streamlit.app/?policy=privacy`
- `https://terminal8.streamlit.app/?policy=security`
- `https://terminal8.streamlit.app/?policy=retention`
- `https://terminal8.streamlit.app/?policy=vulnerability-management`

PIN reset delivery is enabled only after the owner configures a TLS mail account.
It sends only to the server-registered identity email. Links expire after fifteen
minutes, are consumed once, and reset only the PIN. The authenticator remains
mandatory; a successful reset revokes prior sessions and pending login stages.

See [MFA setup](MFA_SETUP.md), [security operations](OPERATIONS.md) and
[Networth setup](../../src/NETWORTH_SETUP.md). Record live activation and actual
attestation receipts here only after observing those outcomes.
