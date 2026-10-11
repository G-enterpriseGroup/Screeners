"""Verify actual React Aria account-chip colors in a browser, not CSS substrings.

Feature-owned regression harness: no E*TRADE login, preview, send, or live order.
Launch a small Streamlit app using the production Vertical Options stylesheet,
select an account and read the computed color on the real selected-chip DOM.
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
PORT = 18679
ADDRESS = f"http://127.0.0.1:{PORT}"

MINIMAL_APP = """
import streamlit as st
from src.vertical_options_ui import _VERTICAL_OPTIONS_CSS

st.set_page_config(page_title="Vertical Options account chip browser regression")
st.html(_VERTICAL_OPTIONS_CSS)
with st.container(key="vertical_options_workspace"):
    st.multiselect(
        "Accounts",
        ["Raj Singh // x5474", "Other Account // x9876"],
        default=["Raj Singh // x5474"],
        key="vo_accounts",
    )
"""


def wait_for_server(server: subprocess.Popen) -> None:
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        if server.poll() is not None:
            raise AssertionError(f"Streamlit demo exited unexpectedly: {server.returncode}")
        try:
            with urllib.request.urlopen(ADDRESS + "/_stcore/health", timeout=2) as response:
                if response.status == 200:
                    return
        except Exception:
            time.sleep(0.5)
    raise AssertionError("Test Streamlit app never became healthy")


def chip_styles(driver: webdriver.Chrome, label: str) -> dict | None:
    return driver.execute_script(
        """
        const label=arguments[0];
        const chip=Array.from(document.querySelectorAll(
            '.st-key-vertical_options_workspace [data-testid="stMultiSelect"] [data-tag]'
        )).find(el => el.getAttribute('aria-label')===label);
        if (!chip) return null;
        const text=chip.querySelector('[title]');
        if (!text) return {missingText:true, outerHTML:chip.outerHTML};
        const chipStyle=getComputedStyle(chip);
        const textStyle=getComputedStyle(text);
        return {
            content:text.textContent,
            foreground:textStyle.color,
            textFill:textStyle.webkitTextFillColor,
            background:chipStyle.backgroundColor
        };
        """, label,
    )


def require_black_on_orange(info: dict | None, label: str) -> None:
    if not info:
        raise AssertionError(f"No selected chip found for {label!r}")
    assert info.get("content") == label, info
    assert info.get("background") == "rgb(251, 139, 30)", info
    assert info.get("foreground") == "rgb(0, 0, 0)", info
    assert info.get("textFill") == "rgb(0, 0, 0)", info


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="raj_vertical_chip_") as tmp:
        app_path = Path(tmp) / "vertical_chip_test.py"
        app_path.write_text(MINIMAL_APP, encoding="utf-8")
        env = os.environ.copy()
        env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
        server = subprocess.Popen(
            [
                sys.executable, "-m", "streamlit", "run", str(app_path),
                "--server.port", str(PORT),
                "--server.headless", "true",
                "--browser.gatherUsageStats", "false",
            ],
            cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT,
        )
        driver = None
        try:
            wait_for_server(server)
            options = Options()
            options.add_argument("--headless=new")
            options.add_argument("--no-sandbox")
            options.add_argument("--disable-dev-shm-usage")
            options.add_argument("--window-size=1280,800")
            driver = webdriver.Chrome(options=options)
            driver.get(ADDRESS)
            wait = WebDriverWait(driver, 70)
            first = "Raj Singh // x5474"
            second = "Other Account // x9876"
            wait.until(lambda _: chip_styles(driver, first) is not None)
            require_black_on_orange(chip_styles(driver, first), first)
            open_selector = (
                '.st-key-vertical_options_workspace [data-testid="stMultiSelect"] '
                'button[aria-label="Open"]'
            )
            driver.find_element(By.CSS_SELECTOR, open_selector).click()
            listbox = '[data-testid="stMultiSelectDropdown"] [role="listbox"][aria-label="Accounts"]'
            wait.until(lambda _: driver.find_elements(By.CSS_SELECTOR, listbox + ' [role="option"]'))
            option = next(
                (el for el in driver.find_elements(By.CSS_SELECTOR, listbox + ' [role="option"]')
                 if second in el.text),
                None,
            )
            assert option is not None, "Other brokerage account missing from selector"
            assert option.value_of_css_property("color") == "rgba(242, 242, 242, 1)", (
                "Account option text is unreadable"
            )
            option.click()
            wait.until(lambda _: chip_styles(driver, second) is not None)
            require_black_on_orange(chip_styles(driver, first), first)
            require_black_on_orange(chip_styles(driver, second), second)
            print("VERTICAL OPTIONS ACCOUNT CHIP BROWSER CONTRAST: PASS")
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
    main()
