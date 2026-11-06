"""One-use, branch-only repair applicator. Remove before production merge."""
from pathlib import Path
import ast
import subprocess

ROOT = Path(__file__).resolve().parents[1]

def edit(path):
    return (ROOT / path).read_text(encoding="utf-8")

def put(path, text):
    ast.parse(text) if path.endswith(".py") else None
    (ROOT / path).write_text(text, encoding="utf-8")

def once(text, old, new):
    if text.count(old) != 1:
        raise AssertionError(f"Expected one patch anchor, found {text.count(old)}: {old[:120]!r}")
    return text.replace(old, new, 1)

def function(text, name, replacement):
    node = next(n for n in ast.parse(text).body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name)
    first = min([node.lineno] + [d.lineno for d in node.decorator_list])
    lines = text.splitlines(keepends=True)
    lines[first - 1:node.end_lineno] = [replacement.rstrip() + "\n"]
    return "".join(lines)

# Shared canonical scanner: one explicit opt-in callback; defaults are unchanged.
p = "src/vertical_options.py"
s = edit(p)
s = once(s, '    progress: Callable[[int], None] | None = None,\n) -> tuple[list[dict[str, Any]], set[str]]:',
         '    progress: Callable[[int], None] | None = None,\n    quote_pricer: Callable[..., dict[str, Any] | None] | None = None,\n) -> tuple[list[dict[str, Any]], set[str]]:')
s = once(s, '                price = price_vertical_quote(\n', '                price = (quote_pricer or price_vertical_quote)(\n')
put(p, s)

p = "src/vertical_options_ui.py"
s = edit(p)
s = once(s, '    price_vertical_quote,\n    scan_vertical_candidates,\n', '')
s = once(s, 'from src.vertical_options_store import VerticalResultStore, indicative_fill_score',
'''from src.vertical_options_store import VerticalResultStore, indicative_fill_score
from src.vertical_options_execution import (
    POLICY_VERSION, price_vertical_quote, scan_vertical_candidates,
    quote_ruler, refreshed_ticket,
)
from streamlit.errors import StreamlitAPIException''')
s = once(s, '_PREVIEW_FRESH_SECONDS = 150.0', '_PREVIEW_FRESH_SECONDS = 30.0')
# Never let an old cached NORMAL/SMART store silently retain the previous policy.
s = once(s, '    if not isinstance(store, VerticalResultStore):\n',
'''    if (not isinstance(store, VerticalResultStore)
            or getattr(store, "_vo_execution_policy", None) != POLICY_VERSION):
''')
s = once(s, '    store.prune_before(_minimum_expiry_date())',
'''    store._vo_execution_policy = POLICY_VERSION
    store.prune_before(_minimum_expiry_date())''')
s = once(s, '            if result.get("pricing_mode", "NORMAL") != pricing_mode:',
'''            if (result.get("pricing_mode", "NORMAL") != pricing_mode
                    or getattr(result["candidates"], "_vo_execution_policy", None) != POLICY_VERSION):''')
s = once(s, '                min_reward_risk = _entered_reward_risk(ratio_text)',
         '                min_reward_risk = max(2.0, _entered_reward_risk(ratio_text))')
s = once(s, '''        "fill_market": {
            "bid": candidate.get("spread_bid"), "mid": candidate.get("spread_mid"),
            "ask": candidate.get("spread_ask"), "debit": candidate.get("debit"),
            "estimate": indicative_fill_score(candidate),
        },''', '        "fill_market": quote_ruler(candidate),')

s = function(s, '_hydrate_vertical_options_pricing', '''def _hydrate_vertical_options_pricing() -> bool:
    """Read optional browser storage without blocking the working scanner.

    No fallback/default is written while a read is pending. A late reply never
    overrides a user's explicit mode change made in the meantime.
    """
    if st.session_state.get("_vo_pricing_mode_hydrated", False):
        return True
    restored = _PRICING_PREFERENCE_COMPONENT(
        action="read", key="vo_pricing_preference_reader", height=0, default=None,
    )
    if not isinstance(restored, dict) or not restored.get("ready"):
        return False
    previous = restored.get("mode")
    if previous not in {"NORMAL", "SMART FILL"}:
        previous = st.session_state.get("_vo_pricing_mode_preference",
                                       st.session_state.get("vo_pricing_mode", "NORMAL"))
    if not st.session_state.get("_vo_pricing_mode_dirty"):
        st.session_state["_vo_pricing_mode_preference"] = (
            previous if previous in {"NORMAL", "SMART FILL"} else "NORMAL")
    st.session_state["_vo_pricing_storage_available"] = bool(restored.get("available", True))
    st.session_state["_vo_pricing_mode_hydrated"] = True
    return True''')
