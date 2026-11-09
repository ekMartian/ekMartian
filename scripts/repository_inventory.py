"""Read-only repository analytics. Results are for private review."""
from collections import Counter
import datetime as dt
import json
import os
from pathlib import PurePosixPath
import subprocess
import sys
from urllib.parse import quote

EXTENSIONS = {
    ".c": "C", ".cpp": "C++", ".cc": "C++", ".cxx": "C++", ".hpp": "C++",
    ".m": "Objective-C", ".mm": "Objective-C++", ".swift": "Swift", ".metal": "Metal",
    ".py": "Python", ".js": "JavaScript", ".jsx": "JavaScript", ".mjs": "JavaScript",
    ".ts": "TypeScript", ".tsx": "TypeScript", ".sql": "SQL", ".sh": "Shell",
    ".rs": "Rust", ".go": "Go", ".java": "Java", ".kt": "Kotlin",
}
EXCLUDED = {
    "node_modules", "vendor", "vendored", "thirdparty", "vendors", "third_party", "third-party", "external", "dependencies",
    "pods", "carthage", "build", ".build", "dist", "out", "deriveddata", "generated",
    "fixtures", "snapshots", "coverage", ".next", ".venv", "venv", "__pycache__",
}
LIST = "user/repos?per_page=100&affiliation=owner,collaborator,organization_member"


def api(endpoint, credential, paginate=False):
    env = {**os.environ, "GH_TOKEN": credential}
    args = ["gh", "api", endpoint]
    if paginate:
        args += ["--paginate", "--slurp"]
    response = subprocess.run(args, env=env, capture_output=True, text=True, check=True, timeout=90)
    data = json.loads(response.stdout)
    return [row for page in data for row in page] if paginate else data


def language_for(filename):
    path = PurePosixPath(filename.lower())
    if EXCLUDED.intersection(path.parts[:-1]) or any(
        marker in path.name for marker in (".min.", ".generated.", ".gen.", ".g.", ".d.ts", "_pb2.", ".bundle.")
    ):
        return None
    if path.name.split(".")[0] in {"turndown", "readability", "purify", "jquery", "highlight", "marked"}:
        return None
    return EXTENSIONS.get(path.suffix)


def collect(credential, metadata_credential=None):
    repos = api(LIST, credential, paginate=True)
    ids = {repo["id"] for repo in repos}
    uncovered = None
    if metadata_credential:
        expected = api(LIST, metadata_credential, paginate=True)
        uncovered = len({repo["id"] for repo in expected if repo["private"]} - ids)

    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=90)
    totals, projects = Counter(), Counter()
    inspected = unavailable = excluded = recent = 0
    trees_seen = set()
    for repo in repos:
        if any(repo[key] for key in ("fork", "archived", "disabled", "is_template")):
            excluded += 1
            continue
        if repo["full_name"].lower() == "ekmartian/ekmartian" or repo["size"] == 0:
            excluded += 1
            continue
        ref = quote(repo["default_branch"], safe="")
        try:
            tree = api(f'repos/{repo["full_name"]}/git/trees/{ref}?recursive=1', credential)
        except subprocess.CalledProcessError:
            unavailable += 1
            continue
        if tree.get("truncated") or "tree" not in tree:
            unavailable += 1
            continue
        if tree["sha"] in trees_seen:
            excluded += 1
            continue
        trees_seen.add(tree["sha"])
        inspected += 1
        if repo["pushed_at"] and dt.datetime.fromisoformat(repo["pushed_at"]) >= cutoff:
            recent += 1
        by_language = Counter()
        for entry in tree["tree"]:
            if entry["type"] == "blob" and entry["mode"] in ("100644", "100755"):
                language = language_for(entry["path"])
                if language:
                    by_language[language] += entry["size"]
        totals.update(by_language)
        projects.update(by_language.keys())

    return {
        "schema_version": 1,
        "coverage": "credential-visible-repositories",
        "accessible_repositories": len(ids),
        "accessible_private_repositories": sum(repo["private"] for repo in repos),
        "known_uncovered_private_repositories": uncovered,
        "trees_measured": inspected,
        "trees_unavailable": unavailable,
        "repositories_excluded": excluded,
        "recently_pushed_measured_repositories": recent,
        "source_bytes_by_language": dict(totals),
        "measured_repositories_by_language": dict(projects),
    }


if __name__ == "__main__":
    credential = os.environ.get("PROFILE_READ_TOKEN")
    if not credential:
        sys.exit("Set PROFILE_READ_TOKEN using a secure credential store.")
    try:
        print(json.dumps(collect(credential, os.environ.get("PROFILE_METADATA_TOKEN")), indent=2, sort_keys=True))
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        sys.exit("Repository collection failed. Check local credential access and API availability.")
