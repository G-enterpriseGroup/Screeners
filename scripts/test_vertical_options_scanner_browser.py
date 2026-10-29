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
from datetime import datetime
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from urllib.parse import parse_qs, urlsplit
import zlib

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
from datetime import date
from pathlib import Path

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
    st.session_state.setdefault("vo_auto_finish", False)
    st.session_state.setdefault("_vo_auto_finish_previous", False)

LOG = Path(os.environ["VO_SCANNER_TEST_LOG"])
EXPIRIES = [date(date.today().year + offset, 6, 20) for offset in (1, 2, 3)]

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
            for day in ([EXPIRIES[0]] if symbol == "MASSIVE" else EXPIRIES)
        ]}}

    def get_option_chain(self, symbol, year, month, day,
                         no_of_strikes=100, chain_type="CALLPUT"):
        record("chain", symbol=symbol, year=year, month=month, day=day,
               no_of_strikes=no_of_strikes, chain_type=chain_type)
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

    def preview_order(self, account_key, payload):
        record("preview", account_key=account_key, payload=payload)
        return {"PreviewOrderResponse": {
            "PreviewIds": [{"previewId": 123456}],
            "totalOrderValue": 100.0, "estimatedCommission": 0.0,
        }}

    def place_order(self, *args, **kwargs):
        record("place")
        raise AssertionError("Checkbox selection must never place an order")

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


def grid_snapshot(driver: webdriver.Chrome) -> dict:
    return in_grid(driver, lambda: driver.execute_script(
        """
        const scroll=document.getElementById('scroll');
        const rows=[...document.querySelectorAll('.row')];
        return {
          rows:rows.map(row=>({
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
          horizontalOverflow:scroll.scrollWidth>scroll.clientWidth||
            document.documentElement.scrollWidth>document.documentElement.clientWidth,
          header:document.getElementById('head').textContent,
        };
        """
    ))


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


