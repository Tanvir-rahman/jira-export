# Security

## Token handling

- This tool authenticates with a Jira API token via Basic auth. The token
  grants **your full Jira permissions** — treat it like a password.
- Keep it in `.env` (gitignored). Never commit it; never paste it in issues.
- Tokens can be revoked/rotated at
  https://id.atlassian.com/manage-profile/security/api-tokens
- The exporter is **read-only** — it sends only GET requests plus POST to
  the two search/GraphQL query endpoints. `jira_seed.py` is the only script
  that writes to your site.

## Exported data

`./jira_export/` contains real names, account IDs, comments, and audit
records from your site. It is gitignored by default — think before moving it
anywhere shared.

## Reporting a vulnerability

Open a GitHub issue for non-sensitive problems. For anything involving
leaked credentials or data exposure, use GitHub's private vulnerability
reporting on this repository instead of a public issue.
