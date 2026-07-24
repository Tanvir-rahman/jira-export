#!/usr/bin/env python3
"""
jira_seed.py — Fill a Jira Cloud site with realistic demo data.

Creates:
  - 2 scrum projects (PHX, RKT) + 1 kanban project (OPS), each with a board
  - Epics, stories, tasks, bugs, sub-tasks (~340 issues), labels, story
    points, priorities, assignees spread across existing site users
  - 6 closed sprints + 1 active sprint per scrum board (sprint reports and
    velocity charts populate from this)
  - Comments and backdated worklogs
  - 5 Atlassian Teams with members (needs JIRA_ORG_ID)
  - Jira Product Discovery ideas + comments IF a JPD project already exists
    (JPD projects cannot be created via REST API — make one in the UI first,
    then rerun with --jpd-only)

Uses the same .env as jira_export.py (JIRA_URL, JIRA_EMAIL, JIRA_API_TOKEN,
JIRA_ORG_ID).

Caveats (API limitations, not bugs):
  - Comment/worklog author is always the API token's user; only assignees vary.
  - created/updated timestamps are server-stamped today, so burndown charts of
    past sprints look flat. Sprint scope/velocity/report numbers are real.

Cleanup: delete projects PHX, RKT, OPS (Project settings -> Delete) and the
seeded Teams. Existing projects are never touched.
"""

import argparse
import base64
import json
import os
import random
import sys
import time
from datetime import datetime, timedelta, timezone

import requests
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

random.seed(42)

MAX_RETRIES = 6
MAX_WAIT = 60

SCRUM_PROJECTS = [("PHX", "Phoenix Platform"), ("RKT", "Rocket Mobile")]
KANBAN_PROJECT = ("OPS", "Ops Board")
ISSUES_PER_SCRUM = 130
ISSUES_KANBAN = 40
SUBTASK_RATIO = 0.10
CLOSED_SPRINTS_PER_BOARD = 6
SPRINT_LEN_DAYS = 14
IDEAS_COUNT = 60

TEAM_NAMES = ["Platform Team", "Mobile Squad", "QA Guild", "Design Crew", "Data & Analytics"]

VERBS = ["Implement", "Refactor", "Design", "Optimize", "Add", "Migrate",
         "Document", "Automate", "Investigate", "Harden"]
OBJECTS = [
    "login flow", "payment webhook retries", "search indexing", "profile page cache",
    "rate limiter", "audit logging", "CSV export", "push notification service",
    "session refresh", "dark mode toggle", "billing invoices", "S3 upload pipeline",
    "GraphQL gateway", "feature flags", "email templates", "onboarding checklist",
    "error tracking", "CI pipeline", "database migrations", "API pagination",
    "OAuth scopes", "webhook signatures", "image thumbnailer", "analytics events",
    "cart checkout", "coupon engine", "user preferences", "SSO integration",
    "mobile deep links", "offline sync", "release notes generator", "SLA alerts",
]
BUG_CONDITIONS = ["locale is not en-US", "token expires mid-request", "list is empty",
                  "user has no avatar", "network flaps", "payload exceeds 1MB",
                  "two tabs are open", "daylight saving switches"]
EPIC_NAMES = ["User Onboarding Revamp", "Payments & Billing", "Search Overhaul",
              "Mobile Offline Mode", "Performance Hardening", "Admin Console"]
LABELS = ["backend", "frontend", "infra", "tech-debt", "customer-request",
          "security", "quick-win", "needs-design"]
