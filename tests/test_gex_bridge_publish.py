"""Regression: refreshing unchanged delayed CBOE levels must still create a GitHub commit."""
import base64
import json
import unittest
from unittest.mock import patch

from src.gex_github_bridge import publish_latest_gex_cboe


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
            self.assertTrue(publish_latest_gex_cboe("test-token", payload))
        self.assertEqual([r.get_method() for r in calls], ["GET", "PUT", "GET"])


if __name__ == "__main__":
    unittest.main()
