"""Isolated R.I.C. browser fixture using synthetic broker-shaped responses.

Run with --serve for Firefox/CUA testing, or --smoke for a headless AppTest.
The fixture renders the actual production feature and navigation components;
it never reads credentials or connects to a broker. Live placement raises.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

FIXTURE = r'''
import ast
from datetime import date, datetime, timedelta
import json
import os
from pathlib import Path
import time
from zoneinfo import ZoneInfo
import streamlit as st
from src import reverse_iron_condor_ui as ric
from src.etrade_data_cache import session_namespace
from src.tab_bar_v4 import render_terminal_tab_bar
from src.theme import _TYPING_CARET_CSS
from src.layout_guardrails import _LAYOUT_GUARDRAIL_CSS

LOG = Path(os.environ['RIC_BROWSER_TEST_LOG'])
TODAY = datetime.now(ZoneInfo('America/New_York')).date()
EXPIRIES = [TODAY + timedelta(days=7)] + [
    date(TODAY.year + offset, 6, 18) for offset in (1, 2, 3)]

def record(event, **values):
    with LOG.open('a') as output:
        output.write(json.dumps({'event':event, **values}) + '\n')

def premium(side, strike):
    distance = abs(float(strike) - 100)
    return {5:1.5, 10:.75, 15:.25}.get(distance, 5.0)

class SyntheticBroker:
    is_offline = False
    _namespace = session_namespace({'oauth_token':'RIC-SYNTHETIC-TOKEN'})

    def get_quote(self, symbol, **kwargs):
        record('quote', symbol=symbol)
        if ':' not in symbol:
            return {'QuoteResponse':{'QuoteData':[{'symbol':symbol,
                'companyName':'Synthetic R.I.C. Fund', 'lastTrade':100., 'bid':99.99, 'ask':100.01}]}}
        quotes = []
        for contract in symbol.split(','):
            root, year, month, day, side, strike = contract.split(':')
            expiry = date(int(year), int(month), int(day))
            last = premium(side, strike)
            quotes.append({'Product':{'symbol':root,'securityType':'OPTN',
                'expiryYear':expiry.year,'expiryMonth':expiry.month,'expiryDay':expiry.day,
                'callPut':side,'strikePrice':float(strike)},
                'All':{'bid':last-.05,'ask':last+.05,
                    'osiKey':f'{root:-<6}{expiry:%y%m%d}{side[0]}{round(float(strike)*1000):08d}'},
                'dateTimeUTC':time.time(),'quoteStatus':'REALTIME'})
        return {'QuoteResponse':{'QuoteData':quotes}}

    def get_option_expirations(self, symbol):
        record('expirations',symbol=symbol)
        return {'OptionExpireDateResponse':{'ExpirationDate':[
            {'year':expiry.year,'month':expiry.month,'day':expiry.day}
            for expiry in [TODAY+timedelta(days=6)] + EXPIRIES]}}

    def get_option_chain(self, symbol, year, month, day, **kwargs):
        record('chain',symbol=symbol,expiry=date(year,month,day).isoformat())
        expiry = date(year,month,day)
        pairs=[]
        for strike in (85,90,95,105,110,115):
            def option(side):
                last=premium(side,strike)
                return {'strikePrice':strike,'lastPrice':last,'bid':last-.05,'ask':last+.05,
                    'volume':100,'openInterest':1000,
                    'osiKey':f'{symbol:-<6}{expiry:%y%m%d}{side}{strike*1000:08d}'}
            pairs.append({'Call':option('C'),'Put':option('P')})
        return {'OptionChainResponse':{'OptionPair':pairs}}

    def list_accounts(self):
        record('accounts')
        return [{'accountIdKey':'synthetic-5474','accountId':'10005474','accountDesc':'Synthetic Account'},
                {'accountIdKey':'synthetic-1234','accountId':'10001234','accountDesc':'Other Synthetic'}]

    def get_balance(self, account_key):
        return {'BalanceResponse':{'Computed':{'cashAvailableForInvestment':12345.67}}}

    def get_portfolio(self, account_key, **kwargs):
        return {'PortfolioResponse':{'AccountPortfolio':[]}}

    def preview_order(self, account_key, payload):
        record('preview',account=account_key,payload=payload)
        assert len(payload['PreviewOrderRequest']['Order'][0]['Instrument']) == 4
        return {'PreviewOrderResponse':{'PreviewIds':[{'previewId':123456}],
            'estimatedCommission':0.0,'totalOrderValue':157.0}}

    def place_order(self, *args, **kwargs):
        record('place')
        raise AssertionError('Browser verification must never submit an order')

st.set_page_config(page_title='R.I.C. synthetic browser verification',layout='wide')
root=Path(os.environ['RIC_BROWSER_ROOT'])
core=ast.parse((root/'src/terminal_core.py').read_text())
for node in core.body:
    if isinstance(node,ast.Expr) and isinstance(node.value,ast.Call) and node.value.args:
        css=node.value.args[0]
        if isinstance(css,ast.Constant) and isinstance(css.value,str) and '<style>' in css.value:
            st.html(css.value)
st.html(_TYPING_CARET_CSS)
st.html(_LAYOUT_GUARDRAIL_CSS)
ric.hydrate_gex_snapshots=lambda *args,**kwargs: None
ric.persist_gex_snapshots=lambda *args,**kwargs: None
st.session_state.setdefault('etrade_access_unlocked',True)
st.session_state.setdefault('etrade_access_token',{'oauth_token':'RIC-SYNTHETIC-TOKEN',
    'oauth_token_secret':'RIC-SYNTHETIC-SECRET','issued_at':time.time()})
st.session_state.setdefault('etrade_last_activity_at',time.time())
st.session_state.setdefault('terminal_tab_state',{'order':['VERTICAL OPTIONS','R.I.C.','VERTI PHONE'],
    'active':'R.I.C.'})
order,active=render_terminal_tab_bar('ric-synthetic-browser')
if active=='R.I.C.':
    st.html('<div style="background:#fb8b1e;color:black;font:bold 24px Courier New">R.I.C. // REVERSE IRON CONDOR</div>')
    ric.render_reverse_iron_condor(SyntheticBroker(),lambda:None,vault_key='ric-synthetic-browser')
else:
    st.info('SYNTHETIC NAVIGATION CHECK // '+active+' // Return to R.I.C. to continue.')
st.caption('SYNTHETIC DATA ONLY // No connected brokerage // Live placement disabled')
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--serve', action='store_true')
    parser.add_argument('--smoke', action='store_true')
    parser.add_argument('--port', type=int, default=8767)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='terminal_ric_browser_') as directory:
        path = Path(directory)
        fixture = path / 'ric_fixture.py'
        fixture.write_text(FIXTURE)
        env = dict(os.environ, PYTHONPATH=str(ROOT), RIC_BROWSER_ROOT=str(ROOT),
                   RIC_BROWSER_TEST_LOG=str(path / 'events.jsonl'))
        if args.serve:
            print('R.I.C. synthetic fixture: http://localhost:' + str(args.port), flush=True)
            print('Synthetic event log: ' + env['RIC_BROWSER_TEST_LOG'], flush=True)
            result = subprocess.run([sys.executable, '-m', 'streamlit', 'run', str(fixture),
                '--server.address=127.0.0.1', '--server.port=' + str(args.port),
                '--server.headless=true', '--browser.gatherUsageStats=false'], env=env)
            return result.returncode
        os.environ.update({key: env[key] for key in ('RIC_BROWSER_ROOT', 'RIC_BROWSER_TEST_LOG')})
        from streamlit.testing.v1 import AppTest
        app = AppTest.from_file(str(fixture), default_timeout=30).run()
        assert not app.exception, [value.message for value in app.exception]
        assert any(value.label == 'Ticker' for value in app.text_input)
        app.text_input(key='ric_symbol_value').set_value('SPY')
        next(button for button in app.button if button.label == 'SCAN ALL YEARS').click().run()
        import time
        deadline = time.monotonic() + 10
        while (not app.exception and not app.session_state['ric_scan_result']['scan_complete']
               and time.monotonic() < deadline):
            time.sleep(.02)
            app.run()
        assert not app.exception, [value.message for value in app.exception]
        result = app.session_state['ric_scan_result']
        assert result['scan_complete'] and result['loaded_expirations'] == 4
        rows = list(result['candidates'].iter_rows())
        assert len(rows) == 20
        assert all(row['reward_risk'] >= 2 and row['put_sell_strike'] < row['put_buy_strike']
                   < 100 < row['buy_strike'] < row['sell_strike'] for row in rows)
        events = [json.loads(line) for line in (path / 'events.jsonl').read_text().splitlines()]
        assert not any(event['event'] in {'preview', 'place'} for event in events)
        print('R.I.C. production renderer synthetic smoke PASS')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
