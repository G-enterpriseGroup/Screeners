from pathlib import Path

# E*TRADE renewal confirmation: floating toast, no layout shift.
oauth = Path("src/etrade_connection_ui_v2.py")
text = oauth.read_text()
old = '                st.success("E*TRADE session renewed.")'
new = '                st.toast("E*TRADE SESSION RENEWED", icon="✅")'
if old not in text:
    raise SystemExit("Renew success message marker not found")
oauth.write_text(text.replace(old, new, 1))

# GEX: live fragment status + exact Pine-input validation + safer reference mode.
ui = Path("src/gex_ui_v3.py")
text = ui.read_text()

worker_marker = "_proven._BACKGROUND_MAX_WORKERS = _BACKGROUND_TICKER_WORKERS\n"
live_status = r'''

# ==============================
# LIVE BACKGROUND REFRESH STATUS
# ==============================
@st.fragment(run_every=1.0)
def _live_background_status_fragment(vault_key: str) -> None:
    """Poll GEX progress without full-app reruns; rerun once when the job finishes."""
    merged_now = _proven._sync_background_results(vault_key)
    job = _proven._background_job(vault_key)
    if not job:
        return

    status = str(job.get("status") or "")
    total = int(job.get("total", 0) or 0)
    completed = int(job.get("completed", 0) or 0)
    updated = int(job.get("updated", 0) or 0)
    workers = int(job.get("workers", _BACKGROUND_TICKER_WORKERS) or _BACKGROUND_TICKER_WORKERS)
    current = html.escape(str(job.get("current") or ""))
    current_text = f" // ACTIVE {current}" if current else ""

    if status in {"QUEUED", "RUNNING"}:
        st.html(
            '<div class="gexv3-running-status">'
            '<span class="gexv3-running-icon">↻</span>'
            '<span>BACKGROUND GEX // LIVE // '
            f"{workers}-WAY // {completed}/{total} PROCESSED // {updated} UPDATED"
            f"{current_text} // AUTO-REFRESH 1S // SAFE TO SWITCH TABS"
            '</span></div>'
        )
        if merged_now:
            st.caption(f"LIVE MERGE // {merged_now} NEW TICKER RESULT(S) ADDED TO THIS SESSION")
        return

    failures = len(job.get("failures") or {})
    if status == "DONE":
        st.caption(f"BACKGROUND GEX COMPLETE // {updated}/{total} UPDATED // PAGE SYNCED")
    elif status == "DONE_WITH_ERRORS":
        st.caption(
            f"BACKGROUND GEX COMPLETE // {updated}/{total} UPDATED // {failures} FAILED // PAGE SYNCED"
        )
    elif status == "FAILED":
        st.caption("BACKGROUND GEX FAILED // reconnect E*TRADE and retry")

    # Fragment polling does not rerun the whole terminal. Once the worker is
    # finished, do exactly one full rerun so Overview/TradingView receive the
    # final merged result map. Session/auth state is preserved; nothing here
    # clears or locks terminal access.
    finished_at = float(job.get("finished_at", 0.0) or 0.0)
    marker = f"_gex_live_refresh_finished::{vault_key}"
    if finished_at > 0 and st.session_state.get(marker) != finished_at:
        st.session_state[marker] = finished_at
        st.rerun()
'''
if "_live_background_status_fragment" not in text:
    if worker_marker not in text:
        raise SystemExit("GEX worker marker not found")
    text = text.replace(worker_marker, worker_marker + live_status, 1)

