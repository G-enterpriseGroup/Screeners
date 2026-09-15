# Raj's Terminal — Production Change Log

Append-only record of production fixes. Read this after `src/ARCHITECTURE.md` before editing an existing feature.

## 2026-09-15 — Architecture guard and feature ownership map

- **Feature changed:** Repository architecture / change discipline.
- **Exact production file(s) changed:** `src/ARCHITECTURE.md`, `scripts/validate_architecture.py`, `.github/workflows/architecture-guard.yml`, `src/production_manifest.py`, `src/gex_workspace_v2.py`, `src/risk_sizing_ui_v10.py`.
- **What was broken:** Fixes to one feature were repeatedly spilling into other features because production ownership was unclear and historical `v2/v4/v7/v9/v10` files made it easy to edit the wrong layer.
- **Root cause:** No single source of truth for production routes and no automated guard against module-level Streamlit monkey patches.
- **What was changed:** Added `src/ARCHITECTURE.md`, a production manifest, CI architecture guard, and explicit ownership notes in production feature files.
- **Important behavior that must remain:** Future fixes must start by reading `src/ARCHITECTURE.md` and this changelog, tracing the import path, then editing only the owning feature files.
- **Files/features intentionally NOT changed:** No feature logic was changed as part of the guard except removing a GEX-owned global caption patch.
- **Tests performed:** GitHub Actions `Terminal Architecture Guard` passed on main.
- **Architecture guard result:** PASS.
- **Commit SHA:** `6947a77`, `2913f48`.
- **Lesson:** Organization and guardrails are part of the product; do not stack quick UI patches that can affect unrelated tabs.

## 2026-09-15 — Navigation gap below terminal tab bar

- **Feature changed:** Top navigation / terminal tab bar spacing.
- **Exact production file(s) changed:** `src/tab_bar_v4.py`, `src/components/terminal_tabs_v3/index.html`, `src/TERMINAL_CHANGELOG.md`.
- **What was broken:** A large black blank area appeared between the top terminal tab bar and the active GEX workspace.
- **Root cause:** The custom Streamlit tab component visually rendered at about 48px, but the parent Streamlit component element could still reserve its larger default frame height under the tab bar. The first fix targeted iframe titles, but the live DOM could still miss that selector.
- **What was changed:** Scoped the navigation component frame to 48px and added a zero-height marker immediately before the tab component so CSS collapses the exact next Streamlit element container. The component itself also repeatedly reports its 48px height during reruns.
- **Important behavior that must remain:** The top tab bar should reserve only its visible height; do not use GEX/Risk/OAuth files to correct navigation whitespace.
- **Files/features intentionally NOT changed:** GEX content, Risk Sizing, OAuth, Holdings, Bull Debit, and municipal tools.
- **Tests performed:** Production navigation files re-fetched from `main`; GitHub Actions `Terminal Architecture Guard` run `34995163033` passed.
- **Architecture guard result:** PASS.
- **Commit SHA:** `2196fe5`, `745cfbd`, `208e604`, `d9364cd`.
- **Lesson:** For custom Streamlit components, target the parent component frame/container, not only the iframe title; title-based CSS may miss the live DOM. Always log terminal fixes in this changelog because project memory is disabled.
