# Raj's Terminal — Production Architecture

This file is the **first place to check before editing the terminal**.

The goal is simple: a change to one feature must not silently change another feature.

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
8. **All top-level terminal tabs use one shared page-header system owned by `streamlit_app.py`.** Holdings, Risk Sizing, Option Book, Bull Debit Spread, Muni Screeners, Orders, and GEX must use the same full-width orange title bar, compact subtitle spacing, typography, and left alignment. Do not add competing one-off top-level headers inside feature files; internal feature section headers remain feature-owned.

## Production feature map

| Feature | Production entry / owner | Supporting files | Do not edit for normal feature UI work |
|---|---|---|---|
| App routing / tab dispatch | `streamlit_app.py` | `src/tab_bar_v4.py` | `src/terminal_core.py` unless changing legacy shared core behavior |
| E*TRADE Risk Sizing production route | `src/risk_sizing_ui_v7.py` → `src/risk_sizing_ui_v10.py` | `src/risk_sizing_ui_v9.py`, `src/risk_sizing_ui_v2.py`, `src/ticker_autocomplete.py` | GEX, OAuth, Holdings, Schwab files |
| Schwab Risk Sizing shell / future broker route | `src/schwab_risk_sizing_ui.py` | `src/risk_sizing.py` formulas after Schwab API/holdings adapter is available | E*TRADE Risk Sizing, GEX, OAuth, Holdings files |
| Risk sizing formulas only | `src/risk_sizing.py` | `src/trade_math.py` | UI files unless the UI needs to display a new result |
| GEX terminal wrapper/context | `src/gex_workspace_v2.py` | `src/gex_ui_v3.py` | Risk/OAuth/Holdings files |
| GEX UI / subtabs / tables | `src/gex_ui_v3.py` | `src/gex_ui.py` only when legacy calculation helpers are intentionally reused | `streamlit_app.py` for ordinary GEX layout changes |
| Option Book options ticket | `src/option_book_ui.py` | `src/option_book.py`, `src/etrade_client.py` preview transport, `src/ticker_autocomplete.py` | Risk/GEX/Holdings/OAuth UI/legacy Orders simulator |
| E*TRADE OAuth connection UI | `src/etrade_connection_ui_v2.py` | `src/etrade_client.py`, `src/session_persistence.py` | Risk/GEX files |
| Holdings presentation | `src/holdings_snapshot_mode.py` | `src/stockanalysis_portfolio_v5.py` | Risk/GEX/OAuth files |
| Top navigation | `src/tab_bar_v4.py` | `src/components/terminal_tabs_v3/` | Feature content renderers |
| Bull debit spread UI | `src/bull_debit_ui.py` | `src/bull_debit_spread.py` | Risk/GEX files |
| Municipal tools | functions loaded from `src/terminal_core.py` + `src/muni_data.py` / `src/treasury_data.py` | muni/treasury data modules | Risk/GEX/OAuth files |
| Theme / shared appearance | `src/theme.py`, `src/layout_guardrails.py` | shared CSS helpers | Change only when the requested change is truly global |

## Risk Sizing edit map

E*TRADE Risk Sizing has several historical versions. The current production import path is:

`streamlit_app.py` → `src/risk_sizing_ui_v7.py` → `src/risk_sizing_ui_v10.py` → v9/v2 helpers

The visible top-tab label is **E*TRADE RISK SIZING**, while the stable internal route key remains `RISK SIZING` so saved tab order/state is preserved. Schwab is a separate route: `streamlit_app.py` → `src/schwab_risk_sizing_ui.py`.

Use this decision tree:

- Change **ticker autocomplete / auto quote / Part 2 fail-safe behavior** → `src/risk_sizing_ui_v10.py` or `src/ticker_autocomplete.py`.
- Change **compact card styling / Part 2 presentation inherited from v9** → `src/risk_sizing_ui_v9.py`.
- Change **existing Part 1 / Part 2 base widget sequence** → `src/risk_sizing_ui_v2.py`, only if a wrapper cannot safely solve it.
- Change **risk formulas** → `src/risk_sizing.py`.
- Change **Schwab Risk Sizing setup/readiness UI or future Schwab data binding** → `src/schwab_risk_sizing_ui.py` and future Schwab-specific API modules only.
- Keep Schwab holdings/session state separate from E*TRADE holdings/session state; never point the Schwab tab at E*TRADE data as a temporary shortcut.
- Do **not** edit GEX/OAuth/navigation to fix Risk Sizing content. Navigation may be edited only for the top-tab label/order itself.

## GEX edit map

Production path:

`streamlit_app.py` → `src/gex_workspace_v2.py` → `src/gex_ui_v3.py`

- Change **subtabs, tables, multi-ticker layout, settings, notes, TradingView presentation** → `src/gex_ui_v3.py`.
- Change **how GEX obtains the live E*TRADE client / vault key / session touch callback / non-sensitive login marker** → `src/gex_workspace_v2.py`.
- GEX auto-refresh-on-login is still GEX-owned. When a non-GEX top-level tab is active, `streamlit_app.py` may dispatch the zero-height `gex_workspace_v2.maybe_auto_refresh_on_login()` hook after authenticated E*TRADE context exists; the hook must not modify OAuth/login behavior or other tab content.
- Do not patch global Streamlit functions from GEX.

## Option Book edit map

Production path:

`streamlit_app.py` → `src/option_book_ui.py` → `src/option_book.py` + `src/etrade_client.py` broker preview transport

- Change **ticket layout, strategy controls, legs, quote presentation, local drafts** → `src/option_book_ui.py`.
- Change **net debit/credit math, option-chain normalization, PreviewOrderRequest construction** → `src/option_book.py`.
- Change **authenticated E*TRADE Preview Order POST transport** → `src/etrade_client.py`; keep OAuth UI changes in `src/etrade_connection_ui_v2.py`.
- Reuse `src/ticker_autocomplete.py` for ticker/company-name search; do not create a second symbol directory.
- The public E*TRADE Order API documents Preview and Place endpoints but not a Power E*TRADE Saved Orders endpoint. Option Book therefore stores drafts locally and may broker-preview them, but must not map a Save button to live Place Order submission.
- Preserve single-leg LIMIT/STOP/STOP_LIMIT/MARKET and multi-leg NET_DEBIT/NET_CREDIT/MARKET behavior. E*TRADE rejects multi-leg stop/stop-limit orders.
- Do not modify the legacy `ORDERS` OCO simulator when changing Option Book.

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

## Top navigation edit map

Production path:

`streamlit_app.py` → `src/tab_bar_v4.py` → `src/components/terminal_tabs_v3/index.html`

- Change **top-tab width/spacing/frame height/navigation appearance** → `src/tab_bar_v4.py` and/or `src/components/terminal_tabs_v3/index.html` only.
- The top navigation component is intentionally **48px tall**. Its Streamlit iframe/container must not reserve additional blank height beneath it.
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
