# Engineering dashboard data

The profile publishes two versioned JSON datasets and deterministic SVG renderings. A website can read the JSON independently of the README. Add future integrations such as Codeforces, LeetCode, or AtCoder as separate datasets with verified handles, explicit reporting windows, and their own validators.

## Contribution calendar

[Calendar JSON](data/calendar.json) contains two calendar-year totals, a rolling 12-month series, and daily totals for 26 weeks. The latest year and month run through the last completed UTC day. The chart compares the last 28 days with the preceding 28 and renders the 26-week calendar as an isometric terrain. The anonymous profile calendar is preferred when it covers the requested dates. GitHub GraphQL supplies older windows and serves as a fallback.

[GitHub applies eligibility rules](https://docs.github.com/en/account-and-profile/reference/profile-contributions-reference) for commits, pull requests, reviews, issues, and other activities. The calendar includes private activity when GitHub shares it anonymously. Restricted contributions are entries for which GitHub withholds the underlying details. This dataset combines the visible calendar counts. Its totals do not describe raw commits, code volume, hours worked, or a reliable private breakdown by event type. Public open-source submissions are linked in the README. GitHub can revise historical totals. Large drops halt publication for review.

## Repository and source measurements

[Source JSON](data/source.json) includes the owned repositories visible to the collector, including public and private totals. Collaborator repositories and repositories invisible to the token are outside that count. GitHub cannot identify repositories hidden from the credential. Maintain the token selection when repositories are added and treat the snapshot as credential-scoped coverage.

The language analysis uses an owner-curated cohort of original projects. It counts regular tracked source blobs at the default-branch tip. It excludes forks, archived repositories, templates, generated filename patterns, known bundled libraries, dependency and build paths, symlinks, and exact duplicate trees. Extension and path rules are conservative heuristics. They cannot identify every nonstandard generated file or allocate authorship within a mixed file. The cohort intentionally excludes coursework, tutorials, and empty repositories. Project categories are fixed labels. Related repositories can share a project group, .

The first observed snapshot per UTC month starts the language history. Up to 24 monthly samples are retained. The source chart displays the current composition, and the JSON provides the historical samples for later trend visualization. Source bytes measure the composition of the selected trees. They do not represent coding time, proficiency, or commits in a language. The original six-month activity files are retained as legacy snapshots. Use the calendar JSON for updates.

## Refresh and credentials

The workflow runs daily at 03:23 UTC (08:53 IST). To refresh on demand, open **Actions → Refresh engineering dashboard → Run workflow** on the default branch. It has independent read jobs and a publisher. The source job receives the source credential and project grouping. The publisher receives only validated data and uses the profile repository’s short-lived Actions token for writes. Workflows run on the default branch only, with no pull-request trigger. Collection errors, invalid inputs, and pushes rejected by GitHub leave the last published files available.

The private source refresh requires a [fine-grained personal access token](https://github.com/settings/personal-access-tokens/new). Choose the profile owner as resource owner, **Only select repositories**, and select the owned private repositories authorized for this inventory. Grant **Contents: Read-only** and keep the automatically enabled **Metadata: Read-only**. The workflow reads trees only for repositories listed in the cohort. Add the credential as the [repository Actions secret](https://github.com/ekMartian/ekMartian/settings/secrets/actions) named `PROFILE_READ_TOKEN`. The encrypted `PROFILE_SOURCE_COHORT` secret maps approved repositories to an integer group and one fixed category. Edit that secret when a newly discovered repository should contribute to the source chart. A missing secret leaves the last verified source snapshot in place while the calendar continues to update.

The publication boundary accepts fixed fields, bounded integers, ISO dates, and allowlisted language/category labels. It rejects private names, file paths, URLs, and unrecognized fields. Diagnostics contain only generic failures. Do not enable shell tracing or dump upstream responses. The README and hero remain editorial.

Run `python3 -m unittest discover -s tests`, `python3 -m scripts.dashboard calendar check`, and `python3 -m scripts.dashboard source check` for local validation.

## Codeforces

[Codeforces JSON](data/codeforces.json) and its SVG are refreshed separately at 04:43 UTC (10:13 IST). The collector uses the [official API](https://codeforces.com/apiHelp/methods). Current and peak ratings come from `user.info`, the chart uses `user.rating`, and solved problems are distinct contest ID and problem index pairs with an accepted verdict across paginated `user.status` results. The checked-in snapshot is the last successful result. API failures, rate limiting, incomplete pages, and inconsistent ratings preserve that snapshot. The collector retries transient failures with backoff and uses no API credentials.

To refresh manually, open **Actions → Refresh Codeforces card → Run workflow**. Once a public solutions repository exists, add a Markdown link beside the Codeforces profile link in the competitive programming section. Keep solution counts separate from GitHub contributions and the independent-product cohort. A future language analysis of problem solutions should report that corpus separately because many small C++ submissions can skew the project language mix.
