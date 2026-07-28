import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from distress_radar.cli import main


class FixtureCliTests(unittest.TestCase):
    def test_fixture_demo_command_requires_no_city_or_credentials(self) -> None:
        fixtures = Path(__file__).parent / "fixtures"
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                main(
                    [
                        "fixture-demo",
                        "--matrix",
                        str(fixtures / "matrix_20.csv"),
                        "--off-market",
                        str(fixtures / "off_market.csv"),
                        "--output-dir",
                        str(output),
                        "--generated-at",
                        "2026-07-24T12:00:00+00:00",
                    ]
                )
            summary = json.loads(stdout.getvalue())
        self.assertEqual(summary["canonical_property_count"], 22)
        self.assertTrue(summary["json_path"].endswith("recommendations.json"))


if __name__ == "__main__":
    unittest.main()
