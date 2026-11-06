from pathlib import Path
import subprocess

ALLOWED = {'src/vertical_options_ui.py', 'src/components/vo_spread_scroller_v1/index.html', 'tests/test_vertical_options.py', 'scripts/test_vertical_options_scanner_browser.py', 'src/TERMINAL_CHANGELOG.md'}
def once(s, a, b):
    assert s.count(a) == 1, (s.count(a), a[:100])
    return s.replace(a, b)

def edit(path, transform):
    assert path in ALLOWED
    p = Path(path)
    p.write_text(transform(p.read_text()))

def ui(s):
    marker = 'def _spread_table_row(candidate: dict[str, Any], annual_ids: set[str], *, tv_exchange: str = "") -> dict[str, Any]:\n'
    helpers = '''# ==============================
# TABLE-ONLY LEG OPEN INTEREST (SAME SNAPSHOT; NO BROKER CALLS)
# ==============================
def _leg_quote_key(row: dict[str, Any], prefix: str = "") -> tuple:
    """Match the exact contract and quote retained by the canonical scanner."""
    def number(value, *, positive=False):
        try:
            value = float(value)
        except (TypeError, ValueError, OverflowError):
            return None
        return value if math.isfinite(value) and (value > 0 if positive else value >= 0) else None
    return (
        row.get("expiry"), row.get("call_put"), number(row.get(prefix + "strike")),
        str(row.get(prefix + "osi") or "").strip(),
        number(row.get(prefix + "last"), positive=True),
        number(row.get(prefix + "bid")), number(row.get(prefix + "ask")),
    )


def _table_open_interest(result: dict[str, Any]) -> dict[tuple, int | None]:
    """Index raw leg OI once per snapshot/batch, not once per spread or rerun.

    Keeping this presentation join here avoids changing shared spread math,
    disk schemas, phone behavior, order payloads, or existing export columns.
    Missing OI stays unknown; zero is a valid reported contract count.
    """
    raw = (st.session_state.get("_vo_chain_cache") or {}).get(
        f'{result.get("source")}:{result.get("symbol")}'
    )
    if (not isinstance(raw, dict) or result.get("raw_scan_id") is None
            or raw.get("scan_id") != result["raw_scan_id"]):
        return {}
    rows = raw.get("rows") or []
    signature = (raw["scan_id"], len(rows))
    cached = raw.get("_vo_table_open_interest")
    if isinstance(cached, dict) and cached.get("signature") == signature:
        return cached["values"]
    values = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        value = row.get("open_interest")
        try:
            count = float(value)
            count = (int(count) if not isinstance(value, bool) and math.isfinite(count)
                     and count >= 0 and count.is_integer() else None)
        except (TypeError, ValueError, OverflowError):
            count = None
        # Equal quoted duplicates use the first contract, as in the scanner.
        values.setdefault(_leg_quote_key(row), count)
    raw["_vo_table_open_interest"] = {"signature": signature, "values": values}
    return values


def _spread_table_row(candidate: dict[str, Any], annual_ids: set[str], *, tv_exchange: str = "",
                      contract_open_interest: dict | None = None) -> dict[str, Any]:
'''
    s = once(s, marker, helpers)
    s = once(s, '        "expiry_detail": _expiry_time_label(candidate["expiry"]),\n        "fill_market": {', '''        "expiry_detail": _expiry_time_label(candidate["expiry"]),
        "open_interest": {
            leg: (contract_open_interest or {}).get(_leg_quote_key(candidate, leg + "_"))
            for leg in ("buy", "sell")
        },
        "fill_market": {''')
    return once(s, '''                displayed = [_spread_table_row(row, annual_ids, tv_exchange=result.get("tv_exchange", ""))
                             for row in displayed_candidates]''', '''                contract_open_interest = _table_open_interest(result)
                displayed = [_spread_table_row(
                    row, annual_ids, tv_exchange=result.get("tv_exchange", ""),
                    contract_open_interest=contract_open_interest,
                ) for row in displayed_candidates]''')
edit('src/vertical_options_ui.py', ui)