def download_csv(driver, wait, folder: Path, key: str, filename: str) -> list[dict]:
    """Wait for the rerendered export action and inspect its actual download."""
    def ready(_):
        for button in driver.find_elements(By.CSS_SELECTOR, f".st-key-{key} button"):
            try:
                if button.is_displayed() and button.is_enabled():
                    return button
            except StaleElementReferenceException:
                pass
        return False

    target = folder / filename
    target.unlink(missing_ok=True)
    wait.until(ready).click()
    wait.until(lambda _: target.exists() and not list(folder.glob("*.crdownload")))
    with target.open(encoding="utf-8", newline="") as downloaded:
        return list(csv.DictReader(downloaded))


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

            expected_labels = ["Target Breakeven", "Max Cost"]
            def ready_scanner_form(_):
                # Streamlit can mount stForm before its inputs hydrate on CI.
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
            assert form.find_element(By.CSS_SELECTOR, 'input[aria-label="Target Breakeven"]').get_attribute("value") == "no break even provided"
            assert form.find_element(By.CSS_SELECTOR, 'input[aria-label="Max Cost"]').get_attribute("value") == "no ceiling cost provided"
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
                    const inputs = ['Ticker','Target Breakeven','Max Cost'].map(name =>
                        document.querySelector('input[aria-label="'+name+'"]'));
                    const labels = inputs.map(input => input.closest('[data-testid="stTextInput"]')
                        .querySelector('[data-testid="stWidgetLabel"]'));
                    return {
                        inputs:inputs.map(input => rect(input.parentElement)),
                        inputDOM:inputs[0].parentElement.outerHTML.slice(0,1600),
                        company:rect(document.querySelector('.vo-company-box')),
                        labels:labels.map(rect),
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
                assert geometry["formPadding"] == "0px", geometry
                assert not geometry["overflow"], geometry
            driver.set_window_size(1440, 900)
            print("Vertical Options aligned scan controls PASS (1440 + 1100)", flush=True)
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
            assert target_input.get_attribute("value") == "no break even provided"
            target_input.send_keys(SELECT_ALL, "a")
            target_input.send_keys("100")
            cost_input = form.find_element(By.CSS_SELECTOR, 'input[aria-label="Max Cost"]')
            cost_input.send_keys(SELECT_ALL, "a")
            cost_input.send_keys("500")
            form.find_element(By.XPATH, './/button[contains(., "SCAN ALL YEARS")]').click()

            wait.until(lambda _: checked_count(driver) == 0)
            assert "30" in driver.find_element(By.TAG_NAME, "body").text, "Expected 30 parsed option contracts"
            chain_calls = [item for item in events(event_path) if item["event"] == "chain"]
            assert len(chain_calls) == 3, chain_calls
            assert len({item["year"] for item in chain_calls}) == 3, chain_calls
            assert all(item["no_of_strikes"] is None and item["chain_type"] == "CALLPUT"
                       for item in chain_calls), chain_calls

            # Both native switches use one role-based local geometry contract.
            switches = driver.execute_script("""
                return ['vo_smart_fill','vo_be_filter_enabled'].map(key=>{
                    const label=document.querySelector('.st-key-'+key+' label');
                    const track=label.querySelector(':scope > div:first-of-type');
                    const rect=track.getBoundingClientRect();
                    return {width:rect.width,height:rect.height,on:getComputedStyle(label,'::after').content};
                });
            """)
            assert all(item['width'] == 36 and item['height'] == 20 for item in switches), switches
            assert switches[1]['on'] == '"OFF"', switches
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
            # The full qualified universe is the default; the optional BE
            # filter requires a user-selected target and explicit opt-in.
            assert driver.find_element(By.XPATH, '//label[contains(., "ALL QUALIFIED")]//input').is_selected()
            assert not driver.find_element(By.CSS_SELECTOR, '.st-key-vo_be_filter_enabled input').is_selected()
            wait.until(lambda _: "ALL 60 QUALIFIED IN FILTER" in driver.find_element(By.TAG_NAME, "body").text)
            driver.find_element(By.CSS_SELECTOR, '.st-key-vo_be_filter_enabled label').click()
            wait.until(lambda _: driver.find_element(By.CSS_SELECTOR, '.st-key-vo_be_filter_enabled input').is_selected())
            driver.find_element(By.XPATH, '//label[contains(., "BEST PER YEAR")]').click()
            wait.until(lambda _: len(grid_snapshot(driver)["rows"]) == 3)
            driver.find_element(By.XPATH, '//label[contains(., "ALL QUALIFIED")]').click()
            wait.until(lambda _: len(grid_snapshot(driver)["rows"]) > 3)
            driver.find_element(By.CSS_SELECTOR, '.st-key-vo_all_pair_sort [role="combobox"]').click()
            wait.until(lambda _: driver.find_element(
                By.XPATH, '//*[@role="option" and normalize-space(.)="TARGET DISTANCE"]',
            )).click()
            nearby = grid_snapshot(driver)["rows"]
            be_values = [float(row["values"][8].replace('$', '').replace(',', '')) for row in nearby]
            assert all(95 <= value <= 105 for value in be_values), be_values
            assert [abs(value - 100) for value in be_values] == sorted(abs(value - 100) for value in be_values), be_values
            filtered_near = download_csv(driver, wait, downloads, "vo_export_filtered_qualified",
                                        "SPY_vertical_filtered.csv")
            assert len(filtered_near) > 3
            assert all(95 <= float(row["Breakeven ($)"]) <= 105 for row in filtered_near)
            assert all(float(row["Risk Reward (to 1)"]) >= 2 for row in filtered_near)
            tolerance = driver.find_element(By.CSS_SELECTOR, '.st-key-vo_be_range_percent input')
            tolerance.send_keys(SELECT_ALL, "a")
            tolerance.send_keys("3", Keys.TAB)
            wait.until(lambda _: "BREAKEVEN $97.00 TO $103.00" in driver.find_element(By.TAG_NAME, "body").text)
            tighter = download_csv(driver, wait, downloads, "vo_export_filtered_qualified",
                                   "SPY_vertical_filtered.csv")
            assert 0 < len(tighter) < len(filtered_near)
            assert all(97 <= float(row["Breakeven ($)"]) <= 103 for row in tighter)
            driver.find_element(By.CSS_SELECTOR, '.st-key-vo_be_filter_enabled label').click()
            wait.until(lambda _: "ALL 60 QUALIFIED IN FILTER" in driver.find_element(By.TAG_NAME, "body").text)
            driver.find_element(By.CSS_SELECTOR, '.st-key-vo_all_pair_sort [role="combobox"]').click()
            wait.until(lambda _: driver.find_element(
                By.XPATH, '//*[@role="option" and normalize-space(.)="FILL + TARGET"]',
            )).click()
            assert len([event for event in events(event_path) if event["event"] == "chain"]) == 3
            leave_button = driver.find_element(By.XPATH, '//button[contains(., "LEAVE SCANNER")]')
            driver.execute_script("arguments[0].scrollIntoView({block:'center'})", leave_button)
            leave_button.click()
            wait.until(lambda _: driver.find_element(By.XPATH, '//button[contains(., "RETURN TO SCANNER")]')).click()
            # Existence can observe Streamlit's temporary cached mount before
            # the returned fragment's actual values reach the frontend.
            wait.until(lambda _: not driver.find_element(By.CSS_SELECTOR, '.st-key-vo_be_filter_enabled input').is_selected())
            assert driver.find_element(By.CSS_SELECTOR, '.st-key-vo_be_range_percent input').get_attribute("value") in ("3.00", "3.0", "3")
            assert driver.find_element(By.XPATH, '//label[contains(., "ALL QUALIFIED")]//input').is_selected()
            assert len([event for event in events(event_path) if event["event"] == "chain"]) == 3
            driver.find_element(By.XPATH, '//label[contains(., "BEST PER YEAR")]').click()
            wait.until(lambda _: len(grid_snapshot(driver)["rows"]) == 3)
            best = grid_snapshot(driver)
            from datetime import date
            from zoneinfo import ZoneInfo
            today = datetime.now(ZoneInfo("America/New_York")).date()
            for row in best["rows"]:
                days = (datetime.strptime(row["values"][2], "%b-%d-%y").date() - today).days
                assert row["expiryDetail"] == f"{days}d · {days / 365.25:.2f}y", row
                assert row["expiryFits"], row
            for width in (1100, 1440):
                driver.set_window_size(width, 900)
                assert all(row["expiryFits"] for row in grid_snapshot(driver)["rows"])
            print("Vertical Options tiny expiry detail PASS (calendar days + years, 43px rows)", flush=True)
            assert len(best["rows"]) == 3, best
            assert all(row["annual"] for row in best["rows"]), best
            assert len({row["values"][2][-2:] for row in best["rows"]}) == 3, best
            assert all(
                row["background"] == ("rgb(234, 1, 1)" if row["values"][1] == "PUT"
                                      else "rgb(163, 230, 53)")
                and row["foreground"] == "rgb(0, 0, 0)"
                for row in best["rows"]
            ), best
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
            assert best["height"] == 3 * 43, best
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

            driver.find_element(By.XPATH, '//label[contains(., "ALL QUALIFIED")]').click()
            wait.until(lambda _: "60 QUALIFIED PAIRS" in driver.find_element(By.TAG_NAME, "body").text)
            # The count above already exists in BEST PER YEAR. Wait for the
            # new view's keyed export controls, then verify real CSV downloads.
            full_csv = download_csv(driver, wait, downloads, "vo_export_all_qualified",
                                    "SPY_vertical_all_qualified.csv")
            assert len(full_csv) == 60 and {row["Type"] for row in full_csv} == {"CALL", "PUT"}
            driver.find_element(By.CSS_SELECTOR, '.st-key-vo_all_pair_side [role="combobox"]').click()
            wait.until(lambda _: driver.find_element(By.XPATH, '//*[@role="option" and normalize-space(.)="CALL"]')).click()
            wait.until(lambda _: "30" in driver.find_element(
                By.CSS_SELECTOR, '.st-key-vo_export_filtered_qualified button').text)
            filtered_csv = download_csv(driver, wait, downloads, "vo_export_filtered_qualified",
                                        "SPY_vertical_filtered.csv")
            assert len(filtered_csv) == 30 and all(row["Type"] == "CALL" for row in filtered_csv)
            driver.find_element(By.CSS_SELECTOR, '.st-key-vo_all_pair_side [role="combobox"]').click()
            wait.until(lambda _: driver.find_element(By.XPATH, '//*[@role="option" and normalize-space(.)="CALL + PUT"]')).click()
            wait.until(lambda _: "60" in driver.find_element(
                By.CSS_SELECTOR, '.st-key-vo_export_filtered_qualified button').text)
            print("Vertical Options CSV downloads PASS (60 all / 30 CALL-filtered)")
            assert "SORT BY" in driver.find_element(By.TAG_NAME, "body").text
            assert "ORDER" in driver.find_element(By.TAG_NAME, "body").text
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
                  put=[...document.querySelectorAll('.row.put')].find(
                    row=>row.querySelectorAll('.cell')[1].textContent==='PUT');
                  if(put)break;
                }
                if(!put)return null;
                put.classList.add('annual');
                return {
                  background:getComputedStyle(put.querySelector('.cell')).backgroundColor,
                  foreground:getComputedStyle(put.querySelector('.cell')).color,
                  button:getComputedStyle(put.querySelector('.select')).backgroundColor
                };
            """))
            assert put_style == {
                "background": "rgb(234, 1, 1)",
                "foreground": "rgb(0, 0, 0)",
                "button": "rgb(234, 1, 1)",
            }, put_style
            assert {"90", "110"}.issubset(set(coverage["strikes"])), coverage

            driver.find_element(By.XPATH, '//label[contains(., "BEST PER YEAR")]').click()
            wait.until(lambda _: len(grid_snapshot(driver)["rows"]) == 3)
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
            assert "TOTAL ORDER DEBIT" in driver.find_element(By.TAG_NAME, "body").text
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
            # A saved BE toggle must not impose a range on a later scan whose
            # target is cleared. Exercise the transition through ticker paste.
            # Leave BE explicitly ON before clearing the target. Scroll the
            # toggle clear of Streamlit's sticky toolbar after the review.
            if not driver.find_element(By.CSS_SELECTOR, '.st-key-vo_be_filter_enabled input').is_selected():
                be_label = driver.find_element(By.CSS_SELECTOR, '.st-key-vo_be_filter_enabled label')
                driver.execute_script("arguments[0].scrollIntoView({block:'center'})", be_label)
                be_label.click()
            wait.until(lambda _: driver.find_element(By.CSS_SELECTOR, '.st-key-vo_be_filter_enabled input').is_selected())
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
            assert "BEST VERTICAL SPREADS BY YEAR" not in driver.find_element(By.TAG_NAME, "body").text
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
            wait.until(lambda _: "MAX COST no ceiling cost provided" in driver.find_element(By.TAG_NAME, "body").text)
            wait.until(lambda _: "TARGET BREAKEVEN no break even provided" in driver.find_element(By.TAG_NAME, "body").text)
            wait.until(lambda _: driver.find_element(
                By.CSS_SELECTOR, 'input[aria-label="Target Breakeven"]'
            ).get_attribute("value") == "no break even provided")
            assert driver.find_element(By.CSS_SELECTOR, 'input[aria-label="Max Cost"]').get_attribute("value") == "no ceiling cost provided"
            assert not driver.find_element(By.CSS_SELECTOR, '.st-key-vo_be_filter_enabled input').is_selected()
            if not driver.find_element(By.XPATH, '//label[contains(., "ALL QUALIFIED")]//input').is_selected():
                driver.find_element(By.XPATH, '//label[contains(., "ALL QUALIFIED")]').click()
            wait.until(lambda _: "ALL 60 QUALIFIED IN FILTER" in driver.find_element(By.TAG_NAME, "body").text)
            uncapped = download_csv(driver, wait, downloads, "vo_export_filtered_qualified",
                                    "MSFT_vertical_filtered.csv")
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
            wait.until(lambda _: "(CUSTOM)" in driver.find_element(By.TAG_NAME, "body").text)
            assert "MAX COST" in driver.find_element(By.TAG_NAME, "body").text
            assert form.find_element(By.CSS_SELECTOR, 'input[aria-label="Max Cost"]').get_attribute("value") == "150"
            assert not any(item["event"] == "place" for item in events(event_path))

            # A manual target belongs to its ticker. Another ticker keeps the
            # no-filter default, and returning restores the manual override.
            ticker = driver.find_element(By.CSS_SELECTOR, 'input[aria-label="Ticker"]')
            ticker.send_keys(SELECT_ALL, "a")
            ticker.send_keys("QQQ", Keys.TAB)
            wait.until(lambda _: driver.find_element(By.CSS_SELECTOR, 'input[aria-label="Target Breakeven"]').get_attribute("value") == "no break even provided")
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
            wait.until(ready_scanner_form)
            ticker = driver.find_element(By.CSS_SELECTOR, 'input[aria-label="Ticker"]')
            ticker.send_keys("SPY", Keys.TAB)
            wait.until(lambda _: "CALL WALL $110.00" in driver.find_element(By.TAG_NAME, "body").text)
            wait.until(lambda _: driver.find_element(By.CSS_SELECTOR, 'input[aria-label="Target Breakeven"]').get_attribute("value") == "no break even provided")
            ticker = driver.find_element(By.CSS_SELECTOR, 'input[aria-label="Ticker"]')
            ticker.send_keys(SELECT_ALL, "a")
            ticker.send_keys("QQQ", Keys.TAB)
            wait.until(lambda _: "LAST $92.00" in driver.find_element(By.TAG_NAME, "body").text)
            wait.until(lambda _: driver.find_element(By.CSS_SELECTOR, 'input[aria-label="Target Breakeven"]').get_attribute("value") == "no break even provided")
            assert len([item for item in events(event_path) if item["event"] == "chain"]) == chain_before_restart
            assert not any(item["event"] == "place" for item in events(event_path))
            print("GEX browser cache PASS after real process restart; optional breakeven defaults PASS", flush=True)
            # The production renderer must show useful partial results, keep
            # checked identities and finish without fetching completed chains.
            form = wait.until(ready_scanner_form)
            form.find_element(By.XPATH, './/button[contains(., "SCAN ALL YEARS")]').click()
            wait.until(lambda _: "PARTIAL ALL-YEAR SCAN // 1/3" in driver.find_element(By.TAG_NAME, "body").text)
            wait.until(lambda _: bool(grid_snapshot(driver)["rows"]))
            in_grid(driver, lambda: driver.find_element(By.CSS_SELECTOR, '.row[data-index="0"] .select').click())
            wait.until(lambda _: checked_count(driver) == 1)
            resume = driver.find_element(By.XPATH, '//button[contains(., "CONTINUE ALL YEARS")]')
            driver.execute_script("arguments[0].scrollIntoView({block:'center'})", resume)
            resume.click()
            wait.until(lambda _: len([e for e in events(event_path) if e["event"] == "chain"]) == chain_before_restart + 2)
            wait.until(lambda _: "PARTIAL ALL-YEAR SCAN // 2/3" in driver.find_element(By.TAG_NAME, "body").text)
            wait.until(lambda _: checked_count(driver) == 1)
            driver.find_element(By.CSS_SELECTOR, '.st-key-vo_auto_finish label').click()
            wait.until(lambda _: "PARTIAL ALL-YEAR SCAN" not in driver.find_element(By.TAG_NAME, "body").text)
            wait.until(lambda _: checked_count(driver) == 1)
            calls = [e for e in events(event_path) if e["event"] == "chain"][chain_before_restart:]
            assert len(calls) == len({(e["year"], e["month"], e["day"]) for e in calls}) == 3
            assert "LAST SCAN PASS" in driver.find_element(By.TAG_NAME, "body").text
            assert not any(e["event"] == "place" for e in events(event_path))
            print("Vertical Options partial results and continuation PASS (3 unique chains, no orders)", flush=True)

            # 101 strikes per side => 10,100 qualifying spreads for ONE expiry.
            # Jump past the old cutoff, select there, return to page one and
            # download the entire dataset rather than the 200-row screen page.
            ticker = driver.find_element(By.CSS_SELECTOR, 'input[aria-label="Ticker"]')
            ticker.send_keys(SELECT_ALL, "a")
            ticker.send_keys("MASSIVE", Keys.TAB)
            form = wait.until(ready_scanner_form)
            target = form.find_element(By.CSS_SELECTOR, 'input[aria-label="Target Breakeven"]')
            assert target.get_attribute("value") == "no break even provided"
            assert form.find_element(By.CSS_SELECTOR, 'input[aria-label="Max Cost"]').get_attribute("value") == "no ceiling cost provided"
            form.find_element(By.XPATH, './/button[contains(., "SCAN ALL YEARS")]').click()
            wait.until(lambda _: "10,100 QUALIFIED PAIRS" in driver.find_element(By.TAG_NAME, "body").text)
            wait.until(lambda _: "PARTIAL ALL-YEAR SCAN" not in driver.find_element(By.TAG_NAME, "body").text)
            # A fresh session starts with every qualifying spread visible.
            assert driver.find_element(By.XPATH, '//label[contains(., "ALL QUALIFIED")]//input').is_selected()
            wait.until(lambda _: driver.find_element(By.XPATH, '//label[contains(., "ALL QUALIFIED")]//input').is_selected())
            if driver.find_element(By.CSS_SELECTOR, '.st-key-vo_be_filter_enabled input').is_selected():
                driver.find_element(By.CSS_SELECTOR, '.st-key-vo_be_filter_enabled label').click()
            wait.until(lambda _: "ALL 10,100 QUALIFIED IN FILTER" in driver.find_element(By.TAG_NAME, "body").text)
            assert "SHORTLIST" not in driver.find_element(By.TAG_NAME, "body").text
            assert "SAFETY MODE" not in driver.find_element(By.TAG_NAME, "body").text
            page = driver.find_element(By.CSS_SELECTOR, 'input[aria-label^="PAGE /"]')
            page.send_keys(SELECT_ALL, "a")
            page.send_keys("51", Keys.TAB)
            wait.until(lambda _: "PAGE 51/51" in driver.find_element(By.TAG_NAME, "body").text)
            wait.until(lambda _: "100 DISPLAYED" in driver.find_element(By.TAG_NAME, "body").text)
            in_grid(driver, lambda: driver.find_element(By.CSS_SELECTOR, '.row[data-index="0"] .select').click())
            wait.until(lambda _: checked_count(driver) == 1)
            page = driver.find_element(By.CSS_SELECTOR, 'input[aria-label^="PAGE /"]')
            page.send_keys(SELECT_ALL, "a")
            page.send_keys("1", Keys.TAB)
            wait.until(lambda _: "PAGE 1/51" in driver.find_element(By.TAG_NAME, "body").text)
            wait.until(lambda _: checked_count(driver) == 1)
            whole = download_csv(driver, wait, downloads, "vo_export_all_qualified",
                                 "MASSIVE_vertical_all_qualified.csv")
            assert len(whole) == 10100
            assert sum(row["Type"] == "CALL" for row in whole) == 5050
            assert sum(row["Type"] == "PUT" for row in whole) == 5050
            assert len({(r["Type"], r["Buy Strike"], r["Sell Strike"]) for r in whole}) == 10100
            assert all(not row["Target Breakeven ($)"] and not row["Max Cost Filter ($)"]
                       for row in whole), whole[:2]
            # A custom component's checkbox event can arrive during a full-app
            # rerun. The backend count must update without a fragment-scope error.
            toggle_put_column_filter(driver)
            wait.until(lambda _: "ALL 5,050 QUALIFIED IN FILTER" in driver.find_element(By.TAG_NAME, "body").text)
            assert "EXPORT FILTERED CSV // 5,050" in driver.find_element(By.TAG_NAME, "body").text
            assert "StreamlitInvalidLayoutContextError" not in driver.find_element(By.TAG_NAME, "body").text
            toggle_put_column_filter(driver)
            wait.until(lambda _: "ALL 10,100 QUALIFIED IN FILTER" in driver.find_element(By.TAG_NAME, "body").text)
            late_row = whole[-1]
            late_identity = (late_row["Type"], late_row["Buy Strike"], late_row["Sell Strike"])
            assert late_identity not in {
                (row["Type"], row["Buy Strike"], row["Sell Strike"])
                for row in whole[:200]
            }
            # The row came from the far end of the 10,100-result export. Header
            # filters must search the stored universe, not the 200-row page.
            set_exact_grid_filter(driver, "buy", "BUY STRIKE", float(late_row["Buy Strike"]))
            wait.until(lambda _: "ALL 100 QUALIFIED IN FILTER" in driver.find_element(By.TAG_NAME, "body").text)
            set_exact_grid_filter(driver, "sell", "SELL STRIKE", float(late_row["Sell Strike"]))
            wait.until(lambda _: "ALL 1 QUALIFIED IN FILTER" in driver.find_element(By.TAG_NAME, "body").text)
            wait.until(lambda _: "PAGE 1/1" in driver.find_element(By.TAG_NAME, "body").text)
            isolated = wait.until(lambda _: grid_snapshot(driver)["rows"] if len(grid_snapshot(driver)["rows"]) == 1 else False)
            assert (isolated[0]["values"][1], float(isolated[0]["values"][3]),
                    float(isolated[0]["values"][4])) == (
                late_row["Type"], float(late_row["Buy Strike"]), float(late_row["Sell Strike"])
            ), (isolated, late_row)
            isolated_csv = download_csv(driver, wait, downloads, "vo_export_filtered_qualified",
                                        "MASSIVE_vertical_filtered.csv")
            identifying_columns = (
                "Ticker", "Type", "Expiration", "Buy Strike", "Sell Strike",
                "Cost Per Contract ($)", "Breakeven ($)", "Risk Reward (to 1)",
            )
            assert len(isolated_csv) == 1 and all(
                isolated_csv[0][column] == late_row[column] for column in identifying_columns
            ), isolated_csv
            assert not any(e["event"] == "place" for e in events(event_path))
            print("Full-universe browser PASS: 10,100 pairs, last page, cross-page selection, full CSV and late-row column filtering", flush=True)
            print("VERTICAL OPTIONS SCANNER BROWSER: PASS")
        except Exception:
            print(server_log.read_text(encoding="utf-8")[-5000:], file=sys.stderr)
            if driver is not None:
                print(driver.find_element(By.TAG_NAME, "body").text[-6000:], file=sys.stderr)
                print([(item.get_attribute("aria-label"), item.get_attribute("value"))
                       for item in driver.find_elements(By.CSS_SELECTOR, 'input[aria-label]')], file=sys.stderr)
            raise
        finally:
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
