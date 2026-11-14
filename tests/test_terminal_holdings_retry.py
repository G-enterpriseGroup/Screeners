"""Exercise the production all-tab holdings primer with synthetic broker failures."""

import ast
from pathlib import Path
from types import SimpleNamespace
import unittest


SOURCE = Path(__file__).resolve().parents[1] / "streamlit_app.py"


class TerminalHoldingsRetryTest(unittest.TestCase):
    def test_failed_reads_do_not_block_every_widget_rerun(self):
        source = ast.parse(SOURCE.read_text())
        function = next(node for node in source.body if isinstance(node, ast.FunctionDef) and node.name == "_cache_all_account_holdings")
        clock = [1000.0]
        state = {"etrade_access_token": {"oauth_token": "synthetic-a"}}

        class Client:
            inventory_calls = 0
            portfolio_calls = 0

            def list_accounts(self):
                self.inventory_calls += 1
                if self.inventory_calls == 1:
                    raise TimeoutError("synthetic inventory timeout")
                return [{"accountIdKey": "synthetic-account"}]

            def get_portfolio(self, account_key):
                self.portfolio_calls += 1
                if self.portfolio_calls == 1:
                    raise TimeoutError("synthetic portfolio timeout")
                return [{"Symbol": "SYNTH"}]

        client = Client()
        scope = {
            "st": SimpleNamespace(session_state=state),
            "time": SimpleNamespace(time=lambda: clock[0]),
            "_live_etrade_client": lambda: client,
            "_etrade_data_cache": SimpleNamespace(session_namespace=lambda token: token["oauth_token"]),
        }
        exec(compile(ast.Module(body=[function], type_ignores=[]), str(SOURCE), "exec"), scope)
        prime = scope["_cache_all_account_holdings"]

        prime()
        prime()
        self.assertEqual(client.inventory_calls, 1)
        clock[0] += 301
        prime()
        prime()
        self.assertEqual((client.inventory_calls, client.portfolio_calls), (2, 1))
        clock[0] += 301
        prime()
        prime()
        self.assertEqual(client.portfolio_calls, 2)
        self.assertEqual(state["etrade_holdings"]["synthetic-account"], [{"Symbol": "SYNTH"}])

        state["etrade_access_token"] = {"oauth_token": "synthetic-b"}
        prime()
        self.assertEqual(client.portfolio_calls, 3)


if __name__ == "__main__":
    unittest.main()