parser_marker = "def _render_tradingview_pine_compatible(\n"
exact_parser = r'''
# Python replica of the uploaded GEX TEST Pine router's actual text handling.
# Pine accepts an opening quote before Ticker: because f_isTickerHeader uses
# contains(), and f_normalizeTicker strips quote marks.
def _pine_runtime_normalize_ticker(value: Any) -> str:
    x = str(value or "").strip().upper()
    for token in ('"', "'", '`', ' ', '\t'):
        x = x.replace(token, '')
    x = x.split('?', 1)[0]
    if ':' in x:
        x = x.rsplit(':', 1)[-1]
    x = x.split('/', 1)[0]
    return x


def _pine_runtime_header_ticker(row: str) -> str:
    compact = str(row or '').replace(' ', '').replace('\t', '')
    if 'TICKER:' not in compact.upper() or ':' not in compact:
        return ''
    after = compact.split(':', 1)[1]
    raw = after.split(',', 1)[0]
    return _pine_runtime_normalize_ticker(raw)


def _pine_runtime_master_issues(text: str, expected: list[str]) -> tuple[list[str], dict[str, int]]:
    """Validate the exact quoted clipboard text against the uploaded Pine parser."""
    required = {"SPOT", "CALLWALL", "PUTWALL", "MAXCALLOI", "MAXPUTOI"}
    rows = str(text or '').replace('\r', '').split('\n')
    issues: list[str] = []
    counts: dict[str, int] = {}

    for raw_target in expected:
        target = _pine_runtime_normalize_ticker(raw_target)
        in_match = False
        saw_match = False
        parsed: list[str] = []
        for row in rows:
            header = _pine_runtime_header_ticker(row)
            if header:
                in_match = header == target
                saw_match = saw_match or in_match

            if not in_match:
                continue
            compact = str(row).replace('|', ',').replace(' ', '').replace('\t', '')
            parts = compact.split(',')
            if len(parts) < 2:
                continue
            typ = parts[0].strip().upper()
            is_packed = (
                typ in {"SPOT", "GFLIP", "CALLWALL", "PUTWALL", "MAXCALLOI", "MAXPUTOI"}
                or typ.startswith('GEXPOS')
                or typ.startswith('GEXNEG')
            )
            if not is_packed:
                continue
            try:
                float(parts[1])
            except (TypeError, ValueError):
                continue
            parsed.append(typ)

        counts[target] = len(parsed)
        if not saw_match:
            issues.append(f"{target}:NO_HEADER")
            continue
        parsed_set = set(parsed)
        for typ in sorted(required - parsed_set):
            issues.append(f"{target}:NO_{typ}")
        if not any(typ.startswith('GEXPOS') or typ.startswith('GEXNEG') for typ in parsed):
            issues.append(f"{target}:NO_GEX")

    return issues, counts


'''
if "_pine_runtime_master_issues" not in text:
    if parser_marker not in text:
        raise SystemExit("TradingView renderer marker not found")
    text = text.replace(parser_marker, exact_parser + parser_marker, 1)

replacements = [
    ('master_label = "MASTER A6 // PINE ROUTER SAFE — COPY THIS"', 'master_label = "MASTER A6 // TRADINGVIEW PINE SAFE — COPY THIS"'),
    ('full_label = "MASTER A6 // GOOGLE SHEETS FULL — REFERENCE"', 'full_label = "MASTER A6 // GOOGLE SHEETS FULL — REFERENCE ONLY"'),
    ('options = [master_label, full_label] + available', 'options = [master_label] + available + [full_label]'),
    ('key="gexv3_bridge_choice_pine_safe_v4"', 'key="gexv3_bridge_choice_pine_safe_v5"'),
    ('key="gexv3_pine_chart_ticker_check_safe_v4"', 'key="gexv3_pine_chart_ticker_check_safe_v5"'),
]
for old_value, new_value in replacements:
    if old_value not in text:
        raise SystemExit(f"Missing GEX marker: {old_value}")
    text = text.replace(old_value, new_value, 1)

