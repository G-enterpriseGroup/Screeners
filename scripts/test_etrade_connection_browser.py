"""Browser check for the synthetic E*TRADE clipboard and verification row."""

import os
from pathlib import Path
import socket
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
sys.path.insert(0, str(ROOT))
from tests.test_etrade_connection_ui import FIXTURE


def main():
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "app.py"
        path.write_text(FIXTURE, encoding="utf-8")
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        base = f"http://127.0.0.1:{port}"
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(ROOT) + os.pathsep + environment.get("PYTHONPATH", "")
        log = Path(directory) / "streamlit.log"
        with log.open("w", encoding="utf-8") as output:
            server = subprocess.Popen(
                [sys.executable, "-m", "streamlit", "run", str(path),
                 "--server.port", str(port), "--server.headless", "true",
                 "--browser.gatherUsageStats", "false"],
                cwd=ROOT, env=environment, stdout=output, stderr=subprocess.STDOUT,
            )
        driver = None
        try:
            for _ in range(100):
                if server.poll() is not None:
                    raise AssertionError(log.read_text(encoding="utf-8")[-3000:])
                try:
                    with urllib.request.urlopen(base + "/_stcore/health", timeout=2) as response:
                        if response.status == 200:
                            break
                except Exception:
                    time.sleep(.3)
            else:
                raise AssertionError("Synthetic OAuth fixture did not start")

            options = Options()
            options.add_argument("--headless=new")
            options.add_argument("--no-sandbox")
            options.add_argument("--disable-dev-shm-usage")
            options.add_argument("--window-size=1100,800")
            driver = webdriver.Chrome(options=options)
            driver.execute_cdp_cmd("Browser.grantPermissions", {
                "origin": base,
                "permissions": ["clipboardReadWrite", "clipboardSanitizedWrite"],
            })
            driver.get(base)
            wait = WebDriverWait(driver, 30)
            paste = wait.until(lambda d: d.find_element(By.ID, "etrade-oauth-paste"))
            verify = wait.until(lambda d: d.find_element(By.CSS_SELECTOR, ".st-key-etrade_verify button"))
            assert not verify.is_enabled()
            assert driver.execute_async_script(
                "const done=arguments[0]; navigator.clipboard.writeText('ABC123').then(()=>done(true),()=>done(false));"
            )
            paste.click()
            wait.until(lambda d: d.find_element(By.CSS_SELECTOR, 'input[placeholder="Enter the code shown by E*TRADE"]').get_attribute("value") == "ABC123")
            verify = wait.until(lambda d: d.find_element(By.CSS_SELECTOR, ".st-key-etrade_verify button") if d.find_element(By.CSS_SELECTOR, ".st-key-etrade_verify button").is_enabled() else False)
            boxes = driver.execute_script(
                "return [document.getElementById('etrade-oauth-paste').getBoundingClientRect(), document.querySelector('.st-key-etrade_verify button').getBoundingClientRect()].map(r=>({x:r.x,y:r.y,w:r.width,h:r.height}));"
            )
            assert abs(boxes[0]["y"] - boxes[1]["y"]) <= 2, boxes
            assert abs(boxes[0]["w"] - boxes[1]["w"]) <= 8, boxes
            verify.click()
            wait.until(lambda d: "CONNECTED" in d.find_element(By.CSS_SELECTOR, '[data-testid="stApp"]').text)
            print("PASS: clipboard paste, native input update, enabled verification, equal-width row, synthetic connection")
        finally:
            if driver is not None:
                driver.quit()
            server.terminate()
            server.wait(timeout=10)


if __name__ == "__main__":
    main()
