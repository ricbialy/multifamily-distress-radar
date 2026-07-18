import json
import unittest
from unittest.mock import patch

from distress_radar.alerts import deliver_webhook


class AlertDeliveryTests(unittest.TestCase):
    @patch("distress_radar.alerts.urllib.request.urlopen")
    def test_webhook_uses_optional_bearer_token(self, urlopen) -> None:
        response = urlopen.return_value.__enter__.return_value
        response.status = 202
        response.read.return_value = b'{"ok":true}'
        receipt = deliver_webhook("https://example.test/hook", [{"id": 1}], "secret")
        request = urlopen.call_args.args[0]
        self.assertEqual(request.get_header("Authorization"), "Bearer secret")
        self.assertEqual(json.loads(request.data)["event"], "distress_radar.alerts")
        self.assertEqual(receipt["status"], 202)


if __name__ == "__main__":
    unittest.main()
