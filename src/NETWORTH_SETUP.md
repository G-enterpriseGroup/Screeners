# Networth activation

Networth is separate from E*TRADE. It reads Plaid balances, saves dated balance sheets, and lets Raj classify each account as Asset, Liability, Equity, Excluded, or Unclassified. Net worth is Assets minus Liabilities; classified Equity is shown separately, with Assets minus Liabilities minus Equity as the reconciliation. Brokerage accounts normally belong in Assets even though they hold stocks.

## Streamlit Secrets

Add this dedicated section in the deployed app's secret settings. Replace the placeholders there; never commit real values or paste them into chat.

```toml
[networth]
plaid_client_id = "YOUR_CLIENT_ID"
plaid_secret = "YOUR_PRODUCTION_SECRET"
plaid_environment = "production"
plaid_products = ["transactions"]
github_token = "PRIVATE_PORTFOLIO_CONTENTS_WRITE_TOKEN"
encryption_key = "YOUR_FERNET_KEY"
repository = "G-enterpriseGroup/Portfolio"
branch = "networth-data"
ledger_id = "raj-terminal"
```

Generate a separate Fernet key locally with `python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'`. Keep a backup outside the repository. Losing the key loses access to the encrypted account tokens and history. Use a fine-grained GitHub token with Contents read/write access to the **private** Portfolio repository. The store refuses public repositories and the default branch; it creates the dedicated data branch if necessary. Existing Acquisition and GEX settings are not reused.

Choose the connection type for each institution: Bank / credit uses `transactions`, Investment uses `investments`, and Loans / liabilities uses `liabilities`. Each chosen product must be enabled for your production Plaid application. The Configured Plaid products choice uses `plaid_products`; its default is `transactions`. Balance itself cannot initialize Link. Networth uses `/accounts/balance/get` for the requested balance pulls, which Plaid bills separately. Plaid availability varies by institution/account type; the feature cannot connect unsupported accounts. Follow Plaid's production Link/OAuth configuration requirements in the Plaid Dashboard. Hosted Link handles the Plaid frontend in a separate window; Networth polls the stored Link token server-side for completion. [Plaid product initialization](https://plaid.com/docs/link/initializing-products/), [Hosted Link](https://plaid.com/docs/link/hosted-link/), [Balance](https://plaid.com/docs/balance/).

## GitHub Actions secrets

In the Screeners repository's Actions secrets, configure matching values:

| Actions secret | Streamlit setting |
|---|---|
| `NETWORTH_PLAID_CLIENT_ID` | `plaid_client_id` |
| `NETWORTH_PLAID_SECRET` | `plaid_secret` |
| `NETWORTH_GITHUB_TOKEN` | `github_token` |
| `NETWORTH_ENCRYPTION_KEY` | `encryption_key` |

Optional Actions **variables** are `NETWORTH_PLAID_ENVIRONMENT` (default `production`), `NETWORTH_REPOSITORY` (default `G-enterpriseGroup/Portfolio`), `NETWORTH_BRANCH` (default `networth-data`), and `NETWORTH_LEDGER_ID` (default `raj-terminal`). They must describe the same Plaid environment and ledger as Streamlit. Use Sandbox credentials and `sandbox` in **both** places when testing; do not mix environments in a ledger.

The workflow `.github/workflows/networth-sync.yml` runs at **09:00, 12:00, 17:00 and 22:00 America/Indiana/Indianapolis**, including weekends, independently of Streamlit or an open browser. It becomes scheduled only after deployment to the repository's default branch and configuration of the secrets. The worker verifies encrypted writes before marking a slot complete; repeated successful slots are skipped. It preserves failed-bank balances with stale status and reports an incomplete run. It never backdates a missed pull or invents balances before the first capture.

GitHub schedules are best effort: jobs can be delayed or dropped under load, and schedules in public repositories can be disabled after 60 days without repository activity. Actual capture timestamps remain authoritative. If exact-minute execution becomes a requirement, run `python scripts/sync_networth.py` from a managed scheduler with this same timezone and secret environment. [GitHub schedule syntax](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax#onschedule), [schedule limitations](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule).

## First connection and verification

1. Reboot/redeploy through Streamlit Community Cloud after the production code and secrets are configured; verify `/_stcore/health` and the rendered Networth tab.
2. Select **LINK ACCOUNTS WITH PLAID**, then **OPEN PLAID TO CONNECT**. Complete the bank connection in Plaid. Keep Networth open until completion is detected. Repeat for additional institutions. Plaid may require later reconnection; use **Manage linked institutions → RECONNECT**.
3. Review every account's classification. Use **Accounts to classify** to add/remove accounts from the selection, choose a classification, then **SAVE CLASSIFICATIONS**. The chooser initially selects accounts ending in 5474 when available and preserves explicit selections, including none, across visits. This selection edits classifications; every linked account continues to sync. Choose **SYNC BALANCES NOW** to record a balance sheet with the new classifications.
4. Open Actions → **Networth scheduled balances**, run the workflow and verify success. A manually dispatched workflow records a Manual capture and does not satisfy a scheduled slot. Verify the next actual scheduled run as well; the tab's last scheduled capture is evidence of a completed scheduled worker pull, not a promise that future jobs will run.
5. Choose the balance sheet date and capture to review/download it. The latest capture is the daily view; earlier intraday captures and their original classifications remain saved. Disconnecting an institution stops future pulls and preserves saved history.

Current balances remain signed, missing values stay unavailable, investment balances use total current value rather than withdrawable cash, and currencies are never combined without exchange rates. Some institutions supply cached balances; their update timestamp is retained when provided. Historical captures record observations, not official bank statements or guaranteed closing balances. State and each day's captures are separate authenticated encrypted documents; no access tokens or account data enter workflow logs or browser storage.
