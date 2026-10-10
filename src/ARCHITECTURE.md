# Raj's Terminal — Production Architecture

This file is the **first place to check before editing the terminal**.

The goal is simple: a change to one feature must not silently change another feature.

## Mandatory pre-edit gate

Before any coding or repository edit:

1. Read the root `AGENTS.md` completely.
2. Read this `src/ARCHITECTURE.md`.
3. Read the relevant feature history in `src/TERMINAL_CHANGELOG.md`.
4. Trace `streamlit_app.py -> feature route -> production owner file`.
5. Establish an explicit file whitelist for the task.
6. Do not edit files outside that whitelist unless a proven production dependency requires it.

`AGENTS.md` is the repository-wide change-isolation contract. These architecture rules refine it for Raj's Terminal. If two rules differ, follow the more restrictive scope-preservation rule.

## Golden rules

1. **Edit the feature owner file, not `terminal_core.py`, for feature UI changes.**
2. **Do not monkey-patch Streamlit globally at import time** (`st.button = ...`, `st.columns = ...`, `st.caption = ...`, etc.).
3. If a temporary Streamlit wrapper is absolutely required, install it only inside the feature render function and restore it in `finally`.
4. **Risk math and Risk UI are different ownership areas.** Never change sizing formulas when fixing appearance/search controls.
5. **GEX UI and GEX terminal-context plumbing are different ownership areas.**
6. Historical `*_v2.py`, `*_v4.py`, etc. files are not automatically production. Follow the production map below.
7. Before merging a feature change, run `python scripts/validate_architecture.py`.

## Standing UI rules

These are durable terminal preferences and should be checked on every UI change:

1. **Do not waste vertical space.** Controls/cards/components should reserve only the height required by their visible content.
2. **Remove redundant blank space** between navigation and active content, between controls, and inside cards.
3. **Keep feature fixes isolated.** A GEX request should not modify Risk Sizing, OAuth, Holdings, or navigation unless the production map explicitly identifies a shared dependency.
4. **Do not fix one feature by globally patching Streamlit.** Scope CSS and widget wrappers to the owning feature/component.
5. **Retest neighboring top-level tabs after any UI change** so a feature-specific improvement does not regress another tab.
6. For custom components, explicitly control iframe/component height when the visible UI is compact; do not rely on Streamlit's larger default frame height.
7. **Every editable text field must keep a clearly visible native blinking insertion caret.** Shared caret styling belongs in `src/theme.py`; do not fake the typing cursor with JavaScript or feature-specific pseudo-elements.
8. **All top-level terminal tabs use one shared page-header system owned by `streamlit_app.py`.** Holdings, Performance, Risk Sizing, Option Book, Bull Debit Spread, Muni Screeners, Rebalance Portfolio, Orders, and GEX must use the same full-width orange title bar, compact subtitle spacing, typography, and left alignment. Do not add competing one-off top-level headers inside feature files; internal feature section headers remain feature-owned.
9. **Session-scoped CSS must be emitted on every Streamlit render/session.** Do not use process-global "CSS already installed" flags for page/header styles; a later browser session may otherwise receive the markup without the stylesheet.

## Production feature map

