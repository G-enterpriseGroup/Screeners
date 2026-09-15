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

## 2026-09-15 — GEX seeded watchlist, quick delete, and TradingView bridge repair

- **Feature changed:** GEX watchlist management and TradingView bridge output.
- **Exact production file(s) changed:** `src/gex_ui_v3.py`, `src/TERMINAL_CHANGELOG.md`.
- **What was broken:** Raj's desired GEX universe was not preloaded, removing a ticker required the Settings workflow instead of a fast trash action, the Overview rendered one refresh button per ticker which became inefficient for a large watchlist, and the TradingView bridge output was malformed/poorly readable.
- **Root cause:** GEX v3 started from the legacy persisted ticker list; the legacy bridge formatter in `src/gex_ui.py` used physical backslash-newline string continuations where real newline separators were intended, and its trailing-zero formatter could turn whole-number open interest such as `5000` into `5`. The bridge code block was also effectively black-on-black in the terminal theme and grew very tall.
- **What was changed:** Added Raj's supplied universe as a one-time 49-symbol seed (the duplicate `FLNG` entry is stored once), preserving one legacy custom slot under the existing 50-ticker core limit. Deletions persist because the seed uses a one-time revision migration. Added a compact saved-ticker selector with a `🗑` delete button and the same trash action in Settings. Replaced dozens of per-ticker refresh buttons with one ticker selector + refresh button. Rebuilt TradingView text locally in the GEX v3 owner with real `\n` separators, correct whole-number OI formatting, MASTER A6 block joining, and a readable scroll-bounded code box.
- **Important behavior that must remain:** The seeded GEX universe must not re-add a ticker after Raj deletes it. `FLNG` stays deduplicated. TradingView bridge rows must keep real line boundaries and must never strip meaningful integer zeros from OI. GEX changes stay inside GEX ownership files; do not touch Risk, OAuth, Holdings, navigation, or `terminal_core.py` for these behaviors.
- **Files/features intentionally NOT changed:** Risk Sizing, E*TRADE OAuth, Holdings, Bull Debit, Muni, top navigation, Touch ID/authentication, `src/gex_workspace_v2.py`, `src/gex_ui.py` calculation engine, and `src/terminal_core.py`.
- **Tests performed:** Compiled the exact replacement `src/gex_ui_v3.py`; verified the committed Git blob SHA matches the compile-tested source; unit-tested bridge output with sample data (`5000` and `12000` OI remain intact and line breaks are real); verified the default list contains 49 unique symbols and `FLNG` exactly once; simulated seed → delete → reseed and confirmed a deleted ticker is not re-added; re-fetched and inspected the exact committed production file; GitHub Actions `validate-boundaries` passed on commit `2a4e6690`.
- **Architecture guard result:** PASS.
- **Commit SHA:** `2a4e6690` plus this changelog commit.
- **Lesson:** TradingView bridge serialization is presentation/export behavior owned by GEX v3. Keep it deterministic and test representative integer values/newlines. Seed large default watchlists as one-time migrations so user deletions remain authoritative, and prefer compact selector/action controls over dozens of repeated buttons.

## 2026-09-15 — Terminal-wide dropdown readability + GEX row trash actions

