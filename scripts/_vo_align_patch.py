from pathlib import Path
import subprocess

BASE = {'src/vertical_options_ui.py': 'b97f6022b32267f70dcfe5acd6fc4b8f138f814c', 'tests/test_vertical_options.py': '808a331ae8880050216688f0ca90cc98d267f7dc', 'scripts/test_vertical_options_scanner_browser.py': '64523c33904337e1fd1a162a3c5b64e6126fe610', 'src/TERMINAL_CHANGELOG.md': 'e708a3361e069cf45375d59c08d0970bfca24e41'}
for path, sha in BASE.items():
    assert subprocess.check_output(['git', 'hash-object', path], text=True).strip() == sha, path

def once(s, a, b):
    assert s.count(a) == 1, (s.count(a), a[:100])
    return s.replace(a, b)

p = Path('src/vertical_options_ui.py')
s = p.read_text()
s = once(s, '/* Pricing mode: center the native track vertically between its two descriptions. */\n.st-key-vertical_options_workspace .vo-mode-guide{min-height:32px;}', '''/* One aligned toolbar: reuse the scan-field columns; only cash cards stretch.
   A bounded mode group prevents empty space between the switch and its labels. */
.st-key-vertical_options_workspace .vo-mode-guide{min-height:38px;}
.st-key-vertical_options_workspace .st-key-vo_mode_controls .vo-mode-guide{white-space:nowrap;}
.st-key-vertical_options_workspace .st-key-vo_mode_switch{gap:0!important;}
.st-key-vertical_options_workspace .vo-liquid-cash .vo-card{
    flex:1 1 100%;width:100%;height:38px;min-height:38px;
}
.st-key-vertical_options_workspace .vo-liquid-cash .vo-label{min-width:0;flex:1;}
.st-key-vertical_options_workspace .vo-liquid-cash .vo-value{flex:none;}''')
s = once(s, '''        note_col, portfolio_col = st.columns([4, 1.4], gap="small", vertical_alignment="center")
        with note_col:
            st.html(
                '<div class="vo-note">ALL-YEAR VERTICAL SCANNER // '
                '<span class="muted">ENTER TICKER // DEBIT / BREAKEVEN IN COLUMN FILTER // PRESS ENTER TO SCAN</span></div>'
            )
        with portfolio_col:
            st.button("PORTFOLIO VERTICALS", key="vo_portfolio_open", use_container_width=True,
                      on_click=_open_portfolio_verticals)
            if st.session_state.get("_vo_portfolio_dialog_open"):
                _render_portfolio_verticals(client, touch_session)
''', '''        st.html(
            '<div class="vo-note">ALL-YEAR VERTICAL SCANNER // '
            '<span class="muted">ENTER TICKER // DEBIT / BREAKEVEN IN COLUMN FILTER // PRESS ENTER TO SCAN</span></div>'
        )
''')
a = s.index('        paste_cols = st.columns([1.15, 3.85], gap="small", vertical_alignment="center")')
b = s.index('        if st.session_state.get("vo_list_open"):', a)
old = s[a:b]
lines = old.splitlines()
new_lines = ['        with st.container(key="vo_toolbar"):',
             '            # Match the ticker/company and ratio/scan edges in the row below.',
             '            paste_cols = st.columns([1.05, 1.7, 3.55], gap="small", vertical_alignment="top")']
new_lines += ['    '+line if line else '' for line in lines[1:]]
new = '\n'.join(new_lines)+'\n'
new = once(new, 'with paste_cols[1], st.container(width=820):', 'with paste_cols[1], st.container(width=250, key="vo_mode_controls"):')
new = once(new, '                # Default NORMAL exactly preserves the existing ASK/LAST/MID scan.\n', '                # Preserve the native switch and its saved pricing preference.\n')
new = once(new, 'mode_guide_col, mode_switch_col, smart_label_col, cash_col = st.columns(', 'mode_guide_col, mode_switch_col, smart_label_col = st.columns(')
new = once(new, '[1.05, .50, 1.25, 1.85], gap="small", vertical_alignment="center"', '[78, 42, 114], gap="small", vertical_alignment="center"')
new = once(new, '                with mode_switch_col:\n                    pricing_mode = _pricing_mode_flip_switch()', '                with mode_switch_col:\n                    with st.container(key="vo_mode_switch", gap=None):\n                        pricing_mode = _pricing_mode_flip_switch()')
new = once(new, '                with cash_col:\n                    st.html(_liquid_cash_markup(client, touch_session))\n', '''            with paste_cols[2]:
                cash_col, portfolio_col = st.columns([.8, 1.1], gap="small", vertical_alignment="top")
                with cash_col:
                    st.html(_liquid_cash_markup(client, touch_session))
                with portfolio_col:
                    st.button("PORTFOLIO VERTICALS", key="vo_portfolio_open", use_container_width=True,
                              on_click=_open_portfolio_verticals)
        if st.session_state.get("_vo_portfolio_dialog_open"):
            _render_portfolio_verticals(client, touch_session)
''')
s = s[:a]+new+s[b:]
p.write_text(s)
assert subprocess.check_output(['git','hash-object',str(p)],text=True).strip() == 'b8f83b16c454018a88f8819e32950132e5c846da'