| Feature | Production entry / owner | Supporting files | Do not edit for normal feature UI work |
|---|---|---|---|
| App routing / tab dispatch | `streamlit_app.py` | `src/tab_bar_v4.py` | `src/terminal_core.py` unless changing legacy shared core behavior |
| E*TRADE Risk Sizing production route | `src/risk_sizing_ui_v7.py` → `src/risk_sizing_ui_v10.py` | `src/risk_sizing_ui_v9.py`, `src/risk_sizing_ui_v2.py`, `src/ticker_autocomplete.py` | GEX, OAuth, Holdings, Schwab files |
| Vertical Options live order ticket | `src/vertical_options_ui.py` | `src/vertical_options.py`, `src/yfinance_options.py` public chain fallback, read-only option-chain normalization from `src/option_book.py`, existing `src/etrade_client.py` Preview/Place transport | Risk Sizing, Option Book UI, OAuth, GEX, Holdings |
| Protective Puts read-only analytics | `src/protective_puts_ui.py` | `src/protective_puts.py`, `src/protective_puts_sources.py`, read-only option-chain normalization from `src/option_book.py`, existing `src/etrade_client.py` quote/chain transport | Risk Sizing, Vertical Options ordering, OAuth, GEX, Holdings |
| Schwab Risk Sizing shell / future broker route | `src/schwab_risk_sizing_ui.py` | `src/risk_sizing.py` formulas after Schwab API/holdings adapter is available | E*TRADE Risk Sizing, GEX, OAuth, Holdings files |
| Risk sizing formulas only | `src/risk_sizing.py` | `src/trade_math.py` | UI files unless the UI needs to display a new result |
| GEX terminal wrapper/context | `src/gex_workspace_v2.py` | `src/gex_ui_v3.py`, `src/yfinance_options.py` public chain fallback when E*TRADE is not live | Risk/OAuth/Holdings files |
| Historical HEATMAPS | `streamlit_app.py` → `src/heatmaps_ui.py` | Existing `src/components/risk_book_state_v1/` browser-only state transport; yfinance historical adjusted-close data | GEX, Risk, OAuth, navigation appearance, shared theme |
| GEX UI / subtabs / tables | `src/gex_ui_v3.py` | `src/gex_ui.py` for E*TRADE GEX formulas/IV calculations; `src/gex_cboe.py` for isolated CBOE delayed-source GEX calculations; `src/gex_realized_vol.py` for GEX-only historical-volatility data/math | `streamlit_app.py` for ordinary GEX layout changes |
| Option Book options ticket | `src/option_book_ui.py` | `src/option_book.py`, `src/yfinance_options.py` public chain fallback, `src/etrade_client.py` preview transport, `src/ticker_autocomplete.py` | Risk/GEX/Holdings/OAuth UI/legacy Orders simulator |
| E*TRADE OAuth connection UI | `src/etrade_connection_ui_v2.py` | `src/etrade_client.py`, `src/session_persistence.py` | Risk/GEX files |
| Terminal access / Touch ID lock | `src/lock_screen_v2.py` | `src/passkey_auth.py`, `src/components/lock_keypad_v2/` | Broker/Risk/GEX logic |
| Commit-triggered fresh-process reboot | `src/reboot_guard.py` | `streamlit_app.py` invokes the guard before app routing | Feature renderers |
| Holdings presentation | `src/holdings_snapshot_mode.py` | `src/stockanalysis_portfolio_v5.py` | Risk/GEX/OAuth files |
| E*TRADE Performance | `src/performance_ui.py` | `src/etrade_client.py` read-only transaction/portfolio/balance transport | Risk/GEX/OAuth/order files |
| Top navigation | `src/tab_bar_v4.py` | `src/components/terminal_tabs_v3/` | Feature content renderers |
| Rebalance Portfolio | `src/rebalance_portfolio_ui.py` | `src/risk_sizing_ui.py`, `src/risk_sizing_ui_v2.py`, `src/risk_sizing.py` for shared read-only Risk book normalization/classification/intent memory | `streamlit_app.py`, Risk live-order workflow, OAuth/session UI, Holdings/GEX/Performance |
| Bull debit spread UI | `src/bull_debit_ui.py` | `src/bull_debit_spread.py`, `src/yfinance_options.py` public chain fallback | Risk/GEX files |
| Municipal tools | functions loaded from `src/terminal_core.py` + `src/muni_data.py` / `src/treasury_data.py` | muni/treasury data modules | Risk/GEX/OAuth files |
| Theme / shared appearance | `src/theme.py`, `src/layout_guardrails.py` | shared CSS helpers | Change only when the requested change is truly global |

## Risk Sizing edit map

E*TRADE Risk Sizing has several historical versions. The current production import path is:

`streamlit_app.py` → `src/risk_sizing_ui_v7.py` → `src/risk_sizing_ui_v10.py` → v9/v2 helpers

The visible top-tab label is **E*TRADE RISK SIZING**, while the stable internal route key remains `RISK SIZING` so saved tab order/state is preserved. Schwab is a separate route: `streamlit_app.py` → `src/schwab_risk_sizing_ui.py`.

Use this decision tree:

