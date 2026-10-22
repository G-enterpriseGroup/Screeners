"""Regression: refreshing unchanged delayed CBOE levels must still create a GitHub commit."""
import base64
import json
import io
import urllib.error
import unittest
from unittest.mock import patch

from src.gex_github_bridge import publish_latest_gex_cboe, publish_latest_gex


class Response:
    def __init__(self, body):
        self.body = json.dumps(body).encode()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self.body


class TestGexBridgePublish(unittest.TestCase):
    def test_unchanged_remote_still_puts_a_new_commit(self):
        payload = '"Ticker: SPY\\nCALLWALL,800,1"'
        remote = {
            "sha": "existing-blob",
            "content": base64.b64encode(payload.encode()).decode(),
        }
        calls = []

        def urlopen(request, timeout=15):
            calls.append(request)
            if request.get_method() == "PUT":
                body = json.loads(request.data)
                self.assertEqual(body["branch"], "gex-bridge-data")
                self.assertEqual(body["sha"], "existing-blob")
                return Response({"commit": {"sha": "new-commit"}})
            return Response(remote)

        with patch("src.gex_github_bridge.urllib.request.urlopen", side_effect=urlopen):
            self.assertEqual(publish_latest_gex_cboe("test-token", payload), "new-commit")
        self.assertEqual([r.get_method() for r in calls], ["GET", "PUT", "GET", "GET"])
        self.assertTrue(calls[2].full_url.endswith("?ref=new-commit"))

    def test_transient_conflict_rereads_sha_and_verifies_commit(self):
        payload = '"Ticker: SPY\nCALLWALL,800,1"'
        remote = {"sha": "new-blob", "content": base64.b64encode(payload.encode()).decode()}
        responses = [Response({"sha": "old-blob"}),
                     urllib.error.HTTPError("https://api.github.com", 409, "conflict", {}, io.BytesIO(b"conflict")),
                     Response(remote), Response({"commit": {"sha": "commit"}}),
                     Response(remote), Response(remote)]
        with (patch("src.gex_github_bridge.urllib.request.urlopen", side_effect=responses) as call,
              patch("src.gex_github_bridge.time.sleep")):
            self.assertEqual(publish_latest_gex_cboe("token", payload), "commit")
        self.assertEqual(json.loads(call.call_args_list[3].args[0].data)["sha"], "new-blob")

    def test_permission_failure_is_actionable_and_not_retried(self):
        error = urllib.error.HTTPError("https://api.github.com", 403, "forbidden", {},
                                       io.BytesIO(b"Resource not accessible by integration"))
        with patch("src.gex_github_bridge.urllib.request.urlopen", side_effect=error) as call:
            with self.assertRaisesRegex(RuntimeError, "GitHub HTTP 403"):
                publish_latest_gex_cboe("token", "payload")
        self.assertEqual(call.call_count, 1)

    def test_wrong_readback_retries_then_fails_instead_of_reporting_success(self):
        remote = {"sha": "blob", "content": base64.b64encode(b"wrong").decode()}
        def response(request, timeout=15):
            return Response({"commit": {"sha": "commit"}} if request.get_method() == "PUT" else remote)
        with (patch("src.gex_github_bridge.urllib.request.urlopen", side_effect=response) as call,
              patch("src.gex_github_bridge.time.sleep")):
            with self.assertRaisesRegex(RuntimeError, "after 3 attempts"):
                publish_latest_gex_cboe("token", "payload")
        self.assertEqual(sum(c.args[0].get_method() == "PUT" for c in call.call_args_list), 3)

    def test_missing_token_does_not_make_network_calls(self):
        with patch("src.gex_github_bridge.urllib.request.urlopen") as call:
            with self.assertRaisesRegex(RuntimeError, "token is not configured"):
                publish_latest_gex_cboe("", "payload")
        call.assert_not_called()

    def test_etrade_keeps_boolean_contract_and_separate_destination(self):
        with patch("src.gex_github_bridge._publish_path", return_value="commit") as publish:
            self.assertIs(publish_latest_gex("token", "payload"), True)
        self.assertEqual(publish.call_args.args[2], "bridge/latest_gex.txt")


if __name__ == "__main__":
    unittest.main()