s = function(s, '_remember_pricing_mode', '''def _remember_pricing_mode() -> None:
    st.session_state["_vo_pricing_mode_preference"] = (
        "SMART FILL" if st.session_state["vo_smart_fill"] else "NORMAL"
    )
    st.session_state["_vo_pricing_mode_dirty"] = True''')
s = function(s, '_pricing_mode_flip_switch', '''def _pricing_mode_flip_switch() -> str:
    """Session mode is immediately usable; browser preferences restore when ready."""
    ready = _hydrate_vertical_options_pricing()
    st.session_state.setdefault("_vo_pricing_mode_preference", "NORMAL")
    st.session_state["vo_smart_fill"] = (
        st.session_state["_vo_pricing_mode_preference"] == "SMART FILL")
    smart = st.toggle(
        "SMART FILL", key="vo_smart_fill", label_visibility="collapsed",
        on_change=_remember_pricing_mode,
        help="LEFT: NORMAL, a MID-to-ASK limit. RIGHT: SMART FILL, natural ASK only. "
             "Both require at least 1:2 reward/risk at the actual limit. No guaranteed fill.",
    )
    mode = "SMART FILL" if smart else "NORMAL"
    st.session_state["_vo_pricing_mode_preference"] = mode
    if (st.session_state.get("_vo_pricing_mode_dirty") or
            (ready and st.session_state.get("_vo_pricing_storage_available", True))):
        _PRICING_PREFERENCE_COMPONENT(
            action="write", value=mode, key="vo_pricing_preference_writer",
            allow_write=True, height=0, default=None,
        )
    return mode''')
s = function(s, '_render_vertical_options_bootstrap', '''def _render_vertical_options_bootstrap(vault_key: str) -> None:
    """Compatibility warm-up only: optional storage is never an entry gate."""
    _hydrate_vertical_options_pricing()
    st.session_state["_vo_workspace_ready"] = vault_key''')
a = s.index('    if st.session_state.get("_vo_workspace_ready") != vault_key:', s.index('def render_vertical_options('))
b = s.index('    _render_vertical_options_fragment(client, touch_session, vault_key)', a)
s = s[:a] + '''    st.session_state.pop("_vo_bootstrap_failed", None)
    st.session_state["_vo_workspace_ready"] = vault_key
''' + s[b:]
# Tight mode row, no OFF/ON marker; NORMAL help belongs on its left.
s = once(s, 'with paste_cols[1], st.container(width=820):', 'with paste_cols[1], st.container(width=680):')
s = once(s, '[1.05, .50, 1.25, 1.85], gap="small", vertical_alignment="center"',
         '[1.0, .48, 1.35, 2.0], gap="small", vertical_alignment="center"')
s = once(s, '''                    '<span>NORMAL <span class="vo-mode-help" tabindex="0" aria-label="Normal mode explanation">?' ''',
         '''                    '<span><span class="vo-mode-help vo-mode-help--normal" tabindex="0" aria-label="Normal mode explanation">?' ''') if '''                    '<span>NORMAL <span class="vo-mode-help" tabindex="0" aria-label="Normal mode explanation">?' ''' in s else s
s = once(s, '<span>NORMAL <span class="vo-mode-help" tabindex="0" aria-label="Normal mode explanation">?',
         '<span><span class="vo-mode-help vo-mode-help--normal" tabindex="0" aria-label="Normal mode explanation">?')
s = once(s, '''                    'Existing pricing: natural ASK first when it meets your selected reward-to-risk '
                    'and cost limits; otherwise qualifying LAST or MID. '
                    'Unquoted LAST* is information only. No guaranteed fill.'
                    '</span></span></span></div>' ''', '') if False else s