- Change **Risk Part 2 ticker entry, separate Company / ETF display, auto quote, quote fail-safe behavior, or the reviewed live stock entry/protective-stop workflow** → `src/risk_sizing_ui_v10.py`.
- Change **authenticated Risk live-order Preview/Place/List/Cancel transport** → `src/etrade_client.py`. Option Book may reuse Preview transport but must remain unable to call live Place Order or Cancel Order.
- Change **Risk automatic fill monitoring, persistent protection-watch log, ready-to-send handoff, or browser restoration of an armed entry** → `src/risk_sizing_ui_v10.py`. The read-only watcher is invoked from the existing hidden `terminal_background_hooks` container in `streamlit_app.py` so it can keep checking while another top-level tab is active.
- **Critical broker constraint:** the background watcher may call E*TRADE `List Orders` only. It must never preview, place, change, or cancel an order. E*TRADE's developer terms prohibit Algorithmic / Automated Order Generation without a discrete, contemporaneous affirmative instruction for the specific order. A confirmed full fill may change the local state to `READY_TO_SEND`, but the protective stop still requires a live user click that rechecks the fill, previews the exact stop, and places it.
- Change **shared ticker/company directory data or Option Book autocomplete behavior** → `src/ticker_autocomplete.py`; E*TRADE Risk Sizing no longer uses the combined `SYMBOL — COMPANY NAME` selector.
- Change **compact card styling / Part 2 presentation inherited from v9** → `src/risk_sizing_ui_v9.py`.
- Change **existing Part 1 / Part 2 base widget sequence** → `src/risk_sizing_ui_v2.py`, only if a wrapper cannot safely solve it.
- Change **risk formulas** → `src/risk_sizing.py`.
- Change **Schwab Risk Sizing setup/readiness UI or future Schwab data binding** → `src/schwab_risk_sizing_ui.py` and future Schwab-specific API modules only.
- Keep Schwab holdings/session state separate from E*TRADE holdings/session state; never point the Schwab tab at E*TRADE data as a temporary shortcut.
- Do **not** edit GEX/OAuth/navigation to fix Risk Sizing content. Navigation may be edited only for the top-tab label/order itself.

## Shared option-chain fallback

Production options-market rule:

- **E*TRADE is primary whenever the connector is live.**
- When E*TRADE is not live, Bull Debit Spread, Vertical Options, Option Book, and GEX use `src/yfinance_options.py` for public quote/expiration/option-chain data.
- The adapter normalizes yfinance into the existing E*TRADE-style payload contract so feature math/parsers do not fork by source. For GEX, yfinance IV is retained and Black-Scholes gamma/vega are calculated in the adapter because public yfinance chains do not provide E*TRADE OptionGreeks.
- Public fallback is **market-data only**. It must never enable E*TRADE preview/place/change/cancel actions; Vertical Options and Option Book keep broker actions disabled until the live connector returns.
- Protective Puts already uses yfinance first when E*TRADE is not live and retains its isolated Yahoo HTML fallback if yfinance itself fails.

## Vertical Options edit map

Production path:

`streamlit_app.py` → `src/vertical_options_ui.py` → `src/vertical_options.py` + existing `src/etrade_client.py` broker transport

- Change **ticker/type/expiration/strike/quantity/limit/account controls, compact layout, quote presentation, multi-account review/send interaction** → `src/vertical_options_ui.py`.
- Change **canonical call/put debit-vertical validation or E*TRADE spread Preview/Place payload construction** → `src/vertical_options.py`.
- Vertical Options is exactly two legs, same expiration/type/quantity: CALL debit = BUY lower strike + SELL higher strike; PUT debit = BUY higher strike + SELL lower strike.
- Multi-account placement must use the same reviewed fingerprint for every selected account. Every account must preview successfully before the live SEND control is enabled.
- The live order action must remain a discrete, contemporaneous user click. Do not add background or automatic option-order submission.
- Preserve fragment-scoped reruns for ticket edits; do not globally patch Streamlit or edit Risk Sizing to achieve live updates.
- Reuse existing E*TRADE OAuth/session and `ETradeClient.preview_order/place_order` transport without changing OAuth UI.
- Do not modify Option Book behavior; its existing UI remains disabled/preview-only unless separately requested.

## Protective Puts edit map

Production path:

`streamlit_app.py` → `src/protective_puts_ui.py` → `src/protective_puts.py` + read-only E*TRADE quote/chain transport

