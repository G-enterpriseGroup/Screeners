"""Synthetic OAuth control regression; no broker credentials or live requests."""

import unittest

from streamlit.testing.v1 import AppTest


FIXTURE = """
import streamlit as st
from types import SimpleNamespace
from src.etrade_connection_ui_v2 import render_compact_etrade_connection

if not st.session_state.get('etrade_access_token'):
    st.session_state.setdefault('etrade_request', {
        'oauth_token': 'synthetic', 'oauth_token_secret': 'synthetic',
        'authorization_url': 'https://example.invalid/',
    })

def complete(*args):
    if args[4] != 'ABC123':
        raise ValueError('Invalid synthetic verifier')
    return {'oauth_token': 'synthetic-access'}

def renew():
    st.session_state['renew_count'] = st.session_state.get('renew_count', 0) + 1

def clear(**kwargs):
    st.session_state['clear_count'] = st.session_state.get('clear_count', 0) + 1
    st.session_state.pop('etrade_access_token', None)

def begin(*args):
    st.session_state['begin_count'] = st.session_state.get('begin_count', 0) + 1
    return SimpleNamespace(oauth_token='synthetic', oauth_token_secret='synthetic', authorization_url='https://example.invalid/')

render_compact_etrade_connection(
    credentials_factory=lambda: ('synthetic', 'synthetic', 'sandbox'),
    live_client_factory=lambda: SimpleNamespace(renew=renew) if st.session_state.get('etrade_access_token') else None,
    session_timer=lambda connected: st.caption('CONNECTED' if connected else 'DISCONNECTED'),
    begin_authorization=begin,
    complete_authorization=complete,
    etrade_error_type=ValueError,
    touch_session=lambda: None,
    clear_runtime=clear,
    refresh_accounts=lambda client: [],
)
"""


class ETradeConnectionUiTest(unittest.TestCase):
    def test_connection_strip_buttons(self):
        app = AppTest.from_string(FIXTURE).run()
        app.button(key="etrade_connect").click().run()
        self.assertEqual(app.session_state["begin_count"], 1)
        app.text_input(key="etrade_verifier").set_value("ABC123").run()
        app.button(key="etrade_verify").click().run()
        app.button(key="etrade_renew").click().run()
        self.assertEqual(app.session_state["renew_count"], 1)
        app.button(key="etrade_disconnect").click().run()
        self.assertEqual(app.session_state["clear_count"], 1)
        self.assertNotIn("etrade_access_token", app.session_state)
        app.button(key="etrade_lock_api").click().run()
        self.assertEqual(app.session_state["clear_count"], 2)
        self.assertFalse(app.exception)

    def test_paste_and_verify_share_the_compact_row(self):
        app = AppTest.from_string(FIXTURE).run()
        self.assertFalse(app.exception)
        self.assertTrue(app.button(key="etrade_verify").disabled)
        paste = [element.value for element in app.get("html") if 'id="etrade-oauth-paste"' in element.value]
        self.assertEqual(len(paste), 1)
        self.assertIn("navigator.clipboard.readText()", paste[0])

        app.text_input(key="etrade_verifier").set_value("wrong").run()
        app.button(key="etrade_verify").click().run()
        self.assertFalse(app.exception)
        self.assertTrue(app.error)
        self.assertNotIn("etrade_access_token", app.session_state)

        app.text_input(key="etrade_verifier").set_value("ABC123").run()
        app.button(key="etrade_verify").click().run()
        self.assertFalse(app.exception)
        self.assertEqual(app.session_state["etrade_access_token"]["oauth_token"], "synthetic-access")
        self.assertNotIn("etrade_request", app.session_state)


if __name__ == "__main__":
    unittest.main()