s = once(s, '''                    'Existing pricing: natural ASK first when it meets your selected reward-to-risk '
                    'and cost limits; otherwise qualifying LAST or MID. '
                    'Unquoted LAST* is information only. No guaranteed fill.'
                    '</span></span></span></div>'
''', '''                    'NORMAL suggests a limit between MID and ASK, capped at your risk and cost limits. '
                    'At least 1:2 reward/risk at the selected debit. A lower price may wait or not fill.'
                    '</span></span> NORMAL</span></div>'
''')
s = once(s, '''                    'Suggests a limit between spread MID and natural ASK using quote width '
                    'and available leg volume/open interest. Keeps your selected ratio and cost cap; '
                    'offers manual DAY-order price steps. Does not guarantee a fill or place orders automatically.' ''', '') if False else s
s = once(s, '''                    'Suggests a limit between spread MID and natural ASK using quote width '
                    'and available leg volume/open interest. Keeps your selected ratio and cost cap; '
                    'offers manual DAY-order price steps. Does not guarantee a fill or place orders automatically.'
''', '''                    'SMART FILL uses natural ASK only: BUY ask minus SELL bid. '
                    'Only spreads meeting at least 1:2 reward/risk at that ASK qualify. '
                    'No cheaper fallback, automatic order, or guaranteed execution. DAY orders only.'
''')
s = once(s, '@media(prefers-reduced-motion:reduce)', '''/* Only the pricing switch is a two-mode selector, not an ON/OFF control. */
.st-key-vertical_options_workspace .st-key-vo_smart_fill label::after{content:none!important;display:none!important;}
.st-key-vertical_options_workspace .vo-mode-help--normal{margin-left:0;margin-right:5px;}
.st-key-vertical_options_workspace .st-key-vo_smart_fill [data-testid="stTooltipIcon"]{display:none!important;}
@media(prefers-reduced-motion:reduce)''')

# Safe fragment rerun; retain fallback for an event delivered during a full render.
helper = '''def _rerun_vertical_fragment() -> None:
    try:
        st.rerun(scope="fragment")
    except StreamlitAPIException:
        st.rerun()


def _consume_vertical_table_update(update: Any, result: dict[str, Any], symbol: str) -> bool:
    """Apply an acknowledged table message before queries; never fetch quotes."""
    if not isinstance(update, dict) or update.get("scope") != result["scope"]:
        return False
    store = result["candidates"]
    filters = st.session_state.get("_vo_column_filters")
    checked = st.session_state.get("_vo_checked_spreads")
    if not filters or not checked or filters.get("scope") != result["scope"]:
        return False
    changed = False
    revision = update.get("filter_revision")
    if type(revision) is int and revision > int(filters.get("revision", 0)):
        incoming = update.get("column_filters")
        if isinstance(incoming, dict):
            try:
                store._where(column_filters=incoming)
            except (ValueError, TypeError):
                return False
            old_cap = (filters["filters"].get("cost") or {}).get("max")
            new_cap = (incoming.get("cost") or {}).get("max")
            if old_cap != new_cap:
                st.session_state.pop("vo_review_bundle", None)
                st.session_state.pop("vo_live_confirm", None)
            filters["filters"] = incoming
            target = store.effective_target({"breakeven": {
                "target": (incoming.get("breakeven") or {}).get("target")}})
            st.session_state["vo_target_breakeven"] = "" if target is None else f"{target:g}"
            _remember_presentation("vo_target_breakeven")
            st.session_state.setdefault("_vo_manual_targets", {})[symbol] = st.session_state["vo_target_breakeven"]
            if symbol in (st.session_state.get("vo_ticker_list") or []):
                st.session_state.setdefault("vo_ticker_targets", {})[symbol] = st.session_state["vo_target_breakeven"]
        incoming_sort, direction = update.get("sort_by"), update.get("sort_direction")
        if incoming_sort in _QUALIFIED_SORT_FIELDS + ("YEAR + FILL",) and direction in {"ASC", "DESC"}:
            prefix = "vo_best_pair" if st.session_state["vo_results_view"] == "BEST PER YEAR" else "vo_all_pair"
            for key, value in ((prefix + "_sort", incoming_sort), (prefix + "_direction", direction)):
                st.session_state[key] = value
                _remember_presentation(key)
        filters["revision"] = revision
        changed = True
    revision = update.get("revision")
    if (type(revision) is int and revision > int(checked.get("revision", 0))
            and isinstance(update.get("selected_ids"), list)):
        checked["ids"] = set(store.get_many(update["selected_ids"]))
        checked["revision"] = revision
        changed = True
    if changed:
        _remember_scan_snapshot()
    return changed


'''
s = once(s, '@st.fragment\ndef _render_vertical_options_fragment', helper + '@st.fragment\ndef _render_vertical_options_fragment')
s = once(s, '                column_filters = dict(filter_state["filters"])',
'''                component_key = "vo_spread_scroll_" + scope
                component_update = st.session_state.get(component_key)
                _consume_vertical_table_update(component_update, result, symbol)
                column_filters = dict(filter_state["filters"])''')
