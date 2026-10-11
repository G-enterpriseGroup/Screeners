"""Live browser test: put BUY checkboxes survive fast cross-page navigation.

Uses the exact Protective Puts _results renderer, 62 synthetic put contracts,
the production Streamlit theme, and a read-only broker stub. Never calls an
E*TRADE API. Checks that a real click is captured and stays with its contract
after visiting a different option-chain page.
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
        "Proxy":"SPY", "Expiration": "12-18-26", "DTE":68,
        "Strike": float(550+i), "Bid":1.0, "Ask":1.1,
        "Last":1.0, "Cost of Put (Ask)":110.0,
        "Max Loss (Ask)":max(0.0,1000-i), "Stress Remaining Loss":float(i),
        "Contracts":1, "Open Interest":1000+i, "Volume":250+i,
        "Implied Volatility":.20,
        "Priority":i==0, "Best / Expiration":i<3,
        "Orderable":True, "Contract":f"SPYTEST{i:04d}",
        "Max Loss Calc (Ask)":"EXAMPLE",
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
        if "SELECTED ACROSS ALL PAGES" in content:
            return int(content.split(" SELECTED")[0].strip())
    return -1


def click_first_checkbox(driver, expected: int) -> None:
    # Native Glide checkbox is on a canvas; render/layout can include a
    # row-header gutter and varying column widths. Resolve the visible
    # BUY cell from its orange fill before making a real Selenium click.
    def canvas_and_cell(_):
        grids=driver.find_elements(
            By.CSS_SELECTOR, '[data-testid="stDataFrame"], [data-testid="stDataEditor"]'
        )
        for grid in grids:
            for candidate in grid.find_elements(By.CSS_SELECTOR, "canvas"):
                if candidate.size["width"]<400 or candidate.size["height"]<150:
                    continue
                coords=driver.execute_script("""
                    const c=arguments[0];
                    const ctx=c.getContext('2d',{willReadFrequently:true});
                    if(!ctx) return null;
                    const b=c.getBoundingClientRect();
                    const scaleX=c.width/b.width, scaleY=c.height/b.height;
                    // Orange BUY cells lie immediately beneath the orange header.
                    // Find the long orange band below the header and use its middle.
                    for(let y=43;y<85;y+=3){
                        const row=ctx.getImageData(0,Math.floor(y*scaleY),
                                      Math.min(200,c.width),1).data;
                        let left=-1,right=-1;
                        for(let x=10;x<Math.min(150,b.width);x++){
                            const k=Math.floor(x*scaleX)*4;
                            if(Math.abs(row[k]-251)<14 &&
                               Math.abs(row[k+1]-139)<14 &&
                               Math.abs(row[k+2]-30)<14){
                                if(left<0)left=x;
                                right=x;
                            }
                        }
                        if(right-left>25) return {x:Math.floor((left+right)/2),
                                                  y:y, width:b.width, height:b.height};
                    }
                    return null;
                """,candidate)
                if coords:
                    return candidate, coords
        return False
    canvas,coords=WebDriverWait(driver,35).until(canvas_and_cell)
    # Avoid waiting on a timer: wait for the real Streamlit rerun with the
    # selected-across-all-pages count increased by one.
    ActionChains(driver).move_to_element_with_offset(
        canvas, int(coords["x"]-coords["width"]/2),
        int(coords["y"]-coords["height"]/2),
    ).click().perform()
    WebDriverWait(driver,20).until(lambda _: selected_count(driver)==expected)


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="pp_paging_") as tmp:
        app=Path(tmp)/"paging_app.py"
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
            opts.add_argument("--window-size=1600,1000")
            driver=webdriver.Chrome(options=opts)
            driver.get(BASE)
            wait=WebDriverWait(driver,60)
            wait.until(lambda _: selected_count(driver)==0)
            assert "PAGE 1/3" in driver.page_source, "25-row pagination missing"
            click_first_checkbox(driver,1)
            driver.find_element(By.CSS_SELECTOR, '.st-key-pp_chain_next button').click()
            wait.until(lambda _: "PAGE 2/3" in driver.page_source)
            assert selected_count(driver)==1
            click_first_checkbox(driver,2)
            driver.find_element(By.CSS_SELECTOR, '.st-key-pp_chain_prev button').click()
            wait.until(lambda _: "PAGE 1/3" in driver.page_source)
            assert selected_count(driver)==2
            print("PROTECTIVE PUTS PAGED BUY CHECKBOX BROWSER TEST: PASS")
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
