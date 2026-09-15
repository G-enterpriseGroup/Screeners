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

## 2026-09-15 — Hard-pinned top tab component height

- **Feature changed:** Top navigation / terminal tab bar spacing.
- **Exact production file(s) changed:** `src/tab_bar_v4.py`, `src/components/terminal_tabs_v3/index.html`, `requirements.txt`, `src/TERMINAL_CHANGELOG.md`.
- **What was broken:** The blank space specifically between the selected `GEX` top tab and `GEX // MULTI-TICKER GAMMA WORKSPACE` still remained after CSS-only iframe targeting.
- **Root cause:** The tab component was still invoked without an explicit Python-side frame height, and the parent Streamlit wrapper could keep the default custom-component height even if the inner iframe rendered a 48px tab row.
- **What was changed:** Passed `height=48` into the custom tab component invocation, changed the component key to force a fresh mount, and added component-side JavaScript that directly pins its own iframe and immediate parent wrappers to 48px while continuing to send Streamlit `setFrameHeight` messages. Added a rebuild marker.
- **Important behavior that must remain:** The gap under the top tab bar belongs to navigation. Do not use GEX negative margins or Risk/OAuth CSS to compensate for top-navigation frame height.
- **Files/features intentionally NOT changed:** GEX content, Risk Sizing, OAuth, Holdings, Bull Debit, and municipal tools.
- **Tests performed:** Production files re-fetched from `main`; GitHub Actions `Terminal Architecture Guard` run `34995709799` passed.
- **Architecture guard result:** PASS.
- **Commit SHA:** `85d82b8`, `a8364ce`, `039d148`, `d88e454`.
- **Lesson:** For custom Streamlit components, set frame height in Python and inside the component; CSS alone can miss Streamlit's generated wrapper structure.

## 2026-09-15 — Blank screen after biometric / Touch ID unlock

- **Feature changed:** Top navigation component safety during terminal unlock reruns.
- **Exact production file(s) changed:** `src/components/terminal_tabs_v3/index.html`, `src/tab_bar_v4.py`, `src/TERMINAL_CHANGELOG.md`.
- **What was broken:** After biometric / Touch ID unlock, the terminal could render a blank screen instead of the application.
- **Root cause:** The previous navigation-gap fix added JavaScript inside the tab iframe that walked up as many as four parent DOM wrappers and forced every ancestor to 48px. On an unlock-triggered Streamlit rerun, the component can be mounted under a different wrapper hierarchy, so that code could collapse a high-level Streamlit content container and hide the whole app.
- **What was changed:** Removed all parent-DOM traversal/resizing from the tab component. The component now uses only Streamlit's supported `streamlit:setFrameHeight` message while Python still passes `height=48`. Changed the component key to `raj_terminal_draggable_tabs_v4_h48_safe_unlock` so existing browser sessions remount the safe component instead of retaining the old iframe instance.
- **Important behavior that must remain:** Never resize arbitrary parent/ancestor DOM nodes from inside a Streamlit iframe. Keep the navigation frame compact through the component API and tightly scoped navigation CSS only. Biometric/unlock reruns must never be coupled to navigation sizing hacks.
- **Files/features intentionally NOT changed:** Touch ID credential/authentication logic, E*TRADE OAuth, GEX, Risk Sizing, Holdings, Bull Debit, and Muni content.
- **Tests performed:** Re-fetched the exact production component and navigation file from `main`; confirmed parent traversal is absent and the new component key is live; GitHub Actions `Terminal Architecture Guard` run `34996846652` completed successfully.
- **Architecture guard result:** PASS.
- **Commit SHA:** `f85bb1c`, `a2e66f6`.
- **Lesson:** A compact iframe must never enforce size by mutating ancestor wrappers. Use `setFrameHeight`/Python component height and scoped CSS only, especially across authentication reruns.

## 2026-09-15 — Exact removal of black spacer under top tabs