- **Feature changed:** Shared dropdown appearance and GEX watchlist row actions.
- **Exact production file(s) changed:** `src/theme.py`, `src/gex_ui_v3.py`, `src/gex_ui_v3_base.py`, `src/gex_workspace_v2.py`, `scripts/validate_architecture.py`, `src/TERMINAL_CHANGELOG.md`.
- **What was broken:** Streamlit/BaseWeb selectboxes could show selected values or menu options in black against dark terminal backgrounds, making choices effectively invisible. GEX also required using a separate saved-ticker selector to delete a symbol instead of offering a delete action directly beside each overview row.
- **Root cause:** Dropdown text colors were not enforced consistently at the shared theme layer, and BaseWeb renders open select menus in a portal outside the closed selectbox DOM. The GEX overview was a compact HTML table with no row-level server action.
- **What was changed:** Added terminal-wide dropdown CSS to `src/theme.py`: closed values use Bloomberg orange, open options use high-contrast light text on dark backgrounds, hover uses orange emphasis, and selected menu rows use orange with black text. Removed the duplicate GEX-specific dropdown color rules so the shared theme is authoritative. Preserved the proven GEX v3 renderer byte-for-byte as `src/gex_ui_v3_base.py`; `src/gex_ui_v3.py` is now a small interaction layer that injects a compact `🗑` cell beside every overview ticker, preserves existing query parameters, consumes only `gex_delete`, persists the deletion through the existing GEX state helper, and restores its temporary `st.markdown` wrapper in `finally`. The architecture guard now also parses `gex_ui_v3_base.py`.
- **Important behavior that must remain:** Dropdowns across every tab must never render black-on-black. Shared dropdown colors belong in `src/theme.py`, not competing feature CSS. Every GEX overview ticker has a row trash icon; deleting a seeded ticker must remain persistent and must not cause the one-time seed to re-add it. The row-action wrapper must remain GEX-local and restore `st.markdown` in `finally`.
- **Files/features intentionally NOT changed:** Risk Sizing calculations/interactions, OAuth behavior/layout, Holdings logic, Bull Debit calculations, Muni logic, top-navigation behavior, Touch ID/authentication, `src/gex_ui.py` calculations, and `src/terminal_core.py`.
- **Tests performed:** Re-fetched the exact committed shared theme and GEX interaction layer; confirmed the theme stylesheet remains a plain string with explicit `.replace()` substitution (no CSS f-string regression); confirmed `gex_ui_v3.py` delegates to the preserved base renderer and restores its temporary markdown wrapper; confirmed `gex_ui_v3_base.py` exists with the prior proven blob; GitHub Actions `validate-boundaries` passed after the combined changes and passed again after adding the base renderer to the architecture guard on commit `bc85363e`.
- **Architecture guard result:** PASS.
- **Commit SHA:** `191196b7`, `6d4f479c`, `c2e93a3a`, `1ed83827`, `bc85363e` plus this changelog commit.
- **Lesson:** Dropdown readability is shared appearance and must be fixed once in the theme, including BaseWeb portal menus. Row-level actions belong in a small GEX interaction layer so the stable analytics renderer does not need to be rewritten for a simple control request.

## 2026-09-15 — Dropdown lifecycle correction + trash icon inside ticker cell

- **Feature changed:** Shared dropdown rendering lifecycle and GEX row-delete placement.
- **Exact production file(s) changed:** `src/theme.py`, `streamlit_app.py`, `src/gex_workspace_v2.py`, `src/gex_ui_v3.py`, `src/TERMINAL_CHANGELOG.md`.
- **What was broken:** The previous dropdown CSS still appeared ineffective in live GEX: selected/menu values could remain black on dark backgrounds, and the requested trash action was not visually located inside the TICKER cell beside each symbol.
- **Root cause:** `.streamlit/config.toml` intentionally sets global `textColor = "#000000"` so interactive dataframe headers render black on orange. The first dropdown fix emitted its CSS from `src.theme` only at module import and guarded it with a process-global flag; Streamlit reruns/new browser sessions can therefore lose that style output and fall back to black text. The first row-action implementation also inserted a separate `DEL` column instead of putting the icon in the ticker cell itself.
- **What was changed:** `src.theme.install_typing_caret_theme()` now emits the shared input/dropdown stylesheet for every Streamlit render instead of using a process-global installed flag, and `streamlit_app.py` calls it after the core has completed `st.set_page_config`. The selectors were widened to cover nested BaseWeb select value nodes plus portal/listbox option descendants. GEX additionally emits a GEX-local dropdown fail-safe stylesheet on every GEX render. `src/gex_ui_v3.py` no longer creates a separate delete column; it rewrites each ticker cell as `TICKER + 🗑` inside the same `<td class="sym">` cell, preserving the existing persistent delete path.
- **Important behavior that must remain:** Every closed dropdown value must be visible on black; every open dropdown option must be readable; selected menu rows may use black text only when their background is orange. Shared input CSS must be emitted after page configuration on every rerun/session. In GEX, each ticker row must show the trash icon immediately beside the ticker inside the ticker column—never in a separate DEL column. Seeded ticker deletions must remain persistent.
- **Files/features intentionally NOT changed:** Risk Sizing formulas/interactions, OAuth workflow behavior, Holdings calculations, Bull Debit calculations, Muni calculations, top-navigation behavior, Touch ID authentication, `src/gex_ui.py` calculation engine, and `src/terminal_core.py`.
- **Tests performed:** Re-fetched the exact production `streamlit_app.py` and confirmed the shared input theme is invoked after core/page setup; re-fetched `src/gex_ui_v3.py` and confirmed the generated row markup places the `<a class="gexv3-trash-link">🗑</a>` inside `<td class="sym">`; GitHub Actions `validate-boundaries` passed on commit `7afb9067`.
- **Architecture guard result:** PASS.
- **Commit SHA:** `949b8495`, `3c2e8c18`, `a23564db`, `7afb9067` plus this changelog commit.
- **Lesson:** Streamlit CSS output is session/rerun scoped, so visual styles must not rely on a process-global "already installed" flag. When the request says the delete icon belongs next to the ticker, put it inside the ticker cell itself rather than approximating with an adjacent action column.