PRIORITIES = ["Highest", "High", "Medium", "Medium", "Medium", "Low", "Lowest"]
SPRINT_GOALS = [
    "Ship the onboarding funnel improvements", "Cut p95 latency below 300ms",
    "Close out payment edge cases", "Stabilize mobile sync", "Reduce bug backlog by 30%",
    "Prepare the Q3 release", "Polish admin console UX", "Migrate legacy jobs",
]
COMMENTS = [
    "Picking this up now.", "Blocked on the API contract — pinged the platform team.",
    "PR is up, review please.", "Repro'd locally, root cause is a stale cache entry.",
    "Moved to QA, staging build 2143.", "Deployed behind a feature flag.",
    "Needs a design pass before we ship.", "Split the remaining work into a follow-up.",
    "Verified on staging, looks good.", "Adding test coverage before closing.",
    "Customer confirmed the fix works.", "Bumping priority per standup discussion.",
    "This depends on the migration in the platform epic.", "Rolled back — caused a regression in checkout.",
    "Re-opened: still reproducible on iOS 19.", "Estimated at 5 points in refinement.",
]
IDEA_THEMES = [
    "AI-assisted ticket triage", "Bulk CSV import", "Slack digest of weekly changes",
    "Customer health score", "Self-serve data export", "Usage-based billing tier",
    "In-app guided tours", "Public roadmap page", "API rate-limit dashboard",
    "Template gallery", "Offline-first mobile mode", "SSO for enterprise",
    "Audit log search", "Custom fields on reports", "Dark mode", "Sandbox environments",
    "Webhooks v2", "Granular permissions", "White-label option", "Multi-language support",
]


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #
def build_session():
    url = os.environ.get("JIRA_URL")
    email = os.environ.get("JIRA_EMAIL")
    token = os.environ.get("JIRA_API_TOKEN")
    if not all([url, email, token]):
        sys.exit("Set JIRA_URL, JIRA_EMAIL, JIRA_API_TOKEN in .env")
    s = requests.Session()
    auth = base64.b64encode(f"{email}:{token}".encode()).decode()
    s.headers.update({"Authorization": f"Basic {auth}", "Accept": "application/json",
                      "Content-Type": "application/json"})
    s.base_url = url.rstrip("/")
    return s


def req(session, method, path, payload=None, params=None, quiet=False):
    """Returns (status_code, parsed_json_or_None). Retries 429/5xx."""
    url = path if path.startswith("http") else f"{session.base_url}{path}"
    r = None
    for attempt in range(MAX_RETRIES):
        r = session.request(method, url, params=params,
                            data=json.dumps(payload) if payload is not None else None)
        if r.status_code == 429:
            wait = min(float(r.headers.get("Retry-After", 2 ** attempt)), MAX_WAIT)
            print(f"    rate limited — waiting {wait:.0f}s")
            time.sleep(wait)
            continue
        if 500 <= r.status_code < 600:
            time.sleep(min(2 ** attempt, MAX_WAIT))
            continue
        break
    if r.status_code >= 400 and not quiet:
        print(f"    HTTP {r.status_code} {method} {path}: {r.text[:300]}")
    try:
        return r.status_code, (r.json() if r.text else None)
    except ValueError:
        return r.status_code, None


def adf(text):
    return {"type": "doc", "version": 1,
            "content": [{"type": "paragraph",
                         "content": [{"type": "text", "text": text}]}]}


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000%z")


# --------------------------------------------------------------------------- #
# Building blocks
# --------------------------------------------------------------------------- #
def ensure_project(s, key, name, template, lead_id):
    code, proj = req(s, "GET", f"/rest/api/3/project/{key}", quiet=True)
    if code == 200:
        print(f"    {key} already exists — reusing")
        return proj
    payload = {"key": key, "name": name, "projectTypeKey": "software",
               "projectTemplateKey": template, "leadAccountId": lead_id}
    code, _ = req(s, "POST", "/rest/api/3/project", payload)
    if code not in (200, 201):
        classic = template.replace("agility-scrum", "scrum-classic") \
                          .replace("agility-kanban", "kanban-classic")
        code, _ = req(s, "POST", "/rest/api/3/project", {**payload, "projectTemplateKey": classic})
        if code not in (200, 201):
            sys.exit(f"Could not create project {key}")
    print(f"    created project {key} — {name}")
    code, proj = req(s, "GET", f"/rest/api/3/project/{key}")
    return proj


def issue_type_names(proj):
    """Map roles -> issue type names actually present in this project."""
    names = [t["name"] for t in proj.get("issueTypes", [])]
    sub = next((t["name"] for t in proj.get("issueTypes", []) if t.get("subtask")), None)
    pick = lambda *cands: next((n for n in names for c in cands if n.lower() == c), None)
    return {"epic": pick("epic"), "story": pick("story") or pick("task"),
            "task": pick("task"), "bug": pick("bug") or pick("task"), "subtask": sub}