def component(s):
    s = once(s, 'grid-template-columns:5fr 8fr 5fr 4fr 10fr 6fr 6fr 7fr 7fr 8fr 8fr 9fr 9fr 12fr', 'grid-template-columns:4fr 6fr 5fr 4fr 10fr 9fr 9fr 9fr 8fr 9fr 9fr 9fr 18fr')
    s = once(s, '#table-content{min-width:1180px}', '#table-content{min-width:1040px}')
    s = once(s, '.filter-icon{flex:none;font-size:.7rem}', '''.filter-icon{flex:none;font-size:.7rem}
/* One compact column per paired leg field; BUY always precedes SELL. */
#head>.leg-head{flex-direction:column;align-items:stretch;justify-content:center;padding:2px 3px;gap:2px}
.leg-column-title{font:900 .64rem/1 "Courier New",monospace;white-space:nowrap}
.leg-filter-pair{display:flex;align-items:center;gap:2px;min-height:17px}
.leg-filter-pair .filter-button{flex:1 1 0;height:18px;padding:0 2px;font-size:.55rem}
.leg-filter-pair .filter-icon{font-size:.55rem}
.leg-order{font:700 .55rem/1 "Courier New",monospace}
.row>.cell.leg-stack{flex-direction:column;justify-content:center;align-items:flex-end;gap:2px;line-height:16px;text-decoration:none}
.leg-stack>.buy-leg{font-size:14px;font-weight:900}
.leg-stack>.sell-leg{font-size:14px;font-weight:500}
.leg-stack>span{max-width:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}''')
    s = once(s, '.row>.cell:nth-child(10),.row>.cell:nth-child(11),.row>.cell:nth-child(13){font-weight:900!important;text-decoration:underline;text-underline-offset:2px}', '.row>.cell.cost,.row>.cell[data-field="breakeven"],.row>.cell[data-field="profit"]{font-weight:900!important;text-decoration:underline;text-underline-offset:2px}')
    a = s.index('for(let index=0;index<HEAD.length;index++){')
    b = s.index('head.addEventListener("click"', a)
    s = s[:a] + '''// Canonical HEAD/KEYS and server filters stay unchanged (also used by phone).
// Only desktop presentation combines the two strike and last-price cells.
function headerFilter(key,title,label=title){
 const button=document.createElement("button");button.type="button";button.className="filter-button";
 button.dataset.key=key;button.setAttribute("aria-label","Filter "+title);
 const name=document.createElement("span");name.className="filter-name";name.textContent=label;
 const icon=document.createElement("span");icon.className="filter-icon";icon.textContent="▾";
 button.append(name,icon);return button;
}
function pairedHeader(title,buyKey,sellKey){
 const cell=document.createElement("span");cell.className="leg-head";cell.dataset.column=title;
 const name=document.createElement("span");name.className="leg-column-title";name.textContent=title;
 const pair=document.createElement("div");pair.className="leg-filter-pair";
 const slash=document.createElement("span");slash.className="leg-order";slash.textContent="/";
 pair.append(headerFilter(buyKey,HEAD[KEYS.indexOf(buyKey)+2],"BUY"),slash,
             headerFilter(sellKey,HEAD[KEYS.indexOf(sellKey)+2],"SELL"));
 cell.append(name,pair);return cell;
}
for(let index=0;index<HEAD.length;index++){
 const headerKey=index===HEAD.length-1?"fill_scale":KEYS[index-2];
 if(["basis","sell","sell_last"].includes(headerKey))continue;
 const title=HEAD[index];let cell;
 if(headerKey==="buy"||headerKey==="buy_last"){
  cell=pairedHeader(headerKey==="buy"?"STRIKE":"LAST PRICE",headerKey,
                    headerKey==="buy"?"sell":"sell_last");
 }else{
  cell=document.createElement("span");cell.title=title;cell.dataset.column=title;
  if(index<2)cell.textContent=title;
  else cell.appendChild(headerFilter(headerKey,title));
 }
 head.appendChild(cell);
 if(headerKey==="buy_last"){
  const oi=document.createElement("span");oi.className="leg-head";oi.dataset.column="OPEN INTEREST";
  const name=document.createElement("span");name.className="leg-column-title";name.textContent="OPEN INTEREST";
  const order=document.createElement("span");order.className="leg-order";order.textContent="(BUY / SELL)";
  oi.title="Open contracts per leg from this scan snapshot; BUY above SELL. — means not supplied.";
  oi.append(name,order);head.appendChild(oi);
 }
}
''' + s[b:]
    s = once(s, 'function makeRow(row,index){', '''function pairedCell(field,buyKey,sellKey,buy,sell){
 const cell=document.createElement("span");cell.className="cell leg-stack";cell.dataset.field=field;
 for(const [side,key,value] of [["buy",buyKey,buy],["sell",sellKey,sell]]){
  const line=document.createElement("span");line.className=side+"-leg";line.dataset.field=key;
  line.textContent=value;line.setAttribute("aria-label",side.toUpperCase()+" "+value);cell.appendChild(line);
 }
 return cell;
}
function strikePrice(value){
 const number=Number(value);
 return value!==null&&value!==undefined&&value!==""&&Number.isFinite(number)?
  number.toLocaleString("en-US",{style:"currency",currency:"USD",minimumFractionDigits:2,maximumFractionDigits:6}):"—";
}
function openInterest(value){
 return typeof value==="number"&&Number.isSafeInteger(value)&&value>=0?value.toLocaleString("en-US"):"—";
}
function makeRow(row,index){''')
    return once(s, '''  if(KEYS[i]==="basis")continue;
  const cell=document.createElement("span");cell.className="cell";''', '''  if(["basis","sell","sell_last"].includes(KEYS[i]))continue;
  if(i===3||i===5){
   line.appendChild(pairedCell(i===3?"strike_pair":"last_pair",KEYS[i],KEYS[i+1],
    i===3?strikePrice(values[i]):String(values[i]??"—"),
    i===3?strikePrice(values[i+1]):String(values[i+1]??"—")));
   if(i===5)line.appendChild(pairedCell("open_interest","buy_open_interest","sell_open_interest",
    openInterest(row.open_interest?.buy),openInterest(row.open_interest?.sell)));
   continue;
  }
  const cell=document.createElement("span");cell.className="cell";cell.dataset.field=KEYS[i]||"fill_scale";''')
