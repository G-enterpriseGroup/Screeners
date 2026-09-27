"""Real-browser Rebalance refresh/memory/theme regression with simulated holdings.

Requires Playwright + Chromium and production dependencies. Never uses a live
broker. The temporary fixture copies production CSS without editing shared files.
"""
from pathlib import Path
import ast
import json
import subprocess
import sys
import tempfile
import time
from urllib.request import urlopen

from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.test_rebalance_connected_ui import APP_SOURCE
from src.rebalance_portfolio_ui import _browser_storage_key

PORT = 8778
URL = f"http://127.0.0.1:{PORT}"


def main():
    core = ast.parse((ROOT / "src/terminal_core.py").read_text())
    css = next(n.value.args[0].value for n in core.body
               if isinstance(n, ast.Expr) and isinstance(n.value, ast.Call)
               and isinstance(n.value.func, ast.Attribute) and n.value.func.attr == "markdown"
               and n.value.args and isinstance(n.value.args[0], ast.Constant)
               and "<style>" in str(n.value.args[0].value))
    fixture = ("import sys\nsys.path.insert(0, " + repr(str(ROOT)) + ")\n"
               "import streamlit as st\nst.set_page_config(layout='wide')\n"
               f"st.html({css!r})\n"
               "from src.theme import install_typing_caret_theme\ninstall_typing_caret_theme()\n")
    fixture += APP_SOURCE.replace('return ACCOUNT\n', 'return {**ACCOUNT, "accountIdKey": st.session_state.get("test_account", ACCOUNT["accountIdKey"])}\n').replace(
        'assert account_key == ACCOUNT["accountIdKey"]', 'assert account_key in (ACCOUNT["accountIdKey"], "SIMULATED-SECOND")')
    fixture = fixture.replace('render_rebalance_portfolio(\n', 'st.selectbox("Test account", [ACCOUNT["accountIdKey"], "SIMULATED-SECOND"], key="test_account")\nrender_rebalance_portfolio(\n')
    process = None
    with tempfile.TemporaryDirectory(prefix="rebalance-browser-") as directory:
        path = Path(directory) / 'fixture.py'
        path.write_text(fixture)
        def start():
            proc = subprocess.Popen([sys.executable, '-m', 'streamlit', 'run', str(path),
                                     f'--server.port={PORT}', '--server.address=127.0.0.1', '--server.headless=true'],
                                    cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            for _ in range(100):
                try:
                    with urlopen(URL + '/_stcore/health', timeout=1) as response:
                        if response.status == 200:
                            return proc
                except Exception:
                    pass
                time.sleep(.1)
            proc.terminate()
            raise AssertionError('Streamlit fixture did not start')
        try:
            process = start()
            with sync_playwright() as p:
                browser = p.chromium.launch()
                context = browser.new_context(viewport={'width': 1440, 'height': 1100})
                page = context.new_page()
                key = _browser_storage_key('SIMULATED-RAJ-ACCOUNT')
                # A v1 record with revision 1 must survive initial default seeding.
                legacy = {'revision':1, 'saved_at':123, 'portfolio_saved_at':123,
                          'rows':[{'symbol':'NVDA','target_pct':10,'band_pct':3.25},
                                  {'symbol':'OLD','target_pct':7,'band_pct':0}],
                          'settings':{'min_trade':1700,'mode':'TO TARGET'}}
                page.add_init_script(f"if (!localStorage.getItem({json.dumps(key)})) localStorage.setItem({json.dumps(key)}, {json.dumps(json.dumps(legacy))});")
                page.goto(URL)
                saved = page.get_by_text('SAVED IN THIS BROWSER', exact=False)
                expect(saved).to_be_visible(timeout=30000)
                band = page.get_by_label('Band ± percentage points', exact=True)
                target = page.get_by_label('Target weight %', exact=True)
                minimum = page.get_by_label('Minimum Rebalance Trade $', exact=True)
                expect(band).to_have_value('3.25')
                expect(target).to_have_value('10.00')
                expect(minimum).to_have_value('1700')
                def edit(field, value, state_expression):
                    field.fill(value)
                    field.press('Enter')
                    page.wait_for_function('(args) => {const state = JSON.parse(localStorage.getItem(args.key)); return state && (' + state_expression + ') === Number(args.value);}', arg={'key':key, 'value':value}, timeout=15000)
                    expect(saved).to_be_visible(timeout=15000)
                    expect(field).to_have_value(value)
                edit(band, '4.50', "state.rows.find(r => r.symbol === 'NVDA').band_pct")
                edit(target, '12.00', "state.rows.find(r => r.symbol === 'NVDA').target_pct")
                edit(minimum, '1900', 'state.settings.min_trade')
                edit(page.get_by_label('Loss Review Trigger %', exact=True), '-18.0', 'state.settings.loss_review_trigger')
                page.reload()
                expect(saved).to_be_visible(timeout=30000)
                expect(band).to_have_value('4.50')
                expect(target).to_have_value('12.00')
                expect(minimum).to_have_value('1900')
                # Native account change keeps independent memory.
                page.get_by_label('Test account', exact=True).click()
                page.get_by_role('option', name='SIMULATED-SECOND', exact=True).click()
                expect(minimum).to_have_value('500')
                page.get_by_label('Test account', exact=True).click()
                page.get_by_role('option', name='SIMULATED-RAJ-ACCOUNT', exact=True).click()
                expect(minimum).to_have_value('1900')
                expect(band).to_have_value('4.50')
                # New server process still restores the browser's exact choices.
                process.terminate(); process.wait(timeout=15)
                process = start()
                page.reload()
                expect(saved).to_be_visible(timeout=30000)
                expect(band).to_have_value('4.50')
                expect(minimum).to_have_value('1900')
                data = page.evaluate('(key) => JSON.parse(localStorage.getItem(key))', key)
                assert next(r for r in data['rows'] if r['symbol'] == 'OLD')['band_pct'] == 0
                section = page.locator('.reb-section').first
                assert section.evaluate('(e) => getComputedStyle(e).backgroundColor') == 'rgb(251, 139, 30)'
                assert section.evaluate('(e) => e.getBoundingClientRect().height') == 36
                assert page.locator('.reb-table').count() >= 2
                assert page.locator('.reb-table').first.evaluate('(e) => e.scrollWidth <= e.clientWidth + 2')
                page.screenshot(path='/tmp/rebalance-theme-memory.png', full_page=True)
                page.set_viewport_size({'width':1000,'height':1000})
                assert page.locator('.reb-table').first.evaluate('(e) => e.scrollWidth <= e.clientWidth + 2')
                # Storage failure must be visible, never a false save success.
                blocked = browser.new_context()
                blocked.add_init_script("Storage.prototype.setItem = function() { throw new Error('blocked'); };")
                blocked_page = blocked.new_page()
                blocked_page.goto(URL)
                expect(blocked_page.get_by_text('Browser memory is unavailable.', exact=False)).to_be_visible(timeout=30000)
                assert blocked_page.get_by_text('SAVED IN THIS BROWSER', exact=False).count() == 0
                browser.close()
                print('REBALANCE BROWSER: PASS (legacy restore, edits, reload, account isolation, fresh process, inactive ticker, theme/width, blocked storage)')
        finally:
            if process:
                process.terminate()
                process.wait(timeout=15)


if __name__ == '__main__':
    main()