- Change **ticker/purchase price/shares/expiration scope, compact layout, scan progress, table, or CSV export** → `src/protective_puts_ui.py`.
- Change **protective-put premium cost, breakeven, strike floor, max-loss math, or normalized analysis columns** → `src/protective_puts.py`.
- Reuse `src/option_book.py::extract_option_rows` for read-only E*TRADE option-chain normalization; do not duplicate broker payload parsing.
- When E*TRADE is not live, public fallback belongs only in `src/protective_puts_sources.py`: try yfinance first, then direct Yahoo Finance HTML scraping. Never route this fallback through OAuth or shared broker files.
- Protective Puts is analytics-only. It must not preview, place, change, or cancel orders.
- Preserve full coverage as one put contract per 100 shares; the production UI accepts only 100-share lots.
- ASK is the conservative put-purchase reference. LAST is retained only to match the legacy Married Put analysis as a historical trade reference.
- Keep Protective Puts styling local to `src/protective_puts_ui.py`; do not edit Risk Sizing or the shared theme to imitate its appearance.

## GEX edit map

Production path:

`streamlit_app.py` → `src/gex_workspace_v2.py` → `src/gex_ui_v3.py`

- Change **subtabs, tables, multi-ticker layout, settings, notes, TradingView presentation** → `src/gex_ui_v3.py`.
- Overview DTE values open the GEX-owned numeric editor. Save snaps to a nonexpired E*TRADE expiration (ties earlier), refreshes only that ticker, and persists the exact integer through `src/gex_ui.py` browser/vault state. Preserve nonpreset values and 0DTE; do not restrict them to the legacy menu. `src/gex_ui_v3_base.py` Settings must display the saved exact override.
- Shared page headings use `st.html`, not raw HTML inside `st.markdown`: Markdown's trailing negative margin can under-measure a title/subtitle block and overlap the first feature row.
- Change **GEX IV Rank / IV-HV formulas and E*TRADE option-IV aggregation** → `src/gex_ui.py`.
- Change **CBOE delayed option-chain retrieval / CBOE-only GEX normalization** → `src/gex_cboe.py`; keep it isolated from E*TRADE session plumbing.
- Change **E*TRADE vs CBOE Overview tabs, saved MASTER A6 source selection, or source-specific TradingView presentation** → `src/gex_ui_v3.py` (with the stable source-overview slot in `src/gex_ui_v3_base.py`).
- Change **GEX-only adjusted-close retrieval or 30-day historical-volatility math** → `src/gex_realized_vol.py`.
- Change **how GEX obtains the live E*TRADE client / vault key / session touch callback / non-sensitive login marker, or loads the optional GEX historical-data token** → `src/gex_workspace_v2.py`.
- GEX auto-refresh-on-login is still GEX-owned. When a non-GEX top-level tab is active, `streamlit_app.py` may dispatch the zero-height `gex_workspace_v2.maybe_auto_refresh_on_login()` hook after authenticated E*TRADE context exists; the hook must not modify OAuth/login behavior or other tab content.
- Do not patch global Streamlit functions from GEX.

## Option Book edit map

Production path:

`streamlit_app.py` → `src/option_book_ui.py` → `src/option_book.py` + `src/etrade_client.py` broker preview transport

- Change **ticket layout, strategy controls, legs, quote presentation, local drafts** → `src/option_book_ui.py`.
- **Visual reference:** E*TRADE Risk Sizing is the canonical Option Book UI reference. Match its orange-filled internal section bars, Courier New typography, 38px control height, 52px metric-card height, 4px vertical rhythm, 8px horizontal rhythm, black surfaces, orange borders, and green/red/blue financial meaning. Copy the visual contract into Option Book locally; do not edit Risk Sizing to style Option Book.
- Change **net debit/credit math, option-chain normalization, PreviewOrderRequest construction** → `src/option_book.py`.
- Change **authenticated E*TRADE order transport** → `src/etrade_client.py`; Option Book is preview-only and must never call `place_order` or `cancel_order`, while Risk Sizing may call Preview/Place/List/Cancel only through explicit user-authorized live-order controls. Keep OAuth UI changes in `src/etrade_connection_ui_v2.py`.
- Reuse `src/ticker_autocomplete.py` for ticker/company-name search; do not create a second symbol directory.
- The public E*TRADE Order API documents Preview and Place endpoints but not a Power E*TRADE Saved Orders endpoint. Option Book therefore stores drafts locally and may broker-preview them, but must not map a Save button to live Place Order submission.
- Preserve single-leg LIMIT/STOP/STOP_LIMIT/MARKET and multi-leg NET_DEBIT/NET_CREDIT/MARKET behavior. E*TRADE rejects multi-leg stop/stop-limit orders.
- Do not modify the legacy `ORDERS` OCO simulator when changing Option Book.