## 2026-09-15 — Left-align GEX trash icons in ticker cells

- **Feature changed:** GEX Overview row-action alignment.
- **Exact production file(s) changed:** `src/gex_ui_v3.py`, `src/TERMINAL_CHANGELOG.md`.
- **What was broken:** The trash icon was inside the ticker cell but the ticker/icon group was centered, and the ticker appeared before the icon.
- **Root cause:** `.gexv3-symbol-wrap` used `justify-content:center`, while the generated markup rendered the symbol before the trash link.
- **What was changed:** The ticker cell now uses left text alignment, the wrapper uses `justify-content:flex-start` across the full cell width, and the trash link is rendered before the ticker text. Rows therefore read visually as `🗑  SPY`, `🗑  QQQ`, etc., with every icon aligned on the same left edge.
- **Important behavior that must remain:** The trash icon stays inside the existing TICKER column, immediately beside its ticker; do not create a separate delete column. Clicking the icon must continue to use the existing persistent GEX delete path.
- **Files/features intentionally NOT changed:** GEX calculations/TradingView bridge, Risk Sizing, OAuth, Holdings, Bull Debit, Muni, navigation, Touch ID/authentication, shared theme, `src/gex_ui.py`, and `src/terminal_core.py`.
- **Tests performed:** Re-fetched the exact committed `src/gex_ui_v3.py` from commit `bee8ee65`; confirmed `text-align:left`, `justify-content:flex-start`, and trash-link-before-symbol markup; GitHub Actions `validate-boundaries` completed successfully on commit `bee8ee65`.
- **Architecture guard result:** PASS.
- **Commit SHA:** `bee8ee65` plus this changelog commit.
- **Lesson:** For row actions, match requested direction and alignment exactly; if the icon is meant to be left-aligned, render it first and align the row wrapper to `flex-start` rather than approximating with centered content.

## 2026-09-15 — Non-blocking background Refresh All GEX

- **Feature changed:** GEX refresh-all execution / background processing.
- **Exact production file(s) changed:** `src/gex_ui_v3.py`, `src/gex_workspace_v2.py`, `src/TERMINAL_CHANGELOG.md`.
- **What was broken:** `REFRESH ALL GEX` refreshed every ticker sequentially inside the Streamlit render thread. While the loop was fetching quotes, expirations, option chains, and calculating GEX for the watchlist, the page was blocked, so Raj could not switch to Holdings, Risk Sizing, Bull Debit, Muni, Orders, or other terminal work until the entire batch finished.
- **Root cause:** The preserved GEX v3 base renderer owns a synchronous `for ticker in state["tickers"]` refresh-all loop. Network/API work and GEX calculations were therefore coupled directly to the UI rerun that handled the button click.
- **What was changed:** `src/gex_ui_v3.py` now intercepts only the `gexv3_refresh_all` button during the GEX render. When a safe background client factory is available it suppresses the legacy synchronous loop, snapshots the current ticker/settings state, and starts a daemon worker. The worker performs the existing `core._build_gex` calculation sequentially in process memory, tracks current ticker/completed/updated/failures, and allows only one active refresh-all job per GEX vault. It never calls Streamlit APIs or accesses `st.session_state`. Completed ticker results are copied into the normal GEX result vault only from the Streamlit render thread when GEX renders again. `src/gex_workspace_v2.py` now captures consumer credentials and the active OAuth token on the main thread and provides a factory that creates a separate `ETradeClient`/OAuth session inside the worker; credentials/tokens remain server-memory only. If a dedicated background client cannot be created, the existing synchronous behavior remains as a safe fallback. The refresh button shows the running count on later GEX reruns, and GEX shows a compact background status line. Starting the worker triggers a quick rerun, after which the user can switch tabs immediately.
- **Important behavior that must remain:** Clicking `REFRESH ALL GEX` must not block top-level tab switching when a live E*TRADE session is available. Background worker code must never call `st.*`, touch `st.session_state`, reuse arbitrary Streamlit context, or write OAuth credentials/brokerage data to GitHub/browser/disk. Use a dedicated E*TRADE client created from a main-thread snapshot. Merge background results into Streamlit-owned GEX state only on the main render thread. Keep at most one active refresh-all job per vault. A Streamlit process restart/redeploy may terminate an in-memory background job; the user can safely restart it. Individual per-ticker refresh remains synchronous unless separately changed later.
- **Files/features intentionally NOT changed:** Risk Sizing, Holdings, OAuth UI/authorization semantics, Bull Debit, Muni, Orders, top navigation, Touch ID/authentication, shared theme, GEX calculation formulas in `src/gex_ui.py`, and `src/terminal_core.py`.
- **Tests performed:** Re-fetched the exact committed background interaction layer and verified `_run_background_refresh` uses the dedicated client and pure GEX calculation path rather than Streamlit/session APIs; re-fetched the production GEX workspace and verified credential/token capture occurs on the render thread and the worker factory creates a fresh `ETradeClient`; GitHub Actions `validate-boundaries` completed successfully on final code commit `a802bba2`.
- **Architecture guard result:** PASS.
- **Commit SHA:** `e363b980`, `a802bba2` plus this changelog commit.
- **Lesson:** Long market-data batches must not execute on Streamlit's UI/render thread when the user needs to keep working. Background workers should be pure backend workers with their own API session and thread-safe process-memory status/results, while Streamlit remains responsible only for launching jobs, showing status, and merging completed data on reruns.

