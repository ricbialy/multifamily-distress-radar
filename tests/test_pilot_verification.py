from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from distress_radar.pilot_verification import _controlled_copy


class PilotVerificationTests(unittest.TestCase):
    def test_controlled_copy_returns_the_exact_mutated_mls_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "Agent Single Line - COM.csv"
            destination = root / "controlled" / source.name
            source.write_text(
                "MLS # Link,St,Address\n"
                "A12057467,A,1440 SW 4th St\n"
                "A12056557,A,934 Meridian Ave\n",
                encoding="utf-8",
            )

            description, mls_number = _controlled_copy(source, destination)

            self.assertEqual(mls_number, "A12057467")
            self.assertIn("A12057467", description)
            self.assertIn(
                "A12057467,W,1440 SW 4th St", destination.read_text()
            )


if __name__ == "__main__":
    unittest.main()