## Terminal access / Touch ID edit map

Production lock path:

`streamlit_app.py` → `src/lock_screen_v2.py` → `src/passkey_auth.py` + `src/components/lock_keypad_v2/index.html`

- Change **lock keypad / Touch ID interaction** → `src/lock_screen_v2.py` and the v2 keypad component only.
- Change **WebAuthn verification or reboot-safe credential-record persistence** → `src/passkey_auth.py`.
- The app must never store or receive a raw fingerprint/biometric template or passkey private key. Only the public WebAuthn verification record may be persisted.
- The reboot-safe browser record must remain integrity-protected by the server-derived HMAC before it is trusted after a process/container restart.
- Touch ID enrollment is **browser/Mac scoped**. Never treat the process-global server credential cache as proof that a fresh browser owns another Mac's passkey. A browser must restore its own sealed record or enroll a new platform passkey after the access-code check.
- Multiple Macs/browsers may each enroll their own platform credential for the same terminal identity. Re-enrolling one Mac must not make another Mac's browser appear enrolled or force it into a cross-device/security-key chooser.
- Do not use the old lock module's OAuth/title wrappers when changing Touch ID; production OAuth remains owned by `src/etrade_connection_ui_v2.py`.

## Production reboot edit map

Production path:

`streamlit_app.py` → `src/reboot_guard.py`

- For non-managed/local hosts, preserve the commit-change guard so a Git revision change cannot silently reuse stale interpreter state.
- On Streamlit Community Cloud (`/mount/src/...`), do **not** call `os._exit` or otherwise hard-kill the managed server from app code. Community Cloud owns deploy/reboot lifecycle; self-termination can surface the browser-level `Oh no. Error running app.` page.
- After production pushes, verify both the health endpoint and a real rendered browser page; a healthy `/_stcore/health` endpoint alone does not prove the frontend avoided the `Oh no` state.
- Do not put feature behavior or broker/session logic in the reboot guard.

## OAuth edit map

Production path:

`streamlit_app.py` → `src/etrade_connection_ui_v2.py`

- Change **OAuth panel height, code field, Verify button, connection strip layout** → `src/etrade_connection_ui_v2.py` only.
- Change **API request/signature/token behavior** → `src/etrade_client.py`.
- Change **saved-session behavior** → `src/session_persistence.py`.

## Holdings edit map

- Change Holdings UI → `src/holdings_snapshot_mode.py`.
- Change classification panels → `src/stockanalysis_portfolio_v5.py` and its cache helpers.
- Do not change Risk Sizing or GEX to fix Holdings.

## Performance edit map

Production path:

`streamlit_app.py` → `src/performance_ui.py` → `src/etrade_client.py` (read-only account/transaction data)

- Change **Performance table, live line chart, metric formulas, account picker, refresh behavior, or coverage messaging** → `src/performance_ui.py`.
- Change **read-only E*TRADE transaction HTTP transport/pagination** → `src/etrade_client.py`.
- Performance is read-only and must never call order preview/place/cancel methods.
- E*TRADE transaction history is a rolling API window. Never label unavailable pre-window history as complete ITD; show the earliest loaded transaction and keep ALL-TIME explicitly coverage-aware until older history is seeded.
- The 30-second live fragment refreshes current portfolio/balance data only while Performance is active. Historical transactions are session-cached until the user selects REFRESH PERFORMANCE.
- Do not change Risk Sizing, GEX, Holdings, OAuth, or shared theme files for Performance-only work.

## Rebalance Portfolio edit map

Production path:

`streamlit_app.py` → `src/rebalance_portfolio_ui.py`