- **Feature changed:** Top navigation / terminal tab component height.
- **Exact production file(s) changed:** `src/components/terminal_tabs_v3/index.html`, `src/tab_bar_v4.py`, `src/TERMINAL_CHANGELOG.md`.
- **What was broken:** A large black dead area remained specifically between the orange bottom line of the selected top tab bar and `GEX // MULTI-TICKER GAMMA WORKSPACE`.
- **Root cause:** The visual tab row was 48px tall, but the live Streamlit custom-component iframe could remain at its default roughly 150px frame height. The screenshot gap matched the unused remainder of that default iframe, plus Streamlit's normal vertical element gap.
- **What was changed:** The tab component now pins only its own iframe and the exact `stCustomComponentV1` / `stElementContainer` wrappers containing that iframe to 48px, offsets the navigation element's normal 1rem bottom gap, and continues to send `streamlit:setFrameHeight(48)`. It never walks into arbitrary ancestors. The component key was changed to force a clean remount in existing browser sessions.
- **Important behavior that must remain:** `GEX // MULTI-TICKER GAMMA WORKSPACE` and every other active tab should start immediately below the top tab bar. Never add GEX-specific negative margins to compensate for navigation height. Never resize generic ancestors from the iframe because that can break biometric unlock.
- **Files/features intentionally NOT changed:** GEX UI/content, Risk Sizing, E*TRADE OAuth, Holdings, Bull Debit, Muni, Touch ID authentication logic.
- **Tests performed:** Re-fetched the exact committed navigation HTML and Python route from `main`; confirmed the component is scoped to exact iframe wrappers and the new remount key is live; GitHub Actions `Terminal Architecture Guard` run `34997907820` passed on commit `233d72f8`.
- **Architecture guard result:** PASS.
- **Commit SHA:** `7f5dff0`, `233d72f8`.
- **Lesson:** When a Streamlit custom component shows a 48px UI inside a ~150px black frame, fix the exact component frame and exact component wrappers—not the feature below it and not arbitrary DOM ancestors.

## 2026-09-15 — Remove GEX hidden pre-header spacer rows

- **Feature changed:** GEX wrapper/render-boundary spacing.
- **Exact production file(s) changed:** `src/gex_workspace_v2.py`, `src/TERMINAL_CHANGELOG.md`.
- **What was broken:** A large black blank area still remained between the top terminal tab bar and `GEX // MULTI-TICKER GAMMA WORKSPACE` after the navigation iframe itself had already been constrained.
- **Root cause:** GEX emitted two standalone style-only `st.markdown(<style>...</style>)` elements before the first visible workspace header: the subtab skin in `gex_workspace_v2.py` and `_css()` in `gex_ui_v3.py`. Streamlit can still allocate normal vertical-stack spacing between those invisible element containers, so the remaining gap was partly GEX-owned rather than only a navigation-frame problem.
- **What was changed:** `gex_workspace_v2.py` now returns the GEX subtab CSS as text instead of rendering it as a standalone row. During GEX rendering only, a temporary `st.markdown` wrapper buffers leading style-only markdown calls and prepends all of that CSS to the first visible GEX markdown block. The original `st.markdown` is restored in `finally`. The first GEX header also receives a tightly scoped one-element top-gap correction.
- **Important behavior that must remain:** Do not create standalone invisible style-only Streamlit elements immediately before the first visible GEX content. Any temporary Streamlit wrapper must remain GEX-local and be restored in `finally`. Preserve the prior Touch ID safety rule: never resize arbitrary ancestor DOM wrappers.
- **Files/features intentionally NOT changed:** Risk Sizing, E*TRADE OAuth, Holdings, Bull Debit, Muni, Touch ID authentication logic, GEX analytics/math, `gex_ui_v3.py`, `terminal_core.py`, and top-navigation files in this pass.
- **Tests performed:** Re-fetched the exact production GEX wrapper from `main`; confirmed the temporary markdown wrapper is installed only inside the GEX render path and restored in `finally`; GitHub Actions `Terminal Architecture Guard` run `34998470522` passed on commit `7e50074d`.
- **Architecture guard result:** PASS.
- **Commit SHA:** `7e50074d`.
- **Lesson:** Invisible `st.markdown(<style>...)` calls can still cost vertical-stack spacing in Streamlit. Fold feature CSS into a visible feature element when compact spacing matters instead of adding hidden rows before content.

## 2026-09-15 — Fix GEX raw-CSS rendering regression

- **Feature changed:** GEX wrapper CSS delivery / compact spacing.
- **Exact production file(s) changed:** `src/gex_workspace_v2.py`, `src/TERMINAL_CHANGELOG.md`.
- **What was broken:** The GEX page displayed the literal `<style>` block and CSS source below the top tabs instead of rendering `GEX // MULTI-TICKER GAMMA WORKSPACE` normally.
- **Root cause:** The prior spacer-row fix buffered CSS and concatenated it into the first visible `st.markdown` block. In the deployed Streamlit renderer that combined payload was surfaced as page text, so the workaround corrupted the GEX render path.
- **What was changed:** Removed all CSS concatenation and the GEX negative-margin rule. GEX wrapper CSS now uses style-only `st.html`, and the existing style-only markdown emitted by `gex_ui_v3.py` is routed to `st.html` only during the GEX render. All normal markdown passes through unchanged and the original `st.markdown` is restored in `finally`.
- **Important behavior that must remain:** Never concatenate `<style>` source into visible GEX markdown. Never expose CSS text in the page. Do not use negative margins to hide spacing. Style-only CSS should use `st.html`, which Streamlit places outside the main layout when the payload contains only style tags.
- **Files/features intentionally NOT changed:** Risk Sizing, E*TRADE OAuth, Holdings, Bull Debit, Muni, Touch ID authentication logic, GEX analytics/math, `gex_ui_v3.py`, `terminal_core.py`, and top-navigation files.
- **Tests performed:** Re-fetched the exact committed `src/gex_workspace_v2.py` from `main`; confirmed no CSS concatenation and no negative-margin rule remain. GitHub Actions `Terminal Architecture Guard` run `34999147469` passed and therefore parsed all production Python files successfully, including the changed GEX wrapper.
- **Architecture guard result:** PASS.
- **Commit SHA:** `6962f37e`.
- **Lesson:** For style-only CSS in Streamlit, use `st.html`; do not fold style tags into visible markdown just to avoid layout spacing.

