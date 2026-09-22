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

## 2026-09-15 — Orange native Streamlit Stop / running toolbar control

- **Feature changed:** Shared terminal appearance for Streamlit's native toolbar/status widget.
- **Exact production file(s) changed:** `src/theme.py`, `src/TERMINAL_CHANGELOG.md`.
- **What was broken:** While Streamlit displayed its native top-right `Stop` / running control, the button label inherited the app's global black `textColor`, making `Stop` appear black against the dark toolbar even though Share/star/menu controls were orange.
- **Root cause:** The terminal deliberately uses black global text in Streamlit's theme so interactive dataframe headers can render black on orange. The native toolbar/status widget lives outside feature-level GEX CSS and therefore inherited that black color unless explicitly overridden in the shared theme.
- **What was changed:** Added terminal-wide toolbar/status selectors to `src/theme.py` covering `stToolbar`, `stStatusWidget`, nested buttons/labels, role-buttons, and SVG nodes. The native running/Stop control now forces Bloomberg orange text/icon color, orange border, and dark button background on every rerun/session. The stylesheet remains the existing plain module string with explicit color substitution, preserving the prior no-CSS-f-string startup-safety rule.
- **Important behavior that must remain:** Native Streamlit toolbar/status controls on the dark terminal header must never render black text or icons. Keep this shared appearance rule in `src/theme.py`; do not patch GEX, Risk, OAuth, Holdings, or navigation merely to recolor Streamlit's own toolbar.
- **Files/features intentionally NOT changed:** GEX calculations/background worker, Risk Sizing, OAuth, Holdings, Bull Debit, Muni, Orders, top navigation behavior, Touch ID/authentication, and `src/terminal_core.py`.
- **Tests performed:** Re-fetched the production theme before editing, confirmed the new selectors remain inside the existing post-`set_page_config` shared stylesheet, and GitHub Actions `validate-boundaries` passed on code commit `976b4c94`.
- **Architecture guard result:** PASS.
- **Commit SHA:** `976b4c94` plus this changelog commit.
- **Lesson:** Streamlit's native toolbar/status widget is shared shell chrome, not feature UI. When the app intentionally uses black global text for dataframe headers, explicitly recolor dark-shell native controls in the shared theme.

## 2026-09-15 — Pine-router-safe MASTER A6 export

- **Feature changed:** GEX TradingView bridge / MASTER A6 transport format.
- **Exact production file(s) changed:** `src/gex_ui_v3.py`, `src/TERMINAL_CHANGELOG.md`.
- **What was broken:** Pasting the Streamlit MASTER A6 into the uploaded `GEX TEST` Pine indicator could leave the current chart blank even though the first SPY section was visible. The screenshot showed a chart around $212 while the input began with SPY, which is consistent with a later matching ticker block (for example NVDA) not being reached/present in the pasted payload.
- **Root cause:** The Pine script routes strictly from `Ticker:` headers and only consumes packed rows (`SPOT`, `GFLIP`, `CALLWALL`, `PUTWALL`, `MAXCALLOI`, `MAXPUTOI`, `GEXPOS*`, `GEXNEG*`). The previous MASTER A6 also carried verbose per-ticker metadata, source URLs, instructions, separators, and blank lines that Pine ignores. With a large ~49-ticker universe that unnecessary text materially increases the payload and makes later ticker sections more vulnerable to TradingView input truncation. The prior stored `summaryText` path could also preserve old packed-number formatting; the proven `_base._packed_gamma_lines()` formatter does not strip meaningful integer zeros from OI.
- **What was changed:** `MASTER A6` now emits a compact Pine-router transport block for every refreshed ticker: one clean `Ticker: SYMBOL` header followed immediately by freshly rebuilt packed rows in the exact row types/order the Pine parser accepts. No ignored source/instruction/separator metadata is included in the default master payload. A separate `MASTER A6 // GOOGLE SHEETS FULL` option preserves the verbose Sheets-style structure for audit/reference. Single-ticker selections also emit the compact Pine-router form. The TradingView subtab now performs a header audit and displays PASS only when every expected refreshed ticker header is present; it also shows the payload character count. The stable base GEX renderer is not rewritten: the interaction layer temporarily swaps only the TradingView subtab renderer during GEX render and restores it in `finally`.
- **Important behavior that must remain:** Default `MASTER A6` is a Pine transport payload, not a verbose report. Keep `Ticker:` headers exact and keep packed types exactly compatible with the uploaded Pine router. Rebuild packed rows from result fields using the known-safe bridge formatter so OI values such as `5000` remain `5000`. Preserve the full Google-Sheets-style export as a separate optional audit mode rather than bloating the default master. Any temporary base-renderer override must remain GEX-local and restore in `finally`.
- **Files/features intentionally NOT changed:** GEX calculation formulas/math in `src/gex_ui.py`, E*TRADE/session plumbing in `src/gex_workspace_v2.py`, Risk Sizing, OAuth, Holdings, Bull Debit, Muni, Orders, top navigation, shared theme, Touch ID/authentication, and `src/terminal_core.py`.
- **Tests performed:** Read the uploaded Pine parser and verified it routes by `Ticker:` then parses only packed types. Simulated the router with SPY followed by later-ticker NVDA and confirmed both sections produce 14 drawable rows: SPOT, gamma flip, call/put wall, max call/put OI, and eight GEX levels. Representative compact blocks estimate the 49-ticker master at about 16.8K characters instead of carrying ignored metadata. Re-fetched the production route `src/gex_workspace_v2.py` and confirmed it still imports `src.gex_ui_v3.render_gex`. GitHub Actions `validate-boundaries` passed on code commit `fa32146a2591961837cbc2b13f09f1090f85a1e3`.
- **Architecture guard result:** PASS.
- **Commit SHA:** `fa32146a2591961837cbc2b13f09f1090f85a1e3` plus this changelog commit.
- **Lesson:** When Pine is acting as a ticker router, optimize the copied payload for the parser, not for human readability. Every unnecessary per-ticker line reduces headroom for later symbols and can create false `NO MATCH` behavior even when the first block is valid.

## 2026-09-15 — Harden full Google-Sheets TradingView serialization