## 2026-09-15 — GEX animated running indicator, larger Overview type, and gamma regime range

- **Feature changed:** GEX Overview presentation and background-refresh status visibility.
- **Exact production file(s) changed:** `src/gex_ui_v3.py`, `src/TERMINAL_CHANGELOG.md`.
- **What was broken:** After moving Refresh All GEX to a detached backend worker, the page no longer showed Streamlit's native running indicator, so an active job was not visually obvious. The Overview table type was also too small, and the RANGE column only displayed a text state instead of showing the relationship among spot, walls, and gamma flip.
- **Root cause:** A detached daemon worker correctly leaves the Streamlit render thread idle, so the framework's own busy indicator is not expected to remain active. The legacy Overview table used `.69rem` text and rendered RANGE as a single `INSIDE/BELOW/ABOVE` label.
- **What was changed:** Added a GEX-owned animated `↻` backend-running indicator and changed the Refresh All button label to `↻ GEX RUNNING x/y` while a job is active. Increased the Overview table typography from the legacy `.69rem` to `11.25pt`, approximately three points larger. The RANGE cell now renders a compact horizontal line with four semantic dots when data exists: Put Wall (red), Gamma Flip (orange), Spot (white), and Call Wall (green). The cell also prints `GF $xx.xx // POSITIVE GAMMA`, `NEUTRAL GAMMA`, or `NEGATIVE GAMMA`. Regime is classified from spot relative to the calculated gamma flip: above flip = positive, below flip = negative, and within ±0.50% of the flip = neutral to avoid quote-noise oscillation. The table uses an explicit column-width map so the wider RANGE visualization still fits the screen without horizontal scrolling.
- **Important behavior that must remain:** The background worker itself remains detached and non-blocking; do not reintroduce a synchronous refresh loop merely to get Streamlit's native busy indicator. Keep the custom running icon GEX-owned. Preserve the four-dot color semantics and the ±0.50% neutral band unless Raj explicitly changes the regime definition. Trash icons remain inside the left-aligned TICKER cell. Overview must continue fitting the available width without unnecessary horizontal scrolling.
- **Files/features intentionally NOT changed:** GEX calculation formulas in `src/gex_ui.py`, GEX workspace/OAuth plumbing, Risk Sizing, Holdings, Bull Debit, Muni, Orders, top navigation, Touch ID/authentication, shared theme, and `src/terminal_core.py`.
- **Tests performed:** Re-fetched the exact committed `src/gex_ui_v3.py`; confirmed the range replacement uses the same `_base._range_status` output generated by the base table before replacing that cell, preserving row matching; confirmed the CSS is a plain module string, not a Python f-string; sanity-checked screenshot values against the regime rule (rows with spot below gamma flip classify negative and XLV with spot above its flip classifies positive); GitHub Actions `validate-boundaries` passed on code commit `23753218`.
- **Architecture guard result:** PASS.
- **Commit SHA:** `23753218` plus this changelog commit.
- **Lesson:** A correct non-blocking backend job should not depend on Streamlit's request-level spinner. Show explicit feature-owned background-job status, and use the Overview interaction layer for presentation upgrades rather than changing the GEX calculation engine.
