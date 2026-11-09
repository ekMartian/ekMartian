import json
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

from scripts import codeforces


class CodeforcesTests(unittest.TestCase):
    def test_solved_count_deduplicates_across_pages_and_ignores_failed_attempts(self):
        first = {"id": 10, "verdict": "OK", "problem": {"contestId": 1, "index": "A"}}
        pages = [[first, {**first, "id": 9, "verdict": "WRONG_ANSWER"}], [{**first, "id": 8}]]
        with patch.object(codeforces, "PAGE_SIZE", 2), patch.object(codeforces, "api", side_effect=pages) as api:
            self.assertEqual(codeforces.solved_problems(), 1)
            self.assertEqual([call.kwargs["from"] for call in api.call_args_list], [1, 3])

    def test_checked_in_snapshot_matches_valid_svg(self):
        data = json.loads(codeforces.DATA.read_text())
        codeforces.validate(data)
        svg = codeforces.render(data)
        self.assertEqual(codeforces.SVG.read_text(), svg)
        ET.fromstring(svg)