- **Feature changed:** GEX TradingView bridge / full Google-Sheets audit export.
- **Exact production file(s) changed:** `src/gex_ui_v3_base.py`, `src/TERMINAL_CHANGELOG.md`.
- **What was broken:** The full Sheets-style bridge could still reuse cached `summaryText`/`packed` strings from an older serializer, so its line endings or number formatting could differ from the supplied Apps Script contract even after the Pine-router-safe default MASTER A6 had been added.
- **Root cause:** `_google_sheets_summary_text()` preferred stored text instead of rebuilding from the current structured GEX result. That made export formatting dependent on when a ticker result had originally been refreshed. The user-supplied Apps Script also creates two blank rows between successful summary blocks; relying on stored trailing newlines could collapse that boundary.
- **What was changed:** The base TradingView serializer now rebuilds every Sheets-style ticker block fresh from structured result fields and `_packed_gamma_lines()`, preserving Apps Script row order and integer OI formatting. The full master forces the exact successful-block delimiter (`\n\n\n`, two blank rows) instead of inheriting cached trailing-newline state. No GEX formulas or E*TRADE values were changed. The production interaction layer continues to make compact Pine-router-safe `MASTER A6` the default and keeps this verbose structure only under `MASTER A6 // GOOGLE SHEETS FULL`.
- **Important behavior that must remain:** Default `MASTER A6` stays compact and Pine-router-safe. The full Google-Sheets option is audit/reference only and must be rebuilt deterministically from structured results. Never trust stale `summaryText` to control TradingView transport formatting, and never strip meaningful integer zeroes from OI.
- **Files/features intentionally NOT changed:** `src/gex_ui.py` calculations, `src/gex_workspace_v2.py` E*TRADE/session plumbing, Risk Sizing, OAuth, Holdings, Bull Debit, Muni, Orders, navigation, shared theme, Touch ID/authentication, and `src/terminal_core.py`.
- **Tests performed:** Compared the supplied working Google-Sheets output with the supplied Streamlit output and verified the successful-block boundary difference; emulated the uploaded Pine router against the Streamlit data and confirmed NVDA yields 14 packed rows; inspected the committed production diff; GitHub Actions `validate-boundaries` passed on code commit `4004ba2458dd90edcf81038b9ad49a8eb763f5bd`.
- **Architecture guard result:** PASS.
- **Commit SHA:** `4004ba2458dd90edcf81038b9ad49a8eb763f5bd` plus this changelog commit.
- **Lesson:** Keep human-readable audit serialization deterministic, but keep the actual Pine transport minimal. A screenshot that still shows `Mode`, `Contracts`, `Source URL`, and the instruction separator under default `MASTER A6` is showing an older verbose bridge build, not the current Pine-router-safe production output.

## 2026-09-15 — High-contrast GEX TradingView copy control

- **Feature changed:** GEX TradingView bridge visibility / copy-to-clipboard affordance.
- **Exact production file(s) changed:** `src/gex_ui_v3.py`, `src/TERMINAL_CHANGELOG.md`.
- **What was broken:** The built-in copy-to-clipboard icon on the GEX TradingView code block could blend into the terminal's dark background and be difficult to see.
- **Root cause:** Streamlit's native code-block copy control inherited generic theme colors, while the terminal deliberately uses dark surfaces and some global black text for other widgets. The GEX bridge did not explicitly force contrast for the copy button's SVG/icon states.
- **What was changed:** Added a plain-string `_TRADINGVIEW_CODE_CSS` stylesheet scoped only to `.st-key-gexv3_bridge_code`. The code block now keeps a near-black background, bright near-white code text, and an orange-edged frame. The native copy control is forced visible with a dark button background, Bloomberg-orange border/text/SVG strokes/fills, and full opacity. Hover/focus reverses to orange background with black icon/text plus a visible focus ring. The style is emitted with style-only `st.html()` immediately before the code block so it does not add a visible spacer row.
- **Important behavior that must remain:** The copy icon must remain clearly visible on black in all normal/hover/focus states. Keep this selector scoped to the GEX TradingView bridge; do not globally recolor every Streamlit button or code block. Preserve the MASTER A6 payload and Pine router logic unchanged.
- **Files/features intentionally NOT changed:** GEX formulas/math in `src/gex_ui.py`, E*TRADE/session plumbing, Overview/Analytics calculations, Risk Sizing, OAuth, Holdings, Bull Debit, Muni, Orders, navigation, shared theme, Touch ID/authentication, and `src/terminal_core.py`.
- **Tests performed:** Re-fetched the exact committed production `src/gex_ui_v3.py` and confirmed the copy selectors are confined to `.st-key-gexv3_bridge_code`, the stylesheet is a normal triple-quoted string rather than an f-string, and the existing TradingView renderer emits it via `st.html()` before the code container. GitHub Actions `validate-boundaries` passed on code commit `1e9f8511f287208a469abe96c951ee3c9e4eb48b`.
- **Architecture guard result:** PASS.
- **Commit SHA:** `1e9f8511f287208a469abe96c951ee3c9e4eb48b` plus this changelog commit.
- **Lesson:** High-contrast native controls should be styled at the narrowest feature-owned container possible. Dark terminal surfaces need explicit icon/SVG colors as well as text colors so controls never disappear into the background.

## 2026-09-16 — Bounded parallel Refresh All GEX