- Change **target weights, per-position drift bands, loss-review threshold, hard concentration limit, minimum-trade rule, cash-first funding, plan table, or Rebalance-only layout** → `src/rebalance_portfolio_ui.py`.
- Rebalance intentionally shares Risk Sizing's `risk_sizing_account` selector state, holdings normalization, account balance source, and persisted LONG-TERM intent memory so both workspaces describe the same E*TRADE book. This is a read-only dependency; do not edit Risk Sizing to change Rebalance behavior.
- Rebalance v1 is long-only and analysis-only. It may read accounts, portfolio, balances, and Risk intent state, but it must never preview, place, change, or cancel an E*TRADE order.
- Targets/bands are browser-persisted per hashed account key. New positions seed their target to the current account weight; this intentionally produces no trade until Raj defines a desired target.
- `TO BAND` is the default low-turnover mode: trade only after a band breach and move just inside the band. `TO TARGET` is optional and intentionally higher turnover.
- Underweights at or below the configured loss-review threshold must be labeled `REVIEW LOSS` and receive no automatic ADD proposal.
- LONG-TERM overweights use the wider saved/default band and remain explicit review items; do not silently convert Risk LONG-TERM classifications into routine tactical trims.
- Excess cash above the target cash allocation funds underweights first; required trim proceeds may fund remaining eligible buys. Trades below the configured minimum dollar threshold are suppressed.
- Do not change OAuth/session UI, Risk live-order logic, Holdings, GEX, Performance, navigation, or shared theme files for Rebalance-only work.

## Historical Heatmaps edit map

Production path: `streamlit_app.py` → `src/heatmaps_ui.py`.

- `src/heatmaps_ui.py` owns sector default symbols, custom watchlist editing, historical adjusted-close download, return math, heatmap styling, CSV exports, and browser-persisted settings.
- Historical market-data pulls use Yahoo/yfinance `period="max"` and adjusted daily closing prices. A fund's *first available* observation is not assumed to equal its official inception date; no pre-inception cells are fabricated. Monthly and annual calendar returns compound the observed daily adjusted-close returns, including partial first/current periods.
- Memory uses the existing zero-height browser state component with a feature-specific localStorage key and four rolling backups. Browser hydration must complete before a new Streamlit process writes anything, to prevent reboot defaults erasing saved selections. The watchlist is browser-profile-specific; do not assume cloud cross-device synchronization.
- Navigation changes only add the stable `HEATMAPS` tab key, its shared header, and the render dispatch. Keep E*TRADE OAuth and order placement completely out of Heatmaps.
- Do not edit `src/theme.py`, GEX, Risk Sizing, OAuth, Holdings, or `src/terminal_core.py` to change Heatmaps content.

## Top navigation edit map

Production path:

`streamlit_app.py` → `src/tab_bar_v4.py` → `src/components/terminal_tabs_v3/index.html`

- Change **top-tab width/spacing/frame height/navigation appearance** → `src/tab_bar_v4.py` and/or `src/components/terminal_tabs_v3/index.html` only.
- The top navigation component is intentionally **48px tall**. Its keyed `terminal_navigation` container and frame CSS are emitted on every render, never only at import time.
- `streamlit_app.py::_terminal_tab_layout` owns the reusable `terminal_tab_shell`: navigation → shared page heading → feature content, with a fixed 4px shell gap. Background state hooks belong in the nonvisual `terminal_background_hooks` container inside content, never between navigation and heading.
- Add a tab key in `DEFAULT_TAB_ORDER`, its title/subtitle in `_TERMINAL_PAGE_HEADERS`, and its dispatch inside `_terminal_tab_layout`. New tabs inherit the same shell automatically. Do not add page-level spacer rows or negative margins; feature-owned sections remain inside `terminal_page_content`.
- The iframe reports its height through Streamlit messages only. Never mutate parent wrappers from component JavaScript.
- Do not edit GEX/Risk/OAuth feature files to correct whitespace caused by the top navigation component.

## Shared-code warning

`src/terminal_core.py` is a large legacy definition source loaded by `streamlit_app.py`. Treat it as **shared infrastructure**. A feature-specific visual request should almost never require editing it.

If a requested change appears to require `terminal_core.py`, first verify that the feature cannot be handled in its production owner module above.

## Required change discipline

For every future change:

1. Identify the feature in the production map.
2. Touch the smallest possible owner file set.
3. Do not introduce a global Streamlit assignment.
4. Run the architecture validator.
5. Re-test the feature changed **and** confirm the other top-level tabs still render.
