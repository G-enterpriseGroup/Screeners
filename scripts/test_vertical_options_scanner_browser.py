"""Browser regression for the production Vertical Options all-year scanner.

Runs the real Streamlit renderer and virtual grid against deterministic,
read-only E*TRADE-shaped quotes. No brokerage login or order is made.
"""
from __future__ import annotations

import csv
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

FIXTURE = r'''
import json
import os
from datetime import date
from pathlib import Path

import streamlit as st
from src.vertical_options_ui import render_vertical_options

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
            "lastTrade": 100.0, "bid": 99.99, "ask": 100.01,
        }]}}

    def get_option_expirations(self, symbol):
        record("expirations", symbol=symbol)
        return {"OptionExpireDateResponse": {"ExpirationDate": [
            {"year": day.year, "month": day.month, "day": day.day}
            for day in EXPIRIES
        ]}}

    def get_option_chain(self, symbol, year, month, day,
                         no_of_strikes=100, chain_type="CALLPUT"):
        record("chain", symbol=symbol, year=year, month=month, day=day,
               no_of_strikes=no_of_strikes, chain_type=chain_type)
        expiry_code = f"{year % 100:02d}{month:02d}{day:02d}"
        pairs = []
        for strike in (90, 95, 100, 105, 110):
            call_last = (115 - strike) / 5 + 1
            put_last = (strike - 85) / 5 + 1
            def option(side, last):
                return {
                    "strikePrice": strike, "lastPrice": last,
                    # Asymmetric quotes: natural ASK differs from Last and MID.
                    "bid": last - .02,
                    "ask": last + .02 + .002 * (strike - 90),
                    "osiKey": f"{symbol}{expiry_code}{side}{strike}",
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
# Simulate the exact stale manually entered debit from a pre-fix session.
st.session_state.setdefault("vo_limit_price", 4.35)
render_vertical_options(StubETrade(), lambda: None)
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

    try:
        WebDriverWait(driver, 25).until(enter)
        return callback()
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
            values:[...row.querySelectorAll('.cell')].map(cell=>cell.textContent),
            selected:row.querySelector('.select').getAttribute('aria-pressed'),
            background:getComputedStyle(row.querySelector('.cell')).backgroundColor,
            foreground:getComputedStyle(row.querySelector('.cell')).color,
            emphasis:[7,8,10].map(i=>({
              weight:getComputedStyle(row.querySelectorAll('.cell')[i]).fontWeight,
              decoration:getComputedStyle(row.querySelectorAll('.cell')[i]).textDecorationLine,
            })),
            href:row.querySelector('.open[href]')?.href||'',
            target:row.querySelector('.open[href]')?.target||'',
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
          const match=caption.textContent.match(/(\\d+) CHECKED \\/\\/ LIME GREEN/);
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
    wait.until(ready).click()
    wait.until(lambda _: target.exists() and not list(folder.glob("*.crdownload")))
    with target.open(encoding="utf-8", newline="") as downloaded:
        return list(csv.DictReader(downloaded))


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

            expected_labels = ["Ticker", "Target Breakeven", "Max Cost"]
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
                    trackOutlined: !!track && getComputedStyle(track).outlineStyle === "solid",
                };
            """)
            assert pricing_layout is not None, "Pricing controls are missing"
            assert pricing_layout["sameToolbarRow"], pricing_layout
            assert 0 <= pricing_layout["toolbarToForm"] < 32, pricing_layout
            assert 0 <= pricing_layout["gap"] < 64, pricing_layout
            assert pricing_layout["toggleWidth"] >= 24, pricing_layout
            assert pricing_layout["trackOutlined"], pricing_layout
            print("Vertical Options compact scan toolbar PASS", pricing_layout)
            # Flip switch saves immediately and must rehydrate after a hard reload.
            flip = wait.until(lambda _: driver.find_element(
                By.CSS_SELECTOR, ".st-key-vo_smart_fill label"
            ))
            flip.click()
            wait.until(lambda _: pricing_preference_in_browser(driver) == "SMART FILL")
            driver.refresh()
            form = wait.until(ready_scanner_form)
            wait.until(lambda _: driver.execute_script(
                "const i=document.querySelector('.st-key-vo_smart_fill input');"
                "return i && i.checked;"
            ))
            driver.find_element(By.CSS_SELECTOR, ".st-key-vo_smart_fill label").click()
            wait.until(lambda _: pricing_preference_in_browser(driver) == "NORMAL")
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
            form.find_element(By.CSS_SELECTOR, 'input[aria-label="Ticker"]').send_keys("SPY")
            form.find_element(By.CSS_SELECTOR, 'input[aria-label="Target Breakeven"]').send_keys("100")
            form.find_element(By.CSS_SELECTOR, 'input[aria-label="Max Cost"]').send_keys("500")
            form.find_element(By.XPATH, './/button[contains(., "SCAN ALL YEARS")]').click()

            wait.until(lambda _: checked_count(driver) == 0)
            assert "30" in driver.find_element(By.TAG_NAME, "body").text, "Expected 30 parsed option contracts"
            chain_calls = [item for item in events(event_path) if item["event"] == "chain"]
            assert len(chain_calls) == 3, chain_calls
            assert len({item["year"] for item in chain_calls}) == 3, chain_calls
            assert all(item["no_of_strikes"] is None and item["chain_type"] == "CALLPUT"
                       for item in chain_calls), chain_calls

            best = grid_snapshot(driver)
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
            assert driver.find_element(
                By.XPATH, '//button[contains(., "SEND LIVE TO")]',
            ).get_attribute("disabled") is not None, "SEND must need affirmative confirmation"
            assert not any(item["event"] == "place" for item in events(event_path)), (
                "Preview alone placed an order"
            )
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
            assert len([item for item in events(event_path) if item["event"] == "chain"]) == chain_before_list
            assert not any(item["event"] == "place" for item in events(event_path))
            # Loading a list resets the prior ticker's result without scanning.
            assert driver.find_elements(By.CSS_SELECTOR, '[data-testid="stSelectbox"]')
            assert "BEST VERTICAL SPREADS BY YEAR" not in driver.find_element(By.TAG_NAME, "body").text
            # Empty inputs use the current underlying Last and impose no cost cap.
            form = wait.until(ready_scanner_form)
            for label in ("Target Breakeven", "Max Cost"):
                field = form.find_element(By.CSS_SELECTOR, f'input[aria-label="{label}"]')
                field.click()
                field.send_keys(Keys.CONTROL, "a")
                field.send_keys(Keys.BACKSPACE)
                assert field.get_attribute("value") == "", (label, field.get_attribute("value"))
            form.find_element(By.XPATH, './/button[contains(., "SCAN ALL YEARS")]').click()
            wait.until(lambda _: "MAX COST NO CEILING" in driver.find_element(By.TAG_NAME, "body").text)
            wait.until(lambda _: "(SPOT DEFAULT)" in driver.find_element(By.TAG_NAME, "body").text)
            wait.until(lambda _: driver.find_element(
                By.CSS_SELECTOR, 'input[aria-label="Target Breakeven"]'
            ).get_attribute("value") == "100.0")
            assert grid_snapshot(driver)["rows"]
            assert not any(item["event"] == "place" for item in events(event_path))

            # The seeded spot target can be overwritten with an explicit target
            # and the cost ceiling restored without changing the one-ticker flow.
            form = wait.until(ready_scanner_form)
            breakeven_input = form.find_element(By.CSS_SELECTOR, 'input[aria-label="Target Breakeven"]')
            breakeven_input.click()
            breakeven_input.send_keys(Keys.CONTROL, "a")
            breakeven_input.send_keys("103")
            form.find_element(By.CSS_SELECTOR, 'input[aria-label="Max Cost"]').send_keys("150")
            form.find_element(By.XPATH, './/button[contains(., "SCAN ALL YEARS")]').click()
            wait.until(lambda _: "(CUSTOM)" in driver.find_element(By.TAG_NAME, "body").text)
            assert "MAX COST" in driver.find_element(By.TAG_NAME, "body").text
            assert form.find_element(By.CSS_SELECTOR, 'input[aria-label="Max Cost"]').get_attribute("value") == "150"
            assert not any(item["event"] == "place" for item in events(event_path))

            print("VERTICAL OPTIONS SCANNER BROWSER: PASS")
        except Exception:
            print(server_log.read_text(encoding="utf-8")[-5000:], file=sys.stderr)
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