- **Feature changed:** GEX Refresh All throughput / backend scheduling.
- **Exact production file(s) changed:** `src/gex_ui_v3.py`, `src/TERMINAL_CHANGELOG.md`.
- **What was broken:** The detached Refresh All job no longer blocked Streamlit, but it still refreshed the 49-symbol watchlist one ticker at a time and therefore took much longer than the Google Apps Script/CBOE workflow.
- **Root cause:** The CBOE Apps Script typically obtains the entire option universe for one ticker in a single JSON fetch, while the E*TRADE path requires a quote request, an expiration-list request, and then a separate option-chain request for every eligible expiration up to that ticker's DTE. The original background scheduler compounded that network cost by allowing only one ticker to be in flight at a time.
- **What was changed:** Replaced the serial ticker loop with a bounded `ThreadPoolExecutor` capped at four ticker workers. Each pool thread lazily creates and reuses its own dedicated E*TRADE OAuth client from the existing captured credential/token factory, while each ticker still calls the exact same `core._build_gex()` path with the same DTE, timezone, wall mode, expirations, option-chain inputs, and GEX calculations. Job status now reports `4-WAY`, tracks the active ticker set, increments processed count as futures complete, and keeps result merging on the Streamlit render thread.
- **Important behavior that must remain:** Keep GEX concurrency bounded at four unless E*TRADE behavior is deliberately re-evaluated. Never share one OAuth session across pool threads, never call `st.*` or access `st.session_state` from background workers, and never change GEX formulas/expiration coverage merely for speed. Continue allowing only one Refresh All batch per vault and continue merging completed results into Streamlit-owned state only on the render thread.
- **Files/features intentionally NOT changed:** `src/gex_ui.py` formulas/math and expiration selection, `src/gex_workspace_v2.py` credential/session plumbing, Risk Sizing, Holdings, OAuth UI, Bull Debit, Muni, Orders, navigation, Touch ID/authentication, shared theme, and `src/terminal_core.py`.
- **Tests performed:** Re-fetched the exact committed `src/gex_ui_v3.py` and confirmed the four-worker executor, thread-local E*TRADE clients, unchanged `_base.core._build_gex()` call, active-ticker status, and no Streamlit calls inside worker functions. GitHub Actions `validate-boundaries` passed on code commit `d0fc1ec49baf0fd25bdbfb285244e0553b41ef0b`.
- **Architecture guard result:** PASS.
- **Commit SHA:** `d0fc1ec49baf0fd25bdbfb285244e0553b41ef0b` plus this changelog commit.
- **Lesson:** Making a market-data batch non-blocking does not make it fast. When the data source requires multiple synchronous requests per ticker, improve throughput in the GEX scheduler with conservative bounded concurrency while leaving the calculation engine unchanged.

## 2026-09-16 — MASTER A6 always-copyable Google Apps Script bridge

- **Feature changed:** GEX TradingView MASTER A6 export finishing behavior and copy affordance.
- **Exact production file(s) changed:** `src/gex_ui_v3.py`, `src/gex_workspace_v2.py`, `src/TERMINAL_CHANGELOG.md`.
- **What was broken:** A failed/no-data ticker could make the older TradingView view say the Pine master was not ready and hide the useful copy block; the native Streamlit clipboard action was also visually an icon instead of the explicit MASTER A6 copy control Raj requested.
- **Root cause:** The older bridge treated complete-ticker coverage as a prerequisite for presenting MASTER A6 and optimized the transport for a compact Pine-only payload. Raj's supplied Google Apps Script does the opposite: it always assembles A6 in saved-ticker order, emits full `summaryText` for successful symbols, emits `Ticker:` + `ERROR:` for failed symbols, trims the final joined text, and still exposes Copy A6.
- **What was changed:** Preserved the current Google Apps Script A6 serializer as the default MASTER export, kept failed/no-result symbols as `Ticker:`/`ERROR:` blocks so one bad ticker never removes the master block, and made Streamlit's real code-block clipboard control visibly read `COPY MASTER A6`. The MASTER heading now explicitly says `GOOGLE APPS SCRIPT FORMAT`. Bumped the visible GEX build marker to `v2026.09.16.03`.
- **Important behavior that must remain:** MASTER A6 must stay copyable whenever saved tickers exist, even when one or more symbols have no GEX data. Successful ticker blocks must remain in the supplied Apps Script field/order contract: `Ticker`, `Mode`, `Spot`, `Max DTE Used`, `Contracts Used`, `Net Current GEX`, `Source URL`, blank line, Pine instruction/separator, then `SPOT`, optional `GFLIP`, `CALLWALL`, `PUTWALL`, `MAXCALLOI`, `MAXPUTOI`, and ranked `GEXPOS/GEXNEG` rows. Failed symbols remain `Ticker:` + `ERROR:` blocks rather than blocking the whole A6.
- **Files/features intentionally NOT changed:** GEX formulas/math in `src/gex_ui.py`, Risk Sizing, OAuth, Holdings, navigation, Bull Debit, Muni, Orders, shared theme, and `src/terminal_core.py`.
- **Tests performed:** Python syntax compilation for the changed GEX Python files; `python scripts/validate_architecture.py`; static assertions that MASTER A6 serializer retains Google Apps Script error blocks and the visible clipboard label is present.
- **Architecture guard result:** PASS if this one-time workflow commit is present on `main`.
- **Commit SHA:** recorded by the resulting workflow commit.
- **Lesson:** MASTER A6 is a copy/export contract, not a completeness gate. Match the supplied Apps Script serialization first; failed tickers should degrade locally, never suppress the entire TradingView paste block.

## 2026-09-16 — GEX script parity, faster gamma flip, and IV Rank

