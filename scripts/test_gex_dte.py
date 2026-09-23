"""GEX DTE snapping, persistence, and atomic ticker refresh regression checks."""
import copy
from datetime import date, datetime, timedelta
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src import gex_ui as core
from src import gex_ui_v3 as ui


def payload(dates):
    return {'OptionExpireDateResponse': {'ExpirationDate': [
        {'year':d.year,'month':d.month,'day':d.day} for d in dates
    ]}}


class DteTests(unittest.TestCase):
    def test_nearest_dates(self):
        today = date(2026, 9, 23)
        data = payload([today+timedelta(days=d) for d in [-3, 0, 2, 9, 23, 58]])
        for requested, expected in [(0,0), (1,0), (6,9), (16,9), (22,23), (45,58), (500,58)]:
            self.assertEqual(ui._nearest_expiration_dte(data, requested, today)[0], expected)
        with self.assertRaises(ValueError):
            ui._nearest_expiration_dte(payload([today-timedelta(days=1)]), 45, today)
        with self.assertRaises(ValueError):
            ui._nearest_expiration_dte({}, 45, today)

    def test_state_round_trip(self):
        state = copy.deepcopy(core.DEFAULT_STATE)
        state.update(tickers=['SPY','QQQ'], dte_overrides={'SPY':23,'QQQ':0})
        for _ in range(3):
            state = core._clean_state(state)
            self.assertEqual(state['dte_overrides'], {'SPY':23,'QQQ':0})
        state['dte_overrides'] = {'SPY':float('nan'),'QQQ':True,'BAD':45}
        self.assertEqual(core._clean_state(state)['dte_overrides'], {})

    def test_save_only_selected_ticker_and_failure_rollback(self):
        today = datetime.now(ZoneInfo('America/New_York')).date()
        state = copy.deepcopy(core.DEFAULT_STATE)
        state.update(tickers=['SPY','QQQ'], dte_overrides={'QQQ':60})
        session = {'_gex_state':state}
        old = {'maxDte':45}
        results = {'SPY':old, 'QQQ':{'maxDte':60}}
        saved = []
        def save(key, value):
            saved.append(copy.deepcopy(value))
            session['_gex_state'] = value
        with patch.object(ui.st,'session_state',session), \
             patch.object(ui._proven,'_background_job',return_value=None), \
             patch.object(ui._proven,'_sync_background_results'), \
             patch.object(core,'_call_api',return_value=payload([today+timedelta(days=23)])), \
             patch.object(core,'_save_state',side_effect=save), \
             patch.object(core,'_results',return_value=results), \
             patch.object(core,'_build_gex',side_effect=RuntimeError('API unavailable')) as build:
            with self.assertRaises(RuntimeError):
                ui._save_ticker_dte(object(),'test','SPY',22,None)
            self.assertFalse(saved)
            self.assertIs(results['SPY'],old)
            build.side_effect = None
            build.return_value = {'maxDte':23}
            self.assertEqual(ui._save_ticker_dte(object(),'test','SPY',22,None)[0],23)
            self.assertEqual(saved[-1]['dte_overrides'], {'SPY':23,'QQQ':60})
            self.assertEqual(results['QQQ'],{'maxDte':60})
            with patch.object(ui._proven,'_background_job',return_value={'status':'RUNNING'}):
                with self.assertRaises(ValueError):
                    ui._save_ticker_dte(object(),'test','SPY',9,None)
            with self.assertRaises(ValueError):
                ui._save_ticker_dte(None,'test','SPY',9,None)

    def test_links_preserve_table_and_query_state(self):
        class Params(dict):
            def get_all(self, key): return [self[key]]
        source = '<table class="gexv3-summary"><tr><td class="sym">SPY</td><td>45</td><td>$100</td></tr></table>'
        with patch.object(ui.st,'query_params',Params(other='kept',gex_delete='QQQ')):
            result = ui._inject_dte_editor_links(source)
        self.assertIn('gex_dte=SPY',result)
        self.assertIn('other=kept',result)
        self.assertNotIn('gex_delete',result)
        self.assertIn('<td>$100</td>',result)
        self.assertIn('Edit SPY DTE',result)


if __name__ == '__main__':
    unittest.main()