s = once(s, '                viewport = st.session_state.get("_vo_table_viewport") or {}',
'''                requested_offset = (component_update or {}).get("viewport_offset") if isinstance(component_update, dict) else None
                if (isinstance(component_update, dict) and component_update.get("viewport_sort_key") == view_identity
                        and type(requested_offset) is int and 0 <= requested_offset < matching_count):
                    st.session_state["_vo_table_viewport"] = {
                        "scope": scope, "sort_key": view_identity,
                        "offset": min(requested_offset, max(0, matching_count - 200)),
                    }
                viewport = st.session_state.get("_vo_table_viewport") or {}''')
a = s.index('                    incoming_filters = selected_update.get("column_filters")')
b = s.index('                checked_ids = set(st.session_state["_vo_checked_spreads"]["ids"])', a)
s = s[:a] + '''                    rerun_for_component |= _consume_vertical_table_update(selected_update, result, symbol)
                    if rerun_for_component:
                        _rerun_vertical_fragment()
''' + s[b:]
# The table is already fragment-owned. No full application rerun for table events.
s = once(s, '                    sortable=True, has_target=effective_target is not None,',
'''                    sortable=True, has_target=effective_target is not None,
                    group_by_year=view == "BEST PER YEAR",''')

# Make order review discoverable, and remove the redundant authorization checkbox.
s = once(s, 'with st.expander("OPTIONAL E*TRADE LIVE ORDER REVIEW // SELECTED SPREAD", expanded=False):',
         'with st.expander("E*TRADE ORDER // PREVIEW → CONFIRM & SEND", expanded=True):')
a = s.index('    ladder = (smart_fill_ladder(', s.index('def _render_selected_order('))
b = s.index('    quantity = int(controls[0].number_input(', a)
s = s[:a] + '''    controls = st.columns([.85, .9, 2.5], gap="small")
''' + s[b:]
a = s.index('    if smart_mode:\n        limit_price = float(controls[3].selectbox(', s.index('def _render_selected_order('))
b = s.index('    try:\n        validate_vertical(', a)
s = s[:a] + s[b:]
s = once(s, '    min_reward_risk = result.get("min_reward_risk", _DEFAULT_MIN_REWARD_RISK)\n    if st.session_state.get("_vo_order_candidate")',
'''    min_reward_risk = max(2.0, result.get("min_reward_risk", _DEFAULT_MIN_REWARD_RISK))
    ticket = st.session_state.get("_vo_ticket_quote") or {}
    if (ticket.get("scope") == result.get("scope") and ticket.get("id") == candidate["id"]
            and ticket.get("mode") == result.get("pricing_mode", "NORMAL")):
        candidate = {**candidate, **ticket["price"]}
    if st.session_state.get("_vo_order_candidate")''')
s = once(s, '        st.session_state.pop("vo_last_send_results", None)\n\n    # SMART FILL uses DAY',
'''        st.session_state.pop("vo_last_send_results", None)

    # SMART FILL uses DAY''')