edit('src/components/vo_spread_scroller_v1/index.html', component)

def tests(s):
    return once(s, 'class VerticalOptionsTests(unittest.TestCase):\n', '''class VerticalOptionsTests(unittest.TestCase):
    def test_stacked_leg_open_interest_uses_exact_cached_quote_and_keeps_zero(self):
        from copy import deepcopy
        from unittest.mock import patch
        import src.vertical_options_ui as vo
        expiry = date(2029, 1, 19)
        rows = [dict(expiry=expiry, call_put="CALL", strike=100, osi="QQQ-C100",
                     last=6.0, bid=5.9, ask=6.1, open_interest=12345),
                dict(expiry=expiry, call_put="CALL", strike=110, osi="QQQ-C110",
                     last=3.0, bid=2.9, ask=3.1, open_interest=0)]
        raw = {"scan_id": 77, "rows": rows}
        result = {"source": "E*TRADE", "symbol": "QQQ", "raw_scan_id": 77}
        state = {"_vo_chain_cache": {"E*TRADE:QQQ": raw}}
        candidates, annual = scan_vertical_candidates(
            symbol="QQQ", spot_last=100, option_rows=rows, target_breakeven=None,
            max_cost=None, max_per_expiration=None, pricing_mode="SMART FILL",
            desktop_fast_fill=True)
        self.assertEqual(len(candidates), 1)
        candidate = candidates[0]
        before = deepcopy(candidate)
        with patch.object(vo.st, "session_state", state):
            index = vo._table_open_interest(result)
            self.assertIs(index, vo._table_open_interest(result))
            payload = vo._spread_table_row(candidate, annual, contract_open_interest=index)
            self.assertEqual(payload["open_interest"], {"buy": 12345, "sell": 0})
            self.assertEqual(payload["values"], vo._spread_table_row(candidate, annual)["values"])
            self.assertEqual(candidate, before)  # Read-only presentation; no repricing.
            self.assertEqual(vo._table_open_interest({**result, "raw_scan_id": 78}), {})
            self.assertEqual(vo._table_open_interest({**result, "source": "YFINANCE"}), {})
            # A second quote of the same OSI must not steal OI from the chosen quote.
            rows.append({**rows[0], "last": 6.2, "open_interest": 999})
            updated = vo._table_open_interest(result)
            self.assertIsNot(index, updated)
            self.assertEqual(vo._spread_table_row(candidate, annual,
                             contract_open_interest=updated)["open_interest"]["buy"], 12345)
            # Without matching quotes show unknown, not OI from another contract.
            missing = {**candidate, "sell_osi": "OTHER"}
            self.assertIsNone(vo._spread_table_row(missing, annual,
                              contract_open_interest=updated)["open_interest"]["sell"])

    def test_stacked_open_interest_missing_invalid_and_put_leg_identity(self):
        from unittest.mock import patch
        import src.vertical_options_ui as vo
        expiry = date(2029, 1, 19)
        for invalid in (None, "", -1, float("nan"), float("inf"), 1.5, True):
            with self.subTest(oi=invalid):
                row = dict(expiry=expiry, call_put="PUT", strike=110, osi="QQQ-P110",
                           last=3.0, bid=2.9, ask=3.1, open_interest=invalid)
                raw = {"scan_id": 1, "rows": [row]}
                state = {"_vo_chain_cache": {"E*TRADE:QQQ": raw}}
                result = {"source": "E*TRADE", "symbol": "QQQ", "raw_scan_id": 1}
                with patch.object(vo.st, "session_state", state):
                    index = vo._table_open_interest(result)
                self.assertIsNone(index[vo._leg_quote_key(row)])
        buy = dict(expiry=expiry, call_put="PUT", strike=110, osi="QQQ-P110",
                   last=3.0, bid=2.9, ask=3.1, open_interest=2500)
        sell = {**buy, "strike": 100, "osi": "QQQ-P100", "last": 1.0,
                "bid": .9, "ask": 1.1, "open_interest": 777}
        index = {vo._leg_quote_key(buy): 2500, vo._leg_quote_key(sell): 777}
        candidates, annual = scan_vertical_candidates(
            symbol="QQQ", spot_last=100, option_rows=[buy, sell], target_breakeven=None,
            max_cost=None, max_per_expiration=None)
        self.assertEqual(vo._spread_table_row(candidates[0], annual,
                         contract_open_interest=index)["open_interest"], {"buy": 2500, "sell": 777})

''')
edit('tests/test_vertical_options.py', tests)

