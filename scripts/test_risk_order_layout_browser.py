#!/usr/bin/env python3
"""Browser geometry regression for Risk Sizing order Parts 3 and 4.

Runs the real v7 -> v10 -> v9 -> v2 Risk route with a simulated broker.
It never previews or submits an E*TRADE order.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import urlopen

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options

from scripts.test_risk_production_ui import FIXTURE


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = ROOT / ".tmp_risk_order_layout_fixture.py"
PORT = 8769


def _rect(driver, element):
    return driver.execute_script(
        """
        const r = arguments[0].getBoundingClientRect();
        return {top:r.top,bottom:r.bottom,left:r.left,right:r.right,width:r.width,height:r.height};
        """,
        element,
    )


def main() -> int:
    fixture = FIXTURE.replace(
        "st.session_state['etrade_accounts']=",
        "st.session_state['risk_tactical_sleeve_pct']=10.0\n"
        "st.session_state['risk_liquid_balance']=100000.0\n"
        "st.session_state['risk_capital_source']='USE LIQUID BALANCE ENTERED'\n"
        "st.session_state['etrade_accounts']=",
    )
    FIXTURE_PATH.write_text(fixture, encoding="utf-8")

    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "streamlit",
            "run",
            str(FIXTURE_PATH),
            "--server.headless=true",
            f"--server.port={PORT}",
            "--server.address=127.0.0.1",
        ],
        cwd=ROOT,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    driver = None
    try:
        deadline = time.time() + 45
        health = f"http://127.0.0.1:{PORT}/_stcore/health"
        while time.time() < deadline:
            try:
                with urlopen(health, timeout=2) as response:
                    if response.status == 200:
                        break
            except Exception:
                time.sleep(0.5)
        else:
            raise AssertionError("Local Risk fixture did not become healthy.")

        options = Options()
        options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--window-size=1440,1100")
        driver = webdriver.Chrome(options=options)
        driver.get(f"http://127.0.0.1:{PORT}/")

        deadline = time.time() + 45
        while time.time() < deadline:
            body = driver.find_element(By.TAG_NAME, "body").text
            if "4. REVIEW + SEND ORDER" in body and "Raj Singh" in body:
                break
            time.sleep(0.5)
        else:
            raise AssertionError("Risk order Parts 3/4 did not render in the browser.")

        headers = driver.find_elements(By.CSS_SELECTOR, ".risk-v9-section")
        part3 = [element for element in headers if element.text.strip() == "3. PICK E*TRADE ACCOUNT"]
        part4 = [element for element in headers if element.text.strip() == "4. REVIEW + SEND ORDER"]
        assert len(part3) == 1, f"Part 3 rendered {len(part3)} times"
        assert len(part4) == 1, f"Part 4 rendered {len(part4)} times"

        warning = driver.find_element(By.CSS_SELECTOR, ".risk-v9-capacity-warning")
        picker = driver.find_element(By.CSS_SELECTOR, ".st-key-risk_live_order_account_key")

        warning_rect = _rect(driver, warning)
        part3_rect = _rect(driver, part3[0])
        picker_rect = _rect(driver, picker)
        part4_rect = _rect(driver, part4[0])

        assert warning_rect["bottom"] <= part3_rect["top"] + 0.5, (
            "Tactical Capacity warning overlaps Part 3",
            warning_rect,
            part3_rect,
        )
        assert picker_rect["bottom"] <= part4_rect["top"] + 0.5, (
            "Order Account picker overlaps Part 4",
            picker_rect,
            part4_rect,
        )

        body = driver.find_element(By.TAG_NAME, "body").text
        assert "Raj Singh" in body and "5474" in body
        print("RISK ORDER LAYOUT BROWSER: PASS")
        print("Single Part 3/4 render, Raj 5474 default, and no warning/picker overlap.")
        return 0
    finally:
        if driver is not None:
            driver.quit()
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
        FIXTURE_PATH.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
