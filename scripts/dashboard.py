"""Publish contribution and source analytics using fixed, numeric schemas."""
import argparse
import calendar
from collections import Counter
import datetime as dt
from html import escape
import json
import os
import subprocess
import sys
from urllib.parse import quote

from scripts.activity import ROOT, dates, graphql_days, public_days, write_atomic
from scripts.repository_inventory import EXTENSIONS, LIST, api, language_for

LANGUAGES = set(EXTENSIONS.values())
CATEGORIES = ("Native desktop", "Web platform", "Browser tooling")
PALETTE = ("#7cebd2", "#9ab6ff", "#e4aeff", "#f6c780", "#84c8ff")
ERRORS = (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError)


def read(name):
    path = ROOT / "data" / f"{name}.json"
    return json.loads(path.read_text()) if path.exists() else None


def utc_today():
    return dt.datetime.now(dt.timezone.utc).date()


def number(value, maximum=10**12):
    if type(value) is not int or not 0 <= value <= maximum:
        raise ValueError("Invalid numeric value")
    return value


def day(value):
    date = dt.date.fromisoformat(value)
    if str(date) != value:
        raise ValueError("Invalid date")
    return date


def fields(data, keys):
    if type(data) is not dict or set(data) != set(keys.split()):
        raise ValueError("Unexpected fields")


def first_month(end):
    return dt.date(end.year - (end.month < 12), end.month % 12 + 1, 1)