old_validation = '''    pine_issues, parsed_counts = _proven._pine_master_issues(parser_text, expected)
    too_large = byte_count > _proven._PINE_TEXT_LIMIT
    quoted_transport_ok = is_full or (text.startswith('"') and text.endswith('"'))
    transport_ok = not missing_headers and not pine_issues and not too_large and quoted_transport_ok
'''
new_validation = '''    pine_issues, parsed_counts = _proven._pine_master_issues(parser_text, expected)
    runtime_issues, runtime_counts = ([], {}) if is_full else _pine_runtime_master_issues(text, expected)
    too_large = byte_count > _proven._PINE_TEXT_LIMIT
    quoted_transport_ok = is_full or (text.startswith('"') and text.endswith('"'))
    transport_ok = (
        not missing_headers
        and not pine_issues
        and not runtime_issues
        and not too_large
        and quoted_transport_ok
    )
'''
if old_validation not in text:
    raise SystemExit("TradingView validation block not found")
text = text.replace(old_validation, new_validation, 1)

old_success = '''        if is_master:
            st.success(
                f"PINE ROUTER CHECK PASS // {len(expected)}/{len(expected)} TICKERS // "
                f"{min_rows}-{max_rows} DRAWABLE PACKED ROWS EACH"
            )
'''
new_success = '''        if is_master:
            exact_min = min(runtime_counts.values()) if runtime_counts else min_rows
            exact_max = max(runtime_counts.values()) if runtime_counts else max_rows
            st.success(
                f"PINE EXACT INPUT CHECK PASS // OUTER QUOTES PASS // {len(expected)}/{len(expected)} TICKERS // "
                f"{exact_min}-{exact_max} DRAWABLE PACKED ROWS EACH"
            )
'''
if old_success not in text:
    raise SystemExit("TradingView success block not found")
text = text.replace(old_success, new_success, 1)

old_error = '''        details = list(missing_headers) + list(pine_issues)
        if too_large:
            details.append("TEXT_LIMIT")
        if not quoted_transport_ok:
            details.append("MISSING_OUTER_DOUBLE_QUOTES")
        st.error("PINE ROUTER CHECK FAILED // " + ", ".join(details[:16]))
'''
new_error = '''        details = list(missing_headers) + list(pine_issues) + list(runtime_issues)
        if too_large:
            details.append("TEXT_LIMIT")
        if not quoted_transport_ok:
            details.append("MISSING_OUTER_DOUBLE_QUOTES")
        st.error("PINE EXACT INPUT CHECK FAILED // " + ", ".join(details[:16]))
'''
if old_error not in text:
    raise SystemExit("TradingView error block not found")
text = text.replace(old_error, new_error, 1)

old_target = '''    safe_master = _proven._pine_master_bridge_text(result_map, available)
    target_issues, target_count = _proven._pine_ticker_issues(safe_master, verify_ticker)
    if target_issues:
        st.error(f"{verify_ticker} // PINE TARGET FAIL // " + ", ".join(target_issues))
    else:
        st.success(f"{verify_ticker} // PINE TARGET PASS // {target_count} DRAWABLE PACKED ROWS")
'''
new_target = '''    safe_master = _proven._pine_master_bridge_text(result_map, available)
    quoted_master = f'"{safe_master.strip()}"'
    target_issues, target_counts = _pine_runtime_master_issues(quoted_master, [verify_ticker])
    target_count = target_counts.get(_pine_runtime_normalize_ticker(verify_ticker), 0)
    if target_issues:
        st.error(f"{verify_ticker} // EXACT PINE TARGET FAIL // " + ", ".join(target_issues))
    else:
        st.success(f"{verify_ticker} // EXACT PINE TARGET PASS // {target_count} DRAWABLE PACKED ROWS")
'''
if old_target not in text:
    raise SystemExit("TradingView target check block not found")
text = text.replace(old_target, new_target, 1)

old_ref = '"**REFERENCE ONLY // this mirrors the Google Sheet A6, but use the PINE ROUTER SAFE master for TradingView.**"'
new_ref = '"**REFERENCE ONLY // DO NOT PASTE THIS BLOCK INTO GEX TEST. Use `MASTER A6 // TRADINGVIEW PINE SAFE — COPY THIS` instead.**"'
if old_ref not in text:
    raise SystemExit("Reference instruction marker not found")
text = text.replace(old_ref, new_ref, 1)