def browser(s):
    import re
    s = once(s, 'def grid_snapshot(driver: webdriver.Chrome) -> dict:\n', '''# Read the same canonical values from the rendered stacked cells so existing
# pricing/export/selection regressions keep checking actual on-screen data.
_STACKED_ROW_VALUES_JS = """
function readValues(row){
 const fields=["ticker","side","expiry","buy","sell","buy_last","sell_last",
               "cost","breakeven","ratio","profit","fill_scale"];
 return fields.map(field=>{
  const cell=row.querySelector('[data-field="'+field+'"]');
  if(field==='buy'||field==='sell')return String(Number(cell.textContent.replace(/[$,]/g,'')));
  if(field==='expiry')return cell.firstChild.textContent;
  if(field==='cost')return cell.querySelector('.debit-value')?.textContent;
  return cell?.textContent;
 }).concat(row.querySelector('.fill-detail')?.textContent);
}
"""


def grid_snapshot(driver: webdriver.Chrome) -> dict:
''')
    a = s.index('def grid_snapshot('); b = s.index('def assert_year_grouping(', a)
    region = s[a:b].replace('driver.execute_script(\n        """', 'driver.execute_script(\n        _STACKED_ROW_VALUES_JS + """')
    pattern = r"values:\[\.\.\.row\.querySelectorAll\('\.cell'\)\]\.map\(cell=>\s+cell\.classList\.contains\('expiry'\) \? cell\.firstChild\.textContent :\s+cell\.classList\.contains\('cost'\) \? cell\.querySelector\('\.debit-value'\)\?\.textContent :\s+cell\.textContent\)\.concat\(row\.querySelector\('\.fill-detail'\)\?\.textContent\),"
    region, count = re.subn(pattern, 'values:readValues(row),', region)
    assert count == 2
    region = once(region, '            cellCount:row.children.length,\n', '''            cellCount:row.children.length,
            stacked:[...row.querySelectorAll('.leg-stack')].map(cell=>{
              const buy=cell.querySelector('.buy-leg'),sell=cell.querySelector('.sell-leg'),
                    c=cell.getBoundingClientRect(),b=buy.getBoundingClientRect(),s=sell.getBoundingClientRect();
              return {field:cell.dataset.field,buy:buy.textContent,sell:sell.textContent,
                fits:b.top>=c.top&&s.top>=b.bottom&&s.bottom<=c.bottom&&
                  buy.scrollWidth<=cell.clientWidth&&sell.scrollWidth<=cell.clientWidth,
                buyWeight:getComputedStyle(buy).fontWeight,sellWeight:getComputedStyle(sell).fontWeight};
            }),
''')
    region = once(region, '''            emphasis:[7,8,10].map(i=>({
              weight:getComputedStyle(row.querySelectorAll('.cell')[i]).fontWeight,
              decoration:getComputedStyle(row.querySelectorAll('.cell')[i]).textDecorationLine,
            })),''', '''            emphasis:["cost","breakeven","profit"].map(field=>({
              weight:getComputedStyle(row.querySelector('[data-field="'+field+'"]')).fontWeight,
              decoration:getComputedStyle(row.querySelector('[data-field="'+field+'"]')).textDecorationLine,
            })),''')
    region = once(region, "headers:[...head.children].map(cell=>cell.querySelector('.filter-name')?.textContent||cell.textContent),", "headers:[...head.children].map(cell=>cell.dataset.column||cell.textContent),")
    region = once(region, "lastColor:getComputedStyle(row.querySelectorAll('.cell')[10]).color,", "lastColor:getComputedStyle(row.querySelector('[data-field=\"profit\"]')).color,")
    s = s[:a] + region + s[b:]
    s = once(s, 'assert len(snapshot["headers"]) == 14 and snapshot["headers"][9] == "Debit" and snapshot["headers"][-1] == "FILL EST", snapshot', '''assert snapshot["headers"] == ["SELECT", "OS / TV", "TICKER", "TYPE", "EXP / YEAR",
                                    "STRIKE", "LAST PRICE", "OPEN INTEREST", "Debit", "BREAKEVEN",
                                    "RISK:REWARD", "MAX PROFIT", "FILL EST"], snapshot''')
    s = once(s, '        assert row["cellCount"] == 14 and len(row["values"]) == 13, row', '''        assert row["cellCount"] == 13 and len(row["values"]) == 13, row
        assert [item["field"] for item in row["stacked"]] == ["strike_pair", "last_pair", "open_interest"], row
        assert all(item["fits"] and int(item["buyWeight"]) > int(item["sellWeight"])
                   for item in row["stacked"]), row''')
    s = once(s, '''                            const cells=row.querySelectorAll('.cell');
                            types.add(cells[1].textContent);
                            strikes.add(cells[3].textContent);
                            strikes.add(cells[4].textContent);''', '''                            types.add(row.querySelector('[data-field="side"]').textContent);
                            strikes.add(String(Number(row.querySelector('[data-field="buy"]').textContent.replace(/[$,]/g,''))));
                            strikes.add(String(Number(row.querySelector('[data-field="sell"]').textContent.replace(/[$,]/g,''))));''')
    s = once(s, "row=>row.querySelectorAll('.cell')[1].textContent==='P');", "row=>row.querySelector('[data-field=\"side\"]').textContent==='P');")
    s = s.replace('PASS (14 columns, 43px rows)', 'PASS (13 columns with stacked legs/OI, 43px rows)')
    s = once(s, '''                    "strikePrice": strike, "lastPrice": last,
                    # Asymmetric quotes''', '''                    "strikePrice": strike, "lastPrice": last,
                    "openInterest": 0 if strike == 90 else strike * 10 + (1000 if side == "P" else 0),
                    # Asymmetric quotes''')
    return once(s, '''        assert row["debitValue"] == row["values"][7] and row["debitValue"].startswith("$"), row''', '''        def expected_oi(strike):
            value = int(float(strike))
            return f'{0 if value == 90 else value * 10 + (1000 if row["values"][1] == "P" else 0):,}'
        assert row["stacked"][2]["buy"] == expected_oi(row["values"][3]), row
        assert row["stacked"][2]["sell"] == expected_oi(row["values"][4]), row
        assert row["debitValue"] == row["values"][7] and row["debitValue"].startswith("$"), row''')
