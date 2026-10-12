"""Browser regression: scroll all loaded Protective Puts and retain BUY state.

Uses the real virtualized Protective Puts chain component, 62 synthetic rows
spanning 2026 and 2027, and read-only broker stubs. Never places orders.
Tests scroll range, yearly lime/black winner styling, exact checkbox clicks,
and selection persistence after scrolling back.
"""
from __future__ import annotations

import os
import re
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
from src.protective_puts_portfolio_ui import (
    _CSS, _results, _load_selection, _write_selection,
    _toggle_selection, _account_token,
)

st.set_page_config(layout="wide", page_title="Put chain selection regression")
account_key="test-hedge-persistence"
partial=st.checkbox("SIMULATE PARTIAL HOLDINGS", key="test_partial_holdings")
memory=_load_selection(account_key)
if memory is not None:
    for symbol in (("QQQ",) if partial else ("SPY","QQQ")):
        widget_key=f"pp_hedge_holding::{_account_token(account_key)}::{symbol}"
        desired=symbol in set(memory["tickers"])
        if st.session_state.get(widget_key)!=desired:
            st.session_state[widget_key]=desired
        st.checkbox(
            "TEST HEDGE "+symbol, key=widget_key,
            on_change=_toggle_selection, args=(account_key,symbol,widget_key),
        )
    _write_selection(account_key)
    if "SPY" in memory["tickers"]:
        st.caption("SPY SAVED IN BROWSER MEMORY")
