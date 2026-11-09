"""Fetch and publish only monthly GitHub contribution aggregates."""
import argparse
import calendar
import datetime as dt
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import urllib.request
from html.parser import HTMLParser

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data/activity.json"
SVG = ROOT / "assets/activity.svg"
LOGIN = "ekMartian"
MONTHS = 6
COVERAGE = "github-profile-calendar"
QUERY = """
query($login:String!, $from:DateTime!, $to:DateTime!) {
  user(login:$login) { contributionsCollection(from:$from,to:$to) {
    contributionCalendar { weeks { contributionDays { date contributionCount } } }
  } }
}
"""


def period(today):
    end = today.replace(day=1) - dt.timedelta(days=1)
    index = end.year * 12 + end.month - MONTHS
    start = dt.date(index // 12, index % 12 + 1, 1)
    return start, end


def dates(start, end):
    return (start + dt.timedelta(days=i) for i in range((end - start).days + 1))


class CalendarHTML(HTMLParser):
    def __init__(self):
        super().__init__()
        self.cells = {}
        self.tooltips = {}
        self.target = None

    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        if tag in ("td", "div") and attrs.get("data-date") and attrs.get("id"):
            self.cells[attrs["id"]] = attrs["data-date"]
        if tag == "tool-tip":
            self.target = attrs.get("for")

    def handle_data(self, data):
        if self.target:
            self.tooltips[self.target] = self.tooltips.get(self.target, "") + data

    def handle_endtag(self, tag):
        if tag == "tool-tip":
            self.target = None

    def counts(self):
        result = {}
        for key, day in self.cells.items():
            label = self.tooltips.get(key, "").strip()
            match = re.match(r"^(No|[0-9,]+) contributions? on ", label)
            if match:
                count = match[1]
                result[day] = 0 if count == "No" else int(count.replace(",", ""))
        return result


def public_days():
    url = f"https://github.com/users/{LOGIN}/contributions"
    request = urllib.request.Request(url, headers={"User-Agent": "ekMartian-profile-calendar", "Accept-Language": "en"})
    with urllib.request.urlopen(request, timeout=25) as response:
        page = response.read(2_000_001)
    if len(page) > 2_000_000:
        raise ValueError("Unexpected calendar size")
    parser = CalendarHTML()
    parser.feed(page.decode())
    return parser.counts()


def graphql_days(start, end):
    response = subprocess.run(
        ["gh", "api", "graphql", "-f", f"query={QUERY}", "-f", f"login={LOGIN}",
         "-f", f"from={start}T00:00:00Z", "-f", f"to={end}T23:59:59Z"],
        check=True, capture_output=True, text=True, timeout=30,
    )
    calendar_data = json.loads(response.stdout)["data"]["user"]["contributionsCollection"]["contributionCalendar"]
    return {day["date"]: day["contributionCount"] for week in calendar_data["weeks"] for day in week["contributionDays"]}


def snapshot(days, start, end):
    totals = {}
    for day in dates(start, end):
        count = days.get(str(day))
        if type(count) is not int or not 0 <= count <= 100_000:
            raise ValueError("Calendar coverage incomplete")
        month = str(day)[:7]
        totals[month] = totals.get(month, 0) + count
    result = {
        "schema_version": 1, "coverage": COVERAGE, "period_start": str(start), "period_end": str(end),
        "total_contributions": sum(totals.values()),
        "months": [{"month": month, "contributions": count} for month, count in totals.items()],
    }
    validate(result)
    return result


def validate(data):
    if set(data) != {"schema_version", "coverage", "period_start", "period_end", "total_contributions", "months"}:
        raise ValueError("Unexpected fields")
    if type(data["schema_version"]) is not int or data["schema_version"] != 1 or data["coverage"] != COVERAGE:
        raise ValueError("Unsupported schema")
    end = dt.date.fromisoformat(data["period_end"])
    start, expected_end = period(end + dt.timedelta(days=1))
    if str(start) != data["period_start"] or str(expected_end) != data["period_end"]:
        raise ValueError("Invalid window")
    months = data["months"]
    if type(months) is not list or len(months) != MONTHS:
        raise ValueError("Invalid months")
    expected = [str(day)[:7] for day in dates(start, end) if day.day == 1]
    for item, month in zip(months, expected):
        if set(item) != {"month", "contributions"} or item["month"] != month:
            raise ValueError("Invalid month")
        if type(item["contributions"]) is not int or not 0 <= item["contributions"] <= 3_100_000:
            raise ValueError("Invalid contribution count")
    if type(data["total_contributions"]) is not int or data["total_contributions"] != sum(item["contributions"] for item in months):
        raise ValueError("Invalid total")


def check_continuity(previous, current):
    old = {row["month"]: row["contributions"] for row in previous["months"]}
    if current["period_end"] < previous["period_end"]:
        raise ValueError("Snapshot went backwards")
    for row in current["months"]:
        prior = old.get(row["month"], 0)
        if prior - row["contributions"] > max(5, prior // 5):
            raise ValueError("Contribution coverage changed substantially")


def svg(data):
    validate(data)
    months = data["months"]
    peak = max(1, *(item["contributions"] for item in months))
    bars = []
    for i, item in enumerate(months):
        x = 52 + i * 100
        count = item["contributions"]
        height = max(2, round(count / peak * 116))
        y = 245 - height
        month = calendar.month_abbr[int(item["month"][5:7])].upper()
        bars.append(f'<rect x="{x}" y="{y}" width="56" height="{height}" rx="4" fill="#86edd1"/>')
        bars.append(f'<text x="{x + 28}" y="{y - 8}" fill="#eef6ff">{count}</text>')
        bars.append(f'<text x="{x + 28}" y="268">{month}</text>')
    start, end = data["period_start"], data["period_end"]
    total = data["total_contributions"]
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="660" height="315" viewBox="0 0 660 315" role="img" aria-labelledby="title desc">
  <title id="title">GitHub contribution activity</title>
  <desc id="desc">{total} contributions between {start} and {end} across six completed UTC months.</desc>
  <rect x="1" y="1" width="658" height="313" rx="17" fill="#101a29" stroke="#45556b"/>
  <path d="M32 113H628M32 245H628" stroke="#344359"/>
  <g font-family="ui-monospace, SFMono-Regular, Menlo, monospace" fill="#acc0d5">
    <text x="33" y="38" fill="#86edd1" font-size="15" letter-spacing="1.5">GITHUB CONTRIBUTION ACTIVITY</text>
    <text x="33" y="82" fill="#f3f7ff" font-size="36" font-weight="700">{total:,}</text>
    <text x="33" y="101" font-size="13">CONTRIBUTIONS</text>
    <text x="628" y="82" text-anchor="end" font-size="15">{start}  /  {end}</text>
    <g text-anchor="middle" font-size="15">{''.join(bars)}</g>
    <text x="33" y="294" font-size="13">GITHUB PROFILE CALENDAR · COMPLETED UTC MONTHS</text>
  </g>
</svg>
'''


def write_atomic(path, content):
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as file:
        temporary = Path(file.name)
        file.write(content)
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("refresh", "bootstrap", "collect", "apply", "check"))
    args = parser.parse_args()
    previous = json.loads(DATA.read_text()) if DATA.exists() else None
    if previous is not None:
        validate(previous)
    if args.mode == "check":
        if previous is None or SVG.read_text() != svg(previous):
            raise ValueError("Snapshot and graphic differ")
        return
    start, end = period(dt.datetime.now(dt.timezone.utc).date())
    if args.mode == "bootstrap":
        if previous is not None:
            raise ValueError("Bootstrap requires an empty snapshot")
        current = snapshot(graphql_days(start, end), start, end)
    elif args.mode == "apply":
        if previous is None:
            raise ValueError("Missing last verified snapshot")
        current = json.loads(sys.stdin.read(4096))
        validate(current)
        if current["period_start"] != str(start) or current["period_end"] != str(end):
            raise ValueError("Unexpected collection window")
        check_continuity(previous, current)
    else:
        if previous is None:
            raise ValueError("Missing last verified snapshot")
        for fetch in (public_days, lambda: graphql_days(start, end)):
            try:
                current = snapshot(fetch(), start, end)
                check_continuity(previous, current)
                break
            except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
                continue
        else:
            raise ValueError("Calendar unavailable or coverage differs from last snapshot")
    if args.mode == "collect":
        print(json.dumps(current, separators=(",", ":")))
        return
    rendered = svg(current)
    write_atomic(DATA, json.dumps(current, indent=2) + "\n")
    write_atomic(SVG, rendered)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        sys.exit("Activity update failed validation or upstream access. No publish step should run.")