# Replace the full-scan demand with a fresh exact-leg preview at the current limit.
a = s.index('            if (not checked_price or not checked_price["orderable"]', s.index('def _render_selected_order('))
b = s.index('        except Exception as exc:\n            st.session_state.pop("vo_review_bundle", None)', a)
s = s[:a] + '''            if not checked_price or not checked_price["orderable"]:
                raise ValueError("The current quoted spread does not meet the selected price/risk limits. No order sent.")
            candidate = refreshed_ticket(candidate, checked_price)
            limit_price = candidate["debit"]
            fingerprint = _review_fingerprint(
                symbol=symbol, call_put=call_put, expiry=expiry,
                buy_strike=buy_strike, sell_strike=sell_strike, quantity=quantity,
                limit_price=limit_price, min_reward_risk=min_reward_risk,
                order_term=order_term, account_keys=account_keys, max_cost=result.get("max_cost"),
            )
            st.session_state["_vo_ticket_quote"] = {
                "scope": result.get("scope"), "id": candidate["id"],
                "mode": result.get("pricing_mode", "NORMAL"), "price": candidate,
            }
            st.caption(f'FRESH REVIEW // {candidate["basis"]} LIMIT {_money(limit_price)} // '
                       f'TOTAL {_money(limit_price * 100 * quantity)} PER ACCOUNT // '
                       f'BREAKEVEN {_money(candidate["breakeven"])} // '
                       f'R:R 1:{candidate["reward_risk"]:.2f}')
''' + s[b:]
# Final explicit SEND click is the authorization; consume preview before any network write.
a = s.index('        confirmed = st.checkbox(', s.index('def _render_selected_order('))
b = s.index('        if send_click:', a)
s = s[:a] + '''        st.caption("Confirm the exact fresh limit, quantity, legs and accounts above. "
                   "The next button submits a live order; it is not a saved draft.")
        send_click = st.button(
            f"CONFIRM & SEND LIVE // {quantity} SPREAD(S) // {_money(limit_price * 100 * quantity)} "
            f"PER ACCOUNT // {len(account_keys)} ACCOUNT(S)",
            key="vo_send", type="primary", use_container_width=True,
        )
''' + s[b:]
s = once(s, '''            results: list[dict[str, Any]] = []
            for account_key in account_keys:''',
'''            # Consume approval before transmission. Reruns/double-clicks cannot
            # re-use a preview, including after an uncertain network response.
            st.session_state.pop("vo_review_bundle", None)
            results: list[dict[str, Any]] = []
            for account_key in account_keys:''')
s = s.replace("'MANUAL LIMIT CHOICES ONLY.'", "'NATURAL-ASK DAY LIMIT; EXECUTION IS NOT GUARANTEED.'")
s = s.replace('"NORMAL ranks natural ASK, in-market LAST, MID, then unquoted LAST*. "',
              '"NORMAL uses a MID-to-ASK limit; unquoted LAST* is reference-only. "')
s = s.replace('f"SMART FILL suggests a MID-to-ASK limit under the 1:{min_reward_risk:g} requirement. "',
              'f"SMART FILL requires natural ASK to meet at least 1:{max(2.0, min_reward_risk):g}. "')
s = s.replace('"FILL EST is an uncalibrated quote-position indicator, not a historical fill probability; "\n                "bid≈5%, midpoint≈50%, ask≈95%, with no estimate for unquoted spreads. "',
              '"BID / MID / ASK shows actual spread quotes and the proposed limit, not a fill probability. "')
put(p, s)

# Local browser preference protocol: do not write before explicit permission.
p = "src/components/vo_pricing_preference_v1/index.html"
s = edit(p)
s = once(s, 'else if (args.action==="write") write(args.value);',
         'else if (args.action==="write" && args.allow_write===true) write(args.value);')
put(p, s)

# Entire-store quote-position sorting remains numeric, but is no longer called probability.
p = "src/vertical_options_store.py"
s = edit(p)
s = s.replace('" THEN 5.0 + 90.0 * min(1.0, max(0.0,"', '" THEN 100.0 * min(1.0, max(0.0,"')
s = s.replace('return 5.0 + 90.0 * min(1.0, max(0.0, (debit - bid) / (ask - bid)))',
              'return 100.0 * min(1.0, max(0.0, (debit - bid) / (ask - bid)))')
s = s.replace('Its 5..95 scale is monotonic', 'Its 0..100 quote-position scale is monotonic')
put(p, s)

p = "src/components/vo_spread_scroller_v1/index.html"
s = edit(p)
s = s.replace('"FILL EST"];', '"BID / MID / ASK"];')
s = s.replace('key==="fill_scale"?"FILL EST":', 'key==="fill_scale"?"PRICE POSITION %":')
s = once(s, 'let sortBy="FILL + TARGET",sortDirection="ASC",sortable=false,hasTarget=false;',
         'let sortBy="FILL + TARGET",sortDirection="ASC",sortable=false,hasTarget=false,groupByYear=false;')
s = once(s, ' sortable=Boolean(args.sortable);', ' sortable=Boolean(args.sortable);groupByYear=Boolean(args.group_by_year);')
s = once(s, 'function chooseSort(field,direction){sortBy=field;sortDirection=direction;changeFilters(columnFilters);closePanel();}',
         'function chooseSort(field,direction){sortBy=field;sortDirection=direction;changeFilters(columnFilters);renderPanel();}')
