"""Public example, for execution inside the worker container."""

import json
import subprocess
import sys
import unittest
from pathlib import Path


class Example(unittest.TestCase):
    def test_migration(self):
        result = subprocess.run(
            [sys.executable, "-I", "migrate.py"],
            input=Path("settings.json").read_bytes(),
            capture_output=True,
            timeout=2,
            check=True,
        )
        self.assertEqual(
            json.loads(result.stdout),
            {
                "version": 2,
                "endpoint": "service.invalid",
                "timeout_seconds": 30,
                "label": "billing",
            },
        )


if __name__ == "__main__":
    unittest.main()