- **Feature changed:** GEX calculation parity/performance, Overview IV Rank reference, and production build marker.
- **Exact production file(s) changed:** `src/gex_ui.py`, `src/gex_ui_legacy.py`, `src/gex_ui_v3.py`, `src/gex_ui_v3_proven.py`, `src/gex_workspace_v2.py`, `src/TERMINAL_CHANGELOG.md`.
- **What was broken:** Cold GEX refreshes still spent avoidable CPU time on a 1,000-step gamma-flip rescan; the production calculation labeled itself as matching the supplied Apps Script while Call Wall was selected by maximum call OI and Put Wall by minimum net GEX instead of the script's strongest positive call-GEX / strongest negative put-GEX definitions; the Overview also had no IV Rank + Cheap/Neutral/Expensive reference column.
- **Root cause:** The original calculation engine accumulated later performance/UI work without reconciling the legacy wall/flip definitions to Raj's supplied CBOE/Apps Script reference. The flip calculation evaluated 1,000 sequential price points over a wider strike-derived range. E*TRADE supplies current option IV but no ready-made 52-week IV Rank series, so a truthful rank needs a retained IV history rather than a one-chain shortcut.
- **What was changed:** Preserved the previously deployed calculation module byte-for-byte as `src/gex_ui_legacy.py` and the proven v3 interaction/TradingView implementation byte-for-byte as `src/gex_ui_v3_proven.py`. The production `src/gex_ui.py` is now a calculation adapter that keeps the same E*TRADE quote/expiration/chain inputs and full expiration coverage, uses 35% fallback IV, selects Call Wall from maximum positive call-side GEX, selects Put Wall from minimum negative put-side GEX, and computes Gamma Flip with the supplied 160-step ±20% spot scan. The scan is evaluated as one NumPy matrix, returning the first interpolated zero crossing or the minimum-absolute-gamma fallback. `src/gex_ui_v3.py` adds one compact `IV RANK / REF` Overview column without touching TradingView serialization. IV Rank uses one already-fetched E*TRADE ATM IV observation per ticker/day, a rolling 365-day min/max formula, `<30 = Cheap`, `30–70 = Neutral`, `>70 = Expensive`, shows `N/A | Building` before 10 daily observations, and marks ranks provisional with `P` until the stored span reaches roughly a full year. No additional market-data request is made for IV Rank. Ticker deletion also deletes that ticker's stored IV history. The visible GEX build marker is `v2026.09.16.04`.
- **Important behavior that must remain:** Preserve the existing 4-way ticker scheduler, 20 chain-prefetch slots, ~3.7 E*TRADE MARKET request starts/sec gate, one up-to-50-symbol batch quote, 5-minute quote/chain cache, 6-hour expiration cache, complete eligible expiration coverage, multi-ticker support, live E*TRADE/session context, and non-blocking background Refresh All. MASTER A6 / Pine field names and row order remain unchanged: `SPOT`, optional `GFLIP`, `CALLWALL`, `PUTWALL`, `MAXCALLOI`, `MAXPUTOI`, then ranked `GEXPOS/GEXNEG`. Do not add IV Rank to the Pine payload unless Raj explicitly changes the TradingView script contract.
- **Files/features intentionally NOT changed:** TradingView/Pine script; `src/gex_ui_v3_base.py`; E*TRADE request/cache mechanics in `src/gex_workspace_v2.py` except the build-version constant; `src/etrade_client.py`; Risk Sizing; E*TRADE OAuth UI; Holdings; Bull Debit; Muni; Orders; top navigation; Touch ID/authentication; shared theme; and `src/terminal_core.py`.
- **Tests performed:** Production route re-traced from `streamlit_app.py` to `src/gex_workspace_v2.py` to `src/gex_ui_v3.py`; new production GEX adapters syntax-compiled; synthetic 320-contract gamma-flip benchmark measured approximately 15.7 ms for the old 1,000-step loop versus 1.6 ms for the new vectorized 160-step scan (about 10× lower flip CPU time on that sample); synthetic wall test confirmed Call Wall can differ from max Call OI and is now selected by call GEX; representative-IV parsing/ranking checks passed; packed Pine type order remained `SPOT,GFLIP,CALLWALL,PUTWALL,MAXCALLOI,MAXPUTOI,...`; Google Drive GEX reference was re-read and confirms the strongest-positive-call-GEX / strongest-negative-put-GEX wall convention; PR diff confirmed no unrelated feature files changed; GitHub Actions `Terminal Architecture Guard` passed the production boundary/syntax check on the staged code.
- **Architecture guard result:** PASS.
- **Commit SHA:** calculation/parity stage `5694e9ff607c772c21a8af700e8cfe4146da4691`; IV lifecycle hardening `51dba8d9d44d529f5407dc15d3b043a3c93e92f2`; build-marker stage `a0f139bea5f154b86073bf780cf896d323bf3243`; plus the final changelog/merge commit.
- **Lesson:** Do not trade GEX correctness for apparent speed by dropping weekly/daily expirations. Keep E*TRADE network concurrency bounded and cache-aware, remove avoidable calculation work, match the supplied script's wall/flip definitions exactly, and never manufacture IV Rank from a single current chain.

## 2026-09-16 — Fill the bounded GEX MARKET pipeline

- **Feature changed:** GEX Refresh All scheduler throughput / cold-load latency hiding.
- **Exact production file(s) changed:** `src/gex_ui_v3.py`, `src/gex_workspace_v2.py`, `src/TERMINAL_CHANGELOG.md`.
- **What was broken:** The live `v2026.09.16.04` process log proved Refresh All was still running only four top-level ticker workers for a 48-symbol watchlist. Even with 20 chain-prefetch slots, the pipeline could sit below the existing ~3.7 MARKET request-starts/sec ceiling while those four tickers waited on quote/expiration-list network responses.
- **Root cause:** Scheduler concurrency and request-rate limiting were coupled too conservatively. Every ticker must obtain its expiration list before its option-chain prefetches can be queued, so four top-level workers could underfill the already-bounded MARKET pipeline when E*TRADE response latency was several seconds.
- **What was changed:** The production `src/gex_ui_v3.py` wrapper now raises only the non-blocking Refresh All ticker-worker cap from 4 to 20, matching the existing 20 chain-fetch slots. The existing shared `0.27s` MARKET start gate in `src/gex_workspace_v2.py` remains unchanged and authoritative at about 3.7 request starts/sec, so the change increases in-flight latency hiding rather than the API request-start rate. The visible build marker/process log is now `v2026.09.16.05 // 20 CALC // 20 FETCH // 3.7 RPS`.
- **Important behavior that must remain:** Keep the shared ~3.7 MARKET request-start gate, one up-to-50-symbol batch quote, 5-minute quote/chain cache, 6-hour expiration cache, exact eligible expiration coverage, 100-strike `CALLPUT` requests, current GEX/IV Rank formulas, multi-ticker state, live E*TRADE/session context, non-blocking Refresh All, and MASTER A6/TradingView serialization unchanged.
- **Files/features intentionally NOT changed:** `src/gex_ui.py` calculation formulas, `src/gex_ui_v3_proven.py` proven interaction/TradingView serializer, `src/etrade_client.py`, Risk Sizing, E*TRADE OAuth UI, Holdings, Bull Debit, Muni, Orders, top navigation, Touch ID/authentication, shared theme, and `src/terminal_core.py`.
- **Tests performed:** Python syntax compilation for the affected GEX Python files; static checks that the production wrapper sets 20 ticker workers while the workspace MARKET interval remains `0.27`; `python scripts/validate_architecture.py`; production route inspection; PR changed-file inspection; GitHub Actions architecture guard before merge and production verification after merge.
- **Architecture guard result:** PASS required before merge.
- **Commit SHA:** final production merge commit recorded by GitHub after validation.
- **Lesson:** When a separate global request-start gate already enforces the E*TRADE ceiling, top-level worker count should be high enough to keep that gate fed across slow network round trips. Do not confuse worker concurrency with request-start rate, and do not sacrifice expiration coverage or TradingView correctness for apparent speed.