def board_for_project(s, key):
    _, data = req(s, "GET", "/rest/agile/1.0/board", params={"projectKeyOrId": key})
    values = (data or {}).get("values", [])
    return values[0]["id"] if values else None


def assignable_users(s, key, fallback):
    _, users = req(s, "GET", "/rest/api/3/user/assignable/search",
                   params={"project": key, "maxResults": 50}, quiet=True)
    ids = [u["accountId"] for u in users or []
           if u.get("accountType") == "atlassian" and u.get("active")]
    return ids or [fallback]


def detect_optional_fields(s, project_key, type_name, sp_field):
    """Create one canary issue with optional fields; drop whatever the API rejects."""
    fields = {"project": {"key": project_key}, "issuetype": {"name": type_name},
              "summary": "Canary — field probe",
              "priority": {"name": "Medium"}, "labels": ["seed"]}
    if sp_field:
        fields[sp_field] = 3
    allowed = {"priority", "labels"} | ({sp_field} if sp_field else set())
    for _ in range(4):
        code, data = req(s, "POST", "/rest/api/3/issue", {"fields": fields}, quiet=True)
        if code in (200, 201):
            return allowed, data["key"]
        bad = set((data or {}).get("errors", {}).keys()) & allowed
        if not bad:
            return set(), None
        allowed -= bad
        for b in bad:
            fields.pop(b, None)
    return set(), None


def bulk_create(s, issue_fields_list):
    """POST /issue/bulk in chunks of 50. Returns list of created keys."""
    keys = []
    for i in range(0, len(issue_fields_list), 50):
        chunk = issue_fields_list[i:i + 50]
        _, data = req(s, "POST", "/rest/api/3/issue/bulk",
                      {"issueUpdates": [{"fields": f} for f in chunk]})
        if data:
            keys.extend(iss["key"] for iss in data.get("issues", []))
            for e in data.get("errors", []):
                print(f"    bulk item failed: {str(e)[:150]}")
        print(f"    created {len(keys)}/{len(issue_fields_list)} issues...")
    return keys


def transition_to(s, key, target_names):
    _, data = req(s, "GET", f"/rest/api/3/issue/{key}/transitions", quiet=True)
    for t in (data or {}).get("transitions", []):
        if (t.get("to") or {}).get("name", "").lower() in target_names:
            req(s, "POST", f"/rest/api/3/issue/{key}/transitions",
                {"transition": {"id": t["id"]}}, quiet=True)
            return True
    return False


def add_comment(s, key, text):
    req(s, "POST", f"/rest/api/3/issue/{key}/comment", {"body": adf(text)}, quiet=True)


def add_worklog(s, key, started_dt, seconds):
    req(s, "POST", f"/rest/api/3/issue/{key}/worklog",
        {"started": iso(started_dt), "timeSpentSeconds": seconds,
         "comment": adf("Work logged")}, quiet=True)


def make_summary(kind):
    if kind == "bug":
        return f"{random.choice(OBJECTS).capitalize()} fails when {random.choice(BUG_CONDITIONS)}"
    return f"{random.choice(VERBS)} {random.choice(OBJECTS)}"


# --------------------------------------------------------------------------- #
# Phases
# --------------------------------------------------------------------------- #
def seed_teams(s, org_id, user_ids):
    print("[1/6] Atlassian Teams...")
    if not org_id:
        print("    JIRA_ORG_ID not set — skipping teams")
        return
    _, existing = req(s, "GET", f"/gateway/api/public/teams/v1/org/{org_id}/teams",
                      params={"size": 100}, quiet=True)
    have = {t.get("displayName") for t in (existing or {}).get("entities", [])}
    for name in TEAM_NAMES:
        if name in have:
            print(f"    team '{name}' exists — skipping")
            continue
        code, team = req(s, "POST", f"/gateway/api/public/teams/v1/org/{org_id}/teams",
                         {"displayName": name, "teamType": "OPEN",
                          "description": f"{name} — seeded demo team"})
        if code not in (200, 201) or not team:
            continue
        tid = team.get("teamId")
        members = random.sample(user_ids, min(len(user_ids), random.randint(3, 6)))
        req(s, "POST",
            f"/gateway/api/public/teams/v1/org/{org_id}/teams/{tid}/members/add",
            {"members": [{"accountId": a} for a in members]}, quiet=True)
        print(f"    created team '{name}' with {len(members)} members")