p = Path('tests/test_vertical_options.py')
s = once(p.read_text(), "        self.assertIn('mode_guide_col, mode_switch_col, smart_label_col, cash_col = st.columns(', ui)", '''        self.assertIn('mode_guide_col, mode_switch_col, smart_label_col = st.columns(', ui)
        self.assertIn('st.container(width=250, key="vo_mode_controls")', ui)
        self.assertIn('with st.container(key="vo_toolbar"):', ui)
        self.assertIn('cash_col, portfolio_col = st.columns([.8, 1.1]', ui)
        self.assertIn('.vo-liquid-cash .vo-card{', ui)
        self.assertNotIn('st.container(width=820)', ui)''')
p.write_text(s)

p = Path('scripts/test_vertical_options_scanner_browser.py')
helper = '''def assert_toolbar_alignment(driver: webdriver.Chrome) -> dict:
    """The cash/portfolio row follows scan-column edges without switch dead space."""
    def measure(_):
        return driver.execute_script("""
            const visible = selector => [...document.querySelectorAll(selector)]
                .reverse().find(el => el.getClientRects().length);
            const rect = el => {const r=el.getBoundingClientRect();return {
                left:r.left,right:r.right,top:r.top,bottom:r.bottom,height:r.height};};
            const paste=visible('.st-key-vo_list_toggle button'),
                cash=visible('.vo-liquid-cash .vo-card'),
                portfolio=visible('.st-key-vo_portfolio_open button'),
                ticker=visible('.st-key-vo_symbol_value [data-testid="stTextInputRootElement"]'),
                ratio=visible('.st-key-vo_min_reward_risk [data-testid="stTextInputRootElement"]'),
                scan=visible('[data-testid="stFormSubmitButton"] button'),
                guides=[...document.querySelectorAll('.st-key-vo_mode_controls .vo-mode-guide')],
                input=visible('.st-key-vo_smart_fill input[role="switch"]');
            if(!paste||!cash||!portfolio||!ticker||!ratio||!scan||!input||guides.length!==2)return null;
            const label=input.closest('label'),track=label?.querySelector(':scope > div:first-of-type'),
                amount=cash.querySelector('.vo-value');
            if(!track||!amount)return null;
            return {width:innerWidth,paste:rect(paste),cash:rect(cash),portfolio:rect(portfolio),
                ticker:rect(ticker),ratio:rect(ratio),scan:rect(scan),
                normal:rect(guides[0]),smart:rect(guides[1]),track:rect(track),
                labelFits:guides.every(el=>el.scrollWidth<=el.clientWidth+1),
                cashFits:amount.scrollWidth<=amount.clientWidth+1,
                suffix:getComputedStyle(label,'::after').content,
                overflow:document.documentElement.scrollWidth>innerWidth};
        """)
    geometry = WebDriverWait(driver, 20).until(measure)
    evidence = os.environ.get('VO_TOOLBAR_EVIDENCE_DIR')
    if evidence:
        folder = Path(evidence)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f'toolbar-{geometry["width"]}.json').write_text(json.dumps(geometry, indent=2))
        driver.find_element(By.CSS_SELECTOR, '.st-key-vertical_options_workspace').screenshot(
            str(folder / f'toolbar-{geometry["width"]}.png'))
    boxes = [geometry[key] for key in ('paste', 'cash', 'portfolio')]
    assert all(abs(box['height'] - 38) <= 1 for box in boxes), geometry
    assert max(box['top'] for box in boxes) - min(box['top'] for box in boxes) <= 2, geometry
    assert max(box['bottom'] for box in boxes) - min(box['bottom'] for box in boxes) <= 2, geometry
    for top, bottom in (('paste', 'ticker'), ('cash', 'ratio'), ('portfolio', 'scan')):
        for edge in ('left', 'right'):
            assert abs(geometry[top][edge] - geometry[bottom][edge]) <= 2, geometry
    for key in ('normal', 'smart'):
        center = (geometry[key]['top'] + geometry[key]['bottom']) / 2
        track_center = (geometry['track']['top'] + geometry['track']['bottom']) / 2
        assert abs(center - track_center) <= 2, geometry
    assert 0 <= geometry['track']['left'] - geometry['normal']['right'] <= 16, geometry
    assert 0 <= geometry['smart']['left'] - geometry['track']['right'] <= 16, geometry
    assert geometry['suffix'] in ('none', 'normal', '\"\"'), geometry
    assert geometry['labelFits'] and geometry['cashFits'] and not geometry['overflow'], geometry
    print('Toolbar edge alignment / compact mode gap PASS', geometry, flush=True)
    return geometry


'''
s = once(p.read_text(), 'def visible_note(driver: webdriver.Chrome):\n', helper+'def visible_note(driver: webdriver.Chrome):\n')
s = once(s, '            for viewport_width in (1440, 1100):\n                driver.set_window_size(viewport_width, 900)', '            for viewport_width in (1529, 1440, 1100, 1040):\n                driver.set_window_size(viewport_width, 900)')
s = once(s, '                assert_liquid_cash_display(driver)\n            driver.set_window_size(1440, 900)', '                assert_liquid_cash_display(driver)\n                assert_toolbar_alignment(driver)\n            driver.set_window_size(1440, 900)')
s = once(s, 'Vertical Options aligned scan controls PASS (1440 + 1100)', 'Vertical Options aligned toolbar + scan controls PASS (1529/1440/1100/1040)')
s = once(s, '                print("Native switch resting contrast PASS", key, appearance, flush=True)', '                if key == "vo_smart_fill":\n                    assert_toolbar_alignment(driver)\n                print("Native switch resting contrast PASS", key, appearance, flush=True)')
p.write_text(s)

