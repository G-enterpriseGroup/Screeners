# Networth activation

Networth is separate from E*TRADE. The main view is a three-column balance sheet: account, balance, and percentage of total assets. It lists Assets, Liabilities, Equity, and Total Liabilities & Equity. Equity is Assets minus Liabilities. If accounts are explicitly classified as Equity, they appear as allocations within that equity total, with the remainder shown as Unallocated equity. Brokerage accounts normally belong in Assets even though they hold stocks.

**Accounts & connections** contains Plaid linking, account nicknames and classifications. **Daily history & CSV** contains saved daily/intraday captures. Everything linked through Plaid is synced regardless of the selected accounts or whether an account is excluded from the visible statement.

## Streamlit Secrets

1. Open [Streamlit Community Cloud](https://share.streamlit.io/) and sign in to the account that owns Terminal8.
2. Open Terminal8's **Manage app → Settings → Secrets**. You can also use the app's menu in the Community Cloud workspace to open Settings.
3. Add or update one `[networth]` section below. Preserve your existing E*TRADE and other secret sections. Replace the placeholders in the secret editor, then save. The complete TOML block belongs in Streamlit Secrets, not GitHub source code. [Official Streamlit settings guide](https://docs.streamlit.io/deploy/streamlit-community-cloud/manage-your-app/app-settings).

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

Get `plaid_client_id` and the production `plaid_secret` from your Plaid Dashboard's API keys. Generate a separate Fernet key in a Python environment with cryptography installed using `python3 -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'`. Keep a backup outside the repository. Keep the original key and `ledger_id` stable: changing either selects a different encrypted ledger, and old connections cannot be restored without the originals. Use a fine-grained GitHub token with Contents read/write access to the **private** Portfolio repository. The store refuses public repositories and the default branch; it creates the dedicated data branch if necessary. Existing Acquisition and GEX settings are not reused. Never paste real keys into chat.

Choose the connection type for each institution: Bank / credit uses `transactions`, Investment uses `investments`, and Loans / liabilities uses `liabilities`. Each chosen product must be enabled for your production Plaid application. The Configured Plaid products choice uses `plaid_products`; its default is `transactions`. Balance itself cannot initialize Link. Networth uses `/accounts/balance/get` for the requested balance pulls, which Plaid bills separately. Plaid availability varies by institution/account type; the feature cannot connect unsupported accounts. Follow Plaid's production Link/OAuth configuration requirements in the Plaid Dashboard. Hosted Link handles the Plaid frontend in a separate window; Networth polls the stored Link token server-side for completion. [Plaid product initialization](https://plaid.com/docs/link/initializing-products/), [Hosted Link](https://plaid.com/docs/link/hosted-link/), [Balance](https://plaid.com/docs/balance/).

## GitHub Actions secrets

1. Open [Screeners Actions secrets](https://github.com/G-enterpriseGroup/Screeners/settings/secrets/actions), or go to **Screeners → Settings → Secrets and variables → Actions**.
2. On the **Secrets** tab, select **New repository secret** four times. Use each exact name below and paste its matching value from the Streamlit `[networth]` settings as the secret value. These are four separate secrets, not a TOML block. [Official GitHub instructions](https://docs.github.com/en/actions/how-tos/write-workflows/choose-what-workflows-do/use-secrets).

| Actions secret | Streamlit setting |
|---|---|
| `NETWORTH_PLAID_CLIENT_ID` | `plaid_client_id` |
| `NETWORTH_PLAID_SECRET` | `plaid_secret` |
| `NETWORTH_GITHUB_TOKEN` | `github_token` |
| `NETWORTH_ENCRYPTION_KEY` | `encryption_key` |

3. The defaults need no extra variables. If you change a default in Streamlit, open the **Variables** tab beside Secrets and select **New repository variable** for the matching setting: `NETWORTH_PLAID_ENVIRONMENT` (default `production`), `NETWORTH_REPOSITORY` (default `G-enterpriseGroup/Portfolio`), `NETWORTH_BRANCH` (default `networth-data`), or `NETWORTH_LEDGER_ID` (default `raj-terminal`). The environment/repository/branch/ledger must match Streamlit exactly. Use Sandbox credentials and `sandbox` in **both** places when testing; use a separate ledger for Sandbox.

The workflow `.github/workflows/networth-sync.yml` runs at **09:00, 12:00, 17:00 and 22:00 America/Indiana/Indianapolis**, including weekends, independently of Streamlit or an open browser. It becomes scheduled only after deployment to the repository's default branch and configuration of the secrets. The worker verifies encrypted writes before marking a slot complete; repeated successful slots are skipped. It preserves failed-bank balances with stale status and reports an incomplete run. It never backdates a missed pull or invents balances before the first capture.

GitHub schedules are best effort: jobs can be delayed or dropped under load, and schedules in public repositories can be disabled after 60 days without repository activity. Actual capture timestamps remain authoritative. If exact-minute execution becomes a requirement, run `python scripts/sync_networth.py` from a managed scheduler with this same timezone and secret environment. [GitHub schedule syntax](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax#onschedule), [schedule limitations](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule).

## First connection and verification

1. Reboot/redeploy through Streamlit Community Cloud after the production code and secrets are configured; verify `/_stcore/health` and the rendered Networth tab.
2. In Networth, open **Accounts & connections**, select a connection type, then **LINK ACCOUNTS WITH PLAID → OPEN PLAID TO CONNECT**. Complete the connection and authorize accounts in Plaid. Keep Networth open until completion is detected. Repeat for additional institutions. Successfully saved connections, all authorized accounts, cached balances, classifications, and nicknames restore after browser refreshes and app restarts; ordinary visits do not require linking again. A bank may later require fresh MFA or renewed consent; use **Manage linked institutions → RECONNECT**.
3. Use **Accounts to classify** to add/remove accounts from the selection, choose a classification, then **SAVE CLASSIFICATIONS**. Select exactly one account to enter an **Account nickname**, then **SAVE NICKNAME**; clear the nickname and save to restore the original bank name. Nicknames appear on current statements, history views and CSV exports without changing the account ID or its bank connection. The chooser initially selects accounts ending in 5474 when available and preserves explicit selections, including none, across visits. It selects accounts to edit; every linked account continues to sync. Choose **SYNC NOW** to record a balance sheet with new classifications.
4. Open Actions → **Networth scheduled balances**, run the workflow and verify success. A manually dispatched workflow records a Manual capture and does not satisfy a scheduled slot. Verify the next actual scheduled run as well; the tab's last scheduled capture is evidence of a completed scheduled worker pull, not a promise that future jobs will run.
5. Open **Daily history & CSV**, then choose the balance sheet date and capture to review/download it. The latest capture is selected first; earlier intraday captures and their original classifications remain saved. Current nicknames are display labels and do not rewrite saved balance/classification history. Disconnecting an institution stops future pulls and preserves saved history.

Current balances remain signed, missing values stay unavailable, investment balances use total current value rather than withdrawable cash, and currencies are never combined without exchange rates. Some institutions supply cached balances; their update timestamp is retained when provided. Historical captures record observations, not official bank statements or guaranteed closing balances. State and each day's captures are separate authenticated encrypted documents; no access tokens or account data enter workflow logs or browser storage.