def seed_project_issues(s, proj, n_issues, me):
    """Create epics + issues + subtasks. Returns (epic_keys, issue_keys)."""
    key = proj["key"]
    types = issue_type_names(proj)
    users = assignable_users(s, key, me)

    _, all_fields = req(s, "GET", "/rest/api/3/field", quiet=True)
    sp_field = next((f["id"] for f in all_fields or []
                     if (f.get("name") or "").lower() in ("story points", "story point estimate")), None)
    allowed, canary = detect_optional_fields(s, key, types["task"], sp_field)
    print(f"    optional fields usable on {key}: {sorted(allowed) or 'none'}")

    epic_keys = []
    if types["epic"]:
        epics = [{"project": {"key": key}, "issuetype": {"name": types["epic"]},
                  "summary": e, "description": adf(f"Epic: {e}")} for e in EPIC_NAMES]
        epic_keys = bulk_create(s, epics)

    batch = []
    for i in range(n_issues):
        kind = random.choices(["story", "task", "bug"], weights=[4, 3, 3])[0]
        f = {"project": {"key": key}, "issuetype": {"name": types[kind]},
             "summary": make_summary(kind),
             "assignee": {"accountId": random.choice(users)}}
        if random.random() < 0.5:
            f["description"] = adf("Acceptance criteria:\n- works on staging\n- has tests\n- reviewed")
        if "priority" in allowed:
            f["priority"] = {"name": random.choice(PRIORITIES)}
        if "labels" in allowed:
            f["labels"] = random.sample(LABELS, random.randint(0, 2))
        if sp_field in allowed and kind != "bug":
            f[sp_field] = random.choice([1, 2, 3, 5, 8, 13])
        if epic_keys and random.random() < 0.7:
            f["parent"] = {"key": random.choice(epic_keys)}
        batch.append(f)
    issue_keys = bulk_create(s, batch)
    if canary:
        issue_keys.append(canary)

    if types["subtask"] and issue_keys:
        subs = [{"project": {"key": key}, "issuetype": {"name": types["subtask"]},
                 "summary": f"Subtask: {random.choice(VERBS).lower()} {random.choice(OBJECTS)}",
                 "parent": {"key": random.choice(issue_keys)},
                 "assignee": {"accountId": random.choice(users)}}
                for _ in range(int(n_issues * SUBTASK_RATIO))]
        issue_keys += bulk_create(s, subs)
    return epic_keys, issue_keys