s = once(s, '  panel.appendChild(sorting);',
'''  sorting.appendChild(menuButton("RESET RANK",()=>chooseSort(groupByYear?"YEAR + FILL":"FILL + TARGET","ASC")));
  panel.appendChild(sorting);''')
s = once(s, '    changeFilters(next,300);', '    changeFilters(next,650);updateBandPreview();')
s = once(s, '   label.appendChild(input);panel.appendChild(label);',
'''   input.addEventListener("keydown",event=>{if(event.key==="Enter"){event.preventDefault();emitValue();}});
   input.addEventListener("change",()=>{if(filterTimer!==null)emitValue();});
   label.appendChild(input);panel.appendChild(label);''')
s = once(s, 'function syncNumericInputs(){', '''function updateBandPreview(){
 const note=panel.querySelector("[data-band-preview]");if(!note)return;
 const target=Number(columnFilters.breakeven?.target),percent=Number(columnFilters.breakeven?.percent??5);
 note.textContent=target>0&&Number.isFinite(percent)?
  "$"+(target*(1-percent/100)).toFixed(2)+" ≤ BREAKEVEN ≤ $"+(target*(1+percent/100)).toFixed(2):
  "Enter TARGET $ and ±%. No target means no price band.";
}
function syncNumericInputs(){''')
s = once(s, '''  }
 }
function menuButton''', '''  }
 }
function menuButton''') if False else s
s = once(s, 'function menuButton(label,handler){', 'function menuButton(label,handler){')
s = once(s, '   const note=document.createElement("div");note.className="filter-empty";note.textContent="Target ±% filters rows. Example: $250 ±5% = $237.50–$262.50.";panel.appendChild(note);',
'''   const presets=document.createElement("div");presets.className="filter-actions";
   for(const percent of [1,2,5,10])presets.appendChild(menuButton("±"+percent+"%",()=>{
    const next={...columnFilters,breakeven:{...(columnFilters.breakeven||{}),percent}};
    changeFilters(next);syncNumericInputs();updateBandPreview();
   }));panel.appendChild(presets);
   const note=document.createElement("div");note.className="filter-empty";note.dataset.bandPreview="true";
   panel.appendChild(note);updateBandPreview();''')
# A valid percent can be edited first; zero is meaningful and must not become 5.
s = once(s, '    if(Object.hasOwn(range,"target")){', '    if(Object.hasOwn(range,"target")||Object.hasOwn(value,"percent")){')
s = once(s, '   if(NUMERIC.has(openKey))syncNumericInputs();',
         '   if(NUMERIC.has(openKey)){syncNumericInputs();updateBandPreview();}')
a = s.index('  if(i===12){')
b = s.index('  if(i===7){', a)
s = s[:a] + '''  if(i===12){
   const market=row.fill_market||{};
   const finite=value=>value!==null&&value!==undefined&&value!==""&&Number.isFinite(Number(value));
   const bid=Number(market.bid),ask=Number(market.ask),debit=Number(market.debit);
   const quoted=finite(market.bid)&&finite(market.ask)&&ask>=bid;
   const mid=quoted?(bid+ask)/2:null;
   const money=value=>finite(value)?Number(value).toLocaleString("en-US",{style:"currency",currency:"USD",minimumFractionDigits:2,maximumFractionDigits:2}):"—";
   const ruler=document.createElement("div");ruler.className="fill-ruler";
   const labels=document.createElement("div");labels.className="fill-labels";
   for(const [name,value] of [["BID",quoted?bid:null],["MID",mid],["ASK",quoted?ask:null]]){
    const label=document.createElement("span"),amount=document.createElement("small");
    label.textContent=name;amount.textContent=money(value);label.appendChild(amount);labels.appendChild(label);
   }
   const track=document.createElement("div");track.className="fill-track";
   const detail=document.createElement("small");detail.className="fill-estimate";
   if(quoted&&finite(market.debit)){
    const marker=document.createElement("i");marker.className="fill-marker";
    marker.style.left=(ask===bid?50:100*Math.max(0,Math.min(1,(debit-bid)/(ask-bid))))+"%";
    track.appendChild(marker);
    detail.textContent="LIMIT "+money(debit)+(Math.abs(debit-ask)<.005?" · ASK":debit<bid?" · BELOW BID":debit>ask?" · ABOVE ASK":"");
    cell.title="BID "+money(bid)+" / MID "+money(mid)+" / ASK "+money(ask)+" / proposed debit "+money(debit)+
     ". Spread prices per share; Debit column is dollars per 100-share contract. No fill probability is inferred.";
   }else{
    detail.textContent="LIMIT "+money(market.debit)+" · NO QUOTES";detail.classList.add("unavailable");
    cell.title="Current spread quotes are unavailable. Displayed debit is reference-only; rescan live quotes before order review.";
   }
   ruler.append(labels,track,detail);cell.classList.add("fill-scale");cell.replaceChildren(ruler);
   line.appendChild(cell);continue;
  }
''' + s[b:]
s = once(s, '.fill-labels{display:flex;justify-content:space-between;white-space:nowrap}',
'''.fill-labels{display:flex;justify-content:space-between;white-space:nowrap;gap:2px}
.fill-labels>span{display:flex;flex-direction:column;gap:1px;align-items:center;font-size:7px}
.fill-labels small{font:800 9px/1 "Courier New",monospace}
.fill-ruler{gap:1px}
.fill-track{margin-top:2px;margin-bottom:3px}
.filter-actions button[aria-pressed="true"]{background:#fb8b1e;color:#000}''')
put(p, s)

