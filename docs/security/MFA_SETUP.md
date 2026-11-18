# Terminal MFA setup

The Terminal uses the existing numeric keypad followed by a six-digit, 30-second
authenticator code. The code is checked on the server. Touch ID cannot bypass
this second step. Real enrollment must be completed by the account owner; an
agent or CI job must not create or publish real credentials on the owner's behalf.

Use an authenticator that supports standard RFC 6238 TOTP with a manually entered
setup key or authenticator QR. The standard path needs no paid validation service.
Broadcom documents adding an external authenticator QR in **VIP Access mobile**
using the blue **+**. This suggests its additional-account mode can also scan the
Terminal's standard QR; actual compatibility remains pending your scan and code
verification. An existing VIP credential ID or its displayed code cannot become
the Terminal's `totp_secret`. Validating that separate VIP credential would need
Broadcom's supported validation service and service credentials.

## Enroll locally

Run this yourself in a private local terminal, from the repository directory:

```sh
python scripts/setup_terminal_mfa.py --email enterpriseghotra@gmail.com
```

The helper asks for a new 8-32 digit PIN without echoing it. It writes a private
file outside the source repository, with owner-only file permissions. Open that
file locally, enter `totp_secret` as the manual setup key in your authenticator,
then enter the current authenticator code when the helper asks. Nothing is sent
over the network, and the PIN, setup key and provisioning URI are never printed.
An unverified enrollment file is rejected by the application.

For VIP Access mobile's blue **+** scanner, first install the optional local-only
QR generator, then request a private SVG next to the enrollment file:

```sh
python -m pip install qrcode==8.2
python scripts/setup_terminal_mfa.py --email enterpriseghotra@gmail.com --qr
```

The helper generates the QR offline with owner-only file permissions. Open the
SVG locally, scan it with VIP Access mobile's blue **+**, then enter the generated
six-digit Terminal account code in the helper to verify enrollment. No push
approval is involved. The existing VIP credential remains separate. If the QR
package is unavailable, the helper keeps the manual-key enrollment option; use
an authenticator that supports manual entry or install the package before
enrolling to a new private filename. Never upload either file to a QR website.

Keep a protected authenticator backup or a second supported authenticator before
relying on it. Do not send the setup key, QR code, provisioning URI, PIN or recovery
material in chat, email, screenshots, source commits or a Plaid attachment. Never
use an online QR service to render the provisioning URI.

## Configure Streamlit Secrets

In the app owner's Streamlit Community Cloud settings, merge the generated
`[security]` entries into the existing section. Do not replace unrelated sections
or remove an existing `touch_id_memory_secret`. The configured PIN hash must be
explicit; the historical public-source fallback hash is rejected.

The helper writes **`login_pin_sha256`**, which changes only the MFA login PIN.
Preserve the existing **`trade_access_code_sha256`** exactly: older broker OAuth,
cache and private-preference encryption also depend on that legacy value.
Replacing it during MFA enrollment would strand those existing encrypted data.
If `login_pin_sha256` is absent, authentication accepts the existing explicitly
configured legacy PIN hash for compatibility; enrollment sets the separate key.

The required primary identity values are:

```toml
[security]
owner_email = "YOUR_OWNER_EMAIL"
require_mfa = true
login_pin_sha256 = "YOUR_EXPLICIT_PRIVATE_LOGIN_PIN_HASH"
totp_secret = "YOUR_PRIVATE_BASE32_SETUP_KEY"
totp_enrollment_verified = true
credential_version = 1
idle_timeout_seconds = 900
absolute_timeout_seconds = 28800
```

The displayed values are placeholders. Only the owner should enter actual
credentials in the secret editor. The setup helper has already verified the
actual authenticator before setting `totp_enrollment_verified = true`.

Authentication requires a durable, encrypted store in a verified private GitHub
repository. The following optional settings override configuration-value
fallbacks from the existing `[networth]` section:

```toml
# These keys belong inside the existing [security] section.
auth_github_token = "PRIVATE_REPOSITORY_CONTENTS_TOKEN"
auth_encryption_key = "STABLE_FERNET_KEY"
auth_repository = "G-enterpriseGroup/Portfolio"
auth_branch = "terminal-security-state"
```

The auth store derives a domain-separated encryption key and initializes an
independent orphan branch containing only `auth-state.enc`. It neither imports
the financial store nor reads financial documents. The branch must start with
`terminal-security-` and cannot be the repository default branch. The token needs
the repository Contents permission required for Git data and file writes. Do not
grant public-repository or organization-administration access for this feature.