## 2026-09-17 — Restore Pine-router-safe GEX MASTER A6

- **Feature changed:** GEX TradingView bridge / MASTER A6 transport compatibility.
- **Exact production file(s) changed:** `src/gex_ui_v3.py`, `src/gex_workspace_v2.py`, `src/TERMINAL_CHANGELOG.md`.
- **What was broken:** Pasting the default terminal MASTER A6 into the existing `GEX TEST` TradingView indicator could leave the chart blank even though GEX data had refreshed.
- **Root cause:** The default copy payload had regressed to the verbose Google-Sheets-style A6 report. The verified Pine router routes on exact `Ticker: SYMBOL` headers and consumes packed rows: `SPOT`, `GFLIP`, `CALLWALL`, `PUTWALL`, `MAXCALLOI`, `MAXPUTOI`, `GEXPOS*`, and `GEXNEG*`.
- **What was changed:** Restored compact Pine-router transport as the first/default MASTER A6 choice. Kept the verbose linked-Google-Sheet A6 as a separate reference option. Added transport and per-chart ticker parser checks.
- **Important behavior that must remain:** Default TradingView copy output must be exact `Ticker:` headers plus packed parser rows only. Never make the verbose Sheets report the default Pine payload again. Do not change GEX math or E*TRADE/session behavior to fix transport.
- **Files/features intentionally NOT changed:** `src/gex_ui.py` formulas, `src/gex_ui_v3_proven.py`, Risk Sizing, OAuth, Holdings, navigation, Bull Debit, Muni, Orders, shared theme, authentication, and `src/terminal_core.py`.
- **Tests performed:** Linked Google Sheet `TradingView Bridge!A6` and `Packed Gamma Levels` inspected; prior Pine-router contract compared line-by-line; production Python syntax check and architecture guard run in CI.
- **Architecture guard result:** PASS on PR validation.
- **Commit SHA:** code staging commit `abff5891893a6b20e7183a1099165f9f0e70d881`; final production merge SHA recorded by GitHub after merge.
- **Lesson:** Human-readable Google Sheets A6 and Pine transport are different representations. Keep TradingView default minimal and parser-native; retain the full sheet-shaped block only for reference/audit.

## 2026-09-16 — Quote-wrap TradingView GEX transport

- **Feature changed:** GEX TradingView / Pine `Packed Gamma Levels` copy transport.
- **Exact production file(s) changed:** `src/gex_ui_v3.py`, `src/gex_workspace_v2.py`, `src/TERMINAL_CHANGELOG.md`.
- **What was broken:** The compact Pine-router payload had the correct internal ticker headers and packed rows but was copied as bare multiline text. The working TradingView input requires the whole paste to be one quoted string value, with an opening double quote before the first `Ticker:` and a closing double quote immediately after the final packed row.
- **Root cause:** The prior router fix corrected the internal row grammar but omitted the outer Pine string delimiters shown by the working paste.
- **What was changed:** Pine-safe MASTER and single-ticker copy payloads are now wrapped exactly once with one leading and one trailing double quote. Parser validation still runs on the unquoted internal payload, and a separate check rejects a Pine-safe copy payload missing either outer quote. The Google Sheets full/reference block remains unchanged. Visible build bumped to `v2026.09.17.07`.
- **Important behavior that must remain:** Pine-safe output begins with `"Ticker: SYMBOL` and ends with the final packed-row value followed immediately by `"`. Do not quote each line. Preserve the internal `Ticker:` plus `SPOT/GFLIP/CALLWALL/PUTWALL/MAXCALLOI/MAXPUTOI/GEXPOS*/GEXNEG*` contract exactly.
- **Files/features intentionally NOT changed:** GEX calculations, E*TRADE request/session plumbing, Refresh All concurrency/rate limits, IV Rank, Google Sheets full/reference serialization, the TradingView Pine script, Risk Sizing, OAuth, Holdings, navigation, Bull Debit, Muni, Orders, shared theme, authentication, and `src/terminal_core.py`.
- **Tests performed:** Quote-contract static assertions; Python syntax compilation for changed GEX production files; `python scripts/validate_architecture.py`; PR changed-file inspection; post-merge architecture guard required.
- **Architecture guard result:** PASS on staging before code commit.
- **Commit SHA:** `e456f1fb4b0bd25e1b22226d92c54742843f8b57` (production code stage); final merge SHA recorded by GitHub after merge.
- **Lesson:** The TradingView contract has two layers: parser-native packed rows internally and one pair of outer double quotes around the complete multiline paste externally.


## 2026-09-17 — Larger TradingView copy control

- **Feature changed:** GEX TradingView Bridge copy/paste interaction.
- **Exact production file(s) changed:** `src/gex_ui_v3.py`, `src/gex_workspace_v2.py`, `src/TERMINAL_CHANGELOG.md`.
- **What was broken:** The Pine-safe payload was correctly wrapped in one opening and one closing double quote, but the actual Streamlit copy control remained a tiny icon that was easy to miss during the TradingView paste workflow.
- **Root cause:** The bridge relied on the default `st.code` copy-button presentation even though copy/paste is the primary action in this subtab.
- **What was changed:** Kept Streamlit's real working clipboard action and enlarged/styled that exact copy control inside only the keyed GEX TradingView bridge container. Its visible label is `COPY FOR TRADINGVIEW`. The instruction line is now a compact three-step copy → TradingView field → paste flow and explicitly says the outer double quotes are already included. Visible GEX build bumped to `v2026.09.17.08`.
- **Important behavior that must remain:** Pine-safe MASTER and single-ticker output must still begin with one `"` before the first `Ticker:` and end with one `"` immediately after the final packed row. Do not quote each line and do not strip the outer quotes when copying.
- **Files/features intentionally NOT changed:** GEX calculations, IV Rank, E*TRADE/session plumbing, Refresh All concurrency/rate limits, TradingView Pine script, Google Sheets full/reference serialization, Risk Sizing, OAuth, Holdings, navigation, Bull Debit, Muni, Orders, shared theme, authentication, and `src/terminal_core.py`.
- **Tests performed:** Python syntax compilation of changed GEX production files; exact quote-contract source assertions; scoped copy-button CSS assertions; `python scripts/validate_architecture.py`; PR changed-file inspection; post-merge architecture guard required.
- **Architecture guard result:** PASS on staging before merge.
- **Commit SHA:** staging commit recorded by GitHub; final production merge SHA recorded after merge.
- **Lesson:** Do not replace a working clipboard mechanism with a custom/fake button. Improve the existing `st.code` clipboard action and scope presentation to the GEX bridge only.

