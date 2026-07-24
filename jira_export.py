#!/usr/bin/env python3
"""
jira_export.py (v2) — Export EVERYTHING from Jira Cloud for a custom dashboard.

Exports:
  - Projects (+ lead)
  - Users, Groups + group members, Atlassian Teams (optional, needs org ID)
  - Issues: ALL types — tasks, epics, stories, bugs, sub-tasks — with
    description, labels, components, versions, links, parent/child linkage,
    story points, sprint(s), epic link
  - Comments (every comment on every issue)
  - Worklogs (time tracking entries)
  - Attachments metadata + issue links
  - Changelogs / issue history (opt-in with --changelogs, it's the heaviest)
  - Boards & sprints (Agile API)
  - Reference data: issue types, statuses, priorities, resolutions, fields
  - Extras (see jira_export_extras.py): dashboards+gadgets, filters, board
    columns, sprint reports, velocity, burndowns, workflows, permission/
    notification/security schemes, versions, components, roles, watchers,
    attachment binaries (--download-attachments)

Rate limiting: every request retries on HTTP 429 honouring the Retry-After
header (falling back to exponential backoff, capped at 60s), and retries
transient 5xx errors with exponential backoff.

--------------------------------------------------------------------------------
SETUP
--------------------------------------------------------------------------------
1. API token: https://id.atlassian.com/manage-profile/security/api-tokens
2. pip install requests
3. Environment variables:

       export JIRA_URL="https://your-site.atlassian.net"
       export JIRA_EMAIL="you@example.com"
       export JIRA_API_TOKEN="your_api_token_here"
       # optional, only for Atlassian "Teams" (the people-groups in your org):
       # find it in your admin.atlassian.com URL: .../o/<THIS-PART>/...
       export JIRA_ORG_ID="your-org-id"

4. Run:
       python jira_export.py

   Flags:
       --jql "project = ABC"   limit issues (default: everything you can see)
       --out ./jira_data       output dir (default ./jira_export)
       --all-fields            dump every field incl. all custom fields (large)
       --changelogs            also export full issue history (slow: 1+ req/issue)
       --no-comments           skip comments
       --no-worklogs           skip worklogs
       --no-agile              skip boards/sprints

--------------------------------------------------------------------------------
NOTES
--------------------------------------------------------------------------------
- Epics / tasks / sub-tasks are all "issues" in Jira, distinguished by the
  issuetype field. issues.csv contains all of them; sub-tasks have
  is_subtask=True and a parent_key; issues in an epic carry the epic in
  parent_key (team-managed) or epic_link (company-managed).
- Jira Cloud comments are FLAT — there is no such thing as a threaded
  "sub-comment"/reply in Jira. comments.csv is the complete comment record.
- "Teams" in Jira usually means either (a) Groups (exported always) or
  (b) Atlassian Teams (exported when JIRA_ORG_ID is set).
- You only get what your account can see; use a Jira admin account for a
  full-org export.
"""

import argparse
import base64
import csv
import json
import os
import sys
import time
from datetime import datetime, timezone

from dotenv import load_dotenv

import jira_endpoints as EP

load_dotenv()

try:
    import requests
except ImportError:
    sys.exit("Missing dependency. Run:  pip install requests")


# --------------------------------------------------------------------------- #
# HTTP with rate-limit handling
# --------------------------------------------------------------------------- #
MAX_RETRIES = 6
MAX_WAIT = 60