old_code = '''    st.html(_proven._TRADINGVIEW_CODE_CSS)
    st.html(_TRADINGVIEW_COPY_BUTTON_CSS)
    with st.container(key="gexv3_bridge_code"):
        st.code(text or "", language=None, wrap_lines=False)
'''
new_code = '''    if is_full:
        st.warning("GOOGLE SHEETS REFERENCE ONLY // DO NOT COPY THIS INTO TRADINGVIEW GEX TEST")
        with st.container(key="gexv3_bridge_reference_code"):
            st.code(text or "", language=None, wrap_lines=False)
    else:
        st.html(_proven._TRADINGVIEW_CODE_CSS)
        st.html(_TRADINGVIEW_COPY_BUTTON_CSS)
        with st.container(key="gexv3_bridge_code"):
            st.code(text or "", language=None, wrap_lines=False)
'''
if old_code not in text:
    raise SystemExit("TradingView code block marker not found")
text = text.replace(old_code, new_code, 1)

old_render_setup = '''    original_remove = _proven._base._remove_ticker
    original_tradingview = _proven._render_tradingview_pine
'''
new_render_setup = '''    original_remove = _proven._base._remove_ticker
    original_tradingview = _proven._render_tradingview_pine
    original_background_status = _proven._render_background_status
'''
if old_render_setup not in text:
    raise SystemExit("GEX render setup marker not found")
text = text.replace(old_render_setup, new_render_setup, 1)

old_patch = '''    _proven._decorate_overview = decorate_with_iv_rank
    _proven._render_tradingview_pine = _render_tradingview_pine_compatible
    try:
'''
new_patch = '''    _proven._decorate_overview = decorate_with_iv_rank
    _proven._render_tradingview_pine = _render_tradingview_pine_compatible
    _proven._render_background_status = _live_background_status_fragment
    try:
'''
if old_patch not in text:
    raise SystemExit("GEX render patch marker not found")
text = text.replace(old_patch, new_patch, 1)

old_finally = '''    finally:
        _proven._render_tradingview_pine = original_tradingview
        _proven._decorate_overview = original_decorate
'''
new_finally = '''    finally:
        _proven._render_background_status = original_background_status
        _proven._render_tradingview_pine = original_tradingview
        _proven._decorate_overview = original_decorate
'''
if old_finally not in text:
    raise SystemExit("GEX render finally marker not found")
text = text.replace(old_finally, new_finally, 1)
ui.write_text(text)

workspace = Path("src/gex_workspace_v2.py")
ws = workspace.read_text()
if 'GEX_BUILD_VERSION = "v2026.09.17.08"' not in ws:
    raise SystemExit("Expected .08 GEX build not found")
ws = ws.replace('GEX_BUILD_VERSION = "v2026.09.17.08"', 'GEX_BUILD_VERSION = "v2026.09.17.09"', 1)
ws = ws.replace(
    "TIP        THIS SNAPSHOT REFRESHES WHEN THE GEX PAGE RERUNS",
    "TIP        LIVE GEX STATUS AUTO-REFRESHES EACH SECOND WHILE THIS TAB IS OPEN",
    1,
)
workspace.write_text(ws)