## 2026-09-15 — Terminal-wide native typing caret

- **Feature changed:** Shared terminal appearance / editable-field interaction.
- **Exact production file(s) changed:** `src/theme.py`, `src/ARCHITECTURE.md`, `src/TERMINAL_CHANGELOG.md`.
- **What was requested:** Every place where a user types should show a traditional, clearly visible flashing text cursor in the text box.
- **Root cause:** Individual Streamlit/BaseWeb inputs relied on browser/theme defaults, so the insertion caret could be difficult to see against the terminal's black background and was not documented as a terminal-wide UI requirement.
- **What was changed:** Added terminal-wide shared CSS in `src/theme.py` using the browser's native blinking insertion caret with Bloomberg orange `caret-color` and the normal text I-beam pointer. The selectors cover text, search, password, number, email, telephone, URL, untyped BaseWeb inputs, textareas, searchable select inputs, and editable content. Added explicit ownership/section comments to `src/theme.py` and recorded the caret as a standing UI rule in `src/ARCHITECTURE.md`.
- **Important behavior that must remain:** Use the native browser insertion caret; do not fake a blinking cursor with JavaScript or pseudo-elements. Shared caret behavior belongs in `src/theme.py`, not in individual Risk/GEX/OAuth/Holdings files.
- **Files/features intentionally NOT changed:** Risk Sizing logic/UI files, GEX files, OAuth files, Holdings, navigation, `terminal_core.py`, Bull Debit, and municipal tools.
- **Tests performed:** Re-fetched the exact committed `src/theme.py`; confirmed the caret CSS is style-only and contains no feature widget replacement. GitHub Actions `Terminal Architecture Guard` run `35001666323` passed on commit `8071cff` and parsed all production Python files successfully.
- **Architecture guard result:** PASS.
- **Commit SHA:** `8071cff`, `95dcd74`.
- **Lesson:** A terminal-wide typing affordance is shared appearance. Keep it in the theme layer and use native caret behavior so selection, keyboard navigation, accessibility, and input-method behavior stay correct.

## 2026-09-15 — Caret CSS startup crash recovery

- **Feature changed:** Shared terminal theme / deployment safety guard.
- **Exact production file(s) changed:** `src/theme.py`, `scripts/validate_architecture.py`, `src/TERMINAL_CHANGELOG.md`.
- **What was broken:** The entire Streamlit app failed at startup with `NameError` while importing `src/theme.py`; the traceback pointed at the new `caret-color` CSS block.
- **Root cause:** `_TYPING_CARET_CSS` was created as a Python f-string while containing normal CSS `{ ... }` braces. Python treated part of the CSS block as an f-string expression during module import, so a shared appearance change prevented every terminal feature from loading.
- **What was changed:** Replaced the caret stylesheet f-string with a plain triple-quoted CSS string and explicit `__RAJ_CARET_COLOR__` substitution via `.replace()`. Added a permanent source comment explaining why CSS blocks must remain plain strings. Expanded the architecture validator to include `src/theme.py` and `src/layout_guardrails.py` and to reject module-level `*_CSS` constants implemented as f-strings.
- **Important behavior that must remain:** Never use a Python f-string for a module-level CSS block. Use a plain string plus explicit substitution. A shared visual change must never be able to crash app startup before Risk, GEX, OAuth, Holdings, navigation, or authentication render.
- **Files/features intentionally NOT changed:** Risk Sizing, GEX, OAuth, Holdings, top navigation, Touch ID/authentication logic, Bull Debit logic, municipal tools, and `terminal_core.py`.
- **Tests performed:** Re-fetched the corrected production theme; the original recovery commit `632a76e` passed the existing architecture workflow. Then strengthened `scripts/validate_architecture.py`; the new `validate-boundaries` check completed successfully on commit `c52726d`.
- **Architecture guard result:** PASS.
- **Commit SHA:** `632a76e`, `c52726d`.
- **Lesson:** Shared CSS is startup-critical code. Treat CSS construction as production code, avoid f-string brace hazards, and make the guard explicitly test shared appearance files so a cosmetic request cannot take down the whole terminal.

