import datetime as dt
import json
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

from scripts import activity, repository_inventory


class ActivityTests(unittest.TestCase):
    def setUp(self):
        self.start, self.end = activity.period(dt.date(2024, 3, 1))
        self.days = {str(day): 1 for day in activity.dates(self.start, self.end)}

    def test_period_crosses_year_and_handles_leap_day(self):
        self.assertEqual(self.start, dt.date(2023, 9, 1))
        self.assertEqual(self.end, dt.date(2024, 2, 29))
        result = activity.snapshot(self.days, self.start, self.end)
        self.assertEqual(result["months"][-1], {"month": "2024-02", "contributions": 29})
        self.assertEqual(result["total_contributions"], len(self.days))

    def test_missing_days_and_non_numeric_values_fail_closed(self):
        for value in (None, True, "1", -1):
            with self.subTest(value=value):
                self.days[str(self.end)] = value
                with self.assertRaises(ValueError):
                    activity.snapshot(self.days, self.start, self.end)

    def test_html_joins_only_calendar_cells_and_tooltips(self):
        parser = activity.CalendarHTML()
        parser.feed('''
          <td data-date="2024-01-02" id="cell1"></td>
          <td data-date="2024-01-03" id="cell2"></td>
          <td data-date="2024-01-04" id="cell3"></td>
          <tool-tip for="cell1">No contributions on January 2nd.</tool-tip>
          <tool-tip for="cell2">1,234 contributions on January 3rd.</tool-tip>
          <tool-tip for="cell3">1 contribution on January 4th.</tool-tip>
          <tool-tip for="other">99 contributions on January 4th.</tool-tip>
        ''')
        self.assertEqual(parser.counts(), {"2024-01-02": 0, "2024-01-03": 1234, "2024-01-04": 1})

    def test_schema_rejects_names_and_calendar_rejects_large_drop(self):
        previous = activity.snapshot(self.days, self.start, self.end)
        for day in self.days:
            self.days[day] = 0
        current = activity.snapshot(self.days, self.start, self.end)
        with self.assertRaises(ValueError):
            activity.check_continuity(previous, current)
        with self.assertRaises(ValueError):
            activity.validate({**previous, "repository": "must-not-be-accepted"})
        previous["months"][0]["private_name"] = "must-not-be-accepted"
        with self.assertRaises(ValueError):
            activity.svg(previous)

    def test_checked_in_assets_are_deterministic_and_valid_xml(self):
        data = json.loads(activity.DATA.read_text())
        self.assertEqual(activity.svg(data), activity.SVG.read_text())
        for name in ("hero.svg", "activity.svg"):
            path = activity.SVG.with_name(name)
            self.assertLess(path.stat().st_size, 25_000)
            root = ET.parse(path).getroot()
            for element in root.iter():
                self.assertNotIn(element.tag.rsplit("}", 1)[-1], {"script", "foreignObject", "image"})
                self.assertFalse(any(key.lower().startswith("on") or key.endswith("href") for key in element.attrib))


class InventoryTests(unittest.TestCase):
    def test_source_exclusions(self):
        for path in (
            "node_modules/pkg/app.ts", "Vendor/lib/main.cpp", "web/dist/main.js",
            "src/icon.generated.swift", "src/worker.min.js", "src/runtime.d.ts", "image.png",
        ):
            self.assertIsNone(repository_inventory.language_for(path), path)
        self.assertEqual(repository_inventory.language_for("Sources/Render.metal"), "Metal")
        self.assertEqual(repository_inventory.language_for("Sources/Interop.mm"), "Objective-C++")

    def test_discovery_coverage_and_duplicate_trees(self):
        base = {
            "private": True, "fork": False, "archived": False, "disabled": False, "is_template": False,
            "size": 10, "default_branch": "main", "pushed_at": None,
        }
        selected = [{**base, "id": i, "full_name": f"owner/sample-{i}"} for i in (1, 2)]
        baseline = selected + [{**base, "id": 3, "full_name": "owner/unavailable"}]
        tree = {"sha": "same", "tree": [{"type": "blob", "mode": "100644", "path": "src/app.swift", "size": 200}]}
        with patch.object(repository_inventory, "api", side_effect=(selected, baseline, tree, tree)):
            result = repository_inventory.collect("fake-read", "fake-metadata")
        self.assertEqual(result["known_uncovered_private_repositories"], 1)
        self.assertEqual(result["repositories_excluded"], 1)
        self.assertEqual(result["source_bytes_by_language"], {"Swift": 200})
        self.assertNotIn("owner/", json.dumps(result))
