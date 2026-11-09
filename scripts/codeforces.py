"""Collect public Codeforces statistics and render the cached profile card."""
import argparse
import datetime as dt
import json
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from scripts.activity import ROOT, write_atomic
from scripts.dashboard import PALETTE, card, day, fields, label, number

HANDLE = "aMartian"
DATA = ROOT / "data/codeforces.json"
SVG = ROOT / "assets/codeforces.svg"
PAGE_SIZE = 500


def api(method, **params):
    url = f"https://codeforces.com/api/{method}?{urlencode(params)}"
    request = Request(url, headers={"User-Agent": "ekMartian-profile/1.0", "Accept": "application/json"})
    for attempt in range(3):
        time.sleep(2.1 if attempt == 0 else 5 * attempt)
        try:
            with urlopen(request, timeout=20) as response:
                payload = json.load(response)
            if payload.get("status") == "OK" and isinstance(payload.get("result"), list):
                return payload["result"]
            if "limit" not in payload.get("comment", "").lower():
                break
        except HTTPError as exc:
            if exc.code not in (429, 500, 502, 503, 504):
                raise
            retry_after = exc.headers.get("Retry-After", "")
            if retry_after.isdecimal():
                if int(retry_after) > 30:
                    raise
                time.sleep(int(retry_after))
        except URLError:
            if attempt == 2:
                raise
    raise ValueError("Codeforces API response unavailable")


def solved_problems():
    solved = set()
    previous_id = None
    for offset in range(1, 50_001, PAGE_SIZE):
        page = api("user.status", handle=HANDLE, **{"from": offset, "count": PAGE_SIZE})
        for submission in page:
            submission_id = number(submission["id"])
            if previous_id is not None and submission_id >= previous_id:
                raise ValueError("Submission pagination changed during collection")
            previous_id = submission_id
            if submission.get("verdict") == "OK":
                problem = submission["problem"]
                contest = problem.get("contestId") or submission.get("contestId")
                if contest is None:
                    raise ValueError("Accepted submission has no stable contest identifier")
                solved.add((contest, problem["index"]))
        if len(page) < PAGE_SIZE:
            return len(solved)
    raise ValueError("Submission pagination incomplete")


def validate(snapshot):
    fields(snapshot, "schema_version handle observed_on rating max_rating problems_solved history")
    if number(snapshot["schema_version"]) != 1 or snapshot["handle"] != HANDLE:
        raise ValueError("Unexpected Codeforces snapshot")
    observed = day(snapshot["observed_on"])
    current = number(snapshot["rating"], 10_000)
    maximum = number(snapshot["max_rating"], 10_000)
    number(snapshot["problems_solved"], 100_000)
    previous = 0
    history = snapshot["history"]
    if type(history) is not list or not history or len(history) > 5000:
        raise ValueError("Unexpected rating history")
    for point in history:
        fields(point, "timestamp rating")
        timestamp = number(point["timestamp"], 4_102_444_800)
        if timestamp <= previous or dt.datetime.fromtimestamp(timestamp, dt.timezone.utc).date() > observed:
            raise ValueError("Invalid rating timestamp")
        if number(point["rating"], 10_000) > maximum:
            raise ValueError("Rating exceeds recorded maximum")
        previous = timestamp
    if history[-1]["rating"] != current:
        raise ValueError("Rating history differs from current rating")


def collect():
    user, = api("user.info", handles=HANDLE)
    if user["handle"].casefold() != HANDLE.casefold():
        raise ValueError("Unexpected Codeforces account")
    history = api("user.rating", handle=HANDLE)
    result = {
        "schema_version": 1,
        "handle": HANDLE,
        "observed_on": dt.datetime.now(dt.timezone.utc).date().isoformat(),
        "rating": user["rating"],
        "max_rating": user["maxRating"],
        "problems_solved": solved_problems(),
        "history": sorted(
            ({"timestamp": row["ratingUpdateTimeSeconds"], "rating": row["newRating"]} for row in history),
            key=lambda point: point["timestamp"],
        ),
    }
    validate(result)
    return result


def render(data):
    content = []
    for index, (title, key) in enumerate((("CURRENT RATING", "rating"), ("PEAK RATING", "max_rating"),
                                          ("PROBLEMS SOLVED", "problems_solved"))):
        x = 39 + 200 * index
        content += [label(x, 110, title, 15), label(x, 148, f"{data[key]:,}", 34, PALETTE[index])]
    history = data["history"]
    low = min(row["rating"] for row in history)
    high = max(row["rating"] for row in history)
    start, end = history[0]["timestamp"], history[-1]["timestamp"]
    points = [
        (round(90 + 527 * (row["timestamp"] - start) / max(1, end - start)),
         round(299 - 87 * (row["rating"] - low) / max(1, high - low)))
        for row in history
    ]
    content.append(label(39, 185, "Rating history", 18, "#f4f8ff"))
    for y, value in ((212, high), (299, low)):
        content.append(f'<path d="M90 {y}H618" stroke="#344359" stroke-dasharray="3 5"/>')
        if low != high or y == 299:
            content.append(label(39, y + 4, value, 13))
    coords = " ".join(f"{x},{y}" for x, y in points)
    content.append(f'<polyline points="{coords}" fill="none" stroke="#7cebd2" stroke-width="3" stroke-linejoin="round"/>')
    for x, y in points:
        content.append(f'<circle cx="{x}" cy="{y}" r="3" fill="#9ab6ff"/>')
    if start != end:
        content.append(label(90, 327, dt.datetime.fromtimestamp(start, dt.timezone.utc).strftime("%b %Y"), 14))
    content.append(label(618, 327, dt.datetime.fromtimestamp(end, dt.timezone.utc).strftime("%b %Y"),
                         14, extra='text-anchor="end"'))
    return card("Codeforces", f"{HANDLE} · C++ · observed {data['observed_on']} UTC", 351, content)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("refresh", "check"))
    args = parser.parse_args()
    previous = json.loads(DATA.read_text()) if DATA.exists() else None
    if previous:
        validate(previous)
    if args.action == "check":
        if previous is None or SVG.read_text() != render(previous):
            raise ValueError("Codeforces snapshot and card differ")
        return
    snapshot = collect()
    if previous and (snapshot["observed_on"] < previous["observed_on"]
                     or snapshot["problems_solved"] < previous["problems_solved"]
                     or len(snapshot["history"]) < len(previous["history"])):
        raise ValueError("Codeforces coverage decreased")
    generated = render(snapshot)
    write_atomic(DATA, json.dumps(snapshot, indent=2, sort_keys=True) + "\n")
    write_atomic(SVG, generated)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, TypeError):
        sys.exit("Codeforces collection or validation failed. The last published card stays in place.")