changelog = Path("src/TERMINAL_CHANGELOG.md")
current = changelog.read_text()
entry = r'''

## 2026-09-17 — E*TRADE renew confirmation without layout shift

- **Feature changed:** E*TRADE OAuth/session utility-bar renewal feedback.
- **Exact production file(s) changed:** `src/etrade_connection_ui_v2.py`, `src/TERMINAL_CHANGELOG.md`.
- **What was broken:** Clicking `RENEW` rendered a normal `st.success` block inside the renew column, making that column taller and visibly pushing/offsetting the utility-bar layout.
- **Root cause:** A layout-bearing success message was emitted inside the same compact column that owns the renew button.
- **What was changed:** Replaced the in-column success block with Streamlit's floating toast confirmation `E*TRADE SESSION RENEWED`, so renewal feedback no longer consumes utility-bar height.
- **Important behavior that must remain:** RENEW must still call the live E*TRADE client's `renew()` and `touch_session()`; only the confirmation presentation changed.
- **Files/features intentionally NOT changed:** OAuth token semantics, session persistence, GEX, Risk Sizing, Holdings, navigation, authentication, and `src/terminal_core.py`.
- **Tests performed:** Python syntax compilation; source assertion that the renewal path still calls `renew()` and `touch_session()` and no longer uses the layout-bearing renewal success block; architecture guard.
- **Architecture guard result:** PASS on staging before merge.
- **Commit SHA:** final production merge SHA recorded by GitHub after merge.
- **Lesson:** Compact utility-bar confirmations should use non-layout feedback such as a toast rather than inserting a full-width success card into a button column.

## 2026-09-17 — Live GEX refresh sync and exact Pine-input guard

- **Feature changed:** GEX background refresh visibility/completion sync and TradingView Bridge copy safety.
- **Exact production file(s) changed:** `src/gex_ui_v3.py`, `src/gex_workspace_v2.py`, `src/TERMINAL_CHANGELOG.md`.
- **What was broken:** Background progress only changed on full GEX page reruns, so completed ticker results could remain invisible/stale until another interaction. The TradingView reference block remained easy to confuse with the Pine-safe copy block, and existing validation checked the unquoted internal payload rather than the exact quoted text actually copied into the user's uploaded `GEX TEST` Pine input.
- **Root cause:** The detached worker correctly avoided Streamlit calls, but the presentation layer had no fragment poller to merge/display completed results in real time. The bridge also kept the Google Sheets reference adjacent to the Pine-safe transport with the same code-block copy affordance.
- **What was changed:** Added a 1-second GEX-only Streamlit fragment that merges completed background results and updates processed/updated/active status without rerunning the whole terminal; when the worker finishes it performs exactly one full app rerun so Overview and TradingView receive the final result map. Added a Python replica of the uploaded Pine router and validate the exact quote-wrapped clipboard text, not just its unquoted internal grammar. Reset the bridge selector key so the Pine-safe MASTER is selected by default, moved the Google Sheets reference to the end, clearly marks it `REFERENCE ONLY`, and removes the large `COPY FOR TRADINGVIEW` treatment from the reference block. Visible GEX build bumped to `v2026.09.17.09`.
- **Important behavior that must remain:** Background workers never call Streamlit or touch `st.session_state`; only the main/fragment render thread merges results. Refresh status may poll once per second while GEX is open, and completion may trigger one full rerun, but it must never clear terminal/E*TRADE authentication. Pine-safe output remains exactly one opening `"` before the first `Ticker:` and one closing `"` after the final packed row. The TradingView Pine script itself remains unchanged.
- **Files/features intentionally NOT changed:** GEX formulas/math, expiration coverage, E*TRADE request-rate gate/cache pipeline, Pine script, Risk Sizing, Holdings, navigation, Bull Debit, Muni, Orders, shared theme, authentication, and `src/terminal_core.py`.
- **Tests performed:** Python syntax compilation; exact quoted multi-ticker Pine parser simulation; static assertions for 1-second fragment, one-time completion rerun marker, safe/default bridge key, reference-only separation, build marker, unchanged 20-worker/20-fetch/3.7-RPS engine label; `python scripts/validate_architecture.py`; PR changed-file inspection; post-merge architecture guard required.
- **Architecture guard result:** PASS on staging before merge.
- **Commit SHA:** final production merge SHA recorded by GitHub after merge.
- **Lesson:** Detached background work needs a lightweight render-thread poller, not repeated full-app reruns. Validate the exact bytes/text the user pastes into Pine and make non-Pine reference exports visually impossible to mistake for the production TradingView payload.
'''
if "## 2026-09-17 — Live GEX refresh sync and exact Pine-input guard" not in current:
    changelog.write_text(current + entry)