def seed_sprints(s, board_id, issue_keys, project_key):
    """6 closed sprints (with resolved issues -> sprint report data) + 1 active."""
    # Scrum templates auto-create an empty future "Sprint 1" — only skip when
    # sprints were actually run (active/closed) on this board.
    _, existing = req(s, "GET", f"/rest/agile/1.0/board/{board_id}/sprint",
                      params={"maxResults": 1, "state": "active,closed"}, quiet=True)
    if (existing or {}).get("values"):
        print(f"    board {board_id} already has active/closed sprints — skipping sprint phase")
        return
    pool = list(issue_keys)
    random.shuffle(pool)
    now = datetime.now(timezone.utc)
    n_sprints = CLOSED_SPRINTS_PER_BOARD + 1
    per_sprint = max(6, len(pool) // (n_sprints + 1))  # leave some in backlog
    first_start = now - timedelta(days=SPRINT_LEN_DAYS * CLOSED_SPRINTS_PER_BOARD)

    for idx in range(n_sprints):
        start = first_start + timedelta(days=SPRINT_LEN_DAYS * idx)
        end = start + timedelta(days=SPRINT_LEN_DAYS)
        is_last = idx == n_sprints - 1
        code, sp = req(s, "POST", "/rest/agile/1.0/sprint",
                       {"name": f"{project_key} Sprint {idx + 1}",
                        "originBoardId": board_id, "goal": random.choice(SPRINT_GOALS),
                        "startDate": iso(start), "endDate": iso(end)})
        if code not in (200, 201):
            print("    sprint create failed — aborting sprint phase")
            return
        sid = sp["id"]
        take, pool = pool[:per_sprint], pool[per_sprint:]
        for j in range(0, len(take), 50):
            req(s, "POST", f"/rest/agile/1.0/sprint/{sid}/issue",
                {"issues": take[j:j + 50]}, quiet=True)
        req(s, "POST", f"/rest/agile/1.0/sprint/{sid}", {"state": "active"})
        if is_last:
            for k in take:
                r = random.random()
                if r < 0.3:
                    transition_to(s, k, {"in progress"})
                elif r < 0.45:
                    transition_to(s, k, {"done"})
            print(f"    sprint {idx + 1} ACTIVE with {len(take)} issues")
        else:
            done = 0
            for k in take:
                if random.random() < 0.75 and transition_to(s, k, {"done"}):
                    done += 1
            req(s, "POST", f"/rest/agile/1.0/sprint/{sid}", {"state": "closed"})
            print(f"    sprint {idx + 1} closed — {done}/{len(take)} done "
                  f"({start.date()} → {end.date()})")


def seed_kanban(s, proj, me):
    print(f"[4/6] Kanban project {proj['key']}...")
    _, keys = seed_project_issues(s, proj, ISSUES_KANBAN, me)
    for k in keys:
        r = random.random()
        if r < 0.35:
            transition_to(s, k, {"in progress"})
        elif r < 0.65:
            transition_to(s, k, {"done"})
    print(f"    {len(keys)} kanban issues spread across columns")
    return keys


def seed_comments_worklogs(s, issue_keys):
    print("[5/6] Comments and worklogs...")
    now = datetime.now(timezone.utc)
    n_comments = n_worklogs = 0
    for i, k in enumerate(issue_keys, 1):
        for _ in range(random.choices([0, 1, 2, 3], weights=[2, 4, 3, 1])[0]):
            add_comment(s, k, random.choice(COMMENTS))
            n_comments += 1
        if random.random() < 0.4:
            for _ in range(random.randint(1, 2)):
                started = now - timedelta(days=random.randint(1, 90),
                                          hours=random.randint(0, 8))
                add_worklog(s, k, started, random.choice([1800, 3600, 7200, 14400]))
                n_worklogs += 1
        if i % 50 == 0:
            print(f"    {i}/{len(issue_keys)} issues... "
                  f"({n_comments} comments, {n_worklogs} worklogs)")
    print(f"    total: {n_comments} comments, {n_worklogs} worklogs")


def seed_jpd(s, me):
    print("[6/6] Product Discovery ideas...")
    _, data = req(s, "GET", "/rest/api/3/project/search",
                  params={"typeKey": "product_discovery"})
    jpd = (data or {}).get("values", [])
    if not jpd:
        print("    No JPD project found. Create one in the UI "
              "(Projects -> Create -> Product Discovery), then rerun: "
              "python jira_seed.py --jpd-only")
        return
    for proj in jpd:
        _, full = req(s, "GET", f"/rest/api/3/project/{proj['key']}")
        idea_type = next((t["name"] for t in (full or {}).get("issueTypes", [])
                          if not t.get("subtask")), "Idea")
        batch = []
        for i in range(IDEAS_COUNT):
            theme = IDEA_THEMES[i % len(IDEA_THEMES)]
            suffix = "" if i < len(IDEA_THEMES) else f" (v{i // len(IDEA_THEMES) + 1})"
            batch.append({"project": {"key": proj["key"]},
                          "issuetype": {"name": idea_type},
                          "summary": theme + suffix,
                          "description": adf(f"Customer signal: multiple requests for {theme.lower()}.")})
        keys = bulk_create(s, batch)
        for k in random.sample(keys, min(len(keys), 25)):
            add_comment(s, k, random.choice(COMMENTS))
        print(f"    {proj['key']}: {len(keys)} ideas created "
              "(views/boards auto-populate in the JPD UI)")


def fetch_sprintable_keys(s, project_key):
    """Non-epic, non-subtask issue keys of a project (for sprint reruns)."""
    jql = (f'project = {project_key} AND issuetype != Epic '
           f'AND issuetype not in subtaskIssueTypes() ORDER BY created ASC')
    keys, token = [], None
    while True:
        payload = {"jql": jql, "maxResults": 100, "fields": ["status"]}
        if token:
            payload["nextPageToken"] = token
        code, data = req(s, "POST", "/rest/api/3/search/jql", payload)
        if code != 200:
            return keys
        keys.extend(i["key"] for i in data.get("issues", []))
        token = data.get("nextPageToken")
        if not token or not data.get("issues"):
            return keys


# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser(description="Seed a Jira Cloud site with demo data.")
    ap.add_argument("--jpd-only", action="store_true",
                    help="Only fill Product Discovery ideas (after creating a JPD project in the UI).")
    ap.add_argument("--teams-only", action="store_true",
                    help="Only (re)create Atlassian Teams.")
    ap.add_argument("--sprints-only", action="store_true",
                    help="Only run the sprint phase on existing scrum projects.")
    args = ap.parse_args()

    s = build_session()
    print("[0/6] Authenticating...")
    code, me = req(s, "GET", "/rest/api/3/myself")
    if code != 200:
        sys.exit("Auth failed")
    my_id = me["accountId"]
    print(f"    OK — {me.get('displayName')}")

    if args.jpd_only:
        seed_jpd(s, my_id)
        return
    if args.teams_only or args.sprints_only:
        if args.teams_only:
            _, users = req(s, "GET", "/rest/api/3/users/search", params={"maxResults": 50})
            user_ids = [u["accountId"] for u in users or []
                        if u.get("accountType") == "atlassian" and u.get("active")]
            seed_teams(s, os.environ.get("JIRA_ORG_ID"), user_ids)
        if args.sprints_only:
            for key, _name in SCRUM_PROJECTS:
                board = board_for_project(s, key)
                issues = fetch_sprintable_keys(s, key)
                print(f"[sprints] {key}: board {board}, {len(issues)} issues")
                if board and issues:
                    seed_sprints(s, board, issues, key)
        return

    _, users = req(s, "GET", "/rest/api/3/users/search", params={"maxResults": 50})
    user_ids = [u["accountId"] for u in users or []
                if u.get("accountType") == "atlassian" and u.get("active")]
    print(f"    {len(user_ids)} active site users to spread work across")

    seed_teams(s, os.environ.get("JIRA_ORG_ID"), user_ids)

    all_keys = []
    for n, (key, name) in enumerate(SCRUM_PROJECTS, 2):
        print(f"[{n}/6] Scrum project {key} — {name}...")
        proj = ensure_project(s, key, name,
                              "com.pyxis.greenhopper.jira:gh-simplified-agility-scrum", my_id)
        epics, issues = seed_project_issues(s, proj, ISSUES_PER_SCRUM, my_id)
        all_keys += epics + issues
        board = board_for_project(s, key)
        if board:
            seed_sprints(s, board, issues, key)
        else:
            print("    no board found — skipped sprints")

    kanban = ensure_project(s, KANBAN_PROJECT[0], KANBAN_PROJECT[1],
                            "com.pyxis.greenhopper.jira:gh-simplified-agility-kanban", my_id)
    all_keys += seed_kanban(s, kanban, my_id)

    seed_comments_worklogs(s, all_keys)
    seed_jpd(s, my_id)

    print(f"\nDone. Seeded {len(all_keys)} issues across "
          f"{[k for k, _ in SCRUM_PROJECTS] + [KANBAN_PROJECT[0]]}. "
          "Check the boards, backlog, sprint reports and velocity charts in the UI.")


if __name__ == "__main__":
    main()