## 2026-09-17 — Full per-ticker TradingView GEX blocks

- **Feature changed:** GEX TradingView Bridge copy/paste transport.
- **Exact production file(s) changed:** `src/gex_ui_v3.py`, `src/gex_workspace_v2.py`, `src/TERMINAL_CHANGELOG.md`.
- **What was broken:** The default TradingView copy payload had become too compact and omitted the metadata header used by the proven working multi-ticker paste.
- **Root cause:** The prior export optimized for parser-minimal `Ticker:` plus packed rows even though the uploaded Pine script safely ignores summary rows and reads `Mode`, `Max DTE Used`, `Contracts Used`, and `Net Current GEX` metadata inside the matched ticker block.
- **What was changed:** Default MASTER now emits, for every refreshed ticker, `Ticker`, `Mode`, `Spot`, `Max DTE Used`, `Contracts Used`, `Net Current GEX`, `Source URL`, then the existing Pine instruction/separator and packed gamma rows. The complete multi-ticker payload is wrapped exactly once with one opening and one closing double quote. Compact Pine remains optional diagnostic only. Build bumped to `v2026.09.17.09`.
- **Important behavior that must remain:** Keep metadata + gamma levels together for each ticker, preserve exact `Ticker:` boundaries, and never quote each line individually.
- **Files/features intentionally NOT changed:** GEX math, IV Rank, E*TRADE/session plumbing, refresh concurrency/rate limits, Pine script, Risk Sizing, OAuth, Holdings, navigation, Bull Debit, Muni, Orders, theme, authentication, and `src/terminal_core.py`.
- **Tests performed:** Python syntax compilation, quote/metadata source assertions, `python scripts/validate_architecture.py`, PR changed-file inspection, and post-merge architecture guard.
- **Architecture guard result:** PASS required before merge.
- **Commit SHA:** code commit recorded on staging branch; final production merge SHA recorded by GitHub after merge.
- **Lesson:** Match the proven full ticker block for TradingView: metadata first, gamma rows second, one quoted multiline payload.

## 2026-09-19 — Separate E*TRADE and Schwab Risk Sizing tabs

- **Feature changed:** Risk Sizing broker separation / top-level navigation.
- **Exact production file(s) changed:** `streamlit_app.py`, `src/tab_bar_v4.py`, `src/components/terminal_tabs_v3/index.html`, `src/schwab_risk_sizing_ui.py`, `src/ARCHITECTURE.md`, `src/production_manifest.py`, `scripts/validate_architecture.py`, `src/TERMINAL_CHANGELOG.md`.
- **What was broken:** The terminal had only one generic `RISK SIZING` tab, so there was no separate place to add a future Charles Schwab holdings/quote-backed risk workflow without risking E*TRADE state crossover.
- **Root cause:** Risk Sizing had only the existing E*TRADE production route and no broker-specific top-level separation or Schwab-owned module.
- **What was changed:** Kept the stable internal `RISK SIZING` route and existing E*TRADE renderer unchanged, but display it as `E*TRADE RISK SIZING`. Added a separate `SCHWAB RISK SIZING` top-level route and an isolated Schwab-owned readiness shell. The Schwab shell explicitly does not read E*TRADE holdings, quotes, or account state while Schwab developer/API credentials are unavailable. Updated architecture/manifest ownership and extended the architecture guard so the Schwab route and E*TRADE display label cannot silently disappear.
- **Important behavior that must remain:** Existing E*TRADE Risk Sizing continues to use `streamlit_app.py → src/risk_sizing_ui_v7.py → src/risk_sizing_ui_v10.py`; its saved tab state/order remains compatible because the internal route key stays `RISK SIZING`. Schwab must keep separate broker state and must eventually use Schwab holdings/balances/quotes with the same shared formulas in `src/risk_sizing.py`; never substitute E*TRADE data into the Schwab tab.
- **Files/features intentionally NOT changed:** `src/risk_sizing_ui_v10.py`, `src/risk_sizing_ui_v9.py`, `src/risk_sizing_ui_v2.py`, `src/risk_sizing.py`, `src/trade_math.py`, GEX, E*TRADE OAuth, Holdings, Bull Debit, Muni, Orders, shared theme, authentication, and `src/terminal_core.py`.
- **Tests performed:** Exact production import path and prior Risk Sizing history reviewed; changed-file diff inspected; existing E*TRADE dispatch confirmed unchanged; new Schwab dispatch and navigation label checked; navigation component JavaScript syntax-checked; PR #9 Terminal Architecture Guard run `35459085420` passed and parsed all production Python files including the new Schwab module.
- **Architecture guard result:** PASS on code commit `f8806cee58984fb953444e84de6feab5672f01eb`; final changelog-state guard required before merge.
- **Commit SHA:** feature code commit `f8806cee58984fb953444e84de6feab5672f01eb`; final production merge SHA recorded by GitHub after merge.
- **Lesson:** Keep broker-specific Risk Sizing state and data sources isolated. A second broker gets its own route/owner module; shared formulas may be reused, but holdings, balances, quotes, and session state must never cross between E*TRADE and Schwab.

## 2026-09-19 — Force-remount navigation after Schwab tab split

