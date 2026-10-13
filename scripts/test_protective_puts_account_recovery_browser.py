"""Protective Puts: recover missing account widget key without crashing.

Exercises the real production portfolio-hedge renderer inside Streamlit. No
broker credentials, live quotes, previews, or orders are used.
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import urllib.request

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait

ROOT = Path(__file__).resolve().parents[1]
PORT = 18685
BASE = f"http://127.0.0.1:{PORT}"

FIXTURE = '''
import streamlit as st
import src.protective_puts_portfolio_ui as ui

st.set_page_config(layout="wide")
if not st.session_state.get("_test_seeded"):
    st.session_state["_test_seeded"] = True
    st.session_state["etrade_accounts"] = [
        {"accountName":"Other","accountIdKey":"test-other"},
        {"accountName":"Raj Singh","accountIdKey":"test-raj"},
    ]
    st.session_state["etrade_holdings"] = {}
    # Simulate stale migration marker but Streamlit-evicted selectbox key.
    st.session_state["_pp_hedge_raj_default_v2"] = True
    st.session_state.pop("pp_hedge_account", None)

if st.checkbox("SIMULATE HOLDINGS LOAD FAILURE", key="test_broken_holdings"):
    def broken_holdings(*args, **kwargs):
        raise KeyError("simulated malformed holdings snapshot")
    ui._holdings = broken_holdings

ui.render_portfolio_hedge(
    object(), lambda:None,
    get_expirations=lambda *args: ([], "fixture"),
    get_chain=lambda *args: ([], None, "fixture"),
    market_today=lambda: __import__("datetime").date(2026,10,11),
    live_etrade=lambda _:False,
    money=lambda value: "$" + format(float(value), ",.2f"),
    metric_markup=lambda items: "<div></div>",
)
'''


def wait_health(server) -> None:
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        if server.poll() is not None:
            raise AssertionError(f"Fixture exited with code {server.returncode}")
        try:
            with urllib.request.urlopen(BASE+"/_stcore/health",timeout=2) as response:
                if response.status==200:
                    return
        except Exception:
            time.sleep(.4)
    raise AssertionError("Fixture failed to start")


def run() -> None:
    with tempfile.TemporaryDirectory(prefix="pp_account_recovery_") as temp:
        app = Path(temp)/"app.py"
        app.write_text(FIXTURE, encoding="utf-8")
        env = os.environ.copy()
        env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH","")
        process = subprocess.Popen([
            sys.executable,"-m","streamlit","run",str(app),
            "--server.port",str(PORT),"--server.headless","true",
            "--browser.gatherUsageStats","false",
        ],cwd=ROOT,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.STDOUT)
        driver=None
        try:
            wait_health(process)
            options=Options()
            options.add_argument("--headless=new")
            options.add_argument("--no-sandbox")
            options.add_argument("--disable-dev-shm-usage")
            options.add_argument("--window-size=1400,850")
            driver=webdriver.Chrome(options=options)
            wait=WebDriverWait(driver,55)
            driver.get(BASE)
            wait.until(lambda _: "PUT TICKER" in driver.find_element(By.TAG_NAME,"body").text)
            body=driver.find_element(By.TAG_NAME,"body").text
            assert "KeyError" not in body, body
            assert "ENTER PUT TICKERS" in body, body

            # The recovery warning belongs inside the collapsed investments
            # expander. Open it first so Selenium checks visible content.
            expander = driver.find_element(
                By.CSS_SELECTOR, '[data-testid="stExpander"] summary'
            )
            expander.click()
            trigger = wait.until(lambda _: driver.find_element(
                By.XPATH, "//label[contains(., 'SIMULATE HOLDINGS LOAD FAILURE')]"
            ))
            trigger.click()
            wait.until(lambda _: "Holdings are temporarily unavailable" in
                       driver.find_element(By.TAG_NAME,"body").text)
            body=driver.find_element(By.TAG_NAME,"body").text
            assert "PUT TICKER" in body, body
            assert "KeyError:" not in body, body
            assert "SUBMIT LIVE" not in body, body
            print("PROTECTIVE PUTS ACCOUNT RECOVERY BROWSER: PASS")
        finally:
            if driver:
                driver.quit()
            process.terminate()
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=4)


HOLDINGS_FIXTURE = '''
import os, time
import streamlit as st
import src.protective_puts_portfolio_ui as ui
from src.etrade_data_cache import OfflineETradeClient, _offline_store

st.set_page_config(layout="wide")
accounts = [
    {"accountName":"Other","accountIdKey":"cache-other"},
    {"accountName":"Raj Singh","accountIdKey":"cache-raj"},
]
def position(symbol, qty):
    return [{"Product":{"symbol":symbol,"securityType":"EQ"},
             "quantity":qty,"lastTrade":100,"marketValue":qty*100,
             "totalCost":qty*90,"totalGain":qty*10,"totalGainPct":11.11}]

class Client:
    is_offline = False
    def __init__(self):
        self.calls = []
        self.loaded = {a["accountIdKey"]:time.time() for a in accounts}
    def list_accounts(self):
        self.calls.append("accounts")
        return accounts
    def cache_age(self, kind, key):
        return time.time() - self.loaded[key]
    def get_portfolio(self, key, force_refresh=False):
        self.calls.append("portfolio:"+str(force_refresh))
        if st.session_state.get("test_fail"):
            raise RuntimeError("simulated outage")
        self.loaded[key] = time.time()
        if st.session_state.get("test_empty"):
            return []
        return position("AAA" if key=="cache-raj" else "BBB", 25)
    def get_balance(self, key, force_refresh=False):
        self.calls.append("balance:"+str(force_refresh))
        return {"totalAccountValue":3500}
@st.cache_resource
def client():
    return Client()

mode = os.environ.get("PP_CACHE_TEST_MODE", "live")
broker = client() if mode=="live" else None
if mode=="live" and not st.session_state.get("test_seeded"):
    st.session_state["test_seeded"] = True
    st.session_state["etrade_accounts"] = accounts
    st.session_state["etrade_holdings"] = {
        "cache-raj":position("AAA",10),"cache-other":position("BBB",5),
    }
    st.session_state["etrade_balances"] = {
        a["accountIdKey"]:{"totalAccountValue":1500} for a in accounts
    }
if mode=="vault":
    if not st.session_state.get("test_seeded"):
        st.session_state["test_seeded"] = True
        _offline_store("pp-cache-fixture", "accounts", "all", accounts)
        for a in accounts:
            key=a["accountIdKey"]
            _offline_store("pp-cache-fixture", "portfolio", key,
                           position("AAA" if key=="cache-raj" else "BBB", 12))
            _offline_store("pp-cache-fixture", "balance", key, {"totalAccountValue":1500})
    broker=OfflineETradeClient("pp-cache-fixture")

if mode=="live":
    st.checkbox("SIMULATE REFRESH FAILURE", key="test_fail")
    st.checkbox("CONFIRM EMPTY ACCOUNT", key="test_empty")
    if st.button("PUBLISH SHARED HOLDINGS CHANGE"):
        st.session_state["etrade_holdings"]["cache-raj"] = position("AAA",40)
        broker.loaded["cache-raj"]=time.time()
    if st.button("REORDER ACCOUNTS"):
        st.session_state["etrade_accounts"] = list(reversed(st.session_state["etrade_accounts"]))
ui._risk_long_term_tickers=lambda _:set()
ui.render_portfolio_hedge(
    broker, lambda:None,
    get_expirations=lambda *args:([],"fixture"),
    get_chain=lambda *args:([],None,"fixture"),
    market_today=lambda:__import__("datetime").date(2026,10,12),
    live_etrade=lambda c:c is not None and not c.is_offline,
    money=lambda value:"$"+format(float(value),",.2f"),
    metric_markup=lambda items:"<div></div>",
)
st.write("READ CALLS: "+str(len(broker.calls) if mode=="live" else 0))
if mode=="live":
    st.write("FORCED PORTFOLIO: "+str(broker.calls.count("portfolio:True")))
'''


def run_holdings_cache() -> None:
    """Real browser + fresh server: no credentials and no live broker actions."""
    with tempfile.TemporaryDirectory(prefix="pp_holdings_cache_") as temp:
        app = Path(temp)/"app.py"
        app.write_text(HOLDINGS_FIXTURE, encoding="utf-8")
        env = os.environ.copy()
        env["PYTHONPATH"] = str(ROOT)+os.pathsep+env.get("PYTHONPATH", "")
        process = None
        driver = None
        def start(mode):
            env["PP_CACHE_TEST_MODE"] = mode
            server = subprocess.Popen([
                sys.executable,"-m","streamlit","run",str(app),
                "--server.port",str(PORT),"--server.headless","true",
                "--browser.gatherUsageStats","false",
            ],cwd=ROOT,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.STDOUT)
            wait_health(server)
            return server
        def stop(server):
            if server:
                server.terminate()
                server.wait(timeout=8)
        try:
            process = start("live")
            options=Options()
            for arg in ("--headless=new", "--no-sandbox", "--disable-dev-shm-usage", "--window-size=1400,1000"):
                options.add_argument(arg)
            driver=webdriver.Chrome(options=options)
            wait=WebDriverWait(driver,45)
            def body():
                return driver.find_element(By.TAG_NAME,"body").text
            def button(text):
                return driver.find_element(By.XPATH, "//button[normalize-space(.)='"+text+"']")
            def open_holdings():
                wait.until(lambda _: "PUT TICKER" in body())
                summary=driver.find_element(By.CSS_SELECTOR,'[data-testid="stExpander"] summary')
                if summary.get_attribute("aria-expanded") != "true":
                    summary.click()
                wait.until(lambda _: "SAVED HOLDINGS" in body())
            def quantity(text):
                return any(el.text.strip()==text for el in driver.find_elements(By.CSS_SELECTOR,".pp-hedge-cell"))
            driver.get(BASE)
            open_holdings()
            wait.until(lambda _: "AAA" in body() and quantity("10.00"))
            wait.until(lambda _: "READ CALLS: 0" in body())
            driver.find_element(By.XPATH,"//label[contains(.,'HEDGE AAA')]").click()
            wait.until(lambda _: "1 / 1 CHECKED" in body())
            assert "READ CALLS: 0" in body(),body()
            button("REFRESH HOLDINGS").click()
            wait.until(lambda _: "FORCED PORTFOLIO: 1" in body() and quantity("25.00"))
            assert "1 / 1 CHECKED" in body(),body()
            button("PUBLISH SHARED HOLDINGS CHANGE").click()
            wait.until(lambda _: quantity("40.00"))
            assert "FORCED PORTFOLIO: 1" in body(),body()
            button("REORDER ACCOUNTS").click()
            wait.until(lambda _: quantity("40.00"))
            assert "1 / 1 CHECKED" in body(),body()
            driver.find_element(By.XPATH,"//label[contains(.,'SIMULATE REFRESH FAILURE')]").click()
            button("REFRESH HOLDINGS").click()
            wait.until(lambda _: "keeping saved holdings" in body())
            assert quantity("40.00"),body()

            # Kill only this test fixture. The new server has no E*TRADE client
            # or process cache, so all quantities must restore from the browser.
            stop(process)
            process=start("none")
            driver.refresh()
            open_holdings()
            wait.until(lambda _: "AAA" in body() and quantity("40.00"))
            assert "1 / 1 CHECKED" in body(),body()
            assert not button("REFRESH HOLDINGS").is_enabled()
            assert "READ CALLS: 0" in body(),body()
            driver.find_element(By.CSS_SELECTOR,'.st-key-pp_hedge_account [role="combobox"]').click()
            wait.until(lambda _: driver.find_element(By.XPATH,"//*[@role='option' and contains(.,'Other')]" )).click()
            wait.until(lambda _: "BBB" in body() and quantity("5.00"))
            assert "AAA" not in body(),body()

            # A saved empty book is authoritative after a process restart.
            stop(process)
            process=start("live")
            driver.refresh()
            open_holdings()
            driver.find_element(By.CSS_SELECTOR,'.st-key-pp_hedge_account [role="combobox"]').click()
            wait.until(lambda _: driver.find_element(By.XPATH,"//*[@role='option' and contains(.,'Raj Singh')]" )).click()
            driver.find_element(By.XPATH,"//label[contains(.,'CONFIRM EMPTY ACCOUNT')]").click()
            button("REFRESH HOLDINGS").click()
            wait.until(lambda _: "This account has no positions" in body())
            stop(process)
            process=start("none")
            driver.refresh()
            open_holdings()
            wait.until(lambda _: "This account has no positions" in body())
            assert "AAA" not in body(),body()

            # The existing read-only offline client can seed a new snapshot,
            # including newer vault data than the persisted empty book.
            stop(process)
            process=start("vault")
            driver.refresh()
            open_holdings()
            wait.until(lambda _: "AAA" in body() and quantity("12.00"))
            assert not button("REFRESH HOLDINGS").is_enabled()
            assert "READ CALLS: 0" in body(),body()
            assert "CONNECT E*TRADE TO ACCESS" not in body().upper(),body()
            assert "SEND LIVE" not in body(),body()
            print("PROTECTIVE PUTS DURABLE HOLDINGS BROWSER: PASS")
        finally:
            if driver:
                driver.quit()
            stop(process)


if __name__ == "__main__":
    run()
    run_holdings_cache()
