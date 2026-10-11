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

if st.query_params.get("broken") == "1":
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

            # A holdings-loader failure must not hide ticker-only scanning.
            driver.get(BASE+"?broken=1")
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


if __name__ == "__main__":
    run()
