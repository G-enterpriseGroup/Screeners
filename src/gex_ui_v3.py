"""Compact multi-ticker GEX workspace for Raj's Terminal.

This UI preserves the functional pieces from the original Google Sheets + HTML
control panel while presenting them as GEX-only sub-tabs:
- multiple saved tickers
- global + per-ticker DTE
- wall value mode + timezone
- multi-ticker summary
- per-ticker analytics
- raw strike GEX / open interest
- TradingView MASTER A6 / per-ticker packed blocks
- append-only ticker notes history

The calculation engine remains src.gex_ui and uses the terminal's authenticated
E*TRADE market-data client.
"""

from __future__ import annotations

import copy
import html
import re
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

import src.gex_ui as core


def _css() -> None:
    st.markdown(
        """
        <style>
        .gexv3-head{
            border:1px solid #fb8b1e;background:#030303;padding:.52rem .72rem;
            margin:.05rem 0 .42rem;font:900 1.08rem/1.1 "Courier New",monospace;
            color:#fb8b1e!important;letter-spacing:.035em;
        }
        .gexv3-sub{
            font:700 .68rem/1.25 "Courier New",monospace;color:#9b641c!important;
            margin:-.12rem 0 .55rem;
        }
        .gexv3-summary{
            width:100%;border-collapse:collapse;table-layout:fixed;background:#020202;
            font:800 .69rem/1.12 "Courier New",monospace;
        }
        .gexv3-summary th{
            color:#fb8b1e;background:#090909;border:1px solid #5d3605;
            padding:.42rem .30rem;text-align:center;white-space:normal;
        }
        .gexv3-summary td{
            border:1px solid #3f290c;padding:.40rem .28rem;text-align:center;
            color:#e8e8e8;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;
        }
        .gexv3-summary td.sym{color:#4af6c3;font-weight:900}
        .gexv3-summary td.red{color:#ff5757}
        .gexv3-summary td.green{color:#4af6c3}
        .gexv3-summary td.orange{color:#fb8b1e}
        .gexv3-empty{
            border:1px dashed #6b4512;padding:.85rem;text-align:center;
            font:800 .75rem/1.3 "Courier New",monospace;color:#9b641c!important;
        }
        .gexv3-chip{
            display:inline-block;border:1px solid #5d3605;background:#060606;
            color:#4af6c3!important;padding:.18rem .42rem;margin:.08rem .14rem .08rem 0;
            font:900 .67rem/1 "Courier New",monospace;
        }
        .gexv3-range{
            border:1px solid #5d3605;background:#050505;padding:.45rem .6rem;
            color:#fb8b1e!important;font:800 .72rem/1.25 "Courier New",monospace;
            margin:.25rem 0 .5rem;white-space:normal;
        }
        div[data-testid="stTabs"] button[role="tab"]{
            font-family:"Courier New",monospace!important;font-weight:900!important;
            font-size:.76rem!important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _normalize_many(raw: str) -> list[str]:
    values = re.split(r"[\s,;|]+", str(raw or "").upper().strip())
    out: list[str] = []
    for value in values:
        ticker = core._normalize_ticker(value)
        if ticker and ticker not in out:
            out.append(ticker)
    return out


def _fmt_money(value: Any) -> str:
    try:
        return f"${float(value):,.2f}"
    except (TypeError, ValueError):
        return "—"


def _fmt_num(value: Any, decimals: int = 0) -> str:
    try:
        return f"{float(value):,.{decimals}f}"
    except (TypeError, ValueError):
        return "—"


def _short_updated(value: Any) -> str:
    try:
        return datetime.fromisoformat(str(value)).strftime("%m/%d %I:%M %p")
    except Exception:
        return "—"


def _range_status(result: dict[str, Any]) -> tuple[str, str]:
    try:
        spot = float(result["spot"])
        put = float(result["putWall"]["strike"])
        call = float(result["callWall"]["strike"])
    except Exception:
        return "WAIT", "orange"
    low, high = min(put, call), max(put, call)
    if low < spot < high:
        return "INSIDE", "green"
    if spot <= low:
        return "BELOW", "red"
    return "ABOVE", "red"


def _overview_html(state: dict[str, Any], result_map: dict[str, Any]) -> str:
    if not state["tickers"]:
        return '<div class="gexv3-empty">ADD ONE OR MORE TICKERS ABOVE.</div>'

    body = []
    for ticker in state["tickers"]:
        result = result_map.get(ticker)
        dte = state["dte_overrides"].get(ticker, state["global_dte"])
        if not result:
            body.append(
                "<tr>"
                f'<td class="sym">{html.escape(ticker)}</td>'
                f"<td>{int(dte)}</td><td>—</td><td>—</td><td>—</td><td>—</td>"
                '<td class="orange">REFRESH</td><td>—</td>'
                "</tr>"
            )
            continue

        status, tone = _range_status(result)
        body.append(
            "<tr>"
            f'<td class="sym">{html.escape(ticker)}</td>'
            f"<td>{int(result['maxDte'])}</td>"
            f"<td>{_fmt_money(result['spot'])}</td>"
            f'<td class="red">{_fmt_money(result["putWall"]["strike"])}</td>'
            f'<td class="orange">{_fmt_money(result.get("gammaFlip"))}</td>'
            f'<td class="green">{_fmt_money(result["callWall"]["strike"])}</td>'
            f'<td class="{tone}">{status}</td>'
            f"<td>{html.escape(_short_updated(result.get('updated')))}</td>"
            "</tr>"
        )

    return (
        '<table class="gexv3-summary"><thead><tr>'
        "<th>TICKER</th><th>DTE</th><th>SPOT</th><th>PUT WALL</th>"
        "<th>GAMMA FLIP</th><th>CALL WALL</th><th>RANGE</th><th>UPDATED</th>"
        "</tr></thead><tbody>"
        + "".join(body)
        + "</tbody></table>"
    )


def _refresh_symbol(client: Any, vault_key: str, state: dict[str, Any], ticker: str, touch: Any):
    return core._refresh_one(
        client,
        vault_key,
        state,
        ticker,
        touch,
        force_refresh=True,
    )


def _top_gex_df(result: dict[str, Any]) -> pd.DataFrame:
    rows = []
    for index, row in enumerate(result.get("topGex") or [], 1):
        rows.append(
            {
                "Rank": index,
                "Strike": row["strike"],
                "Net GEX": row["net_gex"],
                "Call OI": row["call_oi"],
                "Put OI": row["put_oi"],
            }
        )
    return pd.DataFrame(rows)


def _raw_gex_df(result: dict[str, Any]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "Strike": row["strike"],
                "Call GEX": row["call_gex"],
                "Put GEX": row["put_gex"],
                "Net GEX": row["net_gex"],
                "Cum GEX": row["cum_gex"],
            }
            for row in result.get("rawRows") or []
        ]
    )


def _raw_oi_df(result: dict[str, Any]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "Strike": row["strike"],
                "Call OI": row["call_oi"],
                "Put OI": row["put_oi"],
                "Abs Net GEX Rank": row["rank"],
            }
            for row in result.get("rawRows") or []
        ]
    )


def _save_state(vault_key: str, state: dict[str, Any]) -> dict[str, Any]:
    return core._save_state(vault_key, state)


def _render_overview(
    client: Any,
    vault_key: str,
    touch: Any,
    state: dict[str, Any],
    result_map: dict[str, Any],
) -> dict[str, Any]:
    st.markdown(_overview_html(state, result_map), unsafe_allow_html=True)

    if state["tickers"]:
        st.caption("PER-TICKER REFRESH")
        cols = st.columns(min(6, max(1, len(state["tickers"]))), gap="small")
        for index, ticker in enumerate(state["tickers"]):
            with cols[index % len(cols)]:
                if st.button(
                    f"↻ {ticker}",
                    key=f"gexv3_refresh_overview_{ticker}",
                    width="stretch",
                    disabled=client is None,
                ):
                    try:
                        with st.spinner(f"Refreshing {ticker}..."):
                            _refresh_symbol(client, vault_key, state, ticker, touch)
                        st.success(f"{ticker} UPDATED")
                        st.rerun()
                    except Exception as exc:
                        st.error(f"{ticker} REFRESH FAILED // {exc}")

    refreshed = [ticker for ticker in state["tickers"] if ticker in result_map]
    if refreshed:
        st.caption(
            f"{len(refreshed)}/{len(state['tickers'])} TICKERS REFRESHED // "
            "SUMMARY IS INTENTIONALLY COMPACT SO THE PAGE DOES NOT SCROLL LEFT/RIGHT."
        )
    return state


def _render_analytics(
    client: Any,
    vault_key: str,
    touch: Any,
    state: dict[str, Any],
    result_map: dict[str, Any],
) -> None:
    available = [ticker for ticker in state["tickers"] if ticker in result_map]
    if not available:
        st.info("Refresh at least one ticker first.")
        return

    a, b = st.columns([4, 1], gap="small", vertical_alignment="bottom")
    with a:
        selected = st.selectbox(
            "ANALYZE TICKER",
            available,
            key="gexv3_analytics_ticker",
        )
    with b:
        if st.button(
            f"REFRESH {selected}",
            key="gexv3_refresh_selected",
            type="primary",
            width="stretch",
            disabled=client is None,
        ):
            try:
                with st.spinner(f"Refreshing {selected}..."):
                    _refresh_symbol(client, vault_key, state, selected, touch)
                st.rerun()
            except Exception as exc:
                st.error(f"{selected} REFRESH FAILED // {exc}")

    result = result_map[selected]
    status, _ = _range_status(result)

    r1, r2, r3 = st.columns(3, gap="small")
    r1.metric("SPOT", _fmt_money(result["spot"]))
    r2.metric("PUT WALL", _fmt_money(result["putWall"]["strike"]))
    r3.metric("GAMMA FLIP", _fmt_money(result.get("gammaFlip")))

    r4, r5, r6 = st.columns(3, gap="small")
    r4.metric("CALL WALL", _fmt_money(result["callWall"]["strike"]))
    r5.metric("NET CURRENT GEX", _fmt_num(result["netCurrent"], 0))
    r6.metric("CONTRACTS USED", f"{int(result['contractsUsed']):,}")

    st.markdown(
        '<div class="gexv3-range">'
        f"SPOT RANGE // {html.escape(str(result.get('spotRangeVisual') or status))}"
        "</div>",
        unsafe_allow_html=True,
    )

    st.plotly_chart(
        core._plot_gex(result),
        width="stretch",
        config={"displayModeBar": False, "responsive": True},
        key=f"gexv3_plot_{selected}",
    )

    st.markdown("**TOP 8 ABSOLUTE NET-GEX STRIKES**")
    top = _top_gex_df(result)
    st.dataframe(
        top,
        hide_index=True,
        width="stretch",
        height=min(340, 42 + 35 * max(1, len(top))),
        column_config={
            "Rank": st.column_config.NumberColumn(format="%d"),
            "Strike": st.column_config.NumberColumn(format="$%.2f"),
            "Net GEX": st.column_config.NumberColumn(format="localized"),
            "Call OI": st.column_config.NumberColumn(format="localized"),
            "Put OI": st.column_config.NumberColumn(format="localized"),
        },
    )

    o1, o2 = st.columns(2, gap="small")
    o1.metric(
        "MAX CALL OI",
        _fmt_money(result["maxCallOi"]["strike"]),
        f"{_fmt_num(result['maxCallOi']['call_oi'], 0)} OI",
    )
    o2.metric(
        "MAX PUT OI",
        _fmt_money(result["maxPutOi"]["strike"]),
        f"{_fmt_num(result['maxPutOi']['put_oi'], 0)} OI",
    )
    st.caption(
        f"MODE BARCHART_STYLE // MAX DTE {result['maxDte']} // "
        f"EXPIRIES {len(result.get('expiriesUsed', [])):,} // "
        f"WALL VALUE {state['wall_value_mode']} // SOURCE E*TRADE OPTIONS"
    )


def _render_raw(state: dict[str, Any], result_map: dict[str, Any]) -> None:
    available = [ticker for ticker in state["tickers"] if ticker in result_map]
    if not available:
        st.info("Refresh at least one ticker first.")
        return

    selected = st.selectbox("RAW TICKER", available, key="gexv3_raw_ticker")
    result = result_map[selected]
    gex_tab, oi_tab = st.tabs(["GEX BY STRIKE", "OPEN INTEREST + RANK"])

    with gex_tab:
        df = _raw_gex_df(result)
        st.dataframe(
            df,
            hide_index=True,
            width="stretch",
            height=560,
            column_config={
                "Strike": st.column_config.NumberColumn(format="$%.2f"),
                "Call GEX": st.column_config.NumberColumn(format="localized"),
                "Put GEX": st.column_config.NumberColumn(format="localized"),
                "Net GEX": st.column_config.NumberColumn(format="localized"),
                "Cum GEX": st.column_config.NumberColumn(format="localized"),
            },
        )

    with oi_tab:
        df = _raw_oi_df(result)
        st.dataframe(
            df,
            hide_index=True,
            width="stretch",
            height=560,
            column_config={
                "Strike": st.column_config.NumberColumn(format="$%.2f"),
                "Call OI": st.column_config.NumberColumn(format="localized"),
                "Put OI": st.column_config.NumberColumn(format="localized"),
                "Abs Net GEX Rank": st.column_config.NumberColumn(format="%d"),
            },
        )


def _render_tradingview(state: dict[str, Any], result_map: dict[str, Any]) -> None:
    available = [ticker for ticker in state["tickers"] if ticker in result_map]
    if not available:
        st.info("Refresh GEX first. The TradingView bridge is built from refreshed ticker results.")
        return

    options = ["MASTER A6"] + available
    choice = st.selectbox("PACKED GAMMA BLOCK", options, key="gexv3_bridge_choice")
    if choice == "MASTER A6":
        text = core._master_bridge(result_map, state["tickers"])
        filename = "raj_terminal_gex_A6.txt"
    else:
        text = str(result_map[choice]["summaryText"]).strip()
        filename = f"{choice}_gex.txt"

    st.caption(
        "The code box has a built-in copy button. MASTER A6 combines every refreshed ticker "
        "in the same packed-gamma format used by the original TradingView Bridge."
    )
    st.code(text or "REFRESH GEX FIRST", language=None, wrap_lines=True)
    st.download_button(
        "DOWNLOAD PACKED GAMMA BLOCK",
        data=text,
        file_name=filename,
        mime="text/plain",
        width="stretch",
        disabled=not bool(text),
        key="gexv3_download_bridge",
    )


def _render_settings(
    vault_key: str,
    state: dict[str, Any],
    result_map: dict[str, Any],
) -> dict[str, Any]:
    st.markdown("**GLOBAL SETTINGS**")
    with st.form("gexv3_global_settings"):
        c1, c2, c3 = st.columns([1, 1.2, 1.8], gap="small")
        with c1:
            global_dte = st.selectbox(
                "GLOBAL MAX DTE",
                core.DTE_CHOICES,
                index=core.DTE_CHOICES.index(state["global_dte"]),
            )
        with c2:
            wall_label = st.selectbox(
                "WALL VALUE",
                ["NET", "COMPONENT"],
                index=0 if state["wall_value_mode"] == "NET_GEX" else 1,
            )
        with c3:
            timezone_name = st.text_input("TIMEZONE", value=state["timezone"])
        save_global = st.form_submit_button("SAVE GLOBAL SETTINGS", type="primary", width="stretch")

    if save_global:
        timezone_name = str(timezone_name or "").strip() or "America/New_York"
        try:
            ZoneInfo(timezone_name)
        except Exception:
            st.error(f"INVALID TIMEZONE // {timezone_name}")
        else:
            old_global = int(state["global_dte"])
            old_wall = state["wall_value_mode"]
            old_tz = state["timezone"]
            new_wall = "NET_GEX" if wall_label == "NET" else "COMPONENT_GEX"
            state["global_dte"] = int(global_dte)
            state["wall_value_mode"] = new_wall
            state["timezone"] = timezone_name
            state = _save_state(vault_key, state)

            if old_wall != new_wall or old_tz != timezone_name:
                result_map.clear()
            elif old_global != int(global_dte):
                for ticker in list(result_map):
                    if ticker not in state["dte_overrides"]:
                        result_map.pop(ticker, None)
            st.success("GLOBAL GEX SETTINGS SAVED")
            st.rerun()

    st.divider()
    st.markdown("**PER-TICKER DTE**")
    if not state["tickers"]:
        st.info("Add tickers first.")
    else:
        t1, t2 = st.columns([2, 1.3], gap="small")
        with t1:
            ticker = st.selectbox("TICKER", state["tickers"], key="gexv3_dte_ticker")
        choices = ["GLOBAL"] + [str(value) for value in core.DTE_CHOICES]
        current = str(state["dte_overrides"].get(ticker, "GLOBAL"))
        with t2:
            selected = st.selectbox(
                "DTE OVERRIDE",
                choices,
                index=choices.index(current) if current in choices else 0,
                key="gexv3_dte_override",
            )
        d1, d2 = st.columns(2, gap="small")
        if d1.button("SAVE TICKER DTE", type="primary", width="stretch"):
            desired = None if selected == "GLOBAL" else int(selected)
            if desired is None:
                state["dte_overrides"].pop(ticker, None)
            else:
                state["dte_overrides"][ticker] = desired
            state = _save_state(vault_key, state)
            result_map.pop(ticker, None)
            st.success(f"{ticker} DTE UPDATED")
            st.rerun()
        if d2.button("USE GLOBAL DTE", width="stretch"):
            state["dte_overrides"].pop(ticker, None)
            state = _save_state(vault_key, state)
            result_map.pop(ticker, None)
            st.rerun()

        effective = state["dte_overrides"].get(ticker, state["global_dte"])
        source = "OVERRIDE" if ticker in state["dte_overrides"] else "GLOBAL"
        st.caption(f"{ticker} // ≤ {effective} DTE // {source}")

    st.divider()
    st.markdown("**TICKER MANAGER**")
    if state["tickers"]:
        remove = st.selectbox("REMOVE TICKER", state["tickers"], key="gexv3_remove_ticker")
        if st.button("REMOVE SELECTED TICKER", width="stretch"):
            state["tickers"] = [x for x in state["tickers"] if x != remove]
            state["dte_overrides"].pop(remove, None)
            state["notes"].pop(remove, None)
            result_map.pop(remove, None)
            state = _save_state(vault_key, state)
            st.rerun()
    return state


def _render_notes(vault_key: str, state: dict[str, Any]) -> dict[str, Any]:
    if not state["tickers"]:
        st.info("Add tickers first.")
        return state

    ticker = st.selectbox("NOTE TICKER", state["tickers"], key="gexv3_note_ticker")
    note = st.text_area(
        "NEW NOTE",
        key="gexv3_note_text",
        height=125,
        placeholder="Type a new note. Saving creates a new history entry and never overwrites older notes.",
    )
    if st.button(
        "SAVE NEW NOTE",
        type="primary",
        width="stretch",
        disabled=not bool(str(note).strip()),
        key="gexv3_save_note",
    ):
        history = state["notes"].setdefault(ticker, [])
        history.insert(
            0,
            {
                "note": str(note).strip(),
                "updated": datetime.now(ZoneInfo(state["timezone"])).isoformat(),
            },
        )
        state = _save_state(vault_key, state)
        st.session_state["_gexv3_clear_note"] = True
        st.rerun()

    st.markdown(f"**{ticker} NOTE HISTORY**")
    history = state["notes"].get(ticker, [])
    if not history:
        st.caption("NO SAVED NOTES")
    else:
        for index, row in enumerate(history[:30], 1):
            stamp = str(row.get("updated") or "")
            try:
                stamp = datetime.fromisoformat(stamp).strftime("%m/%d/%Y %I:%M %p")
            except Exception:
                pass
            with st.container(border=True):
                st.caption(f"{index:02d} // {stamp}")
                st.write(str(row.get("note") or ""))
    return state


def render_gex(client: Any, vault_key: str, touch_session: Any) -> None:
    _css()
    vault_key = str(vault_key or "default")

    if st.session_state.pop("_gexv3_clear_add", False):
        st.session_state["gexv3_add_batch"] = ""
    if st.session_state.pop("_gexv3_clear_note", False):
        st.session_state["gexv3_note_text"] = ""

    state = core._load_state(vault_key)
    state = copy.deepcopy(state)
    result_map = core._results(vault_key)

    st.markdown('<div class="gexv3-head">GEX // MULTI-TICKER GAMMA WORKSPACE</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="gexv3-sub">BARCHART_STYLE // CALLS +GEX // PUTS −GEX // '
        'GAMMA × OI × 100 × SPOT² × 1% // INDIVIDUAL DTE // PACKED A6 // NOTES HISTORY</div>',
        unsafe_allow_html=True,
    )

    add_col, refresh_col = st.columns([4.4, 1.35], gap="small", vertical_alignment="bottom")
    with add_col:
        batch = st.text_input(
            "ADD TICKERS",
            key="gexv3_add_batch",
            placeholder="SPY, QQQ, NVDA, MSFT",
            help="Add one or many tickers separated by commas or spaces.",
        )
    with refresh_col:
        refresh_all = st.button(
            "REFRESH ALL GEX",
            type="primary",
            width="stretch",
            disabled=not state["tickers"] or client is None,
            key="gexv3_refresh_all",
        )

    add_button_col, status_col = st.columns([1.35, 4.4], gap="small", vertical_alignment="center")
    with add_button_col:
        add_clicked = st.button("ADD TICKER(S)", width="stretch", key="gexv3_add_many")
    with status_col:
        if state["tickers"]:
            chips = "".join(f'<span class="gexv3-chip">{html.escape(t)}</span>' for t in state["tickers"])
            st.markdown(chips, unsafe_allow_html=True)
        else:
            st.caption("NO SAVED TICKERS")

    if add_clicked:
        incoming = _normalize_many(batch)
        if not incoming:
            st.warning("ENTER AT LEAST ONE TICKER")
        else:
            changed = False
            for ticker in incoming:
                if ticker not in state["tickers"] and len(state["tickers"]) < 50:
                    state["tickers"].append(ticker)
                    changed = True
            if changed:
                state = _save_state(vault_key, state)
            st.session_state["_gexv3_clear_add"] = True
            st.rerun()

    if refresh_all:
        failures: list[str] = []
        progress = st.progress(0.0, text="REFRESHING OPTION CHAINS...")
        for index, ticker in enumerate(state["tickers"], 1):
            try:
                _refresh_symbol(client, vault_key, state, ticker, touch_session)
            except Exception as exc:
                failures.append(f"{ticker}: {exc}")
            progress.progress(
                index / len(state["tickers"]),
                text=f"GEX // {ticker} // {index}/{len(state['tickers'])}",
            )
        progress.empty()
        if failures:
            st.warning("SOME TICKERS FAILED // " + " | ".join(failures[:6]))
        else:
            st.success("ALL GEX TICKERS UPDATED")
        st.rerun()

    overview_tab, analytics_tab, raw_tab, tv_tab, settings_tab, notes_tab = st.tabs(
        ["OVERVIEW", "ANALYTICS", "RAW STRIKES", "TRADINGVIEW", "SETTINGS", "NOTES"]
    )

    with overview_tab:
        state = _render_overview(client, vault_key, touch_session, state, result_map)

    with analytics_tab:
        _render_analytics(client, vault_key, touch_session, state, result_map)

    with raw_tab:
        _render_raw(state, result_map)

    with tv_tab:
        _render_tradingview(state, result_map)

    with settings_tab:
        state = _render_settings(vault_key, state, result_map)

    with notes_tab:
        state = _render_notes(vault_key, state)

    core._sync_browser_state(vault_key, state)
