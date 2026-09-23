# Raj's Terminal — Mandatory Coding Guardrails

These instructions apply to every coding agent and every change in this repository.

## PRE-EDIT GATE — READ BEFORE CODING

Before editing any file, you MUST:

1. Read this `AGENTS.md` completely.
2. Read `src/ARCHITECTURE.md`.
3. Read the relevant history in `src/TERMINAL_CHANGELOG.md`.
4. Trace the actual production import path from `streamlit_app.py` to the requested feature.
5. Identify the feature that owns the requested change.
6. Create an explicit internal FILE WHITELIST containing only the files that are necessary for the request.

Do not start editing until those six steps are complete.

If the production owner is unclear, stop editing and trace:
`streamlit_app.py -> feature route -> production owner file`.

Historical files such as `*_v2.py`, `*_v7.py`, `*_v10.py`, etc. are not automatically production. Never assume the highest version number is live.

## 1. ONLY CHANGE WHAT RAJ ASKED FOR

Make the smallest possible change required to complete the request.

Do NOT make unrelated "helpful" improvements.

Unless Raj explicitly requested them, do not change:
- themes
- colors
- fonts
- typography
- CSS
- margins
- padding
- spacing
- sizing
- alignment
- layouts
- table appearance
- calculations
- formulas
- navigation
- authentication
- OAuth/session behavior
- unrelated features
- unrelated comments or documentation
- variable/function names
- import ordering
- formatting of unrelated code

If something unrelated looks imperfect, leave it alone.

## 2. FILE WHITELIST IS MANDATORY

Before editing, determine the exact production files that own the requested feature.

Only files on the task's file whitelist may be changed.

If a possible solution requires another file:
1. prove that file is a real production dependency;
2. confirm there is no owner-file solution;
3. add only that dependency to the whitelist;
4. make the minimum change there.

Convenience is not a valid reason to expand scope.

## 3. PROTECTED FILES

Treat these as protected unless Raj explicitly requests a change to them or a proven shared dependency makes the edit unavoidable:

- `src/theme.py`
- `src/terminal_core.py`
- `.streamlit/config.toml`
- `streamlit_app.py`
- top-navigation files
- authentication files
- OAuth/session files
- shared layout/theme infrastructure

A local feature problem must receive a local feature fix.

Never modify a protected file merely because shared CSS or a global wrapper would be easier.

## 4. FEATURE OWNERSHIP

Use `src/ARCHITECTURE.md` as the source of truth. Current high-level ownership includes:

- Risk Sizing UI / interaction: `src/risk_sizing_ui_v10.py` plus production helper files identified by the architecture map
- Risk Sizing formulas/math: `src/risk_sizing.py`, `src/trade_math.py`
- Risk ticker search/autocomplete: `src/ticker_autocomplete.py`
- GEX layout/tables/subtabs: `src/gex_ui_v3.py`
- GEX E*TRADE/session plumbing: `src/gex_workspace_v2.py`
- E*TRADE OAuth/login UI: `src/etrade_connection_ui_v2.py`
- Holdings: `src/holdings_snapshot_mode.py`
- Top navigation/tabs: `src/tab_bar_v4.py`, `src/components/terminal_tabs_v3/index.html`
- Bull Debit Spread: `src/bull_debit_ui.py`, `src/bull_debit_spread.py`
- Shared legacy infrastructure: `src/terminal_core.py`

If Raj asks to fix GEX, do not modify Risk Sizing, OAuth, Holdings, navigation, or another feature unless there is a proven shared dependency.

If Raj asks to fix Risk Sizing, stay inside Risk Sizing ownership.

Apply the same isolation rule to every feature.

## 5. NO THEME CHANGES UNLESS EXPLICITLY REQUESTED

Never alter global appearance as a side effect of a feature change.

Do not change:
- shared/global CSS
- `src/theme.py`
- Streamlit theme configuration
- `.streamlit/config.toml`
- shared typography
- shared colors
- Bloomberg orange/black styling
- global table styling
- global button styling
- shared spacing rules

unless Raj explicitly asked for a global theme/appearance change.

