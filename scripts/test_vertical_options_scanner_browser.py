"""Browser regression for the production Vertical Options all-year scanner.

Runs the real Streamlit renderer and virtual grid against deterministic,
read-only E*TRADE-shaped quotes. No brokerage login or order is made.
"""
from __future__ import annotations

import csv
import base64
import hashlib
import json
import os
from datetime import date, datetime, timedelta
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from urllib.parse import parse_qs, urlsplit
import zlib
from zoneinfo import ZoneInfo
import zipfile
import xml.etree.ElementTree as ET

from selenium import webdriver
from selenium.common.exceptions import (
    ElementClickInterceptedException,
    ElementNotInteractableException,
    StaleElementReferenceException,
)
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.support.ui import WebDriverWait


ROOT = Path(__file__).resolve().parents[1]
SELECT_ALL = Keys.COMMAND if sys.platform == "darwin" else Keys.CONTROL

FIXTURE = r'''
import json
import os
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import streamlit as st
from src.vertical_options_ui import render_vertical_options
import src.vertical_options_ui as vo
# Reproduce a late browser reply without another user click to recover it.
original_preference_component = getattr(vo, "_fixture_original_preference_component", vo._PRICING_PREFERENCE_COMPONENT)
vo._fixture_original_preference_component = original_preference_component
def delayed_preference_component(**kwargs):
    value = original_preference_component(**kwargs)
    if kwargs.get("action") == "read":
        started = st.session_state.setdefault("fixture_preference_started", time.monotonic())
        waiting = time.monotonic() - started < 2
        record("preference-read", waiting=waiting)
        if waiting:
            return None
    return value
vo._PRICING_PREFERENCE_COMPONENT = delayed_preference_component
if os.environ.get("VO_SCANNER_PARTIAL_TEST"):
    import src.vertical_options_ui as vo
    vo._SCAN_BATCH_EXPIRATIONS = 1

LOG = Path(os.environ["VO_SCANNER_TEST_LOG"])
EXPIRIES = [date(date.today().year + offset, 6, 20) for offset in (1, 2, 3)]
TODAY = datetime.now(ZoneInfo("America/New_York")).date()
SHORT_EXPIRIES = [TODAY + timedelta(days=days) for days in (0, 1, 6)]
BOUNDARY_EXPIRY = TODAY + timedelta(days=7)

def record(event, **details):
    with LOG.open("a", encoding="utf-8") as output:
        output.write(json.dumps({"event": event, **details}) + "\n")

class StubETrade:
    is_offline = False

    def get_quote(self, symbol):
        record("quote", symbol=symbol)
        return {"QuoteResponse": {"QuoteData": [{
            "symbol": symbol, "companyName": "SPDR Test Fund",
            "lastTrade": 92.0 if symbol == "QQQ" else 100.0, "bid": 99.99, "ask": 100.01,
        }]}}

    def get_option_expirations(self, symbol):
        record("expirations", symbol=symbol)
        return {"OptionExpireDateResponse": {"ExpirationDate": [
            {"year": day.year, "month": day.month, "day": day.day}
            for day in SHORT_EXPIRIES + ([BOUNDARY_EXPIRY] if symbol == "BOUNDARY" else
                                        [EXPIRIES[0]] if symbol == "MASSIVE" else EXPIRIES)
        ]}}

    def get_option_chain(self, symbol, year, month, day,
                         no_of_strikes=100, chain_type="CALLPUT"):
        record("chain", symbol=symbol, year=year, month=month, day=day,
               no_of_strikes=no_of_strikes, chain_type=chain_type)
        if os.environ.get("VO_SCANNER_PARTIAL_TEST") and symbol == "QQQ":
            time.sleep(.8)  # Keep automatic in-progress browser state observable.
        if symbol == "SPY" and Path(os.environ["VO_SCANNER_SLOW_RESCAN_FILE"]).exists():
            time.sleep(2)  # Expose a new scan while an older completed grid exists.
        expiry_code = f"{year % 100:02d}{month:02d}{day:02d}"
        pairs = []
        strikes = range(50, 151) if symbol == "MASSIVE" else (90, 95, 100, 105, 110)
        for strike in strikes:
            call_last = ((151 if symbol == "MASSIVE" else 115) - strike) / 5 + 1
            put_last = (strike - (49 if symbol == "MASSIVE" else 85)) / 5 + 1
            def option(side, last):
                return {
                    "strikePrice": strike, "lastPrice": last,
                    # Asymmetric quotes: natural ASK differs from Last and MID.
                    "bid": last - (.01 if symbol == "MASSIVE" else .02),
                    "ask": last + .01 if symbol == "MASSIVE" else last + .02 + .002 * (strike - 90),
                    # Broker-documented dash-padded root and 8-digit strike.
                    "osiKey": f"{symbol:-<6}{expiry_code}{side}{strike * 1000:08d}",
                }
            pairs.append({"Call": option("C", call_last),
                          "Put": option("P", put_last)})
        return {"OptionChainResponse": {"OptionPair": pairs}}

    def list_accounts(self):
        record("accounts")
        return [{"accountIdKey": "stub-key", "accountId": "12345474",
                 "accountDesc": "Test Account"}]

    def get_balance(self, account_key):
        record("balance", account_key=account_key)
        return {"BalanceResponse": {"Computed": {
            "cashAvailableForInvestment": 12345.67, "cashBalance": 2345.67,
            "cashBuyingPower": 999999.0, "marginBuyingPower": 999999.0,
        }}}

    def preview_order(self, account_key, payload):
        record("preview", account_key=account_key, payload=payload)
        return {"PreviewOrderResponse": {
            "PreviewIds": [{"previewId": 123456}],
            "totalOrderValue": 100.0, "estimatedCommission": 0.0,
        }}

    def place_order(self, *args, **kwargs):
        record("place")
        raise AssertionError("Checkbox selection must never place an order")

record("fixture-render", pid=os.getpid())
st.set_page_config(page_title="Vertical scanner browser regression", layout="wide")
# Match the real shared production CSS without executing broker/auth code.
import ast
from src.theme import _TYPING_CARET_CSS
from src.layout_guardrails import _LAYOUT_GUARDRAIL_CSS
core = ast.parse((Path(os.environ["PYTHONPATH"].split(os.pathsep)[0]) / "src/terminal_core.py").read_text())
for node in core.body:
    if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call) and node.value.args:
        css = node.value.args[0]
        if isinstance(css, ast.Constant) and isinstance(css.value, str) and "<style>" in css.value:
            st.html(css.value)
st.html(_TYPING_CARET_CSS)
st.html(_LAYOUT_GUARDRAIL_CSS)
# Simulate the exact stale manually entered debit from a pre-fix session.
st.session_state.setdefault("vo_limit_price", 4.35)
from src.gex_cboe import cboe_results
if not os.environ.get("VO_SCANNER_SKIP_GEX_SEED"):
    for ticker in ("SPY", "QQQ"):
        cboe_results("scanner-fixture").setdefault(ticker, {
            "callWall": {"strike": 110}, "putWall": {"strike": 90},
            "snapshotFetchedAt": "2026-10-21T12:00:00-04:00",
        })
    from src.gex_ui import _results
    _results("scanner-fixture").setdefault("ETRADE_TEST", {
        "callWall": {"strike": 120}, "updated": "2026-10-21T12:00:00-04:00",
    })
if st.session_state.get("fixture_away"):
    if st.button("RETURN TO SCANNER"):
        st.session_state["fixture_away"] = False
        st.rerun()
else:
    if st.button("LEAVE SCANNER"):
        st.session_state["fixture_away"] = True
        st.rerun()
    render_vertical_options(StubETrade(), lambda: None, vault_key="scanner-fixture")
# Same keyed native control and shared helper used by production Risk Sizing.
from src.theme import install_native_switch_theme
install_native_switch_theme("risk_capital_source_tactical")
st.toggle("Capital Source", key="risk_capital_source_tactical", label_visibility="collapsed")
'''


def free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def wait_for_server(server: subprocess.Popen, base: str, log_path: Path) -> None:
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        if server.poll() is not None:
            raise AssertionError(
                f"Streamlit fixture exited ({server.returncode}): "
                + log_path.read_text(encoding="utf-8")[-3000:]
            )
        try:
            with urllib.request.urlopen(base + "/_stcore/health", timeout=2) as response:
                if response.status == 200:
                    return
        except Exception:
            time.sleep(.4)
    raise AssertionError("Vertical scanner fixture never became healthy")