edit('scripts/test_vertical_options_scanner_browser.py', browser)

entry = '''

## 2026-11-06 — Vertical Options stacked BUY/SELL strike, last price and open interest
- **Feature / production route:** Original desktop Vertical Options only: `streamlit_app.py` imports and dispatches `src/vertical_options_ui.py`, which renders `src/components/vo_spread_scroller_v1/index.html`.
- **Exact production files:** `src/vertical_options_ui.py`, `src/components/vo_spread_scroller_v1/index.html`. Focused tests: `tests/test_vertical_options.py`, `scripts/test_vertical_options_scanner_browser.py`; this append-only history.
- **Request / root cause:** Separate BUY STRIKE / SELL STRIKE and BUY LAST / SELL LAST columns consumed unnecessary width; per-leg open interest already existed in the normalized scan but was not presented in the result table.
- **Changes:** Combine strikes into one STRIKE column and last prices into one LAST PRICE column, BUY bold above SELL, with independently accessible BUY and SELL filters/sorts in each header. Add an OPEN INTEREST column using the same stacked order, exact comma-separated contract counts, zero preserved, and an em dash for unavailable values. Retain compact 43px rows; the table is 13 visible columns and its minimum width drops from 1180px to 1040px.
- **Data isolation:** UI-only OI lookup joins the exact expiry/type/strike/OSI and retained quote to the same raw scan ID. The index is built once per snapshot/batch and reused across table reruns. No new broker calls, fabricated values, combined-leg OI, shared schema changes or pricing changes. Old/missing snapshots show unknown rather than borrowing another ticker's/source's data.
- **Must remain:** Canonical table KEYS/value order, full-scan numeric sorting/filtering, target +/- percentage, 1:2+ natural-ASK Smart Fill, selection identity, quote ruler, deferred exports, and explicit broker Preview then Place are preserved. CALL/PUT leg direction determines BUY/SELL order, not ascending strike order.
- **Intentionally NOT changed:** `src/vertical_options.py`, `src/vertical_options_store.py`, Verti Phone UI/components, Risk Sizing, GEX, Holdings, OAuth, broker transport, navigation, shared theme, `streamlit_app.py`, `src/terminal_core.py`, and CSV/Excel column contracts.
- **Tests:** Local 120 desktop + 24 phone tests PASS; Python and component JavaScript syntax PASS; architecture guard PASS. Direct Chromium component tests at 1440/1100/1040px verify 13 columns, 43px rows, BUY above SELL including puts, exact OI/zero/unknown, no horizontal overflow, independent leg filters and descending sorts, and no typing-triggered component rerun. Full production-path browser, neighboring-tab smoke and deployed checks are required in PR/production CI. No live trades submitted; signed-in Cloud access and managed reboot remain unverified unless independently evidenced.
- **Lesson:** Presentation grouping must not change canonical contract identity or field order. Join supplementary market data to the exact cached quote and scan, never fetch it per result or replace missing OI with zero.
'''
with Path('src/TERMINAL_CHANGELOG.md').open('a') as out:
    out.write(entry)
changed = set(subprocess.check_output(['git', 'diff', '--name-only'], text=True).splitlines())
assert changed == ALLOWED, changed
print('Isolated patch complete:', sorted(changed))