- **Feature changed:** Top navigation deployment/remount behavior for the new broker-separated Risk Sizing tabs.
- **Exact production file(s) changed:** `src/tab_bar_v4.py`, `src/TERMINAL_CHANGELOG.md`.
- **What was broken:** After the Schwab Risk Sizing split was merged to `main`, the live Streamlit page still displayed the older six-tab navigation with `RISK SIZING` and no `SCHWAB RISK SIZING` tab.
- **Root cause:** GitHub `main` contained the new navigation and routing, so the observed page was still using an older mounted/deployed navigation instance rather than the current committed tab definition.
- **What was changed:** Changed only the custom navigation component key so Streamlit must create a fresh navigation component instance on the next production code load. This preserves the existing tab order logic, E*TRADE internal route key, Schwab route, 48px navigation height, and saved-order cleanup behavior.
- **Important behavior that must remain:** Visible `E*TRADE RISK SIZING` must continue to map to the stable internal `RISK SIZING` route; `SCHWAB RISK SIZING` remains a separate top-level route; navigation remains exactly 48px high and must not resize arbitrary ancestors.
- **Files/features intentionally NOT changed:** Risk Sizing renderer/math files, GEX, E*TRADE OAuth, Holdings, Bull Debit, Muni, Orders, shared theme, authentication, component HTML, and `src/terminal_core.py`.
- **Tests performed:** Current `main` architecture and prior change history re-read; production routing re-fetched and confirmed; navigation-only component-key change reviewed; architecture guard required on PR and after merge.
- **Architecture guard result:** PASS on PR run `35463961043`.
- **Commit SHA:** final production merge SHA recorded after validation.
- **Lesson:** When current GitHub routing is correct but an existing Streamlit session still shows a prior custom-component mount, force a new component key rather than stacking changes into feature content.



## 2026-09-19 — Live GEX Refresh All progress without browser refresh

- **Feature changed:** GEX Refresh All live progress / automatic UI result refresh.
- **Exact production file(s) changed:** `src/gex_ui_v3.py`, `src/gex_workspace_v2.py`, `src/TERMINAL_CHANGELOG.md`.
- **What was broken:** Refresh All ran correctly in the detached background worker, but the GEX page only re-read the worker registry during a Streamlit rerun. Raj therefore had to use browser refresh / Control-Refresh and sometimes re-enter the terminal before the processed/updated count and newly finished ticker rows became visible.
- **Root cause:** `src/gex_ui_v3_proven.py` already maintained thread-safe live job state and synced completed results, but the production wrapper had no active UI polling loop. The existing running-status snapshot was therefore stale until some unrelated interaction reran the page.
- **What was changed:** Added a GEX-only native Streamlit fragment in `src/gex_ui_v3.py` that polls the existing thread-safe background status once per second only while the job is `QUEUED` or `RUNNING`. The fragment calls the proven result-sync path on the Streamlit UI thread and requests a normal app rerun when processed/updated counts advance or the job reaches a terminal state. This makes the visible processed count update live and promotes newly completed ticker results into Overview/Analytics/TradingView without a browser refresh. Polling stops automatically after the job finishes. The visible GEX build marker was bumped to `v2026.09.19.10`.
- **Important behavior that must remain:** Background worker threads must never call Streamlit or touch `st.session_state`; only the UI fragment may read the thread-safe job registry and sync results. Preserve the existing 20 ticker workers, 20 chain-prefetch slots, ~3.7 E*TRADE MARKET request-start gate, batch quotes, caches, GEX formulas, IV Rank behavior, multi-ticker state, hover process log, TradingView serialization, and live E*TRADE/session context. Do not replace this with browser auto-refresh or login-reset behavior.
- **Files/features intentionally NOT changed:** `src/gex_ui.py` GEX formulas; `src/gex_ui_v3_proven.py`; `src/etrade_client.py`; E*TRADE OAuth/login UI; Risk Sizing; Schwab Risk Sizing; Holdings; navigation; Bull Debit; Muni; Orders; shared theme; authentication; and `src/terminal_core.py`.
- **Tests performed:** Re-read `src/ARCHITECTURE.md` and prior GEX history; traced production route `streamlit_app.py → src/gex_workspace_v2.py → src/gex_ui_v3.py → src/gex_ui_v3_proven.py`; inspected PR #11 changed-file boundary; verified the polling wrapper is installed only during GEX render and restored in `finally`; verified polling is active only for `QUEUED/RUNNING` and stops after completion; verified the fragment uses the existing UI-thread sync path before full-app rerender; confirmed no non-GEX production files are changed; GitHub Actions `Terminal Architecture Guard` run `35487192017` executed `python scripts/validate_architecture.py` and reported that production routes parse successfully with no forbidden module-level Streamlit monkey patches.
- **Architecture guard result:** PASS on PR #11 run `35487192017`.
- **Commit SHA:** live polling code `bf15961ff97848e917b0d0790e749b7dfd5e71a7`; build marker `a9cb38921411e02350010d1054544707447f40a6`.
- **Lesson:** A detached Streamlit background worker needs a UI-thread polling bridge if progress must appear live. Poll the existing thread-safe registry with a native fragment, sync completed results on the render thread, and rerun the app from session state; never force the user to browser-refresh an authenticated terminal just to see background progress.


## 2026-09-20 — Auto-refresh GEX on login with live loading bar