When a UI change is requested for one feature, scope the styling to that feature only.

## 6. NO GLOBAL STREAMLIT MONKEY-PATCHES

Never globally replace Streamlit functions at module import time, including:

- `st.button`
- `st.columns`
- `st.caption`
- `st.selectbox`
- `st.text_input`
- `st.number_input`
- `st.markdown`
- `st.dataframe`
- `st.checkbox`

If a temporary wrapper is genuinely unavoidable:
- install it only inside the owning feature's render function;
- restore the original in a `finally` block.

## 7. PRESERVE UNRELATED CODE

Use the smallest patch possible.

Do not rewrite an entire file when a local patch will solve the request.

Avoid formatters or automated rewrites that modify unrelated lines.

Do not:
- reorder unrelated imports;
- rename unrelated variables/functions;
- re-indent unrelated blocks;
- rewrite comments that are outside scope;
- refactor working code while fixing another issue.

Preserve unrelated behavior and presentation byte-for-byte where practical.

## 8. COMPACT UI RULE

Raj prefers efficient screen space.

For requested UI work:
- remove unnecessary blank vertical space;
- size cards to their actual content;
- keep related controls on the same row when practical;
- avoid unnecessary horizontal scrolling;
- make tables fit available width where reasonable;
- keep spacing aligned and consistent.

Do not make a UI element taller or wider than necessary.

This rule does NOT authorize unsolicited UI changes.

## 9. DIFF AUDIT — EVERY CHANGED LINE MUST BE JUSTIFIED

Before committing, inspect the complete diff.

For every changed line, ask:

> Did Raj's request require this line to change?

If the answer is no, revert it.

Reject your own patch before commit if it contains:
- unrelated theme changes;
- unrelated CSS changes;
- unrelated layout changes;
- unrelated feature edits;
- cleanup/refactoring not required by the request;
- formatting churn;
- modifications outside the file whitelist without a proven dependency.

## 10. TEST BEFORE SAYING "FIXED"

Before claiming completion:

1. verify the production import path;
2. syntax-check every affected Python file;
3. run the focused regression test for the changed feature;
4. run `python scripts/validate_architecture.py`;
5. confirm the architecture guard passes;
6. inspect the exact committed production file;
7. inspect the final Git diff;
8. test the feature that changed;
9. verify unrelated top-level tabs still render.

Do not say something is fixed merely because code was committed or a PR merged.

If a live-browser behavior was not actually tested, say so rather than implying it was.

## 11. CHANGE LOG IS REQUIRED

After every successful Raj's Terminal production fix, append a chronological entry to:

`src/TERMINAL_CHANGELOG.md`

Do not rewrite or delete prior entries.

Record:
- date;
- feature changed;
- exact production file(s) changed;
- what was broken;
- root cause;
- what changed;
- important behavior that must remain;
- files/features intentionally NOT changed;
- tests performed;
- architecture guard result;
- commit SHA;
- lesson/rule for future edits.

Before changing an existing feature, read its prior changelog entries first.

## 12. COMMIT SCOPE

A task-specific commit/PR should contain only:
- production code required for the request;
- focused regression coverage when appropriate;
- required changelog/instruction documentation.

Do not bundle unrelated improvements.

## 13. WHEN MULTIPLE FIXES ARE POSSIBLE

Choose the approach that:
1. touches the fewest files;
2. changes the fewest lines;
3. stays inside feature ownership;
4. preserves current UI and behavior;
5. creates the lowest regression risk.

Do not choose a broader refactor simply because it seems cleaner.

## 14. FINAL REPORT REQUIREMENTS

After a completed coding task, report:
- exact files changed;
- exact behavior changed;
- files/features intentionally not touched;
- tests performed;
- architecture guard result;
- commit SHA.

If any file outside the original feature ownership had to change, explain the proven dependency.

## NON-NEGOTIABLE PRINCIPLE

Correct feature -> minimum files -> no cross-feature regressions -> preserve theme unless requested -> test -> inspect diff -> commit.

When these rules conflict with convenience, convenience loses.