# Record the actual implementation scope and honest verification status.
p = "src/TERMINAL_CHANGELOG.md"
s = edit(p).rstrip() + '''\n\n## 2026-11-06 — Desktop Vertical Options nonblocking startup and ASK execution policy
- **Production route:** streamlit_app.py → src/vertical_options_ui.py; desktop-owned execution helper and vo-only components. Shared canonical scanner changes only add an optional quote_pricer callback; default phone/canonical behavior is unchanged.
- **Production files:** src/vertical_options_ui.py, src/vertical_options_execution.py, src/vertical_options.py, src/vertical_options_store.py, src/components/vo_pricing_preference_v1/index.html, src/components/vo_spread_scroller_v1/index.html. Focused regression files are recorded with the verification receipt.
- **Root causes:** Optional browser-preference timeout permanently gated the feature; a two-mode switch inherited OFF/ON text; quote drawing required a fabricated estimate; every table event escalated to a full-app rerun; SMART allowed midpoint fallback to meet risk criteria; review required rescanning all expirations after a selected quote changed.
- **Changes:** Nonblocking session mode with no default writes before storage hydration; late saved replies cannot override explicit user changes. Normal help on left and switch-only removal of OFF/ON. Actual bid/mid/ask numbers including negative bids and a proposed-debit marker independent of fill estimation. Quote-position scale is not fill probability. Desktop SMART uses natural ASK only, with 2:1 minimum at the actual debit; NORMAL uses a capped MID-to-ASK limit. Numeric filters debounce and show live bands/presets; sorts remain in their menus; updates are consumed before query/render and reruns are fragment-scoped. Selected order review opens directly; exact legs refresh on Preview and updated price/risk are shown before one explicit CONFIRM & SEND, with consumed preview and no automatic broker placement.
- **Must remain:** Complete scan retention, latest-two-year defaults, phone legacy pricing/filter contract, explicit final send, all-account successful previews, exact option identities, cost caps, no repeat placement after uncertain response, existing quotes/exports/drafts.
- **Intentionally unchanged:** Phone UI/components, shared theme, core, routing/navigation, OAuth/auth/session and broker transport, Risk, GEX, Holdings and other top-level features.
- **Testing / commit:** Workbench branch only until syntax, focused regressions, architecture guard, tab smoke and browser checks are verified. Final SHA/verification appended after testing; no live broker trades are used. Managed Cloud reboot and signed-in production remain separate verification requirements.
- **Lesson:** Optional persistence cannot gate a financial workspace. Price/risk selection must use the actual executable limit; never infer a fill probability from bid/ask position.\n'''
put(p,s)

allowed = {
 "src/vertical_options.py", "src/vertical_options_ui.py", "src/vertical_options_execution.py",
 "src/vertical_options_store.py", "src/components/vo_pricing_preference_v1/index.html",
 "src/components/vo_spread_scroller_v1/index.html", "src/TERMINAL_CHANGELOG.md",
}
changed = set(subprocess.check_output(["git", "diff", "--name-only"], cwd=ROOT, text=True).splitlines())
assert changed <= allowed, changed - allowed
print("PATCH SCOPE:", sorted(changed))
