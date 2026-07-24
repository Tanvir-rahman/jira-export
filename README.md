# jira-export

Export **everything a Jira Cloud API token can reach** — issues, sprints,
sprint reports, boards, dashboards, audit log, Product Discovery views &
insights — into flat JSON + CSV files. No database, no framework, no OAuth
app registration. One command.

Also includes `jira_seed.py`: fill an empty Jira site with realistic demo
data (projects, 340+ issues, closed sprints with real velocity, teams) —
useful for building dashboards or demos against a believable dataset.

## Why

- Jira's own CSV export caps at 1000 issues and drops most entities.
- Jira's backup ZIP is admin-only, async, and not analysis-friendly.
- Marketplace exporters cost money and still miss sprint reports & JPD data.

This tool hits the REST API (plus two internal-but-working APIs, see
[Limitations](#limitations)) and writes ~60 plain files you can load into
pandas, a spreadsheet, or a BI tool directly.

## Quick start

```bash
git clone https://github.com/<you>/jira-export && cd jira-export
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# edit .env — get a token at:
# https://id.atlassian.com/manage-profile/security/api-tokens

python jira_export.py
```

Output lands in `./jira_export/`. Add the heavy stuff:

```bash
python jira_export.py --changelogs --remote-links --download-attachments
```

Scope to one project:

```bash
python jira_export.py --jql "project = ABC"
```

## What you get

| Category | Files |
|---|---|
| **Issues** | `issues.json/csv` (all types), `epics.csv`, `subtasks.csv`, `issue_links.csv`, `attachments.csv` + binaries, `changelogs.csv`, `remote_links.csv`, `watchers.csv`, `voters.csv` |
| **Collaboration** | `comments.json/csv`, `worklogs.json/csv` |
| **Agile** | `boards.json`, `sprints.csv`, `sprint_reports.csv` (committed vs completed vs punted per sprint), `velocity.csv`, `burndowns.json`, `board_columns.csv`, `board_settings.json` (swimlanes, quick filters), `backlog_order.csv` (exact rank), `board_epics.csv` |
| **People** | `users.csv`, `groups.json`, `group_members.csv`, `teams.csv` + `team_members.csv` (Atlassian Teams), `project_roles.csv` |
| **Product Discovery** | `jpd_views.csv`, `jpd_viewsets.json`, `jpd_insights.csv` (mapped to idea keys), `jpd_ideas.csv` (Value/Effort/Impact/Reach/Confidence scores) |
| **Projects & config** | `projects.csv`, `versions.csv`, `components.csv`, `project_features.csv`, `workflows.json`, `workflow_schemes.json`, `permission_schemes.json`, `notification_schemes.json`, `issue_security_schemes.json`, `screens.json`, `field_configurations.json`, `issue_type_schemes.json`, `priority_schemes.json` |
| **Site** | `audit_log.csv` (full Jira audit trail), `dashboards.json`, `dashboard_gadgets.csv`, `filters.csv`, `labels.json`, `issue_link_types.json`, `application_roles.json` (license seats), `site_info.json` |
| **Reference** | `fields.json`, `issuetypes.json`, `statuses.json`, `priorities.json`, `resolutions.json` |

Every tabular dataset is written as both raw JSON (full API payload) and
flattened CSV.

## Flags

| Flag | Effect | Cost |
|---|---|---|
| `--jql "..."` | Limit which issues are exported | — |
| `--out DIR` | Output directory (default `./jira_export`) | — |
| `--all-fields` | Dump every field incl. all custom fields | Large files |
| `--changelogs` | Full issue history | ~1 request/issue |
| `--watchers` | Watcher identities for every issue | ~1 request/issue |
| `--remote-links` | Web/Confluence links on issues | 1 request/issue |
| `--download-attachments` | Save attachment binaries | 1 request/file |
| `--no-comments` / `--no-worklogs` / `--no-agile` / `--no-extras` | Skip sections | — |

Rate limits are handled automatically (429 `Retry-After` honoured, 5xx
exponential backoff).

## Seeding demo data

```bash
python jira_seed.py            # 3 projects, ~340 issues, 12 closed sprints,
                               # comments, worklogs, 5 teams
python jira_seed.py --jpd-only # add Product Discovery ideas (create the JPD
                               # project in the UI first — API can't)
```

Sprint reports and velocity charts populate with real numbers because issues
actually move through the seeded sprints.

## How it works

- Plain Basic auth (`email:api_token`) against `/rest/api/3` and
  `/rest/agile/1.0`.
- Issue search uses the new `POST /rest/api/3/search/jql` with
  `nextPageToken` pagination.
- Sprint reports / velocity / burndowns come from the internal
  `greenhopper` chart API — same data the Jira UI renders.
- Product Discovery views & insights come from Atlassian's experimental
  GraphQL API (`X-ExperimentalApi: polaris-v0` on
  `<site>/gateway/api/graphql`) — works with the same token, read-only.

Module layout: `jira_export.py` (entry point + core), `jira_export_extras.py`
(dashboards/reports/schemes), `jira_export_jpd.py` (Product Discovery),
`jira_export_admin.py` (site config/audit/board internals). See
[CLAUDE.md](CLAUDE.md) for architecture details and API gotchas.

## Limitations

Not exportable with an API token (verified, not for lack of trying):

- **Automation rules** — no public API; export from the Jira UI
- **Webhook registrations** — endpoint is OAuth/Connect-app-only
- **Other users' email addresses** — GDPR-hidden unless the user opts in;
  join on `accountId` instead
- **Notification emails Jira sent** — no API exists
- **JPD formula values (RICE)** and per-view filter results — computed
  client-side
- **Org-level data** (last logins, org audit log) — requires an org admin
  API key against admin.atlassian.com

The greenhopper and polaris APIs are internal/experimental: they work today,
Atlassian may change them tomorrow. Every call is isolated so a breakage
skips that dataset instead of killing the export.

- You only get what your account can see. Use a site-admin account for a
  full export.

## License

[MIT](LICENSE)