data=[]
for i in range(62):
    row={
        "Proxy":"SPY", "Expiration": "12-18-26" if i<31 else "01-15-27",
        "DTE":68 if i<31 else 96,
        "Strike": float(550+i), "Spot":600.0, "Bid":1.0, "Ask":1.1,
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
    # Include a second $60k eligible sleeve to verify the portfolio table
    # models unselected equities moving at beta 1, while grid QTY stays direct.
    "portfolio_value":120000.0,
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
    # Streamlit replaces the entire fragment during BUY/risk-slide rerenders.
    # Read all captions in one synchronous browser script, not via stale
    # WebElement instances that can disappear while iterating.
    return driver.execute_script(
        """
        for (const el of document.querySelectorAll(
          '[data-testid="stCaptionContainer"]'
        )) {
          const m=el.textContent.match(/(\\d+) SELECTED ACROSS ALL LOADED CONTRACTS/);
          if(m) return Number(m[1]);
        }
        return -1;
        """
    )


def in_scroll_iframe(driver, callback):
    """Find the real Streamlit component iframe regardless of wrapper markup."""
    wait=WebDriverWait(driver, 25)

    def enter(_):
        driver.switch_to.default_content()
        for frame in driver.find_elements(By.TAG_NAME,"iframe"):
            try:
                driver.switch_to.frame(frame)
                if driver.find_elements(By.ID,"scroll"):
                    return True
            except Exception:
                pass
            driver.switch_to.default_content()
        return False

    try:
        wait.until(enter)
        return callback()
    except Exception:
        driver.switch_to.default_content()
        frames=[
            (f.get_attribute("title"), f.get_attribute("src"))
            for f in driver.find_elements(By.TAG_NAME,"iframe")
        ]
        raise AssertionError(
            f"Scrollable option component check failed; iframes={frames}; "
            f"host={driver.find_element(By.TAG_NAME,'body').text[-600:]}"
        )
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
            assert "62 LOADED PUTS" in driver.page_source
            assert "CHECKED PUT RISK SLIDE" not in driver.find_element(By.TAG_NAME,"body").text
            assert not driver.find_elements(By.CSS_SELECTOR,'.st-key-pp_chain_prev')
            assert not driver.find_elements(By.CSS_SELECTOR,'.st-key-pp_chain_next')
            assert not driver.find_elements(By.CSS_SELECTOR,'[data-testid="stDataFrame"]')
            assert not driver.find_elements(By.CSS_SELECTOR,'[data-testid="stDataEditor"]')

            def visible_stats():
                def measure():
                    scroll=driver.find_element(By.ID,"scroll")
                    return driver.execute_script(
                        """
                        const s=arguments[0];
                        return {visible:s.querySelectorAll(".row").length,
                                total:s.scrollHeight,client:s.clientHeight,
                                ids:[...s.querySelectorAll(".row")].map(el=>el.dataset.index),
                                values:[...s.querySelectorAll(".row .cell:nth-child(3)")]
                                       .map(el=>el.textContent),
                                qty:[...s.querySelectorAll(".row .cell:nth-child(12)")]
                                    .map(el=>el.textContent),
                                header:document.getElementById("head").textContent};
                        """,scroll,
                    )
                return in_scroll_iframe(driver,measure)

            # Default highlights-only filter displays the one annual winner
            # in each year, rather than the 62 loaded quoted contracts.
            only=visible_stats()
            assert only["visible"] == 2, only
            assert only["values"] == ["SPYTEST0020", "SPYTEST0053"], only
            assert only["qty"] == ["1 / 100 SH", "1 / 100 SH"], only
            assert "LOSS CALC" in only["header"], only
            assert "QTY / EQ SH" in only["header"], only
            assert "BASKET LOSS" not in only["header"], only
            assert "2 DISPLAYED / 62 QUOTED PUTS" in driver.page_source

            def set_filter(choice):
                picker=wait.until(lambda _: next((
                    el for el in driver.find_elements(
                        By.CSS_SELECTOR,
                        '.st-key-pp_chain_highlight_filter [role="combobox"], '
                        '.st-key-pp_chain_highlight_filter [data-testid="stSelectbox"]'
                    ) if el.is_displayed()
                ),None))
                picker.click()
                target=wait.until(lambda _: next((
                    el for el in driver.find_elements(
                        By.CSS_SELECTOR,'[role="option"],[data-testid="stSelectboxVirtualDropdown"] li'
                    ) if choice in el.text and el.is_displayed()
                ),None))
                target.click()
                expected=("62 DISPLAYED / 62 QUOTED PUTS"
                          if choice=="ALL CONTRACTS"
                          else "2 DISPLAYED / 62 QUOTED PUTS")
                wait.until(lambda _: expected in driver.find_element(
                    By.TAG_NAME,"body"
                ).text)

            set_filter("ALL CONTRACTS")
            def check_virtualized():
                info=visible_stats()
                assert 0<info["visible"]<40,info
                assert info["total"]>info["client"]*3,info
                assert "LOSS CALC" in info["header"],info
            check_virtualized()
            assert "62 DISPLAYED / 62 QUOTED PUTS" in driver.page_source

            def rapid_toggle_pair(expected):
                # Two clicks in one browser turn outrun a fragment round trip.
                # The iframe and Python must agree after the rerender settles.
                def click_pair():
                    pressed=driver.execute_script(
                        """
                        const first=document.querySelector('.row[data-index="0"] .buy');
                        const second=document.querySelector('.row[data-index="1"] .buy');
                        if (!first || !second) return null;
                        first.click(); second.click();
                        return [first.getAttribute('aria-pressed'),
                                second.getAttribute('aria-pressed')];
                        """
                    )
                    assert pressed==[str(expected>0).lower()]*2, pressed
                in_scroll_iframe(driver,click_pair)
                wait.until(lambda _: selected_count(driver)==expected)
                def check_pair():
                    return driver.execute_script(
                        """
                        return [0,1].map(i=>document.querySelector(
                          `.row[data-index="${i}"] .buy`
                        )?.getAttribute('aria-pressed'));
                        """
                    )
                wait.until(lambda _: in_scroll_iframe(driver,check_pair)
                           ==[str(expected>0).lower()]*2)

            rapid_toggle_pair(2)
            # Every checked BUY exposes an OptionStrat proxy-hedge deep link
            # beside the orange square, without creating a new table column.
            def check_optionstrat_link():
                return driver.execute_script("""
                    const row=document.querySelector('.row[data-index="0"]');
                    const link=row?.querySelector('a.os-link');
                    if(!link) return null;
                    return {visible:getComputedStyle(link).visibility,
                            href:link.href, label:link.textContent,
                            target:link.target, rel:link.rel};
                """)
            os_link=in_scroll_iframe(driver,check_optionstrat_link)
            assert os_link is not None, os_link
            assert os_link["visible"]=="visible", os_link
            assert os_link["label"]=="OS↗", os_link
            assert os_link["target"]=="_blank", os_link
            assert "noopener" in os_link["rel"], os_link
            assert os_link["href"].startswith(
                "https://optionstrat.com/build/protective-put/SPY/"
                "x100@600.00,.SPY261218P570x1@1.10"
            ), os_link
            slide=wait.until(lambda _: next((
                el for el in driver.find_elements(By.CSS_SELECTOR,"table.pp-risk-table")
                if el.is_displayed()
            ),None))
            labels=[el.text for el in slide.find_elements(By.CSS_SELECTOR,"tbody tr th")]
            assert labels==[
                "-30%","-25%","-20%","-15%","-10%","-5%","-2%",
                "+0%","+2%","+5%","+10%","+15%","+20%","+25%","+30%",
            ],labels
            assert [el.text for el in slide.find_elements(By.CSS_SELECTOR,"thead th")]==[
                "MOVE","NO PUT","WITH PUT","CHANGE $","CHANGE %",
            ]
            row_by_move={
                row.find_element(By.CSS_SELECTOR,"th").text:
                [cell.text for cell in row.find_elements(By.CSS_SELECTOR,"td")]
                for row in slide.find_elements(By.CSS_SELECTOR,"tbody tr")
            }
            # Row 0 is the $570 strike / $1.10 ASK SPY PUT. At -30%, the
            # selected 100-share basket loses $18k, the other sleeve loses
            # $18k, and the put returns $15k less its $110 premium.
            assert row_by_move["-30%"]==[
                "$84,000.00","$98,890.00","-$21,110.00","-17.59%",
            ],row_by_move["-30%"]
            assert row_by_move["+0%"][1:]==[
                "$119,890.00","-$110.00","-0.09%",
            ],row_by_move["+0%"]
            geometry=driver.execute_script(
                """
                const t=arguments[0], r=t.getBoundingClientRect();
                return {tableWidth:r.width, tableRight:r.right,
                        viewportWidth:document.documentElement.clientWidth,
                        tableScroll:t.scrollWidth-t.clientWidth,
                        pageScroll:document.documentElement.scrollWidth-
                                   document.documentElement.clientWidth};
                """,slide,
            )
            assert geometry["tableRight"]<=geometry["viewportWidth"]+2,geometry
            assert geometry["tableScroll"]<=2,geometry
            assert geometry["pageScroll"]<=2,geometry
            wait.until(lambda _: "PORTFOLIO CHANGE ($)" in
                       driver.find_element(By.TAG_NAME,"body").text)
            chart_at_minus_30=wait.until(lambda _: driver.execute_script(
                """
                const plot=document.querySelector('.js-plotly-plot');
                if (!plot || !plot.data || !plot.calcdata ||
                    plot.data.length<2 || plot.calcdata.length<2) return null;
                return plot.data.slice(0,2).map((trace,i)=>{
                  const point=plot.calcdata[i].find(p=>Number(p.x)===-30);
                  return {name:trace.name, change:point?Number(point.y):null};
                });
                """
            ))
            assert chart_at_minus_30==[
                {"name":"No PUT","change":-36000.0},
                {"name":"With checked PUT","change":-21110.0},
            ],chart_at_minus_30
            driver.set_window_size(1100,1000)
            compact=wait.until(lambda _: driver.execute_script(
                """
                const t=document.querySelector('table.pp-risk-table');
                if (!t) return null;
                const r=t.getBoundingClientRect();
                return {right:r.right,
                  viewport:document.documentElement.clientWidth,
                  pageOverflow:document.documentElement.scrollWidth-
                               document.documentElement.clientWidth,
                  tableOverflow:t.scrollWidth-t.clientWidth,
                  cellOverflow:Math.max(...[...t.querySelectorAll('th,td')]
                    .map(cell=>cell.scrollWidth-cell.clientWidth)),
                  clippedCells:[...t.querySelectorAll('th,td')]
                    .filter(cell=>cell.scrollWidth-cell.clientWidth>2)
                    .map(cell=>({text:cell.textContent,
                      column:cell.cellIndex, width:cell.clientWidth,
                      contentWidth:cell.scrollWidth})),
                  visibleRows:[...t.querySelectorAll('tbody tr')]
                    .filter(row=>row.getBoundingClientRect().height>0).length};
                """
            ))
            assert compact["right"]<=compact["viewport"]+2,compact
            assert compact["pageOverflow"]<=2,compact
            assert compact["tableOverflow"]<=2,compact
            assert compact["cellOverflow"]<=2,compact
            assert compact["visibleRows"]==15,compact
            driver.set_window_size(1450,1000)
            rapid_toggle_pair(0)

            # LOWEST LOSS default now sorts both annual winners to the top.
            inspect_and_click_row(driver,0,1)
            wait.until(lambda _: "CHECKED PUT RISK SLIDE" in
                       driver.find_element(By.TAG_NAME, "body").text)
            inspect_and_click_row(driver,1,2)

            def check_saved():
                scroll=driver.find_element(By.ID,"scroll")
                driver.execute_script(
                    "arguments[0].scrollTop=40*43;"
                    "arguments[0].dispatchEvent(new Event('scroll'));",scroll,
                )
                assert WebDriverWait(driver,20).until(lambda _: next((
                    el for el in driver.find_elements(By.CSS_SELECTOR,'.row[data-index="40"]')
                    if el.is_displayed()
                ), None))
                driver.execute_script(
                    "arguments[0].scrollTop=0;"
                    "arguments[0].dispatchEvent(new Event('scroll'));",scroll,
                )
                row=WebDriverWait(driver,20).until(lambda _: next((
                    el for el in driver.find_elements(By.CSS_SELECTOR,'.row[data-index="0"]')
                    if el.is_displayed()
                ), None))
                assert row.find_element(By.CSS_SELECTOR,"button.buy").get_attribute("aria-pressed")=="true"
            in_scroll_iframe(driver,check_saved)
            assert selected_count(driver)==2
            set_filter("HIGHLIGHTED ONLY")
            wait.until(
                lambda _: visible_stats()["values"] == ["SPYTEST0020", "SPYTEST0053"]
            )
            assert selected_count(driver)==2
            set_filter("ALL CONTRACTS")
            wait.until(
                lambda _: visible_stats()["total"] > visible_stats()["client"] * 3
            )
            assert selected_count(driver)==2

            # The SAME browser-backed checkbox memory used by the real hedge
            # must survive partial holdings and a full Streamlit page refresh.
            def visible_hedge_label(text):
                # Query atomically inside Chrome while Streamlit swaps DOM nodes;
                # looping over WebElements during a rerun can raise stale-element.
                return driver.execute_script(
                    """
                    return [...document.querySelectorAll("label")].find(
                      el => el.offsetParent !== null
                         && el.textContent.includes(arguments[0])
                    ) || null;
                    """, text,
                )
            hedge=wait.until(lambda _: visible_hedge_label("TEST HEDGE SPY"))
            hedge.click()
            wait.until(lambda _: "SPY SAVED IN BROWSER MEMORY" in
                       driver.find_element(By.TAG_NAME, "body").text)
            partial=wait.until(lambda _: visible_hedge_label("SIMULATE PARTIAL HOLDINGS"))
            partial.click()
            wait.until(lambda _: visible_hedge_label("TEST HEDGE SPY") is None)
            wait.until(lambda _: visible_hedge_label("SIMULATE PARTIAL HOLDINGS")).click()
            wait.until(lambda _: visible_hedge_label("TEST HEDGE SPY") is not None)
            assert "SPY SAVED IN BROWSER MEMORY" in driver.find_element(By.TAG_NAME,"body").text
            driver.refresh()
            wait.until(lambda _: "SPY SAVED IN BROWSER MEMORY" in
                       driver.find_element(By.TAG_NAME, "body").text)
            assert visible_hedge_label("TEST HEDGE SPY") is not None
            print("PROTECTIVE PUTS SORT / SCROLL / BUY / BROWSER SELECTION RELOAD: PASS")
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