p=Path('src/TERMINAL_CHANGELOG.md')
p.write_text(p.read_text().rstrip()+'''

## 2026-11-06 — Vertical Options aligned cash/portfolio toolbar and compact mode switch
- **Feature / production route:** Original desktop Vertical Options, `streamlit_app.py -> src/vertical_options_ui.py`, based on stacked-leg/OI release `f3ee85b5d363a622e9462bd651931c642f547902`.
- **Exact production file:** `src/vertical_options_ui.py` only. Coverage: `tests/test_vertical_options.py`, `scripts/test_vertical_options_scanner_browser.py`; this append-only history.
- **Root cause:** Portfolio Verticals was in a separate row above Liquid Cash; cash cards were 34px versus 38px buttons. Proportional mode columns left an empty gap beside the switch.
- **Change:** One toolbar uses the scan row's column boundaries. Cash and buttons share 38px height. A 250px bounded native mode group keeps NORMAL/switch/SMART close. Cash values stay fully visible; only long account labels can truncate. The portfolio callback, multi-account cash display and native preference behavior are preserved.
- **Must remain:** Existing pricing and 1:2+ natural-ASK Smart rules; cash field and account semantics; stacked legs/OI, filters, exports, quote caching and explicit Preview/Place approval. No new broker calls, polling or global patches.
- **Not changed:** Table component, shared spread math/store, Verti Phone, Risk, GEX, Holdings, OAuth, navigation, shared theme, terminal_core.py, streamlit_app.py and configuration.
- **Tests:** 144 desktop/phone tests and architecture/Python syntax passed locally. Browser regression now measures matching edges, 38px height, <=16px mode gaps, both switch states, unclipped cash and no overflow at 1529/1440/1100/1040px. Workbench/PR checks must pass before publication. The local browser blocked fixture navigation; no restriction was bypassed and no authenticated session or managed Cloud reboot is claimed. No live trades submitted.
- **Lesson:** Fix layout structure inside the feature, not with negative margins or unrelated global CSS. Implementation SHA recorded below after successful verification.
''')
changed=set(subprocess.check_output(['git','diff','--name-only'],text=True).splitlines())
assert changed == set(BASE), changed
print('Applied isolated alignment patch:', sorted(changed))