## 2026-09-15 — Live app served stale caret-crash build after source recovery

- **Feature changed:** Deployment/rebuild discipline after startup-critical fixes.
- **Exact production file(s) changed:** `requirements.txt`, `src/TERMINAL_CHANGELOG.md`.
- **What was broken:** The live Streamlit app continued to show the old `NameError` traceback referencing `caret-color:{BB_ORANGE}` even though `main` had already replaced that code.
- **Root cause:** The deployed Streamlit process/build was stale relative to the corrected `main` branch. The exact failing source line no longer existed on `main`, proving the live traceback came from an older checkout/process rather than the current source.
- **What was changed:** Verified the `main` branch head and exact `src/theme.py` contents, confirmed the bad f-string line was absent, and added a no-op rebuild marker to `requirements.txt` to force Streamlit Cloud to rebuild from current `main` without changing feature behavior.
- **Important behavior that must remain:** When a live traceback references source text that no longer exists on `main`, compare the deployed traceback to the current committed file before making additional code changes. Force a clean rebuild rather than stacking another workaround on correct source.
- **Files/features intentionally NOT changed:** Risk Sizing, GEX, OAuth, Holdings, navigation, Touch ID/authentication, Bull Debit, Muni, `terminal_core.py`, and all business logic.
- **Tests performed:** Verified repository default branch is `main`; verified `main` head was the corrected caret-crash recovery history; re-fetched `src/theme.py` from `main` and confirmed `caret-color:{BB_ORANGE}` is absent; pushed rebuild commit `ca15c12` containing only a requirements comment.
- **Architecture guard result:** Not applicable to the no-op `requirements.txt` rebuild commit; the corrected source and strengthened guard had already passed on commits `632a76e`, `c52726d`, and `28de7aa`.
- **Commit SHA:** `ca15c12` plus this changelog commit.
- **Lesson:** Distinguish stale deployment failures from source-code failures before editing production code. A stale cloud process should be rebuilt, not "fixed" with unrelated code changes.

## 2026-09-15 — Unified top-level terminal page headers

- **Feature changed:** Shared top-level tab page chrome / visual consistency.
- **Exact production file(s) changed:** `streamlit_app.py`, `src/ARCHITECTURE.md`, `src/TERMINAL_CHANGELOG.md`.
- **What was broken:** The six top-level tabs used visibly different page-header treatments: Holdings/Risk/Bull used Streamlit subheaders, Muni had no page header, Orders rendered its header only inside a narrow centered column, and GEX used a custom black outlined header. Font size, bar width, subtitle spacing, and left alignment therefore changed from tab to tab.
- **Root cause:** Each feature historically owned its first visible heading independently, so page-level chrome drifted even though the top navigation was shared.
- **What was changed:** Added one top-level page-header map and renderer in `streamlit_app.py`. Every active tab now receives the same full-width Bloomberg-orange title bar, black title text, Courier typography, compact subtitle, and spacing. During each feature render only, exact legacy top headings/subtitles are suppressed with temporary `st.subheader`/`st.caption`/`st.markdown` wrappers restored in `finally`; internal feature section headings remain untouched. Muni now gets the same header structure as the other tabs. Orders keeps its centered simulator body while its page header is full width. GEX's old outlined `gexv3-head/gexv3-sub` page chrome is suppressed without changing GEX analytics or subtabs.
- **Important behavior that must remain:** All top-level terminal tabs must use the shared page-header system in `streamlit_app.py`. Do not add competing feature-specific page titles. Internal section headers remain feature-owned. Temporary wrappers must stay render-scoped and restore in `finally`.
- **Files/features intentionally NOT changed:** Risk sizing formulas/math, Risk Part 2 interactions, GEX calculations/subtabs/tables, E*TRADE OAuth, Holdings data logic, Bull Debit calculations, navigation component behavior, Touch ID/authentication, and `src/terminal_core.py`.
- **Tests performed:** Re-fetched the exact committed `streamlit_app.py`; inspected the commit diff to confirm only page-header/render routing changed; GitHub Actions `validate-boundaries` passed on code commit `9128054e` and again after the architecture rule commit `448e6781`.
- **Architecture guard result:** PASS.
- **Commit SHA:** `9128054e`, `448e6781` plus this changelog commit.
- **Lesson:** Top-level page chrome is shared application routing/presentation, not feature-owned UI. Centralize it once and suppress only the legacy first heading during each feature render instead of letting six tabs drift independently.