- **Feature changed:** GEX automatic Refresh All after E*TRADE login, persisted Settings control, and live loading progress.
- **Exact production file(s) changed:** `src/gex_ui.py`, `src/gex_ui_v3.py`, `src/gex_workspace_v2.py`, `streamlit_app.py`, `src/ARCHITECTURE.md`, `src/TERMINAL_CHANGELOG.md`.
- **What was broken / missing:** Refresh All still required a manual button press after login; there was no saved ON/OFF preference for automatic refresh; and although background progress text updated live, there was no real-time loading bar showing how far the multi-ticker refresh had progressed.
- **Root cause:** The GEX state schema had no persisted auto-refresh preference, the background engine had no one-per-login trigger, and the live polling fragment rendered status text only. Also, a true login-triggered refresh had to work when a non-GEX top-level tab was active without moving GEX behavior into OAuth.
- **What was changed:** Added `auto_refresh_on_login` to the production GEX persisted state schema with a default of ON. Added an `AUTO REFRESH ALL GEX ON E*TRADE LOGIN` toggle at the top of GEX Settings; it saves immediately into the existing browser-persisted GEX state so the prior session's ON/OFF choice is restored. Added a non-sensitive SHA-256-derived login marker from the active E*TRADE token/issued-at snapshot and a session marker so at most one automatic Refresh All starts per authenticated login/session. When GEX is active, its normal render path starts the job; when another top-level tab is active, `streamlit_app.py` dispatches only the GEX-owned zero-height state/start hook so refresh can begin in the background without changing OAuth or other tab content. Added a one-second live `st.progress` bar showing processed/total, updated count, percent complete, and a compact list of active tickers. The prior auto-rerender/result-sync behavior remains in place so finished rows appear automatically. Visible GEX build bumped to `v2026.09.20.11`.
- **Important behavior that must remain:** Auto-refresh must never start more than once per authenticated login/session; a manual Refresh All already in progress satisfies the login refresh instead of creating a duplicate job. The saved toggle must survive browser/app sessions and default ON only when no prior choice exists. Background worker threads must never call Streamlit or touch `st.session_state`; only UI-thread code may load saved GEX state, sync completed results, or render progress. Preserve the 20 ticker workers, 20 chain-prefetch slots, ~3.7 E*TRADE MARKET request-start gate, quote/chain/expiration caches, GEX formulas, IV Rank, multi-ticker state, TradingView serialization, and existing E*TRADE session behavior.
- **Files/features intentionally NOT changed:** `src/etrade_connection_ui_v2.py`, `src/etrade_client.py`, session-persistence/token semantics, Risk Sizing, Schwab Risk Sizing, Holdings, navigation component files, Bull Debit, Muni, Orders, shared theme, authentication logic, `src/terminal_core.py`, and GEX formula calculations.
- **Tests performed:** Re-read `src/ARCHITECTURE.md` and prior GEX history; traced production path `streamlit_app.py → src/gex_workspace_v2.py → src/gex_ui_v3.py`; verified the existing `gex_state_v1` component is zero-height before allowing cross-tab login dispatch; inspected the final changed-file boundary; verified the saved toggle writes through `_core._save_state`, the login marker prevents duplicate automatic jobs, a running manual job satisfies the login trigger, and the progress fragment remains on a one-second poll while syncing completed results. The first PR guard correctly caught that the original GEX import string must remain present for the architecture contract; the import was restored unchanged and the hook was added separately. GitHub Actions `Terminal Architecture Guard` run `35489671398` then executed `python scripts/validate_architecture.py` successfully.
- **Architecture guard result:** PASS on PR #12 run `35489671398` after correcting the required production import contract.
- **Commit SHA:** persisted-state stage `b8c260fb5a3cd54e439476728cfbad7dce9031b3`; login-marker/build stage `3b1b4ef27a1c4e423113e2774c44406f1bd8a2f9`; main UI stage `401b763f0b98d8e833a6a4115958ad6497c617a1`; cross-tab login dispatch stage `1b34c2c1152b708044b3eec49058a5f19c99908d`; architecture-contract correction `2105ad32910a6747b741656ad82af625d277af12`.
- **Lesson:** Keep login-triggered GEX work GEX-owned. Use the zero-height persisted-state component only to recover saved GEX preferences, derive a non-sensitive per-login marker, start the existing bounded background job once, and render progress through the UI polling fragment; do not implement this by modifying OAuth/token semantics or by browser-refreshing the terminal.


## 2026-09-22 — Dataframe column-menu readability and Risk Book fit-to-data width

- **Feature changed:** Shared dataframe column-menu appearance and E*TRADE Risk Sizing Risk Book table presentation.
- **Exact production file(s) changed:** `src/theme.py`, `src/risk_sizing_ui_v2.py`, `src/TERMINAL_CHANGELOG.md`.
- **What was broken:** The three-dot menu on dataframe column headers opened with black labels/icons on the terminal's dark menu background, making actions such as the column name and Statistics effectively unreadable. The Risk Book table also forced `width="stretch"`, so its columns expanded across unused space instead of sizing to their data.
- **Root cause:** Raj's Terminal intentionally uses a black global Streamlit `textColor` so the native interactive dataframe header text stays black on the orange header band. Streamlit's dataframe column menu is rendered in a separate overlay portal and inherited that black foreground on its dark panel. Separately, the Risk Book explicitly requested a stretched dataframe width even though Streamlit supports content-sized tables.
- **What was changed:** Added a CSS-only shared appearance override scoped to Streamlit's exact dataframe overlay testids (`stDataFrameColumnMenu`, `stDataFrameStatisticsMenu`, and `stDataFrameColumnFormattingMenu`) so menu labels/icons are light and focused/hovered actions use the existing terminal accent. Changed only the Risk Book dataframe from `width="stretch"` to `width="content"`, allowing Streamlit to size the table/columns to their contents without exceeding the parent container. Added the required ownership/editing notes to the edited base Risk UI file. No Risk calculations or account data behavior changed.
- **Important behavior that must remain:** Keep dataframe headers black on orange and preserve existing red/green/blue/orange financial cell styling. Keep the menu CSS scoped to dataframe overlay testids; do not recolor unrelated popovers. Keep only the Risk Book content-sized unless another table is explicitly requested. Preserve the production Risk path `streamlit_app.py → src/risk_sizing_ui_v7.py → src/risk_sizing_ui_v10.py → v9/v2 helpers`.
- **Files/features intentionally NOT changed:** `src/risk_sizing_ui_v10.py`, `src/risk_sizing_ui_v9.py`, Risk formulas in `src/risk_sizing.py` / `src/trade_math.py`, ticker autocomplete, GEX, E*TRADE OAuth/session plumbing, Holdings, Schwab Risk Sizing, navigation, Bull Debit, Muni, Orders, authentication, and `src/terminal_core.py`.
- **Tests performed:** Re-read `src/ARCHITECTURE.md` and prior Risk Sizing history; re-traced the production import path; inspected Streamlit's current `ColumnMenu.tsx` / Statistics / Formatting menu source to verify the exact overlay testids used by the CSS; verified this repo requires Streamlit >=1.61 where content-width dataframes are supported; inspected the exact changed Risk Book call and shared CSS; inspected PR #13 file scope; GitHub Actions `Terminal Architecture Guard` run `35687081402` executed `python scripts/validate_architecture.py` and passed.
- **Architecture guard result:** PASS on the production-code stage of PR #13; a final guard is required after this changelog-only commit before merge.
- **Commit SHA:** shared menu CSS `25d145e3bf03719f3f4b7f796fa886280362b38d`; Risk Book content-width change `1c79c576bb180ca4a49fe12641fcfce8603c8645`; final merge SHA recorded by GitHub after validation.
- **Lesson:** When native Streamlit dataframe headers require black global text, explicitly style dataframe overlay menus by their own stable testids. For compact tables, remove the stretch request and use Streamlit's native content sizing instead of hard-coding per-column pixel widths.
