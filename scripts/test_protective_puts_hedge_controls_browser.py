"""Browser check for Protective Puts portfolio hedge input/stepper contrast.

Runs a standalone Streamlit fixture with the production feature-owned CSS,
without connecting to E*TRADE or submitting orders. Checks actual computed
styles in the browser, not just whether CSS source text exists.
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
PORT = 18681
BASE = f"http://127.0.0.1:{PORT}"
FIXTURE = """
import streamlit as st
from src.protective_puts_portfolio_ui import _CSS

st.set_page_config(page_title="Protective Put Portfolio Controls")
with st.container(key="pp_portfolio_hedge", gap="small"):
    st.html(_CSS)
    a, b, c = st.columns([2.1, 1, 1], gap="small")
    with a:
        st.text_input("PUT ETF TICKERS", value="SPY", key="pp_hedge_proxies")
    with b:
        st.number_input("PRIORITY DTE", min_value=1, max_value=730,
                        value=61, step=1, key="pp_hedge_dte")
    with c:
        st.number_input("DTE +/- DAYS", min_value=0, max_value=365,
                        value=21, step=1, key="pp_hedge_band")
    x, y, z, w = st.columns(4, gap="small")
    with x:
        st.number_input("ETF DROP %", min_value=1.0, max_value=90.0,
                        value=15.0, key="pp_hedge_stress")
    with y:
        st.number_input("ASSUMED BASKET BETA", min_value=0.1, max_value=3.0,
                        value=1.0, key="pp_hedge_beta")
    with z:
        st.number_input("TARGET STRESS LOSS %", min_value=0.0, max_value=50.0,
                        value=0.0, key="pp_hedge_target")
    with w:
        st.number_input("MAX PREMIUM %", min_value=0.1, max_value=100.0,
                        value=10.0, key="pp_hedge_budget")
    st.button("SCAN PORTFOLIO PUT HEDGES", type="primary",
              width="stretch", key="pp_hedge_scan")
"""


def launch(tmp: Path) -> subprocess.Popen:
    app = tmp / "pp_hedge_controls_fixture.py"
    app.write_text(FIXTURE, encoding="utf-8")
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(ROOT) + os.pathsep + environment.get("PYTHONPATH", "")
    return subprocess.Popen(
        [sys.executable, "-m", "streamlit", "run", str(app),
         "--server.port", str(PORT), "--server.headless", "true",
         "--browser.gatherUsageStats", "false"],
        cwd=ROOT, env=environment, stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
    )


def style(driver: webdriver.Chrome, selector: str) -> dict | None:
    return driver.execute_script(
        """
        const el=document.querySelector(arguments[0]);
        if (!el) return null;
        const s=getComputedStyle(el);
        return {
            color:s.color, fill:s.webkitTextFillColor, background:s.backgroundColor,
            height:parseFloat(s.height), border:s.borderTopColor, text:el.textContent
        };
        """, selector,
    )


def test() -> None:
    with tempfile.TemporaryDirectory(prefix="pp_hedge_theme_") as directory:
        server = launch(Path(directory))
        driver = None
        try:
            until = time.monotonic() + 45
            while time.monotonic() < until:
                if server.poll() is not None:
                    raise AssertionError("Fixture Streamlit process exited unexpectedly")
                try:
                    with urllib.request.urlopen(BASE + "/_stcore/health", timeout=2) as response:
                        if response.status == 200:
                            break
                except Exception:
                    time.sleep(0.5)
            else:
                raise AssertionError("Fixture Streamlit app failed to start")
            options = Options()
            options.add_argument("--headless=new")
            options.add_argument("--no-sandbox")
            options.add_argument("--disable-dev-shm-usage")
            options.add_argument("--window-size=1440,900")
            driver = webdriver.Chrome(options=options)
            driver.get(BASE)
            wait = WebDriverWait(driver, 65)
            prefix = ".st-key-pp_portfolio_hedge "
            scan = prefix + ".st-key-pp_hedge_scan button"
            wait.until(lambda _: style(driver, scan) is not None)
            target = style(driver, scan)
            assert target["background"] == "rgb(251, 139, 30)", target
            assert target["color"] == "rgb(0, 0, 0)", target
            assert target["height"] == 40, target
            text = style(driver, prefix + '[data-testid="stTextInputField"]')
            assert text["color"] == "rgb(251, 139, 30)", text
            input_root = style(driver, prefix + '[data-testid="stTextInputRootElement"]')
            assert input_root["background"] == "rgb(8, 8, 8)", input_root
            assert input_root["height"] == 40, input_root
            field = style(driver, prefix + '[data-testid="stNumberInputField"]')
            assert field["color"] == "rgb(251, 139, 30)", field
            number_root = style(driver, prefix + '[data-testid="stNumberInputContainer"]')
            assert number_root["background"] == "rgb(8, 8, 8)", number_root
            assert number_root["height"] == 40, number_root
            minus = prefix + ".st-key-pp_hedge_dte [data-testid='stNumberInputStepDown']"
            # Native number steppers animate color for 300 ms; wait until
            # their final high-contrast style settles before asserting.
            wait.until(
                lambda _: (style(driver, minus) or {}).get("color")
                == "rgb(251, 139, 30)"
            )
            step = style(driver, minus)
            assert step["background"] == "rgb(21, 16, 10)", step
            disabled = prefix + ".st-key-pp_hedge_target [data-testid='stNumberInputStepDown']"
            step_disabled = style(driver, disabled)
            assert step_disabled is not None, "Target loss decrement missing"
            assert step_disabled["color"] == "rgb(170, 119, 67)", step_disabled
            # All seven editable widgets remain visible and usable at desktop width.
            count = len(driver.find_elements(By.CSS_SELECTOR,
                                              prefix + '[data-testid="stNumberInput"]'))
            assert count == 6, count
            print("PROTECTIVE PUTS HEDGE CONTROLS THEME: PASS")
        finally:
            if driver is not None:
                driver.quit()
            server.terminate()
            try:
                server.wait(timeout=8)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=4)


if __name__ == "__main__":
    test()
