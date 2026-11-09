import copy
import unittest
import xml.etree.ElementTree as ET

from scripts import dashboard


class DashboardTests(unittest.TestCase):
    def test_published_assets_match_validated_snapshots(self):
        for name, (validate, render) in dashboard.RENDERERS.items():
            with self.subTest(dataset=name):
                data = dashboard.read(name)
                validate(data)
                svg = render(data)
                self.assertEqual(svg, (dashboard.ROOT / "assets" / f"{name}.svg").read_text())
                self.assertLess(len(svg), 30_000)
                ET.fromstring(svg)

    def test_source_rejects_repository_strings_and_unknown_labels(self):
        data = dashboard.read("source")
        for key, value in (
            ("project_categories", {"https://github.com/private-name": data["projects"]}),
            ("source_bytes", {"private/source/path": 10}),
            ("private_repo_names", ["private/example"]),
        ):
            with self.subTest(key=key):
                with self.assertRaises(ValueError):
                    dashboard.validate_repository({**data, key: value})

    def test_calendar_checks_aggregate_consistency(self):
        data = copy.deepcopy(dashboard.read("calendar"))
        data["years"][-1]["contributions"] += 1
        with self.assertRaises(ValueError):
            dashboard.validate_calendar(data)