def events(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def in_grid(driver: webdriver.Chrome, callback):
    """Find and enter the real Streamlit component iframe after each rerun."""
    def enter(_):
        driver.switch_to.default_content()
        for frame in driver.find_elements(By.TAG_NAME, "iframe"):
            try:
                driver.switch_to.frame(frame)
                if driver.find_elements(By.ID, "scroll"):
                    return True
            except Exception:
                pass
            driver.switch_to.default_content()
        return False

    deadline = time.monotonic() + 25
    try:
        while True:
            WebDriverWait(driver, max(1, deadline - time.monotonic())).until(enter)
            try:
                return callback()
            except StaleElementReferenceException:
                # A Streamlit fragment rerun may replace the iframe between
                # lookup and click; re-enter the current component once mounted.
                if time.monotonic() >= deadline:
                    raise
                time.sleep(.1)
    finally:
        driver.switch_to.default_content()


def grid_is_mounted(driver: webdriver.Chrome) -> bool:
    """Check for a result grid without waiting for one to appear."""
    try:
        driver.switch_to.default_content()
        for frame in driver.find_elements(By.TAG_NAME, "iframe"):
            try:
                driver.switch_to.frame(frame)
                if driver.find_elements(By.ID, "scroll"):
                    return True
            except StaleElementReferenceException:
                pass
            finally:
                driver.switch_to.default_content()
        return False
    finally:
        driver.switch_to.default_content()


def grid_is_visible(driver: webdriver.Chrome) -> bool:
    """Detect a displayed result grid, including a stale grid during a rescan."""
    try:
        driver.switch_to.default_content()
        for frame in driver.find_elements(By.TAG_NAME, "iframe"):
            try:
                if not frame.is_displayed():
                    continue
                driver.switch_to.frame(frame)
                if driver.find_elements(By.ID, "scroll"):
                    return True
            except StaleElementReferenceException:
                pass
            finally:
                driver.switch_to.default_content()
        return False
    finally:
        driver.switch_to.default_content()


def grid_snapshot(driver: webdriver.Chrome) -> dict:
    return in_grid(driver, lambda: driver.execute_script(
        """
        const scroll=document.getElementById('scroll');
        const rows=[...document.querySelectorAll('.row')];
        return {
          rows:rows.map(row=>({
            index:Number(row.dataset.index),
            annual:row.classList.contains('annual'),
            values:[...row.querySelectorAll('.cell')].map(cell=>
                cell.classList.contains('expiry') ? cell.firstChild.textContent : cell.textContent),
            expiryDetail:row.querySelector('.expiry-detail')?.textContent,
            expiryFits:(()=>{
                const cell=row.querySelector('.expiry'), detail=cell?.querySelector('small');
                if(!detail)return false;
                const c=cell.getBoundingClientRect(),d=detail.getBoundingClientRect();
                return getComputedStyle(detail).fontSize==='8px' &&
                    d.top>=cell.firstChild.getBoundingClientRect().bottom &&
                    d.bottom<=c.bottom && detail.scrollWidth<=cell.clientWidth &&
                    row.getBoundingClientRect().height===43;
            })(),
            selected:row.querySelector('.select').getAttribute('aria-pressed'),
            background:getComputedStyle(row.querySelector('.cell')).backgroundColor,
            foreground:getComputedStyle(row.querySelector('.cell')).color,
            emphasis:[7,8,10].map(i=>({
              weight:getComputedStyle(row.querySelectorAll('.cell')[i]).fontWeight,
              decoration:getComputedStyle(row.querySelectorAll('.cell')[i]).textDecorationLine,
            })),
            href:row.querySelector('.open[href]')?.href||'',
            target:row.querySelector('.open[href]')?.target||'',
            tvHref:[...row.querySelectorAll('.open[href]')].find(a=>a.textContent==='TV')?.href||'',
            tvTarget:[...row.querySelectorAll('.open[href]')].find(a=>a.textContent==='TV')?.target||'',
          })),
          height:scroll.offsetHeight,
          scrollHeight:scroll.scrollHeight,
          rowFontSize:rows[0] ? getComputedStyle(rows[0]).fontSize : null,
          horizontalOverflow:scroll.scrollWidth>scroll.clientWidth||
            document.documentElement.scrollWidth>document.documentElement.clientWidth,
          header:document.getElementById('head').textContent,
        };
        """
    ))


def grid_page_rows(driver: webdriver.Chrome) -> dict:
    """Read every row of a small fixture through the single virtual scroller."""
    return in_grid(driver, lambda: driver.execute_script(
        """
        const scroll=document.getElementById('scroll');
        const space=document.getElementById('space');
        const seen=new Map();
        const capture=()=>{
          for(const row of document.querySelectorAll('.row')){
            seen.set(Number(row.dataset.index),{
              index:Number(row.dataset.index),
              annual:row.classList.contains('annual'),
              values:[...row.querySelectorAll('.cell')].map(cell=>
                cell.classList.contains('expiry') ? cell.firstChild.textContent : cell.textContent),
              background:getComputedStyle(row.querySelector('.cell')).backgroundColor,
              foreground:getComputedStyle(row.querySelector('.cell')).color,
              selectBackground:getComputedStyle(row.querySelector('.select')).backgroundColor,
              selectForeground:getComputedStyle(row.querySelector('.select')).color,
              annualBorder:getComputedStyle(row.querySelector('.select')).boxShadow,
              linkColors:[...row.querySelectorAll('.open')].map(link=>getComputedStyle(link).color),
              lastColor:getComputedStyle(row.querySelector('.cell:last-child')).color
            });
          }
        };
        const step=Math.max(43,Math.floor(scroll.clientHeight/2));
        for(let offset=0;offset<=scroll.scrollHeight;offset+=step){
          scroll.scrollTop=offset;
          scroll.dispatchEvent(new Event('scroll'));
          capture();
        }
        scroll.scrollTop=scroll.scrollHeight;
        scroll.dispatchEvent(new Event('scroll'));
        capture();
        scroll.scrollTop=0;
        scroll.dispatchEvent(new Event('scroll'));
        return {total:Math.round(Number.parseInt(space.style.height||'0',10)/43),
                rows:[...seen.values()].sort((a,b)=>a.index-b.index)};
        """
    ))


def assert_year_grouping(page: dict, *, per_year: int | None = None) -> None:
    """Every match is present, grouped by year, with one top marker per year."""
    rows = page["rows"]
    assert len(rows) == page["total"], page
    years = [datetime.strptime(row["values"][2], "%b-%d-%y").year for row in rows]
    assert years == sorted(years), years
    first_in_year = [index for index, year in enumerate(years)
                     if index == 0 or year != years[index - 1]]
    assert [index for index, row in enumerate(rows) if row["annual"]] == first_in_year, page
    if per_year is not None:
        assert all(years.count(year) == per_year for year in set(years)), years


def assert_row_colors(page: dict) -> None:
    """CALL/PUT fills and black text apply to ordinary and annual rows alike."""
    assert len(page["rows"]) == page["total"], page
    assert {row["values"][1] for row in page["rows"]} == {"CALL", "PUT"}, page
    assert any(not row["annual"] for row in page["rows"]), page
    for row in page["rows"]:
        expected = "rgb(163, 230, 53)" if row["values"][1] == "CALL" else "rgb(234, 1, 1)"
        assert row["background"] == row["selectBackground"] == expected, row
        assert row["foreground"] == row["selectForeground"] == row["lastColor"] == "rgb(0, 0, 0)", row
        assert row["linkColors"] and all(color == "rgb(0, 0, 0)" for color in row["linkColors"]), row
        assert ("rgb(255, 255, 255)" in row["annualBorder"]) == row["annual"], row


def grouped_page_if_ready(driver: webdriver.Chrome, total: int,
                          *, per_year: int | None = None) -> dict | bool:
    """Wait through a radio rerun that may briefly retain the old grid rows."""
    page = grid_page_rows(driver)
    if page["total"] != total:
        return False
    try:
        assert_year_grouping(page, per_year=per_year)
    except AssertionError:
        return False
    return page


def checked_count(driver: webdriver.Chrome) -> int:
    return driver.execute_script(
        """
        for(const caption of document.querySelectorAll('[data-testid="stCaptionContainer"]')){
          const match=caption.textContent.match(/(\\d+) CHECKED(?:\\s|$)/);
          if(match)return Number(match[1]);
        }
        return -1;
        """
    )


def select_results_view(driver: webdriver.Chrome, label: str) -> None:
    """Keep the native radio clear of Streamlit's sticky toolbar after review."""
    def choose(_):
        try:
            option = driver.find_element(By.XPATH, f'//label[contains(., "{label}")]')
            driver.execute_script("arguments[0].scrollIntoView({block:'center'})", option)
            option.click()
            return True
        except (ElementClickInterceptedException, StaleElementReferenceException):
            return False
    WebDriverWait(driver, 15).until(choose)


def pricing_preference_in_browser(driver) -> str | None:
    """Read the same isolated iframe localStorage that backs the flip switch."""
    frames = driver.find_elements(By.TAG_NAME, "iframe")
    for frame in frames:
        try:
            driver.switch_to.frame(frame)
            saved = driver.execute_script(
                "return window.localStorage.getItem('raj-terminal-vo-pricing-mode-v1')"
            )
            if saved in ("NORMAL", "SMART FILL"):
                return saved
        except Exception:
            pass
        finally:
            driver.switch_to.default_content()
    return None


def exact_optionstrat_url(values: list[str]) -> str:
    symbol, side, expiration, buy, sell = values[:5]
    code = datetime.strptime(expiration, "%b-%d-%y").strftime("%y%m%d")
    option_type = "C" if side == "CALL" else "P"
    long_leg = f".{symbol}{code}{option_type}{float(buy):g}"
    short_leg = f"-.{symbol}{code}{option_type}{float(sell):g}"
    strategy = "bull-call-spread" if side == "CALL" else "bear-put-spread"
    legs = f"{long_leg},{short_leg}" if side == "CALL" else f"{short_leg},{long_leg}"
    return f"https://optionstrat.com/build/{strategy}/{symbol}/{legs}"


def assert_exact_tradingview_formula(row: dict) -> None:
    symbol, side, expiration, buy, sell = row["values"][:5]
    code = datetime.strptime(expiration, "%b-%d-%y").strftime("%y%m%d")
    suffix = "C" if side == "CALL" else "P"
    def strike_text(value):
        text = f"{float(value):g}"
        return text if "." in text else text + ".0"
    expected = f"OPRA:{symbol}{code}{suffix}{strike_text(buy)}-OPRA:{symbol}{code}{suffix}{strike_text(sell)}"
    parsed = urlsplit(row["tvHref"])
    assert (parsed.scheme, parsed.netloc, parsed.path) == ("https", "www.tradingview.com", "/chart/"), row
    assert parse_qs(parsed.query)["symbol"] == [expected], row
    assert row["tvTarget"] == "_blank", row


def download_file(driver, wait, folder: Path, key: str, filename: str) -> Path:
    """Wait for a settled export button and its actual browser download."""
    settled = {"button": None, "since": 0.0}

    def ready(_):
        app = driver.find_element(By.CSS_SELECTOR, '[data-testid="stApp"]')
        if (app.get_attribute("data-test-script-state") != "notRunning"
                or driver.find_elements(By.CSS_SELECTOR, '[data-stale="true"]')):
            settled.update(button=None, since=0.0)
            return False
        for button in driver.find_elements(By.CSS_SELECTOR, f".st-key-{key} button"):
            try:
                if button.is_displayed() and button.is_enabled():
                    if settled["button"] != button.id:
                        settled.update(button=button.id, since=time.monotonic())
                    elif time.monotonic() - settled["since"] >= 0.5:
                        return button
            except StaleElementReferenceException:
                settled.update(button=None, since=0.0)
        return False

    target = folder / filename
    target.unlink(missing_ok=True)
    wait.until(ready).click()
    wait.until(lambda _: target.exists() and not list(folder.glob("*.crdownload")))
    return target


def download_csv(driver, wait, folder: Path, key: str, filename: str) -> list[dict]:
    """Read every row from an actual CSV download."""
    target = download_file(driver, wait, folder, key, filename)
    with target.open(encoding="utf-8", newline="") as downloaded:
        rows = list(csv.DictReader(downloaded))
    today = datetime.now(ZoneInfo("America/New_York")).date()
    assert all((date.fromisoformat(row["Expiration"]) - today).days >= 7 for row in rows), rows[:2]
    assert all(row["Highlight"] == ("GREEN" if row["Type"] == "CALL" else "RED")
               for row in rows), rows[:2]
    return rows


def download_xlsx(driver, wait, folder: Path, key: str, filename: str,
                  expected_sides=("CALL", "PUT")) -> dict:
    """Verify the browser downloaded a readable, styled Excel workbook."""
    target = download_file(driver, wait, folder, key, filename)
    with zipfile.ZipFile(target) as workbook:
        assert workbook.testzip() is None, filename
        raw_styles = workbook.read("xl/styles.xml")
        styles = raw_styles.decode("utf-8").upper()
        sheet = ET.fromstring(workbook.read("xl/worksheets/sheet1.xml"))
        shared = (ET.fromstring(workbook.read("xl/sharedStrings.xml"))
                  if "xl/sharedStrings.xml" in workbook.namelist() else [])
    namespace = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    rows = sheet.findall(".//x:sheetData/x:row", namespace)
    assert "000000" in styles, filename
    if "CALL" in expected_sides:
        assert "A3E635" in styles, filename
    if "PUT" in expected_sides:
        assert "EA0101" in styles, filename
    style_xml = ET.fromstring(raw_styles)
    fills = [fill.find("x:patternFill/x:fgColor", namespace)
             for fill in style_xml.find("x:fills", namespace)]
    style_fills = [int(style.get("fillId", "0"))
                   for style in style_xml.find("x:cellXfs", namespace)]
    strings = ["".join(item.itertext()) for item in shared]
    for row in rows[1:]:
        cells = row.findall("x:c", namespace)
        def value(cell):
            number = cell.find("x:v", namespace)
            text = number.text if number is not None else "".join(
                cell.find("x:is", namespace).itertext()
            ) if cell.find("x:is", namespace) is not None else ""
            return strings[int(text)] if cell.get("t") == "s" else text
        side = value(cells[1])
        assert side in expected_sides, side
        expected = "A3E635" if side == "CALL" else "EA0101"
        assert all(fills[style_fills[int(cell.get("s", "0"))]].get("rgb", "")[-6:].upper() == expected
                   for cell in cells), (side, ET.tostring(row))
    return {"rows": len(rows) - 1, "styles": styles}


def assert_no_result_overlap(driver) -> None:
    """Check actual visible feature rows under the production shared stylesheet."""
    geometry = driver.execute_script("""
        const root=document.querySelector('.st-key-vertical_options_workspace');
        const box=element=>{
            const r=element.getBoundingClientRect();
            return {top:r.top,bottom:r.bottom,left:r.left,right:r.right,
                label:(element.innerText||element.title||element.src||'').slice(0,100)};
        };
        const elements=[...root.querySelectorAll('.vo-grid,.vo-section,.vo-summary,'+
            '[data-testid="stCaptionContainer"],.vo-help,[class*="st-key-vo_results_view_"],'+
            '[class*="st-key-vo_year_select_"],'+
            '.st-key-vo_export_qualified_csv,.st-key-vo_export_qualified_excel,iframe')]
            .filter(element=>{
                const r=element.getBoundingClientRect();
                return r.width>0&&r.height>2&&getComputedStyle(element).visibility!=='hidden';
            });
        const overlap=[];
        for(let i=0;i<elements.length;i++)for(let j=i+1;j<elements.length;j++){
            if(elements[i].contains(elements[j])||elements[j].contains(elements[i]))continue;
            const a=box(elements[i]),b=box(elements[j]);
            if(Math.min(a.right,b.right)-Math.max(a.left,b.left)>2&&
                Math.min(a.bottom,b.bottom)-Math.max(a.top,b.top)>2)overlap.push([a,b]);
        }
        return overlap;
    """)
    assert not geometry, geometry
    assert not driver.find_elements(By.CSS_SELECTOR, 'input[aria-label^="PAGE /"]')
    assert not driver.find_elements(By.XPATH, '//button[normalize-space(.)="« FIRST" or '
                                    'normalize-space(.)="← PREVIOUS" or '
                                    'normalize-space(.)="NEXT →" or normalize-space(.)="LAST »"]')


def assert_liquid_cash_display(driver) -> None:
    geometry = driver.execute_script("""
        const cash=document.querySelector('.vo-liquid-cash');
        const smart=document.querySelector('.vo-mode-help--smart').closest('.vo-mode-guide');
        const box=element=>{const r=element.getBoundingClientRect();
            return {top:r.top,bottom:r.bottom,left:r.left,right:r.right};};
        return {cash:box(cash),smart:box(smart),text:cash.innerText,
            inputs:cash.querySelectorAll('input,select,textarea,button').length,
            cashFields:[...document.querySelectorAll('input')].filter(input=>
                /cash|liquid/i.test(input.getAttribute('aria-label')||'')).length,
            labelSize:getComputedStyle(cash.querySelector('.vo-label')).fontSize,
            valueSize:getComputedStyle(cash.querySelector('.vo-value')).fontSize};
    """)
    assert "LIQUID CASH" in geometry["text"] and "x5474" in geometry["text"], geometry
    assert "$12,345.67" in geometry["text"] and "999,999" not in geometry["text"], geometry
    assert geometry["cash"]["left"] >= geometry["smart"]["right"], geometry
    assert min(geometry["cash"]["bottom"], geometry["smart"]["bottom"]) > max(
        geometry["cash"]["top"], geometry["smart"]["top"]
    ), geometry
    assert geometry["cash"]["right"] <= driver.execute_script("return window.innerWidth"), geometry
    assert geometry["inputs"] == geometry["cashFields"] == 0, geometry
    assert geometry["labelSize"] == "9.92px" and geometry["valueSize"] == "13.76px", geometry


def grid_menu_action(driver, key: str, action: str) -> None:
    def enter():
        panel = driver.find_element(By.ID, "filter-panel")
        button = driver.find_element(By.CSS_SELECTOR, f'.filter-button[data-key="{key}"]')
        if (not panel.is_displayed() or panel.find_element(By.CSS_SELECTOR, ".filter-title").text !=
                "FILTER " + button.find_element(By.CSS_SELECTOR, ".filter-name").text):
            button.click()
        panel.find_element(By.XPATH, f'.//button[normalize-space(.)="{action}"]').click()
    in_grid(driver, enter)


def set_grid_range(driver, key: str, label: str, low: float, high: float) -> None:
    def enter():
        button = driver.find_element(By.CSS_SELECTOR, f'.filter-button[data-key="{key}"]')
        panel = driver.find_element(By.ID, "filter-panel")
        if not panel.is_displayed() or not panel.find_elements(By.CSS_SELECTOR, f'input[aria-label="{label} min"]'):
            button.click()
        driver.execute_script("""
            const panel=document.getElementById('filter-panel');
            for(const [bound,value] of [['min',arguments[1]],['max',arguments[2]]]){
                const field=panel.querySelector('input[aria-label="'+arguments[0]+' '+bound+'"]');
                field.value=String(value);field.dispatchEvent(new Event('input',{bubbles:true}));
            }
        """, label, low, high)
    in_grid(driver, enter)


def set_exact_grid_filter(driver: webdriver.Chrome, key: str, label: str, value: float) -> None:
    """Use the real header popover to set an inclusive exact-value range."""
    def enter() -> None:
        driver.find_element(By.CSS_SELECTOR, f'.filter-button[data-key="{key}"]').click()
        changed = driver.execute_script(
            """
            const panel=document.getElementById('filter-panel');
            if(panel.hidden)return false;
            for(const bound of ['min','max']){
              const field=panel.querySelector('input[aria-label="'+arguments[0]+' '+bound+'"]');
              if(!field)return false;
              field.value=String(arguments[1]);
              field.dispatchEvent(new Event('input',{bubbles:true}));
            }
            return true;
            """,
            label, value,
        )
        assert changed, (key, label)
    in_grid(driver, enter)


def toggle_put_column_filter(driver: webdriver.Chrome) -> None:
    """Click the real TYPE menu checkbox, exercising component-to-app reruns."""
    def enter() -> None:
        panel = driver.find_element(By.ID, "filter-panel")
        if not panel.is_displayed():
            driver.find_element(By.CSS_SELECTOR, '.filter-button[data-key="side"]').click()
        choices = panel.find_elements(By.CSS_SELECTOR, ".filter-choice")
        assert {item.find_element(By.TAG_NAME, "span").text for item in choices} == {"CALL", "PUT"}
        put = next(item for item in choices if item.find_element(By.TAG_NAME, "span").text == "PUT")
        put.find_element(By.CSS_SELECTOR, 'input[type="checkbox"]').click()
    in_grid(driver, enter)


def test() -> None:
    with tempfile.TemporaryDirectory(prefix="vo_scanner_browser_") as temporary:
        folder = Path(temporary)
        app_path = folder / "app.py"
        app_path.write_text(FIXTURE, encoding="utf-8")
        event_path = folder / "broker_events.jsonl"
        event_path.write_text("", encoding="utf-8")
        server_log = folder / "streamlit.log"
        port = free_port()
        base = f"http://127.0.0.1:{port}"
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(ROOT) + os.pathsep + environment.get("PYTHONPATH", "")
        environment["VO_SCANNER_TEST_LOG"] = str(event_path)
        # Keep the timing marker outside Streamlit's watched app directory.
        slow_rescan_file = folder.parent / f"{folder.name}_slow_rescan"
        environment["VO_SCANNER_SLOW_RESCAN_FILE"] = str(slow_rescan_file)
        with server_log.open("w", encoding="utf-8") as output:
            server = subprocess.Popen(
                [sys.executable, "-m", "streamlit", "run", str(app_path),
                 "--server.port", str(port), "--server.headless", "true",
                 "--browser.gatherUsageStats", "false"],
                cwd=ROOT, env=environment, stdout=output, stderr=subprocess.STDOUT,
            )
        driver = None
        try:
            wait_for_server(server, base, server_log)
            downloads = folder / "downloads"
            downloads.mkdir()
            options = Options()
            options.add_experimental_option("prefs", {
                "download.default_directory": str(downloads),
                "download.prompt_for_download": False,
            })
            options.add_argument("--headless=new")
            options.add_argument("--no-sandbox")
            options.add_argument("--disable-dev-shm-usage")
            options.add_argument("--force-device-scale-factor=1")
            options.add_argument("--window-size=1440,900")
            driver = webdriver.Chrome(options=options)
            driver.get(base)
            wait = WebDriverWait(driver, 60)

            expected_labels = ["Target Breakeven", "Max Cost", "MIN RISK:REWARD 1:"]
            def ready_scanner_form(_):
                # Streamlit can mount stForm before its inputs hydrate on CI.
                apps = driver.find_elements(By.CSS_SELECTOR, '[data-testid="stApp"]')
                if (not apps or apps[0].get_attribute("data-test-script-state") != "notRunning"
                        or driver.find_elements(By.CSS_SELECTOR, '[data-stale="true"]')):
                    return False
                forms = driver.find_elements(By.CSS_SELECTOR, '[data-testid="stForm"]')
                if not forms:
                    return False
                labels = driver.execute_script(
                    "return [...arguments[0].querySelectorAll('input')]"
                    ".filter(input=>!input.disabled&&input.type==='text')"
                    ".map(input=>input.getAttribute('aria-label'))", forms[0],
                )
                return forms[0] if labels == expected_labels else False
            form = wait.until(ready_scanner_form)
            assert form.find_element(By.CSS_SELECTOR, 'input[aria-label="Target Breakeven"]').get_attribute("value") == ""
            assert form.find_element(By.CSS_SELECTOR, 'input[aria-label="Max Cost"]').get_attribute("value") == ""
            assert form.find_element(By.CSS_SELECTOR, 'input[aria-label="Target Breakeven"]').get_attribute("placeholder") == "Not provided"
            assert form.find_element(By.CSS_SELECTOR, 'input[aria-label="Max Cost"]').get_attribute("placeholder") == "Not provided"
            optional_field_styles = driver.execute_script("""
                return ['Target Breakeven','Max Cost'].map(label=>{
                    const input=document.querySelector('input[aria-label="'+label+'"]');
                    const text=getComputedStyle(input),placeholder=getComputedStyle(input,'::placeholder');
                    return {label,background:text.backgroundColor,fontSize:text.fontSize,
                        placeholderWeight:placeholder.fontWeight,placeholderColor:placeholder.color,
                        placeholderOpacity:placeholder.opacity};
                });
            """)
            assert all(item["background"] == "rgb(68, 68, 68)" and item["fontSize"] == "14px"
                       and item["placeholderWeight"] == "400"
                       and item["placeholderColor"] == "rgb(251, 139, 30)"
                       and item["placeholderOpacity"] == "1"
                       for item in optional_field_styles), optional_field_styles
            assert form.find_element(By.CSS_SELECTOR, 'input[aria-label="MIN RISK:REWARD 1:"]').get_attribute("value") == "2"
            initial_reads = [event for event in events(event_path) if event["event"] == "preference-read"]
            assert any(event["waiting"] for event in initial_reads), initial_reads
            assert any(not event["waiting"] for event in initial_reads), initial_reads
            assert not [event for event in events(event_path) if event["event"] == "chain"]
            print("Vertical Options first visit recovers late browser preference without tab switching PASS")
            # Pricing controls stay on one compact row. Hidden preference frames
            # must never reserve the custom-component default 150px height.
            pricing_layout = driver.execute_script("""
                const guide = document.querySelector(".vo-mode-guide");
                const form = document.querySelector('[data-testid="stForm"]');
                const label = document.querySelector(".st-key-vo_smart_fill label");
                if (!guide || !form || !label) return null;
                const track = label.querySelector(":scope > div:first-of-type");
                const paste = document.querySelector(".st-key-vo_list_toggle button").getBoundingClientRect();
                const guideBox = guide.getBoundingClientRect();
                return {
                    sameToolbarRow: paste.top <= guideBox.bottom && paste.bottom >= guideBox.top,
                    toolbarToForm: form.getBoundingClientRect().top - paste.bottom,
                    gap: form.getBoundingClientRect().top - guide.getBoundingClientRect().bottom,
                    toggleWidth: label.getBoundingClientRect().width,
                    normalX: guideBox.left,
                    switchX: label.getBoundingClientRect().left,
                    smartX: document.querySelector('.vo-mode-help--smart').getBoundingClientRect().left,
                };
            """)
            assert pricing_layout is not None, "Pricing controls are missing"
            assert pricing_layout["sameToolbarRow"], pricing_layout
            assert 0 <= pricing_layout["toolbarToForm"] < 32, pricing_layout
            assert 0 <= pricing_layout["gap"] < 64, pricing_layout
            assert pricing_layout["toggleWidth"] >= 24, pricing_layout
            assert pricing_layout["normalX"] < pricing_layout["switchX"] < pricing_layout["smartX"], pricing_layout
            print("Vertical Options compact scan toolbar PASS", pricing_layout)
            # Compare visible box edges and labels with the shared production
            # theme at both desktop widths, including nested form boundaries.
            for viewport_width in (1440, 1100):
                driver.set_window_size(viewport_width, 900)
                geometry = driver.execute_script("""
                    const rect = el => { const r=el.getBoundingClientRect();
                        return {top:r.top,bottom:r.bottom,height:r.height,left:r.left,right:r.right}; };
                    const inputs = ['Ticker','Target Breakeven','Max Cost','MIN RISK:REWARD 1:'].map(name =>
                        document.querySelector('input[aria-label="'+name+'"]'));
                    const labels = inputs.map(input => input.closest('[data-testid="stTextInput"]')
                        .querySelector('[data-testid="stWidgetLabel"]'));
                    return {
                        inputs:inputs.map(input => rect(input.parentElement)),
                        inputDOM:inputs[0].parentElement.outerHTML.slice(0,1600),
                        company:rect(document.querySelector('.vo-company-box')),
                        labels:labels.map(rect),
                        labelFits:labels.map(label=>label.scrollWidth<=label.clientWidth+1),
                        companyLabel:rect(document.querySelector('.vo-company-label')),
                        scan:rect(document.querySelector('[data-testid="stFormSubmitButton"] button')),
                        formPadding:getComputedStyle(document.querySelector('[data-testid="stForm"]')).padding,
                        overflow:document.documentElement.scrollWidth > window.innerWidth,
                    };
                """)
                print("Vertical Options control geometry", viewport_width, geometry, flush=True)
                boxes = geometry["inputs"] + [geometry["company"], geometry["scan"]]
                labels = geometry["labels"] + [geometry["companyLabel"]]
                assert max(b["top"] for b in boxes)-min(b["top"] for b in boxes) <= 2, geometry
                assert max(b["bottom"] for b in boxes)-min(b["bottom"] for b in boxes) <= 2, geometry
                assert max(b["top"] for b in labels)-min(b["top"] for b in labels) <= 2, geometry
                assert all(geometry["labelFits"]), geometry
                assert geometry["formPadding"] == "0px", geometry
                assert not geometry["overflow"], geometry
                assert_liquid_cash_display(driver)
            driver.set_window_size(1440, 900)
            print("Vertical Options aligned scan controls PASS (1440 + 1100)", flush=True)
            assert [event for event in events(event_path) if event["event"] == "balance"]
            print("Read-only selected-account liquid cash beside SMART FILL PASS (1440 + 1100)", flush=True)
            def assert_switch_visibility(checked, key="vo_smart_fill"):
                # Click a static note to remove keyboard focus, then move the
                # pointer away. Hover/focus must not be required for contrast.
                def clear_focus(_):
                    try:
                        note = driver.find_element(By.CSS_SELECTOR, ".vo-note")
                        note.click()
                        ActionChains(driver).move_to_element(note).perform()
                        return True
                    except (StaleElementReferenceException, ElementClickInterceptedException):
                        return False
                wait.until(clear_focus)
                def settled(_):
                    info = driver.execute_script("""
                        const root=document.querySelector('.st-key-'+arguments[0]);
                        if(!root)return null;
                        const input=root.querySelector('input[role="switch"]');
                        if(!input)return null;
                        const label=input.closest('label');
                        const track=label.querySelector(':scope > div:first-of-type');
                        const thumb=track.firstElementChild;
                        const t=getComputedStyle(track), h=getComputedStyle(thumb);
                        const box=track.getBoundingClientRect(), dot=thumb.getBoundingClientRect();
                        return {checked:input.checked,hover:label.matches(':hover'),
                            focus:input.matches(':focus-visible'),track:t.backgroundColor,
                            thumb:h.backgroundColor,outline:t.outlineColor,outlineStyle:t.outlineStyle,
                            trackMid:box.left+box.width/2,thumbMid:dot.left+dot.width/2};
                    """, key)
                    return info if info and info["checked"] == checked and ((info["thumbMid"] > info["trackMid"]) == checked) else False
                appearance = wait.until(settled)
                assert not appearance["hover"] and not appearance["focus"], appearance
                assert appearance["outline"] == "rgb(251, 139, 30)", appearance
                assert appearance["outlineStyle"] == "solid", appearance
                assert appearance["track"] == ("rgb(251, 139, 30)" if checked else "rgb(0, 0, 0)"), appearance
                assert appearance["thumb"] == ("rgb(0, 0, 0)" if checked else "rgb(251, 139, 30)"), appearance
                # An actual hover followed by leaving must keep the same colors.
                def hover_and_leave(_):
                    try:
                        target = driver.find_element(By.CSS_SELECTOR, f".st-key-{key} label")
                        stable_note = driver.find_element(By.CSS_SELECTOR, ".vo-note")
                        ActionChains(driver).move_to_element(target).move_to_element(stable_note).perform()
                        return True
                    except StaleElementReferenceException:
                        return False
                wait.until(hover_and_leave)
                assert wait.until(settled)["track"] == appearance["track"]
                print("Native switch resting contrast PASS", key, appearance, flush=True)
            assert_switch_visibility(False)
            assert_switch_visibility(False, "risk_capital_source_tactical")
            driver.find_element(By.CSS_SELECTOR, ".st-key-risk_capital_source_tactical label").click()
            assert_switch_visibility(True, "risk_capital_source_tactical")
            # Flip switch saves immediately and must rehydrate after a hard reload.
            flip = wait.until(lambda _: driver.find_element(
                By.CSS_SELECTOR, ".st-key-vo_smart_fill label"
            ))
            flip.click()
            wait.until(lambda _: pricing_preference_in_browser(driver) == "SMART FILL")
            assert_switch_visibility(True)
            driver.refresh()
            form = wait.until(ready_scanner_form)
            wait.until(lambda _: driver.execute_script(
                "const i=document.querySelector('.st-key-vo_smart_fill input');"
                "return i && i.checked;"
            ))
            assert_switch_visibility(True)
            leave_button = driver.find_element(By.XPATH, '//button[contains(., "LEAVE SCANNER")]')
            driver.execute_script("arguments[0].scrollIntoView({block:'center'})", leave_button)
            leave_button.click()
            wait.until(lambda _: driver.find_element(By.XPATH, '//button[contains(., "RETURN TO SCANNER")]')).click()
            wait.until(lambda _: driver.find_element(By.CSS_SELECTOR, '.st-key-vo_smart_fill input').is_selected())
            driver.find_element(By.CSS_SELECTOR, ".st-key-vo_smart_fill label").click()
            wait.until(lambda _: pricing_preference_in_browser(driver) == "NORMAL")
            assert_switch_visibility(False)
            form = wait.until(ready_scanner_form)
            print("Vertical Options flip toggle persistence PASS (refresh + both positions)")
            for label, text in (
                ("Normal mode explanation", "natural ASK first"),
                ("Smart Fill mode explanation", "between spread MID and natural ASK"),
            ):
                help_icon = wait.until(lambda _: driver.find_element(
                    By.CSS_SELECTOR, f'.vo-mode-help[aria-label="{label}"]'
                ))
                tooltip = help_icon.find_element(By.CSS_SELECTOR, ".vo-mode-tooltip")
                assert text in (
                    driver.execute_script("return arguments[0].textContent", tooltip) or ""
                )
                ActionChains(driver).move_to_element(help_icon).perform()
                wait.until(lambda _: driver.execute_script(
                    "return getComputedStyle(arguments[0]).visibility", tooltip
                ) == "visible")
            ActionChains(driver).move_to_element(form).perform()
            ticker = driver.find_element(By.CSS_SELECTOR, 'input[aria-label="Ticker"]')
            ticker.send_keys("SPY", Keys.TAB)
            wait.until(lambda _: "LAST $100.00" in driver.find_element(By.TAG_NAME, "body").text)
            wait.until(lambda _: "CALL WALL $110.00" in driver.find_element(By.TAG_NAME, "body").text)
            assert "PUT WALL $90.00" in driver.find_element(By.TAG_NAME, "body").text
            assert not [item for item in events(event_path) if item["event"] == "chain"]
            form = wait.until(ready_scanner_form)
            target_input = form.find_element(By.CSS_SELECTOR, 'input[aria-label="Target Breakeven"]')
            assert target_input.get_attribute("value") == ""
            target_input.send_keys(SELECT_ALL, "a")
            target_input.send_keys("100")
            cost_input = form.find_element(By.CSS_SELECTOR, 'input[aria-label="Max Cost"]')
            cost_input.send_keys(SELECT_ALL, "a")
            cost_input.send_keys("500")
            assert driver.execute_script("""
                return ['Target Breakeven','Max Cost'].every(label=>{
                    const input=document.querySelector('input[aria-label="'+label+'"]');
                    return input.value!==''&&getComputedStyle(input).fontSize==='14px';
                });
            """)
            form.find_element(By.XPATH, './/button[contains(., "SCAN ALL YEARS")]').click()

            wait.until(lambda _: checked_count(driver) == 0)
            assert "30" in driver.find_element(By.TAG_NAME, "body").text, "Expected 30 parsed option contracts"
            chain_calls = [item for item in events(event_path) if item["event"] == "chain"]
            assert len(chain_calls) == 3, chain_calls
            assert all((date(item["year"], item["month"], item["day"])
                        - datetime.now(ZoneInfo("America/New_York")).date()).days >= 7
                       for item in chain_calls), chain_calls
            assert len({item["year"] for item in chain_calls}) == 3, chain_calls
            assert all(item["no_of_strikes"] is None and item["chain_type"] == "CALLPUT"
                       for item in chain_calls), chain_calls

            # The pricing switch keeps the native local geometry contract.
            switches = driver.execute_script("""
                return ['vo_smart_fill'].map(key=>{
                    const label=document.querySelector('.st-key-'+key+' label');
                    const track=label.querySelector(':scope > div:first-of-type');
                    const rect=track.getBoundingClientRect();
                    return {width:rect.width,height:rect.height,on:getComputedStyle(label,'::after').content};
                });
            """)
            assert all(item['width'] == 36 and item['height'] == 20 for item in switches), switches
            help_button = driver.find_element(By.CSS_SELECTOR, '.vo-help button')
            ActionChains(driver).move_to_element(help_button).perform()
            def help_visible(_):
                return driver.execute_script("""
                    const panel=document.querySelector('.vo-help>div'),box=panel.getBoundingClientRect();
                    return getComputedStyle(panel).display!=='none'&&box.width>0&&box.left>=0&&box.right<=innerWidth;
                """)
            wait.until(help_visible)
            help_button.click()  # Click/focus keeps the same panel accessible.
            wait.until(help_visible)
            driver.find_element(By.CSS_SELECTOR, '.vo-note').click()
            ActionChains(driver).move_to_element(driver.find_element(By.CSS_SELECTOR, '.vo-note')).perform()
            # The default view includes every qualifying spread, grouped and
            # ranked within each year. Only the top row per year is highlighted.
            # Header menus are the sole result filter and sort controls.
            assert driver.find_element(By.XPATH, '//label[contains(., "BEST PER YEAR")]//input').is_selected()
            assert not driver.find_elements(By.CSS_SELECTOR, '.st-key-vo_be_filter_enabled,.st-key-vo_be_range_percent')
            wait.until(lambda _: "60 DISPLAYED // 60 MATCHING OF 60 QUALIFIED PAIRS" in driver.find_element(By.TAG_NAME, "body").text)
            initial_best = wait.until(lambda _: grouped_page_if_ready(driver, 60, per_year=20))
            assert_year_grouping(initial_best, per_year=20)
            assert_row_colors(initial_best)
            years = sorted({datetime.strptime(row["values"][2], "%b-%d-%y").year
                            for row in initial_best["rows"]})
            assert len(years) == 3, years
            year_labels = ["ALL YEARS // 60"] + [f"{year} // 20" for year in years]
            export_labels = ["EXPORT CSV // 60", "EXPORT EXCEL // 60"]
            for width in (1100, 1440):
                driver.set_window_size(width, 900)
                year_layout = driver.execute_script("""
                    const labels=new Set(arguments[0]);
                    const buttons=[...document.querySelectorAll('button')].filter(button=>
                      labels.has(button.textContent.trim().replace(/\\s+/g,' ')));
                    return {overflow:document.documentElement.scrollWidth>innerWidth,
                      buttons:buttons.map(button=>{
                        const text=button.querySelector('p')||button;
                        const box=button.getBoundingClientRect();
                        return {label:button.textContent.trim().replace(/\\s+/g,' '),
                          height:box.height,textHeight:text.getBoundingClientRect().height,
                          left:box.left,right:box.right};
                      })};
                """, year_labels)
                assert len(year_layout["buttons"]) == 4 and not year_layout["overflow"], year_layout
                assert all(button["height"] <= 42 and button["textHeight"] <= 20
                           and 0 <= button["left"] < button["right"] <= width
                           for button in year_layout["buttons"]), year_layout
                export_layout = driver.execute_script("""
                    const labels=new Set(arguments[0]);
                    const buttons=[...document.querySelectorAll('button')].filter(button=>
                      labels.has(button.textContent.trim().replace(/\\s+/g,' '))&&
                      button.getBoundingClientRect().height>0);
                    return {overflow:document.documentElement.scrollWidth>innerWidth,
                      buttons:buttons.map(button=>{
                        const text=button.querySelector('p')||button;
                        const box=button.getBoundingClientRect();
                        return {label:button.textContent.trim().replace(/\\s+/g,' '),
                          height:box.height,textHeight:text.getBoundingClientRect().height,
                          left:box.left,right:box.right};
                      })};
                """, export_labels)
                assert len(export_layout["buttons"]) == 2 and not export_layout["overflow"], export_layout
                assert all(button["height"] <= 42 and button["textHeight"] <= 20
                           and 0 <= button["left"] < button["right"] <= width
                           for button in export_layout["buttons"]), export_layout
                assert_row_colors(grid_page_rows(driver))
                assert_no_result_overlap(driver)
                assert grid_snapshot(driver)["rowFontSize"] == "11.68px"
                card_fonts = driver.execute_script("""
                    return [...document.querySelectorAll('.vo-grid .vo-card')]
                        .filter(card=>!card.closest('.vo-liquid-cash')).map(card=>({
                        label:getComputedStyle(card.querySelector('.vo-label')).fontSize,
                        value:getComputedStyle(card.querySelector('.vo-value')).fontSize
                    }));
                """)
                assert len(card_fonts) == 6 and all(
                    card["label"] == "9.92px" and card["value"] == "13.76px"
                    for card in card_fonts
                ), card_fonts
            driver.set_window_size(1440, 900)
            year_button = driver.find_element(By.XPATH,
                f'//button[contains(normalize-space(.), "{years[1]} // 20")]')
            driver.execute_script("arguments[0].scrollIntoView({block:'center'})", year_button)
            year_button.click()
            one_year = wait.until(lambda _: (page if (page := grid_page_rows(driver))["total"] == 20 else False))
            assert "20 DISPLAYED // 20 MATCHING OF 60 QUALIFIED PAIRS" in driver.find_element(By.TAG_NAME, "body").text
            assert all(datetime.strptime(row["values"][2], "%b-%d-%y").year == years[1]
                       for row in one_year["rows"]), one_year
            assert_year_grouping(one_year, per_year=20)
            one_year_excel = download_xlsx(driver, wait, downloads,
                "vo_export_qualified_excel", "SPY_vertical_qualified.xlsx")
            assert one_year_excel["rows"] == 20, one_year_excel["rows"]
            one_year_csv = download_csv(driver, wait, downloads,
                "vo_export_qualified_csv", "SPY_vertical_qualified.csv")
            assert len(one_year_csv) == 20 and all(
                date.fromisoformat(row["Expiration"]).year == years[1] for row in one_year_csv
            ), one_year_csv[:2]
            assert len([event for event in events(event_path) if event["event"] == "chain"]) == 3
            all_years_button = driver.find_element(By.XPATH,
                '//button[contains(normalize-space(.), "ALL YEARS // 60")]')
            driver.execute_script("arguments[0].scrollIntoView({block:'center'})", all_years_button)
            all_years_button.click()
            all_years_page = wait.until(lambda _: grouped_page_if_ready(driver, 60, per_year=20))
            assert "60 DISPLAYED // 60 MATCHING OF 60 QUALIFIED PAIRS" in driver.find_element(By.TAG_NAME, "body").text
            assert_year_grouping(all_years_page, per_year=20)
            result_spacing = driver.execute_script("""
                const metrics=document.querySelector('.vo-grid').getBoundingClientRect();
                const section=document.querySelector('.vo-section').getBoundingClientRect();
                return {metricsBottom:metrics.bottom,sectionTop:section.top};
            """)
            assert result_spacing["sectionTop"] >= result_spacing["metricsBottom"] + 2, result_spacing
            if os.environ.get("VO_SCANNER_SCREENSHOT"):
                driver.execute_script("window.scrollTo(0,0)")
                driver.save_screenshot(os.environ["VO_SCANNER_SCREENSHOT"])
            select_results_view(driver, "ALL QUALIFIED")
            wait.until(lambda _: driver.find_element(By.XPATH, '//label[contains(., "ALL QUALIFIED")]//input').is_selected())
            set_grid_range(driver, "breakeven", "BREAKEVEN", 95, 105)
            nearby = wait.until(lambda _: (page["rows"] if 3 < (page := grid_page_rows(driver))["total"] < 60 else False))
            be_values = [float(row["values"][8].replace('$', '').replace(',', '')) for row in nearby]
            assert all(95 <= value <= 105 for value in be_values), be_values
            filtered_near = download_csv(driver, wait, downloads, "vo_export_qualified_csv",
                                        "SPY_vertical_qualified.csv")
            assert len(filtered_near) > 3
            assert all(95 <= float(row["Breakeven ($)"]) <= 105 for row in filtered_near)
            assert all(float(row["Risk Reward (to 1)"]) >= 2 for row in filtered_near)
            grid_menu_action(driver, "breakeven", "NEAREST TARGET")
            distances = [abs(float(row["Breakeven ($)"]) - 100) for row in download_csv(
                driver, wait, downloads, "vo_export_qualified_csv", "SPY_vertical_qualified.csv")]
            assert distances == sorted(distances), distances
            set_grid_range(driver, "breakeven", "BREAKEVEN", 97, 103)
            tighter = download_csv(driver, wait, downloads, "vo_export_qualified_csv",
                                   "SPY_vertical_qualified.csv")
            assert 0 < len(tighter) < len(filtered_near)
            assert all(97 <= float(row["Breakeven ($)"]) <= 103 for row in tighter)
            grid_menu_action(driver, "breakeven", "CLEAR ALL")
            wait.until(lambda _: "60 DISPLAYED // 60 MATCHING OF 60 QUALIFIED PAIRS" in driver.find_element(By.TAG_NAME, "body").text)
            grid_menu_action(driver, "basis", "FILL + TARGET")
            assert len([event for event in events(event_path) if event["event"] == "chain"]) == 3
            leave_button = driver.find_element(By.XPATH, '//button[contains(., "LEAVE SCANNER")]')
            driver.execute_script("arguments[0].scrollIntoView({block:'center'})", leave_button)
            leave_button.click()
            wait.until(lambda _: driver.find_element(By.XPATH, '//button[contains(., "RETURN TO SCANNER")]')).click()
            # Existence can observe Streamlit's temporary cached mount before
            # the returned fragment's actual values reach the frontend.
            wait.until(lambda _: driver.find_element(By.XPATH, '//label[contains(., "ALL QUALIFIED")]//input').is_selected())
            assert len([event for event in events(event_path) if event["event"] == "chain"]) == 3
            select_results_view(driver, "BEST PER YEAR")
            wait.until(lambda _: driver.find_element(By.XPATH, '//label[contains(., "BEST PER YEAR")]//input').is_selected())
            best_page = wait.until(lambda _: grouped_page_if_ready(driver, 60, per_year=20))
            assert_year_grouping(best_page, per_year=20)
            best = grid_snapshot(driver)
            today = datetime.now(ZoneInfo("America/New_York")).date()
            for row in best["rows"]:
                days = (datetime.strptime(row["values"][2], "%b-%d-%y").date() - today).days
                assert row["expiryDetail"] == f"{days}d · {days / 365.25:.2f}y", row
                assert row["expiryFits"], row
            for width in (1100, 1440):
                driver.set_window_size(width, 900)
                assert all(row["expiryFits"] for row in grid_snapshot(driver)["rows"])
            print("Vertical Options tiny expiry detail PASS (calendar days + years, 43px rows)", flush=True)
            assert len(best_page["rows"]) == 60, best_page
            assert any(not row["annual"] for row in best["rows"]), best
            assert_row_colors(best_page)
            assert all(
                all(value["weight"] == "900" and "underline" in value["decoration"]
                    for value in row["emphasis"])
                for row in best["rows"]
            ), best
            assert all(row["href"].startswith("https://optionstrat.com/build/") and
                       row["target"] == "_blank" for row in best["rows"]), best
            assert "RISK:REWARD" in best["header"] and "FILL" in best["header"], best
            assert all(row["values"][-1] == "ASK" for row in best["rows"]), best
            assert not best["horizontalOverflow"], best
            assert best["height"] == 516, best
            for row in best["rows"]:
                assert_exact_tradingview_formula(row)

            in_grid(driver, lambda: driver.find_element(By.CSS_SELECTOR, '.row[data-index="0"] .select').click())
            wait.until(lambda _: checked_count(driver) == 1)
            wait.until(lambda _: grid_snapshot(driver)["rows"][0]["selected"] == "true")

            expected_url = exact_optionstrat_url(best["rows"][0]["values"])
            assert best["rows"][0]["href"] == expected_url, best["rows"][0]
            original_tab = driver.current_window_handle
            in_grid(driver, lambda: driver.find_element(By.CSS_SELECTOR, '.row[data-index="0"] .open').click())
            new_tab = wait.until(lambda _: next(
                (handle for handle in driver.window_handles if handle != original_tab), None
            ))
            driver.switch_to.window(new_tab)
            wait.until(lambda _: driver.current_url.startswith("https://optionstrat.com/build/"))
            assert driver.current_url.rstrip("/") == expected_url, driver.current_url
            driver.close()
            driver.switch_to.window(original_tab)
            assert checked_count(driver) == 1, "Opening OptionStrat changed the selected spread"

            tv_url = best["rows"][0]["tvHref"]
            in_grid(driver, lambda: driver.find_element(By.CSS_SELECTOR,
                '.row[data-index="0"] .link-pair').find_element(By.LINK_TEXT, 'TV').click())
            tv_tab = wait.until(lambda _: next(
                (handle for handle in driver.window_handles if handle != original_tab), None
            ))
            driver.switch_to.window(tv_tab)
            wait.until(lambda _: driver.current_url.startswith("https://www.tradingview.com/chart/"))
            assert parse_qs(urlsplit(driver.current_url).query)["symbol"] == parse_qs(urlsplit(tv_url).query)["symbol"]
            driver.close()
            driver.switch_to.window(original_tab)
            assert checked_count(driver) == 1, "Opening TradingView changed the selected spread"
            assert not any(event["event"] in {"preview", "place"} for event in events(event_path))
            print("TradingView button click opens exact broker-normalized formula in a new chart tab PASS")

            select_results_view(driver, "ALL QUALIFIED")
            wait.until(lambda _: "60 DISPLAYED // 60 MATCHING OF 60 QUALIFIED PAIRS" in driver.find_element(By.TAG_NAME, "body").text)
            # Both views contain all 60 results. Wait for the ALL view's keyed
            # export controls, then verify real CSV downloads.
            full_csv = download_csv(driver, wait, downloads, "vo_export_qualified_csv",
                                    "SPY_vertical_qualified.csv")
            assert len(full_csv) == 60 and {row["Type"] for row in full_csv} == {"CALL", "PUT"}
            full_excel = download_xlsx(driver, wait, downloads,
                "vo_export_qualified_excel", "SPY_vertical_qualified.xlsx")
            assert full_excel["rows"] == 60, full_excel["rows"]
            toggle_put_column_filter(driver)
            wait.until(lambda _: "30" in driver.find_element(
                By.CSS_SELECTOR, '.st-key-vo_export_qualified_csv button').text)
            filtered_csv = download_csv(driver, wait, downloads, "vo_export_qualified_csv",
                                        "SPY_vertical_qualified.csv")
            assert len(filtered_csv) == 30 and all(row["Type"] == "CALL" for row in filtered_csv)
            filtered_excel = download_xlsx(driver, wait, downloads,
                "vo_export_qualified_excel", "SPY_vertical_qualified.xlsx",
                expected_sides=("CALL",))
            assert filtered_excel["rows"] == 30, filtered_excel["rows"]
            toggle_put_column_filter(driver)
            wait.until(lambda _: "60" in driver.find_element(
                By.CSS_SELECTOR, '.st-key-vo_export_qualified_csv button').text)
            print("Vertical Options CSV/Excel downloads PASS (60 all / 30 CALL-filtered / 20 one-year)")
            for old_key in ("vo_all_pair_expiry", "vo_all_pair_side", "vo_all_pair_sort",
                            "vo_all_pair_direction"):
                assert not driver.find_elements(By.CSS_SELECTOR, f".st-key-{old_key}"), old_key
            assert not driver.find_elements(By.CSS_SELECTOR, 'input[aria-label^="PAGE /"]')
            def expanded_grid(_):
                snapshot = grid_snapshot(driver)
                return snapshot if len(snapshot["rows"]) > 3 else False
            all_rows = wait.until(expanded_grid)
            assert not all_rows["horizontalOverflow"], all_rows
            for row in all_rows["rows"]:
                assert_exact_tradingview_formula(row)
            assert checked_count(driver) == 1

            def all_types_and_strikes():
                def inspect():
                    return driver.execute_script(
                        """
                        const scroll=document.getElementById('scroll');
                        const types=new Set(),strikes=new Set();
                        for(let offset=0;offset<=scroll.scrollHeight;offset+=scroll.clientHeight){
                          scroll.scrollTop=offset;scroll.dispatchEvent(new Event('scroll'));
                          for(const row of document.querySelectorAll('.row')){
                            const cells=row.querySelectorAll('.cell');
                            types.add(cells[1].textContent);
                            strikes.add(cells[3].textContent);
                            strikes.add(cells[4].textContent);
                          }
                        }
                        scroll.scrollTop=0;scroll.dispatchEvent(new Event('scroll'));
                        return {types:[...types],strikes:[...strikes]};
                        """
                    )
                return in_grid(driver, inspect)
            coverage = all_types_and_strikes()
            assert set(coverage["types"]) == {"CALL", "PUT"}, coverage
            put_style = in_grid(driver, lambda: driver.execute_script("""
                const scroller=document.getElementById('scroll');
                let put=null;
                for(let offset=0;offset<=scroller.scrollHeight;offset+=scroller.clientHeight){
                  scroller.scrollTop=offset;scroller.dispatchEvent(new Event('scroll'));
                  put=[...document.querySelectorAll('.row.put:not(.annual)')].find(
                    row=>row.querySelectorAll('.cell')[1].textContent==='PUT');
                  if(put)break;
                }
                if(!put)return null;
                return {
                  background:getComputedStyle(put.querySelector('.cell')).backgroundColor,
                  foreground:getComputedStyle(put.querySelector('.cell')).color,
                  button:getComputedStyle(put.querySelector('.select')).backgroundColor,
                  link:getComputedStyle(put.querySelector('.open')).color
                };
            """))
            assert put_style == {
                "background": "rgb(234, 1, 1)",
                "foreground": "rgb(0, 0, 0)",
                "button": "rgb(234, 1, 1)",
                "link": "rgb(0, 0, 0)",
            }, put_style
            assert {"90", "110"}.issubset(set(coverage["strikes"])), coverage

            select_results_view(driver, "BEST PER YEAR")
            wait.until(lambda _: driver.find_element(By.XPATH, '//label[contains(., "BEST PER YEAR")]//input').is_selected())
            wait.until(lambda _: grouped_page_if_ready(driver, 60, per_year=20))
            wait.until(lambda _: grid_snapshot(driver)["rows"][0]["selected"] == "true")
            assert checked_count(driver) == 1
            assert len([item for item in events(event_path) if item["event"] == "chain"]) == 3
            assert not any(item["event"] in {"preview", "place"} for item in events(event_path))

            order_expander = driver.find_element(
                By.XPATH,
                '//div[@data-testid="stExpander"]//summary[contains(., "OPTIONAL E*TRADE LIVE ORDER REVIEW")]',
            )
            order_expander.click()
            review = wait.until(lambda _: next((
                button for button in driver.find_elements(
                    By.XPATH, '//button[contains(., "REVIEW WITH E*TRADE")]',
                ) if button.is_displayed() and button.is_enabled()
            ), None))
            chip = wait.until(lambda _: driver.execute_script(
                """
                const chip=document.querySelector(
                  '.st-key-vertical_options_workspace [data-testid="stMultiSelect"] [data-tag]'
                );
                if(!chip)return null;
                const title=chip.querySelector('[title]')||chip;
                return {label:title.textContent,
                        background:getComputedStyle(chip).backgroundColor,
                        foreground:getComputedStyle(title).color};
                """
            ))
            assert "Test Account" in chip["label"], chip
            assert chip["background"] == "rgb(251, 139, 30)" and chip["foreground"] == "rgb(0, 0, 0)", chip
            assert not driver.find_elements(By.CSS_SELECTOR, 'input[aria-label="Debit Limit"]'), (
                "Debit Limit must not be editable in the optional order review"
            )
            wait.until(lambda _: "TOTAL ORDER DEBIT" in driver.find_element(By.TAG_NAME, "body").text)
            assert not any(item["event"] in {"preview", "place"} for item in events(event_path)), (
                "Opening the optional order expander sent a broker order call"
            )
            def click_review(_):
                try:
                    button = next((item for item in driver.find_elements(
                        By.XPATH, '//button[contains(., "REVIEW WITH E*TRADE")]',
                    ) if item.is_displayed() and item.is_enabled()), None)
                    if button is None:
                        return False
                    driver.execute_script("arguments[0].scrollIntoView({block:'center'})", button)
                    button.click()
                    return True
                except (ElementClickInterceptedException, ElementNotInteractableException,
                        StaleElementReferenceException):
                    return False
            wait.until(click_review)
            wait.until(lambda _: len([item for item in events(event_path)
                                      if item["event"] == "preview"]) == 1)
            wait.until(lambda _: "PREVIEW ACCEPTED FOR ALL SELECTED ACCOUNTS"
                       in driver.find_element(By.TAG_NAME, "body").text)
            preview = next(item for item in events(event_path) if item["event"] == "preview")
            assert preview["account_key"] == "stub-key", preview
            request = preview["payload"]["PreviewOrderRequest"]
            assert request["orderType"] == "SPREADS" and len(request["Order"]) == 1, request
            order = request["Order"][0]
            assert order["priceType"] == "NET_DEBIT" and len(order["Instrument"]) == 2, order
            # Quote-based scanner and E*TRADE preview must share the same exact
            # selected debit, regardless of stale pre-fix 4.35 session input.
            selected_values = best["rows"][0]["values"]
            def dollars(value):
                return float(value.replace("$", "").replace(",", ""))
            buy_strike = float(selected_values[3])
            expected_natural = round(
                dollars(selected_values[5]) + .02 + .002 * (buy_strike - 90)
                - (dollars(selected_values[6]) - .02), 2
            )
            assert selected_values[-1] == "ASK", selected_values
            assert order["limitPrice"] == expected_natural, (
                "E*TRADE preview price diverged from selected ASK spread",
                order["limitPrice"], expected_natural
            )
            assert order["limitPrice"] == round(dollars(selected_values[7]) / 100, 2), order
            assert len([item for item in events(event_path) if item["event"] == "chain"]) == 4, (
                "E*TRADE review must requote only the checked expiration once"
            )
            buy, sell = order["Instrument"]
            selected_values = best["rows"][0]["values"]
            selected_expiry = datetime.strptime(selected_values[2], "%b-%d-%y")
            assert [leg["orderAction"] for leg in (buy, sell)] == ["BUY_OPEN", "SELL_OPEN"]
            assert [leg["Product"]["strikePrice"] for leg in (buy, sell)] == [
                float(selected_values[3]), float(selected_values[4]),
            ]
            assert all(
                leg["Product"]["symbol"] == selected_values[0]
                and leg["Product"]["callPut"] == selected_values[1]
                and (leg["Product"]["expiryYear"], leg["Product"]["expiryMonth"],
                     leg["Product"]["expiryDay"]) ==
                (selected_expiry.year, selected_expiry.month, selected_expiry.day)
                and leg["orderedQuantity"] == 1
                for leg in (buy, sell)
            ), order
            assert wait.until(lambda _: driver.find_element(
                By.CSS_SELECTOR, '.st-key-vo_send button',
            )).get_attribute("disabled") is not None, "PLACE must need affirmative confirmation"
            assert not any(item["event"] == "place" for item in events(event_path)), (
                "Preview alone placed an order"
            )
            # Result filters belong to the table menus.
            select_results_view(driver, "ALL QUALIFIED")
            wait.until(lambda _: driver.find_element(By.XPATH, '//label[contains(., "ALL QUALIFIED")]//input').is_selected())
            # Bulk paste must not fetch quotes or chains by itself or place orders.
            chain_before_list = len([item for item in events(event_path) if item["event"] == "chain"])
            paste_button = driver.find_element(By.XPATH, '//button[contains(., "PASTE TICKER LIST")]')
            driver.execute_script("arguments[0].scrollIntoView({block:'center'})", paste_button)
            paste_button.click()
            textarea = wait.until(lambda _: driver.find_element(
                By.CSS_SELECTOR, 'textarea[aria-label^="PASTE TICKERS"]',
            ))
            textarea.send_keys("| Ticker |\n| ------ |\n| MSFT |\n| SPY |\n| GOOGL |\n| MSFT |")
            driver.find_element(By.XPATH, '//button[contains(., "LOAD TICKERS")]').click()
            def list_loaded(_):
                inputs = driver.find_elements(By.CSS_SELECTOR, 'input[aria-label="Ticker"]')
                return inputs and inputs[0].get_attribute("value") == "MSFT"
            wait.until(list_loaded)
            assert "LOADED TICKERS (3)" in driver.find_element(By.TAG_NAME, "body").text
            assert "Not in GEX" in driver.find_element(By.TAG_NAME, "body").text
            assert len([item for item in events(event_path) if item["event"] == "chain"]) == chain_before_list
            assert not any(item["event"] == "place" for item in events(event_path))
            # Loading a list resets the prior ticker's result without scanning.
            assert driver.find_elements(By.CSS_SELECTOR, '[data-testid="stSelectbox"]')
            assert not grid_is_mounted(driver)
            # Clearing either input restores the literal no-filter defaults.
            # The scan must not derive a BE ceiling from spot or a wall.
            form = wait.until(ready_scanner_form)
            for label in ("Target Breakeven", "Max Cost"):
                field = form.find_element(By.CSS_SELECTOR, f'input[aria-label="{label}"]')
                field.click()
                field.send_keys(SELECT_ALL, "a")
                field.send_keys(Keys.BACKSPACE)
                assert field.get_attribute("value") == "", (label, field.get_attribute("value"))
            form.find_element(By.XPATH, './/button[contains(., "SCAN ALL YEARS")]').click()
            wait.until(lambda _: "MAX COST ANY" in driver.find_element(By.TAG_NAME, "body").text)
            wait.until(lambda _: "TARGET ANY" in driver.find_element(By.TAG_NAME, "body").text)
            wait.until(lambda _: driver.find_element(
                By.CSS_SELECTOR, 'input[aria-label="Target Breakeven"]'
            ).get_attribute("value") == "")
            assert driver.find_element(By.CSS_SELECTOR, 'input[aria-label="Max Cost"]').get_attribute("value") == ""
            assert not driver.find_elements(By.CSS_SELECTOR, '.st-key-vo_be_filter_enabled'), (
                "An unused breakeven target reserved filter space"
            )
            assert driver.find_element(By.XPATH, '//label[contains(., "BEST PER YEAR")]//input').is_selected(), (
                "A new scan retained the previous ALL QUALIFIED view"
            )
            select_results_view(driver, "ALL QUALIFIED")
            wait.until(lambda _: "60 DISPLAYED // 60 MATCHING OF 60 QUALIFIED PAIRS" in driver.find_element(By.TAG_NAME, "body").text)
            uncapped = download_csv(driver, wait, downloads, "vo_export_qualified_csv",
                                    "MSFT_vertical_qualified.csv")
            assert len(uncapped) == 60
            assert all(not row["Target Breakeven ($)"] and not row["Max Cost Filter ($)"]
                       for row in uncapped), uncapped[:2]
            assert all(float(row["Risk Reward (to 1)"]) >= 2 for row in uncapped)
            assert grid_snapshot(driver)["rows"]
            assert not any(item["event"] == "place" for item in events(event_path))

            # An explicit target and cost ceiling can still be supplied after
            # a scan with no requested BE or cost filtering.
            form = wait.until(ready_scanner_form)
            breakeven_input = form.find_element(By.CSS_SELECTOR, 'input[aria-label="Target Breakeven"]')
            breakeven_input.click()
            breakeven_input.send_keys(SELECT_ALL, "a")
            breakeven_input.send_keys("103")
            cost_input = form.find_element(By.CSS_SELECTOR, 'input[aria-label="Max Cost"]')
            cost_input.click()
            cost_input.send_keys(SELECT_ALL, "a")
            cost_input.send_keys("150")
            form.find_element(By.XPATH, './/button[contains(., "SCAN ALL YEARS")]').click()
            wait.until(lambda _: "TARGET $103.00 // MAX COST $150.00" in driver.find_element(By.TAG_NAME, "body").text)
            assert driver.find_element(By.XPATH, '//label[contains(., "BEST PER YEAR")]//input').is_selected()
            assert "MAX COST" in driver.find_element(By.TAG_NAME, "body").text
            assert form.find_element(By.CSS_SELECTOR, 'input[aria-label="Max Cost"]').get_attribute("value") == "150"
            assert not any(item["event"] == "place" for item in events(event_path))

            # A manual target belongs to its ticker. Another ticker keeps the
            # no-filter default, and returning restores the manual override.
            ticker = driver.find_element(By.CSS_SELECTOR, 'input[aria-label="Ticker"]')
            ticker.send_keys(SELECT_ALL, "a")
            ticker.send_keys("QQQ", Keys.TAB)
            wait.until(lambda _: driver.find_element(By.CSS_SELECTOR, 'input[aria-label="Target Breakeven"]').get_attribute("value") == "")
            ticker = driver.find_element(By.CSS_SELECTOR, 'input[aria-label="Ticker"]')
            ticker.send_keys(SELECT_ALL, "a")
            ticker.send_keys("MSFT", Keys.TAB)
            wait.until(lambda _: driver.find_element(By.CSS_SELECTOR, 'input[aria-label="Target Breakeven"]').get_attribute("value") == "103")

            # Restart the actual Streamlit process. The replacement fixture does
            # not seed GEX: only the same browser's persisted backup can restore it.
            token = hashlib.sha256(b"scanner-fixture").hexdigest()[:24]
            def saved_gex(_):
                for frame in driver.find_elements(By.TAG_NAME, "iframe"):
                    if "raj_gex_snapshot_memory_v1" not in str(frame.get_attribute("src")):
                        continue
                    try:
                        driver.switch_to.frame(frame)
                        stored = driver.execute_async_script("""
                            const key=arguments[0],done=arguments[1];
                            const request=indexedDB.open('raj-terminal-gex-snapshots-v1',1);
                            request.onsuccess=()=>{
                                const db=request.result;
                                const get=db.transaction('snapshots','readonly').objectStore('snapshots').get(key);
                                get.onsuccess=()=>{done(get.result?.current||null);db.close();};
                                get.onerror=()=>{done(null);db.close();};
                            };
                            request.onerror=()=>done(null);
                            """,
                            "raj-terminal-gex-snapshots-v1:" + token,
                        )
                        if stored:
                            decoded = json.loads(zlib.decompress(base64.b64decode(stored["packed"])))
                            if "SPY" in (decoded.get("cboe") or {}).get("results", {}) and "ETRADE_TEST" in decoded.get("etrade", {}):
                                return True
                    finally:
                        driver.switch_to.default_content()
                return False
            wait.until(saved_gex)
            chain_before_restart = len([item for item in events(event_path) if item["event"] == "chain"])
            server.terminate()
            server.wait(timeout=8)
            environment["VO_SCANNER_SKIP_GEX_SEED"] = "1"
            environment["VO_SCANNER_PARTIAL_TEST"] = "1"
            with server_log.open("a", encoding="utf-8") as output:
                server = subprocess.Popen(
                    [sys.executable, "-m", "streamlit", "run", str(app_path),
                     "--server.port", str(port), "--server.headless", "true",
                     "--browser.gatherUsageStats", "false"],
                    cwd=ROOT, env=environment, stdout=output, stderr=subprocess.STDOUT,
                )
            wait_for_server(server, base, server_log)
            driver.get(base)
            wait.until(lambda _: any(event["event"] == "fixture-render"
                                     and event.get("pid") == server.pid
                                     for event in events(event_path)))
            wait.until(ready_scanner_form)
            ticker = driver.find_element(By.CSS_SELECTOR, 'input[aria-label="Ticker"]')
            ticker.send_keys(SELECT_ALL, "a")
            ticker.send_keys("SPY", Keys.TAB)
            wait.until(lambda _: "CALL WALL $110.00" in driver.find_element(By.TAG_NAME, "body").text)
            wait.until(lambda _: driver.find_element(By.CSS_SELECTOR, 'input[aria-label="Target Breakeven"]').get_attribute("value") == "")
            ticker = driver.find_element(By.CSS_SELECTOR, 'input[aria-label="Ticker"]')
            ticker.send_keys(SELECT_ALL, "a")
            ticker.send_keys("QQQ", Keys.TAB)
            wait.until(lambda _: "LAST $92.00" in driver.find_element(By.TAG_NAME, "body").text)
            wait.until(lambda _: driver.find_element(By.CSS_SELECTOR, 'input[aria-label="Target Breakeven"]').get_attribute("value") == "")
            assert len([item for item in events(event_path) if item["event"] == "chain"]) == chain_before_restart
            assert not any(item["event"] == "place" for item in events(event_path))
            print("GEX browser cache PASS after real process restart; optional breakeven defaults PASS", flush=True)
            # Automatic continuation works through one-expiration batches.
            # Neither metrics nor a result grid mount during an unfinished pass.
            form = wait.until(ready_scanner_form)
            form.find_element(By.XPATH, './/button[contains(., "SCAN ALL YEARS")]').click()
            wait.until(lambda _: len([e for e in events(event_path) if e["event"] == "chain"])
                       > chain_before_restart)
            assert not grid_is_mounted(driver), "Incomplete scan displayed a result grid"
            wait.until(lambda _: grid_page_rows(driver)["total"] == 60)
            assert driver.find_element(By.XPATH, '//label[contains(., "BEST PER YEAR")]//input').is_selected()
            assert_year_grouping(grid_page_rows(driver), per_year=20)
            in_grid(driver, lambda: driver.find_element(By.CSS_SELECTOR, '.row[data-index="0"] .select').click())
            wait.until(lambda _: checked_count(driver) == 1)
            calls = [e for e in events(event_path) if e["event"] == "chain"][chain_before_restart:]
            assert len(calls) == len({(e["year"], e["month"], e["day"]) for e in calls}) == 3
            assert "RISK:REWARD ≥ 1:2" in driver.find_element(By.TAG_NAME, "body").text
            assert not any(e["event"] == "place" for e in events(event_path))
            print("Vertical Options complete-only results PASS (3 unique chains, no orders)", flush=True)

            # Every one-year match shares a single virtual table. Select
            # beyond the old page boundary and at the final row, then return
            # to the top without losing either checked identity.
            ticker = driver.find_element(By.CSS_SELECTOR, 'input[aria-label="Ticker"]')
            ticker.send_keys(SELECT_ALL, "a")
            ticker.send_keys("MASSIVE", Keys.TAB)
            form = wait.until(ready_scanner_form)
            target = form.find_element(By.CSS_SELECTOR, 'input[aria-label="Target Breakeven"]')
            assert target.get_attribute("value") == ""
            assert form.find_element(By.CSS_SELECTOR, 'input[aria-label="Max Cost"]').get_attribute("value") == ""
            form.find_element(By.XPATH, './/button[contains(., "SCAN ALL YEARS")]').click()
            wait.until(lambda _: "10,100 QUALIFIED PAIRS" in driver.find_element(By.TAG_NAME, "body").text)
            wait.until(lambda _: "SCANNING ALL YEARS //" not in driver.find_element(By.TAG_NAME, "body").text)
            assert driver.find_element(By.XPATH, '//label[contains(., "BEST PER YEAR")]//input').is_selected()
            assert not driver.find_elements(By.CSS_SELECTOR, '.st-key-vo_be_filter_enabled')
            wait.until(lambda _: "10,100 DISPLAYED // 10,100 MATCHING OF 10,100 QUALIFIED PAIRS" in driver.find_element(By.TAG_NAME, "body").text)
            assert "SHORTLIST" not in driver.find_element(By.TAG_NAME, "body").text
            assert "SAFETY MODE" not in driver.find_element(By.TAG_NAME, "body").text
            assert_no_result_overlap(driver)
            massive_grid = grid_snapshot(driver)
            assert massive_grid["scrollHeight"] == 10100 * 43, massive_grid
            assert len(massive_grid["rows"]) < 100, massive_grid
            assert massive_grid["rows"][0]["index"] == 0 and massive_grid["rows"][0]["annual"], massive_grid
            for index, count in ((200, 1), (10099, 2)):
                in_grid(driver, lambda index=index: driver.execute_script("""
                    const scroll=document.getElementById('scroll');
                    scroll.scrollTop=arguments[0]*43;
                    scroll.dispatchEvent(new Event('scroll'));
                """, index))
                wait.until(lambda _, index=index: any(
                    row["index"] == index for row in grid_snapshot(driver)["rows"]))
                in_grid(driver, lambda index=index: driver.find_element(
                    By.CSS_SELECTOR, f'.row[data-index="{index}"] .select').click())
                wait.until(lambda _, count=count: checked_count(driver) == count)
            in_grid(driver, lambda: driver.execute_script("""
                const scroll=document.getElementById('scroll');
                scroll.scrollTop=0;scroll.dispatchEvent(new Event('scroll'));
            """))
            wait.until(lambda _: any(row["index"] == 0 for row in grid_snapshot(driver)["rows"]))
            assert checked_count(driver) == 2
            select_results_view(driver, "ALL QUALIFIED")
            wait.until(lambda _: driver.find_element(By.XPATH, '//label[contains(., "ALL QUALIFIED")]//input').is_selected())
            wait.until(lambda _: "10,100 DISPLAYED // 10,100 MATCHING OF 10,100 QUALIFIED PAIRS" in driver.find_element(By.TAG_NAME, "body").text)
            assert checked_count(driver) == 2
            whole = download_csv(driver, wait, downloads, "vo_export_qualified_csv",
                                 "MASSIVE_vertical_qualified.csv")
            assert len(whole) == 10100
            whole_excel = download_xlsx(driver, wait, downloads,
                "vo_export_qualified_excel", "MASSIVE_vertical_qualified.xlsx")
            assert whole_excel["rows"] == 10100, whole_excel["rows"]
            assert sum(row["Type"] == "CALL" for row in whole) == 5050
            assert sum(row["Type"] == "PUT" for row in whole) == 5050
            assert len({(r["Type"], r["Buy Strike"], r["Sell Strike"]) for r in whole}) == 10100
            assert all(not row["Target Breakeven ($)"] and not row["Max Cost Filter ($)"]
                       for row in whole), whole[:2]
            grid_menu_action(driver, "cost", "SORT DESC")
            sorted_whole = download_csv(driver, wait, downloads, "vo_export_qualified_csv",
                                        "MASSIVE_vertical_qualified.csv")
            costs = [float(row["Cost Per Contract ($)"]) for row in sorted_whole]
            assert len(sorted_whole) == 10100 and costs == sorted(costs, reverse=True), costs[:5]
            grid_menu_action(driver, "basis", "FILL + TARGET")
            # A custom component's checkbox event can arrive during a full-app
            # rerun. The backend count must update without a fragment-scope error.
            toggle_put_column_filter(driver)
            wait.until(lambda _: "5,050 DISPLAYED // 5,050 MATCHING OF 10,100 QUALIFIED PAIRS" in driver.find_element(By.TAG_NAME, "body").text)
            assert "EXPORT CSV // 5,050" in driver.find_element(By.TAG_NAME, "body").text
            assert "StreamlitInvalidLayoutContextError" not in driver.find_element(By.TAG_NAME, "body").text
            toggle_put_column_filter(driver)
            wait.until(lambda _: "10,100 DISPLAYED // 10,100 MATCHING OF 10,100 QUALIFIED PAIRS" in driver.find_element(By.TAG_NAME, "body").text)
            late_row = whole[-1]
            late_identity = (late_row["Type"], late_row["Buy Strike"], late_row["Sell Strike"])
            assert late_identity not in {
                (row["Type"], row["Buy Strike"], row["Sell Strike"])
                for row in whole[:200]
            }
            # The row came from the far end of the 10,100-result export. Header
            # filters must search the stored universe beyond visible DOM rows.
            set_exact_grid_filter(driver, "buy", "BUY STRIKE", float(late_row["Buy Strike"]))
            wait.until(lambda _: "EXPORT CSV // 100" in driver.find_element(By.TAG_NAME, "body").text)
            set_exact_grid_filter(driver, "sell", "SELL STRIKE", float(late_row["Sell Strike"]))
            wait.until(lambda _: "EXPORT CSV // 1" in driver.find_element(By.TAG_NAME, "body").text)
            assert not driver.find_elements(By.XPATH, '//button[normalize-space(.)="NEXT →"]')
            isolated = wait.until(lambda _: grid_snapshot(driver)["rows"] if len(grid_snapshot(driver)["rows"]) == 1 else False)
            assert (isolated[0]["values"][1], float(isolated[0]["values"][3]),
                    float(isolated[0]["values"][4])) == (
                late_row["Type"], float(late_row["Buy Strike"]), float(late_row["Sell Strike"])
            ), (isolated, late_row)
            isolated_csv = download_csv(driver, wait, downloads, "vo_export_qualified_csv",
                                        "MASSIVE_vertical_qualified.csv")
            identifying_columns = (
                "Ticker", "Type", "Expiration", "Buy Strike", "Sell Strike",
                "Cost Per Contract ($)", "Breakeven ($)", "Risk Reward (to 1)",
            )
            assert len(isolated_csv) == 1 and all(
                isolated_csv[0][column] == late_row[column] for column in identifying_columns
            ), isolated_csv
            assert not any(e["event"] == "place" for e in events(event_path))
            print("Full-universe browser PASS: 10,100 rows in one virtual table, final-row selection, CSV/Excel and late-row filters", flush=True)

            # Ignore 0-, 1- and 6-day expirations before any broker-chain
            # fetch; exactly seven New York calendar days remains eligible.
            chain_before_boundary = len([e for e in events(event_path) if e["event"] == "chain"])
            ticker = driver.find_element(By.CSS_SELECTOR, 'input[aria-label="Ticker"]')
            ticker.send_keys(SELECT_ALL, "a")
            ticker.send_keys("BOUNDARY", Keys.TAB)
            form = wait.until(ready_scanner_form)
            form.find_element(By.XPATH, './/button[contains(., "SCAN ALL YEARS")]').click()
            wait.until(lambda _: "20 DISPLAYED // 20 MATCHING OF 20 QUALIFIED PAIRS" in driver.find_element(By.TAG_NAME, "body").text)
            boundary_grid = wait.until(lambda _: grouped_page_if_ready(driver, 20, per_year=20))
            assert_row_colors(boundary_grid)
            boundary_calls = [e for e in events(event_path) if e["event"] == "chain"][chain_before_boundary:]
            today = datetime.now(ZoneInfo("America/New_York")).date()
            assert len(boundary_calls) == 1 and date(
                boundary_calls[0]["year"], boundary_calls[0]["month"], boundary_calls[0]["day"]
            ) == today + timedelta(days=7), boundary_calls
            boundary_csv = download_csv(driver, wait, downloads, "vo_export_qualified_csv",
                                        "BOUNDARY_vertical_qualified.csv")
            boundary_excel = download_xlsx(driver, wait, downloads, "vo_export_qualified_excel",
                                          "BOUNDARY_vertical_qualified.xlsx")
            assert boundary_excel["rows"] == len(boundary_csv) == 20
            assert {row["Highlight"] for row in boundary_csv} == {"GREEN", "RED"}
            assert all(date.fromisoformat(row["Expiration"]) == today + timedelta(days=7)
                       for row in boundary_csv), boundary_csv[:2]
            assert_no_result_overlap(driver)
            assert not any(e["event"] == "place" for e in events(event_path))
            print("Minimum 7-day expiry boundary and actual green/red export fills PASS", flush=True)

            # A user-edited threshold must reach the actual candidate scan,
            # while the next scan still opens on BEST PER YEAR. This fixture
            # has some SPY spreads above 1:4.01 but fewer than at default 1:2.
            ticker = driver.find_element(By.CSS_SELECTOR, 'input[aria-label="Ticker"]')
            ticker.send_keys(SELECT_ALL, "a")
            ticker.send_keys("SPY", Keys.TAB)
            form = wait.until(ready_scanner_form)
            ratio = form.find_element(By.CSS_SELECTOR, 'input[aria-label="MIN RISK:REWARD 1:"]')
            assert ratio.get_attribute("value") == "2"
            ratio.send_keys(SELECT_ALL, "a")
            ratio.send_keys("4.01")
            form.find_element(By.XPATH, './/button[contains(., "SCAN ALL YEARS")]').click()
            def custom_best_ready(_):
                if "RISK:REWARD ≥ 1:4.01" not in driver.find_element(By.TAG_NAME, "body").text:
                    return False
                page = grid_page_rows(driver)
                return page if 3 < page["total"] < 60 and all(
                    row["values"][0] == "SPY" for row in page["rows"]
                ) else False
            custom_best = wait.until(custom_best_ready)
            assert driver.find_element(By.XPATH, '//label[contains(., "BEST PER YEAR")]//input').is_selected()
            assert_year_grouping(custom_best)
            assert all(float(row["values"][9].split(":", 1)[1]) >= 4.01
                       for row in custom_best["rows"]), custom_best
            select_results_view(driver, "ALL QUALIFIED")
            custom_csv = download_csv(driver, wait, downloads, "vo_export_qualified_csv",
                                      "SPY_vertical_qualified.csv")
            assert 0 < len(custom_csv) < 60, len(custom_csv)
            assert all(float(row["Risk Reward (to 1)"]) >= 4.01 for row in custom_csv)
            assert not any(e["event"] == "place" for e in events(event_path))
            print(f"Editable risk:reward browser PASS (default 1:2, custom 1:4.01, {len(custom_csv)} matches)", flush=True)

            # Starting another scan from a completed grid must clear old
            # results before a deliberately slow broker chain can finish.
            slow_rescan_file.touch()
            prior_chain_count = len([e for e in events(event_path) if e["event"] == "chain"])
            form = wait.until(ready_scanner_form)
            form.find_element(By.XPATH, './/button[contains(., "SCAN ALL YEARS")]').click()
            wait.until(lambda _: "SCANNING ALL YEARS // SPY" in driver.find_element(By.TAG_NAME, "body").text)
            wait.until(lambda _: len([e for e in events(event_path) if e["event"] == "chain"]) > prior_chain_count)
            assert not grid_is_visible(driver), "Previous scan results flashed during a new scan"
            assert not any(item.is_displayed() for item in driver.find_elements(
                By.CSS_SELECTOR, ".vo-grid:not(.vo-liquid-cash .vo-grid)"
            )), (
                "Previous scan metrics remained visible during a new scan"
            )
            assert not any(item.is_displayed() for item in driver.find_elements(
                By.CSS_SELECTOR, ".st-key-vo_export_qualified_csv button"
            )), "Previous scan export remained visible during a new scan"
            wait.until(lambda _: driver.find_element(By.XPATH, '//label[contains(., "BEST PER YEAR")]//input').is_selected())
            wait.until(custom_best_ready)
            slow_rescan_file.unlink()
            assert not any(e["event"] == "place" for e in events(event_path))
            print("Completed grid clears before slow rescan and reappears only after completion PASS", flush=True)
            print("VERTICAL OPTIONS SCANNER BROWSER: PASS")
        except Exception:
            print(server_log.read_text(encoding="utf-8")[-5000:], file=sys.stderr)
            if driver is not None:
                print(driver.find_element(By.TAG_NAME, "body").text[-6000:], file=sys.stderr)
                print([(item.get_attribute("aria-label"), item.get_attribute("value"))
                       for item in driver.find_elements(By.CSS_SELECTOR, 'input[aria-label]')], file=sys.stderr)
            raise
        finally:
            slow_rescan_file.unlink(missing_ok=True)
            if driver is not None:
                driver.quit()
            server.terminate()
            try:
                server.wait(timeout=8)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=5)


if __name__ == "__main__":
    test()
