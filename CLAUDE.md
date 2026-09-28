# CLAUDE.md

Guidance for Claude Code (and other AI agents) working with this repository.

## What this project is

A zero-dependency-beyond-`requests` toolkit that exports **everything a Jira
Cloud API token can reach** into flat JSON + CSV files, plus a seeding script
that fills an empty Jira site with realistic demo data.

Two entry points, both driven by the same `.env`:

| Script | Purpose |
|---|---|
| `jira_export.py` | Export all Jira data to `./jira_export/` (JSON + CSV). **Run this.** |
| `jira_seed.py` | Seed an empty Jira Cloud site with demo projects/sprints/issues/teams |

## Architecture

`jira_export.py` is the only entry point. It imports the other modules lazily —
they are **not** runnable on their own:

```
jira_export.py          steps 0-9: auth, projects, users, groups, teams,
 |                      reference data, issues, comments, worklogs,
 |                      changelogs, boards, sprints
 |                      + all shared helpers (session, pagination, ADF, CSV)
 +-- jira_export_extras.py   steps 10-17: dashboards, filters, board configs,
 |    |                      sprint reports, velocity, burndowns, workflows,
 |    |                      schemes, versions, components, roles, watchers,
 |    |                      attachment binaries
 |    +-- jira_export_jpd.py    step 17: Jira Product Discovery views +
 |    |                         insights via experimental GraphQL
 |    +-- jira_export_admin.py  steps 18-22: site config, audit log, board
 |                              settings (swimlanes/quick filters), backlog
 |                              rank order, project features, voters,
 |                              remote links
```

**Every URL path lives in `jira_endpoints.py`** — imported everywhere as
`import jira_endpoints as EP`. Constants use `str.format()` placeholders
(`EP.ISSUE_COMMENTS.format(key="ABC-1")`) and are annotated with stability
tiers: `[stable]` (documented REST v3 / Agile 1.0), `[internal]`
(greenhopper), `[experimental]` (polaris GraphQL). When Atlassian moves an
endpoint, fix it there — no other file may contain a URL path.

Shared helpers live in `jira_export.py` and are imported by the submodules:

- `build_session(url, email, token)` — `requests.Session` with Basic auth
- `api_get / api_post` — retry on 429 (honours `Retry-After`) and 5xx
  (exponential backoff, capped 60s); `quiet=True` suppresses 4xx noise for
  endpoints that legitimately fail per-item
- `paginate_startat(session, path, results_key)` — for
  `{startAt, total/isLast, <key>: []}` endpoints
- `paginate_bare_list` — for endpoints returning a bare JSON array per page
  (e.g. `/users/search`), dedupes by `accountId`/`id`
- `search_all_issues` — POST `/rest/api/3/search/jql` with
  `nextPageToken` pagination (the old `GET /search` with `startAt` is dead)
- `adf_to_text(node)` — Atlassian Document Format → plain text
- `write_json / write_csv` — every dataset is written as both when tabular

## Commands

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # fill in your values

python jira_export.py                          # standard export
python jira_export.py --changelogs --remote-links --download-attachments  # everything
python jira_export.py --jql "project = ABC"    # one project only
python jira_export.py --no-extras              # fast: core data only

python jira_seed.py                            # seed demo data (destructive-ish: creates projects)
python jira_seed.py --jpd-only                 # only seed JPD ideas (JPD project must exist)
```

Flags on `jira_export.py`:

| Flag | Effect | Cost |
|---|---|---|
| `--jql "..."` | Limit issue selection | — |
| `--out DIR` | Output dir (default `./jira_export`) | — |
| `--all-fields` | Every field incl. all custom fields | Large JSON |
| `--changelogs` | Full issue history | ~1 request/issue |
| `--watchers` | Watcher identities for every issue (default: only issues with >1 watcher) | ~1 request/issue |
| `--remote-links` | Web/Confluence links per issue | 1 request/issue |
| `--download-attachments` | Binaries to `<out>/attachments/<KEY>/` | 1 request/file |
| `--no-comments / --no-worklogs / --no-agile / --no-extras` | Skip sections | — |

## Environment

`.env` (loaded via `python-dotenv`, never commit it):

```
JIRA_URL=https://your-site.atlassian.net
JIRA_EMAIL=you@example.com
JIRA_API_TOKEN=...          # https://id.atlassian.com/manage-profile/security/api-tokens
JIRA_ORG_ID=...             # optional; only for Atlassian Teams export
                            # find it in your admin.atlassian.com URL: /o/<THIS>/