Keep the store encryption key stable. A changed key must not silently create an
empty replay/lockout ledger: decryption failure keeps access locked. Key rotation
requires a verified migration. A storage outage also keeps login and protected
checks locked; browser/session-only replay protection is never substituted.

## Additional identities and revocation

The primary `owner` identity owns financial access. Additional identities can be
declared server-side with independent PINs, authenticator secrets and roles:

```toml
[security.users.review]
email = "REVIEWER_EMAIL"
pin_sha256 = "REVIEWER_PRIVATE_PIN_HASH"
totp_secret = "REVIEWER_PRIVATE_SETUP_KEY"
role = "auditor"
enabled = true
expires_at = "2027-01-31T23:59:59Z"
credential_version = 1
```

Only `owner` and `auditor` roles are accepted. An auditor has no financial-access
permission. Set `enabled = false`, remove an additional identity, or set its
timezone-aware `expires_at` to revoke access. For the primary identity, use
`owner_enabled` and `owner_expires_at`. Every protected check reloads the policy
and validates its fingerprint, so registry changes invalidate existing sessions.
Increment `credential_version` when rotating credentials or revoking all prior
credential-specific state. Changing the configured login PIN hash also supersedes an
older email-recovery PIN override.

Five failed PIN/TOTP attempts create a five-minute account-wide lockout. Correct
PIN entry does not reset the TOTP guessing budget. First-factor stages expire
after five minutes. Each accepted TOTP counter is consumed atomically in the
encrypted store before an MFA session is issued, and cannot be reused after a
new browser session or app reboot.

## Recovery and verification

Forgot-PIN email recovery, when separately configured, resets only the PIN and
does not disable or replace the authenticator requirement. A successful reset
uses a salted PBKDF2-SHA256 PIN hash and changes the durable credential revision,
revoking old sessions and any first-factor stage created before the reset.
Losing the authenticator requires a deliberate owner-admin recovery of the
server-side credential configuration, with credential-version rotation. Do not
add a PIN-only or email-only path to the financial workspace.

To enable email recovery, configure authenticated SMTP in the app's server-side
secrets. Use your provider's supported app password or SMTP credential and a
verified sender; never use an ordinary password in source code. Port 465 uses
implicit TLS; port 587 requires STARTTLS before credentials are sent.

```toml
[security.pin_recovery]
enabled = true
smtp_host = "YOUR_SMTP_PROVIDER_HOST"
smtp_port = 465
smtp_username = "YOUR_SMTP_ACCOUNT"
smtp_password = "YOUR_PRIVATE_SMTP_APP_PASSWORD"
sender = "YOUR_VERIFIED_SENDER_EMAIL"
public_base_url = "https://YOUR_PUBLIC_STREAMLIT_HOST"
```

The reset destination comes only from the server-side identity registry. The
primary owner's confirmed address is `enterpriseghotra@gmail.com`; set
`owner_email` accordingly. A form cannot choose an arbitrary recipient or redirect
host. The public app URL must be fixed HTTPS without a query or fragment. Reset
links expire after 15 minutes, are accepted once, and are stored only as token
digests in the encrypted authentication branch. Requests are limited to one per
minute and five per hour per identity. The page gives the same response for an
unknown identity, a throttled request or unavailable mail service.

After configuring your provider, request a real test reset yourself, verify the
sender and fixed destination URL, and confirm the old PIN and old sessions stop
working while a new PIN still needs a fresh authenticator code. Automated tests
mock SMTP; they do not send mail or establish live recovery delivery. Do not
attach reset links or SMTP credentials to a Plaid review.

After saving secrets, reboot through Streamlit Community Cloud and verify an
actual owner login: PIN alone remains locked; an invalid code is denied; a valid
code reaches the correct role; reusing that code is denied. Check a fresh browser
session, expiry/revocation, and the public policy pages without authenticating.
Keep the enrollment file protected and retain a separate credential backup.

The helper and automated tests do not establish live MFA. Plaid's MFA attestations
must remain pending until real production enrollment, configuration and a genuine
login have been verified. GitHub, Plaid Dashboard and hosting administrator MFA
must also be enabled in those services; the Terminal login does not change them.

Technical references: [RFC 6238](https://www.rfc-editor.org/rfc/rfc6238),
[PyOTP security checklist](https://pyauth.github.io/pyotp/), and
[NIST authentication guidance](https://pages.nist.gov/800-63-4/sp800-63b.html).
QR setup references: [Broadcom VIP Access mobile external-authenticator setup](https://knowledge.broadcom.com/external/article/385307)
and [qrcode's SVG documentation](https://pypi.org/project/qrcode/).