def calendar_snapshot(today, previous):
    end = today - dt.timedelta(days=1)
    heat_start = end - dt.timedelta(days=181)
    first = first_month(end)
    try:
        public = public_days()
    except ERRORS:
        public = {}
    monthly = Counter()
    annual = []
    recent = {}
    for year in (end.year - 1, end.year):
        start = dt.date(year, 1, 1)
        stop = min(end, dt.date(year, 12, 31))
        # Prefer anonymous calendar data where it covers the full requested window.
        candidates = public if all(str(d) in public for d in dates(start, stop)) else graphql_days(start, stop)
        total = 0
        for date in dates(start, stop):
            count = number(candidates[str(date)], 100_000)
            if date >= first:
                monthly[str(date)[:7]] += count
            if date >= heat_start:
                recent[str(date)] = count
            total += count
        annual.append({"year": year, "contributions": total})
    result = {
        "schema_version": 2, "coverage": "github-profile-calendar", "through": str(end),
        "years": annual, "months": dict(sorted(monthly.items())), "heatmap_start": str(heat_start),
        "daily": list(recent.values()),
    }
    validate_calendar(result)
    if previous:
        old_through = day(previous["through"])
        if end < old_through:
            raise ValueError("Snapshot went backwards")
        old = previous["months"]
        # Compare complete months only. A fallback with missing private activity must not replace the chart.
        for month, count in result["months"].items():
            if month in old and month < str(old_through)[:7] and old[month] - count > max(5, old[month] // 5):
                raise ValueError("Calendar coverage changed")
        old_years = {row["year"]: row["contributions"] for row in previous["years"]}
        for row in annual:
            old_total = old_years.get(row["year"], 0)
            if old_total - row["contributions"] > max(5, old_total // 5):
                raise ValueError("Annual calendar coverage changed")
    return result


def validate_calendar(data):
    fields(data, "schema_version coverage through years months heatmap_start daily")
    if number(data["schema_version"]) != 2 or data["coverage"] != "github-profile-calendar":
        raise ValueError("Unexpected calendar schema")
    end, start = day(data["through"]), day(data["heatmap_start"])
    if (end - start).days != 181 or len(data["daily"]) != 182:
        raise ValueError("Unexpected heatmap window")
    for count in data["daily"]:
        number(count, 100_000)
    if len(data["years"]) != 2:
        raise ValueError("Unexpected annual window")
    for row, year in zip(data["years"], (end.year - 1, end.year)):
        fields(row, "year contributions")
        if number(row["year"]) != year:
            raise ValueError("Unexpected year")
        number(row["contributions"], 36_600_000)
    first = first_month(end)
    expected = {str(d)[:7] for d in dates(first, end)}
    if set(data["months"]) != expected:
        raise ValueError("Unexpected month window")
    for count in data["months"].values():
        number(count, 3_100_000)
    current = sum(count for month, count in data["months"].items() if month.startswith(str(end.year)))
    if current != data["years"][-1]["contributions"]:
        raise ValueError("Monthly and annual totals disagree")
    for date, count in zip(dates(start, end), data["daily"]):
        if date.day == 1 and str(date)[:7] != str(end)[:7]:
            month = str(date)[:7]
            offset = (date - start).days
            length = calendar.monthrange(date.year, date.month)[1]
            if data["months"][month] != sum(data["daily"][offset:offset + length]):
                raise ValueError("Daily and monthly totals disagree")


def repository_snapshot(today, previous):
    credential = os.environ["PROFILE_READ_TOKEN"]
    cohort = json.loads(os.environ["PROFILE_SOURCE_COHORT"])
    if not isinstance(cohort, dict) or not cohort:
        raise ValueError("Invalid source cohort")
    category_by_group = {}
    for group, category in cohort.values():
        if type(group) is not int or group < 1 or category not in CATEGORIES:
            raise ValueError("Invalid cohort group")
        if category_by_group.setdefault(group, category) != category:
            raise ValueError("Inconsistent project grouping")
    repos = api(LIST, credential, paginate=True)
    owned = {r["full_name"].lower(): r for r in repos if r["full_name"].split("/")[0].lower() == "ekmartian"}
    if not set(cohort) <= owned.keys():
        raise ValueError("A cohort repository is missing")
    totals = Counter()
    seen = set()
    measured = private_measured = 0
    groups, active = set(), set()
    for name, (group, _) in sorted(cohort.items()):
        repo = owned[name]
        if any(repo[key] for key in ("fork", "is_template", "disabled", "archived")):
            raise ValueError("Cohort eligibility changed")
        ref = quote(repo["default_branch"], safe="")
        tree = api(f'repos/{name}/git/trees/{ref}?recursive=1', credential)
        if tree.get("truncated"):
            raise ValueError("Source tree incomplete")
        if tree["sha"] in seen:
            continue
        seen.add(tree["sha"])
        measured += 1
        private_measured += repo["private"]
        groups.add(group)
        if repo["pushed_at"] and day(repo["pushed_at"][:10]) >= today - dt.timedelta(days=89):
            active.add(group)
        for entry in tree["tree"]:
            if entry["type"] != "blob" or entry["mode"] not in ("100644", "100755"):
                continue
            language = language_for(entry["path"])
            if language and entry["size"]:
                totals[language] += entry["size"]
    if not private_measured or not totals:
        raise ValueError("Private source coverage unavailable")
    private = sum(r["private"] for r in owned.values())
    result = {
        "schema_version": 1, "coverage": "authorized-owned-inventory-curated-source",
        "as_of": str(today), "visible_public": len(owned) - private, "visible_private": private,
        "measured_repositories": measured, "private_measured": private_measured,
        "projects": len(groups), "active_projects_90d": len(active),
        "source_bytes": dict(totals), "monthly_samples": [],
        "project_categories": dict(Counter(category_by_group[group] for group in groups)),
    }
    if previous:
        for key in ("visible_private", "private_measured", "measured_repositories"):
            if result[key] < previous[key]:
                raise ValueError("Repository coverage decreased")
        result["monthly_samples"] = previous["monthly_samples"][:]
    samples = result["monthly_samples"]
    if not samples or samples[-1]["date"][:7] != str(today)[:7]:
        samples.append({"date": str(today), "source_bytes": dict(totals)})
    result["monthly_samples"] = samples[-24:]
    validate_repository(result)
    return result


def source_bytes(counts):
    if not counts or set(counts) - LANGUAGES:
        raise ValueError("Unrecognized language")
    for count in counts.values():
        if not number(count):
            raise ValueError("Empty language")


def validate_repository(data):
    fields(data, "schema_version coverage as_of visible_public visible_private measured_repositories private_measured projects active_projects_90d source_bytes monthly_samples project_categories")
    if number(data["schema_version"]) != 1 or data["coverage"] != "authorized-owned-inventory-curated-source":
        raise ValueError("Unexpected repository schema")
    end = day(data["as_of"])
    public, private, measured, private_measured, projects, active = (
        number(data[key], 100_000) for key in ("visible_public", "visible_private", "measured_repositories",
                                              "private_measured", "projects", "active_projects_90d")
    )
    if not 0 < private_measured <= min(private, measured) or measured - private_measured > public or not 0 <= active <= projects <= measured:
        raise ValueError("Inconsistent inventory")
    categories = data["project_categories"]
    if set(categories) - set(CATEGORIES) or sum(number(value, 100_000) for value in categories.values()) != projects:
        raise ValueError("Inconsistent project categories")
    source_bytes(data["source_bytes"])
    samples = data["monthly_samples"]
    if not 1 <= len(samples) <= 24:
        raise ValueError("Invalid history")
    previous_month = ""
    for row in samples:
        fields(row, "date source_bytes")
        date = day(row["date"])
        if date > end or row["date"][:7] <= previous_month:
            raise ValueError("Invalid sample date")
        previous_month = row["date"][:7]
        source_bytes(row["source_bytes"])


def label(x, y, value, size=16, color="#b1c1d7", extra=""):
    return f'<text x="{x}" y="{y}" font-size="{size}" fill="{color}" {extra}>{escape(str(value))}</text>'


def card(title, subtitle, height, content):
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="660" height="{height}" viewBox="0 0 660 {height}" role="img" aria-labelledby="title desc">
<title id="title">{escape(title)}</title><desc id="desc">{escape(subtitle)}</desc>
<rect x="1" y="1" width="658" height="{height-2}" rx="18" fill="#101827" stroke="#465873"/>
<path d="M24 22v50" stroke="#7cebd2" stroke-width="3" stroke-linecap="round"/>
<g font-family="system-ui, -apple-system, sans-serif">
{label(39, 43, title, 24, "#f4f8ff", 'font-weight="700"')}
{label(39, 69, subtitle)}
{''.join(content)}
</g></svg>
'''


def rect(x, y, width, height, color, radius=3):
    return f'<rect x="{x}" y="{y}" width="{width}" height="{height}" rx="{radius}" fill="{color}"/>'


def render_calendar(data):
    end, start = day(data["through"]), day(data["heatmap_start"])
    recent = data["daily"]
    months = list(sorted(data["months"].items()))[-12:]
    peak = max(1, *(count for _, count in months))
    content = []
    for i, row in enumerate(data["years"]):
        x = 39 + i * 180
        suffix = "YTD" if row["year"] == end.year and (end.month, end.day) != (12, 31) else "TOTAL"
        content += [label(x, 108, f'{row["year"]} {suffix}', 15), label(x, 145, f'{row["contributions"]:,}', 32, PALETTE[i])]
    content += [label(399, 108, "LAST 28 DAYS", 15), label(399, 145, sum(recent[-28:]), 32, PALETTE[2]),
                label(483, 145, f'vs {sum(recent[-56:-28])} prior', 15)]
    content += [label(39, 183, "Monthly contributions", 18, "#f4f8ff")]
    for i, (month, count) in enumerate(months):
        x = 40 + i * 49
        height = max(1, round(82 * count / peak))
        content += [rect(x, 297-height, 34, height, PALETTE[1] if i == len(months)-1 else PALETTE[0]),
                    label(x+17, 289-height, count, 13, "#f4f8ff", 'text-anchor="middle"'),
                    label(x+17, 317, calendar.month_abbr[int(month[5:7])], 13, extra='text-anchor="middle"')]
    content += [label(39, 360, "Contribution terrain", 21, "#f4f8ff"),
                label(39, 384, "26 weeks · one tile per day · height scales with activity", 15)]
    offset = (start.weekday() + 1) % 7
    peak_day = max(1, *recent)
    tiles = []
    for i, count in enumerate(recent):
        week, weekday = divmod(i + offset, 7)
        x, y = 132 + 17 * week - 11 * weekday, 473 + 3 * week + 6 * weekday
        height = round(66 * (count / peak_day) ** .65) if count else 0
        tiles.append((y, x, height))
    # Draw rear tiles first so foreground columns occlude them.
    for y, x, height in sorted(tiles):
        top = y - height
        if height:
            content.append(f'<path d="M{x-10} {top}l9 6v{height}l-9 -6z" fill="#257768"/>')
            content.append(f'<path d="M{x-1} {top+6}l15 -3v{height}l-15 3z" fill="#329989"/>')
        shade = "#9cf4dd" if height else "#273b54"
        content.append(f'<path d="M{x-10} {top}l15 -3 9 6 -15 3z" fill="{shade}"/>')
    content += [label(39, 610, f"{start} to {end}", 15),
                label(620, 610, f"Highest day: {max(recent)}", 15, extra='text-anchor="end"')]
    return card("GitHub activity", f"Profile calendar · through {end} UTC", 636, content)


def render_repository(data):
    counts = data["source_bytes"]
    total = sum(counts.values())
    ordered = sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))
    content = []
    for i, (language, size) in enumerate(ordered):
        y = 103 + i*36
        pct = 100*size/total
        content += [label(39, y+13, language, 17, "#f4f8ff"), rect(194, y, 335, 14, "#26344b", 6),
                    rect(194, y, max(1, round(335*pct/100)), 14, PALETTE[i % len(PALETTE)], 6),
                    label(620, y+13, f"{pct:.1f}%", 17, extra='text-anchor="end"')]
    top = 121 + len(ordered)*36
    stats = (("PUBLIC REPOS", data["visible_public"]), ("PRIVATE REPOS", data["visible_private"]))
    for i, (name, count) in enumerate(stats):
        x = 39 + i*200
        content += [label(x, top+9, name, 15), label(x, top+45, count, 32, PALETTE[i])]
    content += [label(39, top+77, f'{data["measured_repositories"]} source repos · {data["private_measured"]} private', 15),
                label(39, top+103, "  ·  ".join(f"{name} {data['project_categories'][name]}"
                                               for name in CATEGORIES if name in data["project_categories"]), 15),
                label(39, top+129, f'First monthly language sample: {data["monthly_samples"][0]["date"]}', 14)]
    return card("Source & repositories", f"Filtered default-branch source bytes · {data['as_of']} UTC", top+154, content)


RENDERERS = {"calendar": (validate_calendar, render_calendar), "source": (validate_repository, render_repository)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", choices=RENDERERS)
    parser.add_argument("action", choices=("collect", "apply", "check"))
    args = parser.parse_args()
    validator, renderer = RENDERERS[args.dataset]
    previous = read(args.dataset)
    if previous:
        validator(previous)
    image = ROOT / "assets" / f"{args.dataset}.svg"
    today = utc_today()
    if args.action == "collect":
        collector = calendar_snapshot if args.dataset == "calendar" else repository_snapshot
        result = collector(today, previous)
        print(json.dumps(result, separators=(",", ":"), sort_keys=True))
    elif args.action == "apply":
        result = json.loads(sys.stdin.read(32_768))
        validator(result)
        cutoff = "through" if args.dataset == "calendar" else "as_of"
        expected = today - dt.timedelta(days=1) if args.dataset == "calendar" else today
        if result[cutoff] != str(expected) or previous and result[cutoff] < previous[cutoff]:
            raise ValueError("Snapshot date mismatch")
        generated = renderer(result)
        write_atomic(ROOT / "data" / f"{args.dataset}.json", json.dumps(result, sort_keys=True, indent=2) + "\n")
        write_atomic(image, generated)
    elif not previous or image.read_text() != renderer(previous):
        raise ValueError("Published files do not match")


if __name__ == "__main__":
    try:
        main()
    except ERRORS:
        sys.exit("Dashboard collection or validation failed. Prior publication stays in place.")