```

## Non-obvious API knowledge baked into this repo

Hard-won facts — do not "fix" these without re-verifying against a live site:

1. **Issue search**: `POST /rest/api/3/search/jql` with `nextPageToken`.
   Unbounded JQL is rejected — the default JQL is
   `created >= "1970-01-01" order by created ASC` to bound it while matching
   everything.
2. **Emails are GDPR-hidden**: `emailAddress` appears only for the token's own
   account or users who set profile visibility to "Anyone". Join on
   `accountId`, never on email.
3. **Greenhopper charts are real but internal**:
   `/rest/greenhopper/1.0/rapid/charts/{sprintreport,velocity,scopechangeburndownchart,epicreport}`
   work with a plain API token. They can break without notice — every call is
   wrapped in per-item try/except.
4. **JPD (Product Discovery) GraphQL**: `POST <site>/gateway/api/graphql` with
   header `X-ExperimentalApi: polaris-v0` and Basic auth works. Query
   `polarisProject(id: "ari:cloud:jira:<cloudId>:project/<projectId>")` for
   `viewsets/views/insights`. `cloudId` comes from `GET /_edge/tenant_info`.
   Read-only; experimental; may change without notice.
5. **Velocity quirk (team-managed boards)**: greenhopper `velocity` reports
   `estimated = 0` when estimates were set after sprint start. Use
   `sprint_reports.csv` `committed_points` instead — those are correct.
6. **RICE / formula fields return null** over REST — computed client-side.
7. **Comments/worklogs embed only their first page** in search results.
   `collect_embedded_or_fetch` tops up per-issue only when `total > len(items)`
   to keep request counts minimal.
8. **Audit log** (`/rest/api/3/auditing/record`, offset/limit pagination) works
   for site admins. Webhooks (`/rest/api/3/webhook`) do NOT — Connect/OAuth
   apps only (403).
9. **Backlog rank order** comes from the *returned order* of
   `/rest/agile/1.0/board/{id}/backlog` — there is no rank number field.
10. **Jira comments are flat.** No threads. Do not model reply trees.
11. **JPD idea screens mix field types.** Filtering custom fields on
    `"polaris" in schema.custom` silently drops the plain Jira custom fields
    placed on idea screens. Request all `customfield_*` instead.
12. **Field display names are not unique per site.** Several fields can share
    one name (one site had six "Product Area"s) — when keying idea rows by
    display name, first non-null value wins so a null from an unrelated
    same-named field never clobbers a real one.
13. **View filters reference select options by numeric id.** Nothing else in
    the export maps id → label, so `jpd_field_options.json` is harvested from
    the raw idea field values before they are flattened.

## Known-impossible with an API token

Do not attempt; verified dead ends (HTTP status in parens):

- Automation rules export (404 on `cb-automation`, `/rest/automation/1.0`,
  gateway internal) — UI export only
- Webhook registrations (403 — app-only endpoint)
- Other users' hidden emails (GDPR) — org admin directory export only
- Notification emails Jira sent — no API exists
- JPD per-view filter *results* / RICE values — client-side computed
  (filter *definitions* do export, in `jpd_views.json`)
- Org-level audit log & last-login times — needs org admin API key
  (admin.atlassian.com, different auth scheme entirely)
- Comment reactions — internal API only

## Conventions for extending

- New endpoint → declare the path in `jira_endpoints.py` first (with a
  stability tag), then add the export function to the matching module by
  topic (core / extras / jpd / admin); keep `jira_export.py` the only entry
  point. Never inline a URL path in an export module.
- Wrap per-item calls in `try/except requests.HTTPError` and continue —
  a single 403/404 must never kill the export.
- Every tabular dataset gets **both** `write_json` and `write_csv`.
- Per-issue loops (N requests) are gated behind an opt-in flag; document the
  request cost in `--help`.
- Probe endpoints against a live site before wiring in (write a scratch
  script; hit the endpoint; check status + payload shape).
- Files stay under ~800 lines; split into a new module when approaching it.

## Output

~60 files in `./jira_export/` (gitignored — contains real user data). Naming:
`<entity>.json` = raw API payloads, `<entity>.csv` = flattened for
spreadsheets/BI. `_manifest.json` records export time, site, JQL, and counts.
