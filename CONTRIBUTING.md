# Contributing

PRs welcome. The bar: CI green (ruff + compile + offline tests) and the
conventions below — they exist because Atlassian's API breaks in creative
ways.

## Adding a new endpoint / dataset

1. **Probe it live first.** Write a scratch script, hit the endpoint against
   a real site with a plain API token, confirm status code and payload shape.
   Endpoints that need OAuth apps, Connect, or org-admin keys don't belong
   here — document them in README "Limitations" instead.
2. **Declare the path in `jira_endpoints.py`** with a stability tag
   (`[stable]` / `[internal]` / `[experimental]`). No URL literals in export
   modules — ever.
3. **Add the export function to the module matching its topic**:
   - `jira_export.py` — core entities (issues, people, reference data)
   - `jira_export_extras.py` — agile reports, dashboards, schemes
   - `jira_export_jpd.py` — Product Discovery (polaris GraphQL)
   - `jira_export_admin.py` — site config, audit, board internals
   `jira_export.py` stays the only entry point.
4. **Never let one failure kill the export.** Wrap per-item calls in
   `try/except requests.HTTPError` and continue; use `quiet=True` for calls
   that legitimately 4xx per item.
5. **Write both formats**: `write_json` (raw payload) + `write_csv`
   (flattened) for anything tabular.
6. **Per-issue loops (N requests) go behind an opt-in flag** and the request
   cost goes in `--help` (see `--changelogs`, `--remote-links`).
7. **Pure logic gets an offline test** in `tests/` — flatteners, parsers,
   path-walkers. No network, no credentials, no mocking of Jira itself
   beyond a fake session.

## Style

- Follow the file's existing formatting; keep modules under ~800 lines —
  split a new module when approaching it.
- No new dependencies without discussion — `requests` + `python-dotenv` is
  the point.

## Running checks locally

```bash
pip install ruff pytest
ruff check .
pytest
```
