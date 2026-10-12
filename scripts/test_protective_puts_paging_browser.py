"""Browser regression: scroll all loaded Protective Puts and retain BUY state.

Uses the real virtualized Protective Puts chain component, 62 synthetic rows
spanning 2026 and 2027, and read-only broker stubs. Never places orders.
Tests scroll range, yearly lime/black winner styling, exact checkbox clicks,
and selection persistence after scrolling back.
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
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait

ROOT = Path(__file__).resolve().parents[1]
PORT = 18683
BASE = f"http://127.0.0.1:{PORT}"
FIXTURE = '''
import pandas as pd
import streamlit as st
from src.protective_puts_portfolio_ui import _CSS, _results

st.set_page_config(layout="wide", page_title="Put chain selection regression")
data=[]
for i in range(62):
    row={
        "Proxy":"SPY", "Expiration": "12-18-26" if i<31 else "01-15-27",
        "DTE":68 if i<31 else 96,
        "Strike": float(550+i), "Bid":1.0, "Ask":1.1,
        "Last":1.0, "Cost of Put (Ask)":110.0,
        "Max Loss (Ask)":0.0 if i in (20,53) else 1000.0+i,
        "Stress Remaining Loss":float(i),
        "Contracts":1, "Open Interest":1000+i, "Volume":250+i,
        "Implied Volatility":.20,
        "Priority":i==0, "Best / Expiration":i<3,
        "Orderable":True, "Contract":f"SPYTEST{i:04d}",
        "Max Loss Calc (Ask)":f"max(0, (600.00 + 1.10 - {550+i:.2f}) × 100)",
        "Cost of Put (Last)":100.0, "Max Loss (Last)":max(0.0,1100-i),
        "Max Loss Calc (Last)":"EXAMPLE", "Put Cost":110.0,
    }
    data.append(row)
frame=pd.DataFrame(data)
result={
    "data":frame, "direct_example":True, "scan_id":963321,
    "fingerprint":("test","SPY"), "exposure":0,
    "stress_loss":0, "stress":15.0, "beta":1.0,
    "chains":1, "errors":[],
}
def money(v):
    return f"${float(v):,.2f}"
def metric_markup(items):
    return "<div>TEST METRICS</div>"

with st.container(key="pp_portfolio_hedge", gap="small"):
    st.html(_CSS)
    _results(result, money, metric_markup, object(), lambda:None,
             lambda client:False, "")
'''


def wait_health(proc: subprocess.Popen) -> None:
    limit=time.monotonic()+45
    while time.monotonic()<limit:
        if proc.poll() is not None:
            raise AssertionError(f"Streamlit fixture exited unexpectedly: {proc.returncode}")
        try:
            with urllib.request.urlopen(BASE+"/_stcore/health", timeout=2) as res:
                if res.status==200:
                    return
        except Exception:
            time.sleep(.5)
    raise AssertionError("Browser fixture did not start")


def selected_count(driver) -> int:
    labels=driver.find_elements(By.CSS_SELECTOR, '[data-testid="stCaptionContainer"]')
    for el in labels:
        content=el.text
        if "SELECTED ACROSS ALL LOADED CONTRACTS" in content:
            return int(content.split(" SELECTED")[0].strip())
    return -1


def in_scroll_iframe(driver, callback):
    """Interact with the actual, independently scrolling Streamlit component."""
    wait=WebDriverWait(driver, 45)
    frame=wait.until(lambda _: next((
        el for el in driver.find_elements(
            By.CSS_SELECTOR, '[data-testid="stCustomComponentV1"] iframe'
        ) if el.is_displayed()
    ), None))
    driver.switch_to.frame(frame)
    try:
        wait.until(lambda _: driver.find_elements(By.CSS_SELECTOR, "#scroll .row"))
        return callback()
    finally:
        driver.switch_to.default_content()


def inspect_and_click_row(driver, index: int, expected: int) -> None:
    """A full-width orange outlined BUY square remains clickable at any scroll depth."""
    def inspect():
        wait=WebDriverWait(driver, 20)
        scroll=driver.find_element(By.ID,"scroll")
        driver.execute_script(
            "arguments[0].scrollTop=arguments[1];"
            "arguments[0].dispatchEvent(new Event('scroll'));",
            scroll, max(0, index-2)*43,
        )
        row=wait.until(lambda _: next((
            el for el in driver.find_elements(By.CSS_SELECTOR, f'.row[data-index="{index}"]')
            if el.is_displayed()
        ), None))
        assert "annual" in row.get_attribute("class"), row.get_attribute("class")
        styles=driver.execute_script(
            """
            const cell=arguments[0].querySelector(".cell");
            const buy=arguments[0].querySelector(".buy");
            const r=buy.getBoundingClientRect();
            const square=getComputedStyle(arguments[0].querySelector(".square"));
            return {background:getComputedStyle(cell).backgroundColor,
                    foreground:getComputedStyle(cell).color,
                    width:r.width,height:r.height,
                    squareWidth:square.width,squareBorder:square.borderTopColor,
                    selected:buy.getAttribute("aria-pressed")};
            """, row,
        )
        assert styles["background"] == "rgb(163, 230, 53)", styles
        assert styles["foreground"] == "rgb(0, 0, 0)", styles
        assert styles["width"] >= 35 and styles["height"] >= 40, styles
        assert styles["squareWidth"] == "16px", styles
        assert styles["squareBorder"] == "rgb(251, 139, 30)", styles
        assert styles["selected"] == "false", styles
        row.find_element(By.CSS_SELECTOR,"button.buy").click()

    in_scroll_iframe(driver,inspect)
    WebDriverWait(driver,35).until(lambda _: selected_count(driver)==expected)


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="pp_scrolling_") as tmp:
        app=Path(tmp)/"scrolling_app.py"
        app.write_text(FIXTURE,encoding="utf-8")
        env=os.environ.copy()
        env["PYTHONPATH"]=str(ROOT)+os.pathsep+env.get("PYTHONPATH","")
        proc=subprocess.Popen([
            sys.executable,"-m","streamlit","run",str(app),
            "--server.port",str(PORT),"--server.headless","true",
            "--browser.gatherUsageStats","false",
        ],cwd=ROOT,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.STDOUT)
        driver=None
        try:
            wait_health(proc)
            opts=Options()
            opts.add_argument("--headless=new")
            opts.add_argument("--no-sandbox")
            opts.add_argument("--disable-dev-shm-usage")
            opts.add_argument("--force-device-scale-factor=1")
            opts.add_argument("--window-size=1450,1000")
            driver=webdriver.Chrome(options=opts)
            driver.get(BASE)
            wait=WebDriverWait(driver,60)
            wait.until(lambda _: selected_count(driver)==0)
            assert "SCROLL ALL 62 LOADED PUTS" in driver.page_source
            assert not driver.find_elements(By.CSS_SELECTOR,'.st-key-pp_chain_prev')
            assert not driver.find_elements(By.CSS_SELECTOR,'.st-key-pp_chain_next')
            assert not driver.find_elements(By.CSS_SELECTOR,'[data-testid="stDataFrame"]')
            assert not driver.find_elements(By.CSS_SELECTOR,'[data-testid="stDataEditor"]')

            def check_virtualized():
                scroll=driver.find_element(By.ID,"scroll")
                info=driver.execute_script(
                    """
                    const s=arguments[0];
                    return {visible:s.querySelectorAll(".row").length,
                            total:s.scrollHeight,client:s.clientHeight,
                            header:document.getElementById("head").textContent};
                    """, scroll,
                )
                assert 0<info["visible"]<40, info
                assert info["total"]>info["client"]*3, info
                assert "LOSS CALC" in info["header"], info
                assert "100×(" in scroll.text, scroll.text
            in_scroll_iframe(driver,check_virtualized)

            inspect_and_click_row(driver,20,1)
            inspect_and_click_row(driver,53,2)

            def check_saved():
                scroll=driver.find_element(By.ID,"scroll")
                driver.execute_script(
                    "arguments[0].scrollTop=0;"
                    "arguments[0].dispatchEvent(new Event('scroll'));",scroll,
                )
                assert scroll.find_elements(By.CSS_SELECTOR, ".row")
                driver.execute_script(
                    "arguments[0].scrollTop=20*43;"
                    "arguments[0].dispatchEvent(new Event('scroll'));",scroll,
                )
                row=WebDriverWait(driver,20).until(lambda _: next((
                    el for el in driver.find_elements(By.CSS_SELECTOR,'.row[data-index="20"]')
                    if el.is_displayed()
                ), None))
                assert row.find_element(By.CSS_SELECTOR,"button.buy").get_attribute("aria-pressed")=="true"
            in_scroll_iframe(driver,check_saved)
            assert selected_count(driver)==2
            print("PROTECTIVE PUTS SCROLL / YEAR HIGHLIGHT / BUY BROWSER TEST: PASS")
        finally:
            if driver is not None:
                driver.quit()
            proc.terminate()
            try:
                proc.wait(timeout=7)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)


if __name__ == "__main__":
    main()