def build_session(base_url, email, token):
    s = requests.Session()
    auth = base64.b64encode(f"{email}:{token}".encode()).decode()
    s.headers.update({
        "Authorization": f"Basic {auth}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    })
    s.base_url = base_url.rstrip("/")
    return s


def _wait_time(resp, attempt):
    """Honour Retry-After if present, else exponential backoff. Capped."""
    ra = resp.headers.get("Retry-After")
    if ra:
        try:
            return min(float(ra), MAX_WAIT)
        except ValueError:
            pass
    return min(2 ** attempt, MAX_WAIT)


def _request(session, method, path, params=None, payload=None, quiet=False):
    url = path if path.startswith("http") else f"{session.base_url}{path}"
    last = None
    for attempt in range(MAX_RETRIES):
        if method == "GET":
            r = session.get(url, params=params)
        else:
            r = session.post(url, params=params, data=json.dumps(payload or {}))
        last = r
        if r.status_code == 429:
            w = _wait_time(r, attempt)
            print(f"    rate limited (429) — waiting {w:.0f}s")
            time.sleep(w)
            continue
        if 500 <= r.status_code < 600:
            time.sleep(min(2 ** attempt, MAX_WAIT))
            continue
        if r.status_code >= 400 and not quiet:
            print(f"    HTTP {r.status_code} from {url}: {r.text[:500]}")
        r.raise_for_status()
        return r.json() if r.text else {}
    last.raise_for_status()


def api_get(session, path, params=None, quiet=False):
    return _request(session, "GET", path, params=params, quiet=quiet)


def api_post(session, path, payload):
    return _request(session, "POST", path, payload=payload)


# --------------------------------------------------------------------------- #
# Pagination helpers
# --------------------------------------------------------------------------- #
def paginate_startat(session, path, results_key, params=None, page_size=50,
                     quiet=False):
    """Endpoints returning {startAt, isLast/total, <results_key>: [...]}."""
    params = dict(params or {})
    params["maxResults"] = page_size
    start, out = 0, []
    while True:
        params["startAt"] = start
        data = api_get(session, path, params, quiet=quiet)
        batch = data.get(results_key, [])
        out.extend(batch)
        if data.get("isLast") is True or not batch:
            break
        total = data.get("total")
        if total is not None and start + len(batch) >= total:
            break
        start += len(batch)
    return out


def paginate_bare_list(session, path, page_size=50):
    """Endpoints returning a bare JSON list per page (e.g. /users/search)."""
    start, out, seen = 0, [], set()
    while True:
        batch = api_get(session, path, {"startAt": start, "maxResults": page_size})
        if not batch:
            break
        for item in batch:
            key = item.get("accountId") or item.get("id") or json.dumps(item, sort_keys=True)
            if key not in seen:
                seen.add(key)
                out.append(item)
        if len(batch) < page_size:
            break
        start += len(batch)
    return out


# --------------------------------------------------------------------------- #
# Issue search — POST /rest/api/3/search/jql (token-based pagination)
# --------------------------------------------------------------------------- #
CURATED_FIELDS = [
    "summary", "description", "issuetype", "status", "priority", "project",
    "assignee", "reporter", "creator", "created", "updated", "resolutiondate",
    "resolution", "duedate", "labels", "components", "fixVersions", "versions",
    "parent", "subtasks", "issuelinks", "attachment", "comment", "worklog",
    "timeoriginalestimate", "timeestimate", "timespent", "aggregatetimespent",
    "votes", "watches", "environment", "statuscategorychangedate",
]


def search_all_issues(session, jql, fields, page_size=100):
    out, next_token = [], None
    while True:
        payload = {"jql": jql, "maxResults": page_size, "fields": fields}
        if next_token:
            payload["nextPageToken"] = next_token
        data = api_post(session, EP.SEARCH_JQL, payload)
        issues = data.get("issues", [])
        out.extend(issues)
        print(f"    fetched {len(out)} issues...")
        next_token = data.get("nextPageToken")
        if data.get("isLast") or not next_token or not issues:
            break
    return out


# --------------------------------------------------------------------------- #
# ADF (rich text) -> plain text
# --------------------------------------------------------------------------- #
def adf_to_text(node):
    """Jira Cloud v3 returns descriptions/comments as Atlassian Document Format."""
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    if isinstance(node, list):
        return "".join(adf_to_text(n) for n in node)
    if isinstance(node, dict):
        if node.get("type") == "text":
            return node.get("text", "")
        if node.get("type") == "hardBreak":
            return "\n"
        text = adf_to_text(node.get("content", []))
        if node.get("type") in ("paragraph", "heading", "listItem", "blockquote",
                                "codeBlock", "tableRow"):
            text += "\n"
        return text
    return ""


# --------------------------------------------------------------------------- #
# Per-issue detail fetchers (comments / worklogs / changelog top-up)
# --------------------------------------------------------------------------- #
def get_all_comments(session, issue_key):
    return paginate_startat(session, EP.ISSUE_COMMENTS.format(key=issue_key),
                            "comments", page_size=100)


def get_all_worklogs(session, issue_key):
    return paginate_startat(session, EP.ISSUE_WORKLOGS.format(key=issue_key),
                            "worklogs", page_size=100)


def get_changelog(session, issue_key):
    return paginate_startat(session, EP.ISSUE_CHANGELOG.format(key=issue_key),
                            "values", page_size=100)


def collect_embedded_or_fetch(session, issues, field_name, list_key, fetcher, label):
    """
    Comments/worklogs come embedded in search results, but only the first page.
    If an issue has more than what's embedded (or the field is missing), fetch
    the full set for just that issue. Keeps request count minimal.
    """
    out = []
    to_fetch = []
    for iss in issues:
        f = iss.get("fields") or {}
        blob = f.get(field_name)
        if blob is None:
            to_fetch.append(iss["key"])
            continue
        items = blob.get(list_key, []) or []
        total = blob.get("total", len(items))
        if total > len(items):
            to_fetch.append(iss["key"])
        else:
            for it in items:
                it["_issue_key"] = iss["key"]
            out.extend(items)
    if to_fetch:
        print(f"    fetching full {label} for {len(to_fetch)} issues "
              f"(more than embedded page)...")
        for n, key in enumerate(to_fetch, 1):
            try:
                items = fetcher(session, key)
                for it in items:
                    it["_issue_key"] = key
                out.extend(items)
            except requests.HTTPError as e:
                print(f"      {key}: skipped ({e})")
            if n % 50 == 0:
                print(f"      {n}/{len(to_fetch)} done")
    return out


# --------------------------------------------------------------------------- #
# Flatteners
# --------------------------------------------------------------------------- #
def _dn(obj, key="displayName"):
    return (obj or {}).get(key)


def flatten_issue(issue, story_points_ids, sprint_id, epic_link_id):
    f = issue.get("fields", {}) or {}
    status = f.get("status") or {}
    comment_blob = f.get("comment") or {}
    attach = f.get("attachment") or []

    story_points = None
    for fid in story_points_ids:
        if f.get(fid) is not None:
            story_points = f.get(fid)
            break

    sprints = f.get(sprint_id) or [] if sprint_id else []
    sprint_names = ", ".join(s.get("name", "") for s in sprints if isinstance(s, dict))

    return {
        "key": issue.get("key"),
        "id": issue.get("id"),
        "project_key": _dn(f.get("project"), "key"),
        "project_name": _dn(f.get("project"), "name"),
        "issuetype": _dn(f.get("issuetype"), "name"),
        "is_subtask": (f.get("issuetype") or {}).get("subtask"),
        "summary": f.get("summary"),
        "description": adf_to_text(f.get("description")).strip(),
        "status": status.get("name"),
        "status_category": _dn(status.get("statusCategory"), "name"),
        "priority": _dn(f.get("priority"), "name"),
        "resolution": _dn(f.get("resolution"), "name"),
        "assignee": _dn(f.get("assignee")),
        "assignee_account_id": (f.get("assignee") or {}).get("accountId"),
        "reporter": _dn(f.get("reporter")),
        "creator": _dn(f.get("creator")),
        "created": f.get("created"),
        "updated": f.get("updated"),
        "resolutiondate": f.get("resolutiondate"),
        "duedate": f.get("duedate"),
        "parent_key": _dn(f.get("parent"), "key"),
        "epic_link": f.get(epic_link_id) if epic_link_id else None,
        "story_points": story_points,
        "sprints": sprint_names,
        "labels": ", ".join(f.get("labels") or []),
        "components": ", ".join(_dn(c, "name") or "" for c in (f.get("components") or [])),
        "fix_versions": ", ".join(_dn(v, "name") or "" for v in (f.get("fixVersions") or [])),
        "subtask_keys": ", ".join(s.get("key", "") for s in (f.get("subtasks") or [])),
        "num_comments": comment_blob.get("total", 0),
        "num_attachments": len(attach),
        "time_spent_sec": f.get("timespent"),
        "original_estimate_sec": f.get("timeoriginalestimate"),
        "remaining_estimate_sec": f.get("timeestimate"),
        "votes": (f.get("votes") or {}).get("votes"),
        "watchers": (f.get("watches") or {}).get("watchCount"),
    }


def flatten_comment(c):
    return {
        "issue_key": c.get("_issue_key"),
        "comment_id": c.get("id"),
        "author": _dn(c.get("author")),
        "author_account_id": (c.get("author") or {}).get("accountId"),
        "created": c.get("created"),
        "updated": c.get("updated"),
        "body": adf_to_text(c.get("body")).strip(),
    }


def flatten_worklog(w):
    return {
        "issue_key": w.get("_issue_key"),
        "worklog_id": w.get("id"),
        "author": _dn(w.get("author")),
        "author_account_id": (w.get("author") or {}).get("accountId"),
        "started": w.get("started"),
        "time_spent_sec": w.get("timeSpentSeconds"),
        "comment": adf_to_text(w.get("comment")).strip(),
    }


def flatten_attachments(issues):
    rows = []
    for iss in issues:
        for a in (iss.get("fields", {}) or {}).get("attachment") or []:
            rows.append({
                "issue_key": iss.get("key"),
                "attachment_id": a.get("id"),
                "filename": a.get("filename"),
                "size_bytes": a.get("size"),
                "mime_type": a.get("mimeType"),
                "author": _dn(a.get("author")),
                "created": a.get("created"),
                "content_url": a.get("content"),
            })
    return rows


def flatten_links(issues):
    rows = []
    for iss in issues:
        for ln in (iss.get("fields", {}) or {}).get("issuelinks") or []:
            other = ln.get("outwardIssue") or ln.get("inwardIssue") or {}
            direction = "outward" if ln.get("outwardIssue") else "inward"
            ltype = ln.get("type") or {}
            rows.append({
                "issue_key": iss.get("key"),
                "direction": direction,
                "link_type": ltype.get("name"),
                "relation": ltype.get(direction),
                "other_issue_key": other.get("key"),
            })
    return rows


def flatten_changelog(issue_key, histories):
    rows = []
    for h in histories:
        for item in h.get("items", []):
            rows.append({
                "issue_key": issue_key,
                "changed_at": h.get("created"),
                "author": _dn(h.get("author")),
                "field": item.get("field"),
                "from": item.get("fromString"),
                "to": item.get("toString"),
            })
    return rows


# --------------------------------------------------------------------------- #
# Output
# --------------------------------------------------------------------------- #
def write_json(out_dir, name, data):
    path = os.path.join(out_dir, name)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
    n = len(data) if isinstance(data, list) else 1
    print(f"    wrote {name} ({n} records)")


def write_csv(out_dir, name, rows):
    path = os.path.join(out_dir, name)
    if not rows:
        print(f"    ({name}: no rows, skipped)")
        return
    keys = list(rows[0].keys())
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=keys, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    print(f"    wrote {name} ({len(rows)} rows)")


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser(description="Export all data from Jira Cloud.")
    # /rest/api/3/search/jql rejects unbounded JQL; created >= 1970 bounds it
    # while still matching every issue.
    ap.add_argument("--jql", default='created >= "1970-01-01" order by created ASC')
    ap.add_argument("--out", default="./jira_export")
    ap.add_argument("--all-fields", action="store_true")
    ap.add_argument("--changelogs", action="store_true",
                    help="Export full issue history (adds ~1 request per issue).")
    ap.add_argument("--no-comments", action="store_true")
    ap.add_argument("--no-worklogs", action="store_true")
    ap.add_argument("--no-agile", action="store_true")
    ap.add_argument("--no-extras", action="store_true",
                    help="Skip dashboards/filters/sprint reports/schemes/etc.")
    ap.add_argument("--watchers", action="store_true",
                    help="Fetch watcher identities for EVERY issue "
                         "(default: only issues with >1 watcher).")
    ap.add_argument("--download-attachments", action="store_true",
                    help="Download attachment binaries to <out>/attachments/.")
    ap.add_argument("--remote-links", action="store_true",
                    help="Fetch remote/web links per issue (1 request each).")
    args = ap.parse_args()

    base_url = os.environ.get("JIRA_URL")
    email = os.environ.get("JIRA_EMAIL")
    token = os.environ.get("JIRA_API_TOKEN")
    org_id = os.environ.get("JIRA_ORG_ID")  # optional, for Atlassian Teams
    if not all([base_url, email, token]):
        sys.exit("Set JIRA_URL, JIRA_EMAIL and JIRA_API_TOKEN first — see SETUP "
                 "at the top of this file.")

    os.makedirs(args.out, exist_ok=True)
    s = build_session(base_url, email, token)

    print("[0/9] Authenticating...")
    me = api_get(s, EP.MYSELF)
    print(f"    OK — {me.get('displayName')} <{me.get('emailAddress', 'n/a')}>")

    # ---- 1. Projects -------------------------------------------------------
    print("[1/9] Projects...")
    projects = paginate_startat(s, EP.PROJECT_SEARCH, "values",
                                params={"expand": "description,lead,insight"})
    write_json(args.out, "projects.json", projects)
    write_csv(args.out, "projects.csv", [{
        "id": p.get("id"), "key": p.get("key"), "name": p.get("name"),
        "type": p.get("projectTypeKey"), "style": p.get("style"),
        "lead": _dn(p.get("lead")),
        "issues": (p.get("insight") or {}).get("totalIssueCount"),
    } for p in projects])

    # ---- 2. Users ----------------------------------------------------------
    print("[2/9] Users...")
    try:
        users = paginate_bare_list(s, EP.USERS_SEARCH)
        write_json(args.out, "users.json", users)
        write_csv(args.out, "users.csv", [{
            "account_id": u.get("accountId"), "display_name": u.get("displayName"),
            "email": u.get("emailAddress"), "account_type": u.get("accountType"),
            "active": u.get("active"),
        } for u in users])
    except requests.HTTPError as e:
        print(f"    skipped users (needs 'Browse users and groups'): {e}")

    # ---- 3. Groups + members ----------------------------------------------
    print("[3/9] Groups and group members...")
    try:
        groups = paginate_startat(s, EP.GROUP_BULK, "values")
        write_json(args.out, "groups.json", groups)
        member_rows = []
        for g in groups:
            gid, gname = g.get("groupId"), g.get("name")
            try:
                members = paginate_startat(s, EP.GROUP_MEMBER,
                                           "values", params={"groupId": gid})
                for m in members:
                    member_rows.append({
                        "group_id": gid, "group_name": gname,
                        "account_id": m.get("accountId"),
                        "display_name": m.get("displayName"),
                        "active": m.get("active"),
                    })
            except requests.HTTPError:
                pass
        write_json(args.out, "group_members.json", member_rows)
        write_csv(args.out, "group_members.csv", member_rows)
    except requests.HTTPError as e:
        print(f"    skipped groups (needs admin/browse permission): {e}")

    # ---- 4. Atlassian Teams (optional) -------------------------------------
    print("[4/9] Atlassian Teams...")
    if org_id:
        try:
            teams, cursor = [], None
            while True:
                params = {"size": 50}
                if cursor:
                    params["cursor"] = cursor
                data = api_get(s, EP.TEAMS.format(org_id=org_id), params)
                ents = data.get("entities", [])
                teams.extend(ents)
                cursor = data.get("cursor")
                if not cursor or not ents:
                    break
            write_json(args.out, "teams.json", teams)
            write_csv(args.out, "teams.csv", [{
                "team_id": t.get("teamId"), "name": t.get("displayName"),
                "description": t.get("description"), "type": t.get("teamType"),
            } for t in teams])
            # team members
            team_member_rows = []
            for t in teams:
                tid = t.get("teamId")
                try:
                    after = None
                    while True:
                        body = {"first": 50}  # API rejects first > 50
                        if after:
                            body["after"] = after
                        data = api_post(
                            s, EP.TEAM_MEMBERS.format(org_id=org_id, team_id=tid),
                            body)
                        for m in data.get("results", []):
                            team_member_rows.append({
                                "team_id": tid, "team_name": t.get("displayName"),
                                "account_id": m.get("accountId"),
                            })
                        page = data.get("pageInfo") or {}
                        if not page.get("hasNextPage"):
                            break
                        after = page.get("endCursor")
                except requests.HTTPError:
                    pass
            write_json(args.out, "team_members.json", team_member_rows)
            write_csv(args.out, "team_members.csv", team_member_rows)
        except requests.HTTPError as e:
            print(f"    skipped Teams (check JIRA_ORG_ID / permissions): {e}")
    else:
        print("    JIRA_ORG_ID not set — skipping Atlassian Teams. "
              "(Groups above usually cover 'teams'.)")

    # ---- 5. Reference data --------------------------------------------------
    print("[5/9] Reference data...")
    all_fields = api_get(s, EP.FIELDS)
    write_json(args.out, "fields.json", all_fields)
    write_json(args.out, "issuetypes.json", api_get(s, EP.ISSUE_TYPES))
    write_json(args.out, "statuses.json", api_get(s, EP.STATUSES))
    write_json(args.out, "priorities.json", api_get(s, EP.PRIORITIES))
    write_json(args.out, "resolutions.json", api_get(s, EP.RESOLUTIONS))

    # Detect useful custom fields by name
    story_points_ids, sprint_id, epic_link_id = [], None, None
    for fld in all_fields:
        nm = (fld.get("name") or "").lower()
        if nm in ("story points", "story point estimate"):
            story_points_ids.append(fld["id"])
        elif nm == "sprint":
            sprint_id = fld["id"]
        elif nm == "epic link":
            epic_link_id = fld["id"]
    print(f"    detected custom fields — story points: {story_points_ids or 'none'}, "
          f"sprint: {sprint_id or 'none'}, epic link: {epic_link_id or 'none'}")

    # ---- 6. Issues ----------------------------------------------------------
    print(f"[6/9] Issues (JQL: {args.jql})...")
    if args.all_fields:
        fields = ["*all"]
    else:
        fields = CURATED_FIELDS + story_points_ids
        if sprint_id:
            fields.append(sprint_id)
        if epic_link_id:
            fields.append(epic_link_id)
    issues = search_all_issues(s, args.jql, fields)
    write_json(args.out, "issues.json", issues)
    flat = [flatten_issue(i, story_points_ids, sprint_id, epic_link_id)
            for i in issues]
    write_csv(args.out, "issues.csv", flat)
    write_csv(args.out, "epics.csv",
              [r for r in flat if (r.get("issuetype") or "").lower() == "epic"])
    write_csv(args.out, "subtasks.csv", [r for r in flat if r.get("is_subtask")])
    write_csv(args.out, "attachments.csv", flatten_attachments(issues))
    write_csv(args.out, "issue_links.csv", flatten_links(issues))

    # ---- 7. Comments & worklogs --------------------------------------------
    if not args.no_comments:
        print("[7/9] Comments...")
        comments = collect_embedded_or_fetch(s, issues, "comment", "comments",
                                             get_all_comments, "comments")
        write_json(args.out, "comments.json", comments)
        write_csv(args.out, "comments.csv", [flatten_comment(c) for c in comments])
    if not args.no_worklogs:
        print("      Worklogs...")
        worklogs = collect_embedded_or_fetch(s, issues, "worklog", "worklogs",
                                             get_all_worklogs, "worklogs")
        write_json(args.out, "worklogs.json", worklogs)
        write_csv(args.out, "worklogs.csv", [flatten_worklog(w) for w in worklogs])

    # ---- 8. Changelogs (opt-in) --------------------------------------------
    if args.changelogs:
        print(f"[8/9] Changelogs for {len(issues)} issues (this is the slow part)...")
        rows = []
        for n, iss in enumerate(issues, 1):
            try:
                rows.extend(flatten_changelog(iss["key"], get_changelog(s, iss["key"])))
            except requests.HTTPError:
                pass
            if n % 100 == 0:
                print(f"    {n}/{len(issues)} issues done")
        write_json(args.out, "changelogs.json", rows)
        write_csv(args.out, "changelogs.csv", rows)
    else:
        print("[8/9] Changelogs skipped (run with --changelogs to include history).")

    # ---- 9. Boards & sprints ------------------------------------------------
    boards, all_sprints = [], []
    if not args.no_agile:
        print("[9/9] Boards and sprints...")
        try:
            boards = paginate_startat(s, EP.BOARDS, "values")
            write_json(args.out, "boards.json", boards)
            all_sprints, seen = [], set()
            for b in boards:
                try:
                    for sp in paginate_startat(s, EP.BOARD_SPRINTS.format(board_id=b['id']),
                                               "values", quiet=True):
                        if sp.get("id") not in seen:
                            seen.add(sp.get("id"))
                            sp["_board_id"] = b["id"]
                            all_sprints.append(sp)
                except requests.HTTPError:
                    pass  # Kanban boards have no sprints
            write_json(args.out, "sprints.json", all_sprints)
            write_csv(args.out, "sprints.csv", [{
                "id": sp.get("id"), "name": sp.get("name"), "state": sp.get("state"),
                "board_id": sp.get("_board_id"), "start": sp.get("startDate"),
                "end": sp.get("endDate"), "complete": sp.get("completeDate"),
                "goal": sp.get("goal"),
            } for sp in all_sprints])
        except requests.HTTPError as e:
            print(f"    skipped agile data: {e}")

    # ---- 10+. Extras: dashboards, filters, sprint reports, schemes... -------
    if not args.no_extras:
        from jira_export_extras import export_extras
        export_extras(s, args.out, projects, issues, boards, all_sprints,
                      fetch_all_watchers=args.watchers,
                      fetch_attachments=args.download_attachments,
                      fetch_remote_links=args.remote_links)

    # ---- Manifest -----------------------------------------------------------
    write_json(args.out, "_manifest.json", {
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "site": base_url,
        "jql": args.jql,
        "counts": {"projects": len(projects), "issues": len(flat)},
    })
    print(f"\nDone. Everything is in: {os.path.abspath(args.out)}")


if __name__ == "__main__":
    main()
