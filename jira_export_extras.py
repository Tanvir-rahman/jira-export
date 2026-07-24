"""
jira_export_extras.py — additional exports layered on top of jira_export.py.

Adds (all verified working with a plain API token):
  - Dashboards + gadgets
  - Saved filters (with JQL)
  - Board configurations (columns -> statuses)
  - Velocity per scrum board (greenhopper chart API)
  - Sprint reports per sprint: committed vs completed, punted, added mid-sprint
  - Burndown scope-change events per sprint
  - Workflows + workflow schemes
  - Permission / notification / issue-security schemes
  - Project versions, components, roles (with role actors)
  - Watcher identities per issue
  - Attachment binary download (--download-attachments)
  - JPD views + insights via experimental GraphQL (see jira_export_jpd.py)

NOT possible with an API token (kept out on purpose):
  - Automation rules (no public API; UI export only)
  - Other users' hidden emails (GDPR profile visibility)
  - Notification emails Jira sent
"""

import os
import re

import requests

import jira_endpoints as EP
from jira_export import _dn, api_get, paginate_startat, write_csv, write_json


# --------------------------------------------------------------------------- #
# Dashboards, filters, board config
# --------------------------------------------------------------------------- #
def export_dashboards(s, out_dir):
    dashboards = paginate_startat(s, EP.DASHBOARDS, "dashboards")
    write_json(out_dir, "dashboards.json", dashboards)
    gadget_rows = []
    for d in dashboards:
        try:
            for g in api_get(s, EP.DASHBOARD_GADGETS.format(dashboard_id=d['id'])).get("gadgets", []):
                gadget_rows.append({
                    "dashboard_id": d.get("id"), "dashboard_name": d.get("name"),
                    "gadget_id": g.get("id"), "module_key": g.get("moduleKey"),
                    "title": g.get("title"), "color": g.get("color"),
                })
        except requests.HTTPError:
            pass
    write_json(out_dir, "dashboard_gadgets.json", gadget_rows)
    write_csv(out_dir, "dashboard_gadgets.csv", gadget_rows)


def export_filters(s, out_dir):
    filters = paginate_startat(s, EP.FILTER_SEARCH, "values",
                               params={"expand": "jql,owner,sharePermissions"})
    write_json(out_dir, "filters.json", filters)
    write_csv(out_dir, "filters.csv", [{
        "id": f.get("id"), "name": f.get("name"), "owner": _dn(f.get("owner")),
        "jql": f.get("jql"), "favourite": f.get("favourite"),
    } for f in filters])


def export_board_configs(s, out_dir, boards):
    configs, column_rows = [], []
    for b in boards:
        try:
            cfg = api_get(s, EP.BOARD_CONFIG.format(board_id=b['id']))
        except requests.HTTPError:
            continue
        configs.append(cfg)
        for col in (cfg.get("columnConfig") or {}).get("columns", []):
            column_rows.append({
                "board_id": cfg.get("id"), "board_name": cfg.get("name"),
                "column": col.get("name"),
                "statuses": ", ".join(st.get("id", "") for st in col.get("statuses", [])),
            })
    write_json(out_dir, "board_configs.json", configs)
    write_csv(out_dir, "board_columns.csv", column_rows)


# --------------------------------------------------------------------------- #
# Greenhopper charts: velocity, sprint report, burndown
# --------------------------------------------------------------------------- #
def export_velocity(s, out_dir, boards):
    raw, rows = [], []
    for b in boards:
        try:
            data = api_get(s, EP.GH_VELOCITY, {"rapidViewId": b["id"]},
                           quiet=True)
        except requests.HTTPError:
            continue
        if not data.get("sprints"):
            continue
        data["_board_id"] = b["id"]
        raw.append(data)
        entries = data.get("velocityStatEntries") or {}
        for sp in data.get("sprints", []):
            e = entries.get(str(sp.get("id"))) or {}
            rows.append({
                "board_id": b["id"], "sprint_id": sp.get("id"),
                "sprint_name": sp.get("name"), "state": sp.get("state"),
                "goal": sp.get("goal"),
                "committed": (e.get("estimated") or {}).get("value"),
                "completed": (e.get("completed") or {}).get("value"),
            })
    write_json(out_dir, "velocity.json", raw)
    write_csv(out_dir, "velocity.csv", rows)


def _issue_keys(items):
    return ", ".join(i.get("key", "") for i in items)


def export_sprint_reports(s, out_dir, sprints):
    raw, rows = [], []
    for sp in sprints:
        if sp.get("state") == "future":
            continue
        board_id = sp.get("_board_id") or sp.get("originBoardId")
        try:
            data = api_get(s, EP.GH_SPRINT_REPORT,
                           {"rapidViewId": board_id, "sprintId": sp["id"]},
                           quiet=True)
        except requests.HTTPError:
            continue
        data["_board_id"], data["_sprint_id"] = board_id, sp["id"]
        raw.append(data)
        c = data.get("contents") or {}
        rows.append({
            "sprint_id": sp.get("id"), "sprint_name": sp.get("name"),
            "board_id": board_id, "state": sp.get("state"),
            "committed_points": (c.get("allIssuesEstimateSum") or {}).get("value"),
            "completed_points": (c.get("completedIssuesEstimateSum") or {}).get("value"),
            "not_completed_points": (c.get("issuesNotCompletedEstimateSum") or {}).get("value"),
            "punted_points": (c.get("puntedIssuesEstimateSum") or {}).get("value"),
            "completed_keys": _issue_keys(c.get("completedIssues") or []),
            "not_completed_keys": _issue_keys(c.get("issuesNotCompletedInCurrentSprint") or []),
            "punted_keys": _issue_keys(c.get("puntedIssues") or []),
            "completed_in_another_sprint_keys": _issue_keys(
                c.get("issuesCompletedInAnotherSprint") or []),
            "added_during_sprint_keys": ", ".join(
                (c.get("issueKeysAddedDuringSprint") or {}).keys()),
        })
    write_json(out_dir, "sprint_reports.json", raw)
    write_csv(out_dir, "sprint_reports.csv", rows)


def export_burndowns(s, out_dir, sprints):
    raw = []
    for sp in sprints:
        if sp.get("state") == "future":
            continue
        board_id = sp.get("_board_id") or sp.get("originBoardId")
        try:
            data = api_get(s, EP.GH_BURNDOWN,
                           {"rapidViewId": board_id, "sprintId": sp["id"]},
                           quiet=True)
        except requests.HTTPError:
            continue
        data["_board_id"], data["_sprint_id"] = board_id, sp["id"]
        raw.append(data)
    write_json(out_dir, "burndowns.json", raw)


# --------------------------------------------------------------------------- #
# Workflows and schemes
# --------------------------------------------------------------------------- #
def export_workflows_and_schemes(s, out_dir):
    write_json(out_dir, "workflows.json",
               paginate_startat(s, EP.WORKFLOW_SEARCH, "values"))
    write_json(out_dir, "workflow_schemes.json",
               paginate_startat(s, EP.WORKFLOW_SCHEMES, "values"))
    write_json(out_dir, "permission_schemes.json",
               api_get(s, EP.PERMISSION_SCHEMES,
                       {"expand": "permissions"}).get("permissionSchemes", []))
    write_json(out_dir, "notification_schemes.json",
               paginate_startat(s, EP.NOTIFICATION_SCHEMES, "values",
                                params={"expand": "all"}))
    write_json(out_dir, "issue_security_schemes.json",
               api_get(s, EP.ISSUE_SECURITY_SCHEMES)
               .get("issueSecuritySchemes", []))


# --------------------------------------------------------------------------- #
# Per-project: versions, components, roles
# --------------------------------------------------------------------------- #
def export_project_details(s, out_dir, projects):
    versions, components, role_rows = [], [], []
    for p in projects:
        key = p.get("key")
        for v in api_get(s, EP.PROJECT_VERSIONS.format(key=key)):
            v["_project_key"] = key
            versions.append(v)
        for c in api_get(s, EP.PROJECT_COMPONENTS.format(key=key)):
            c["_project_key"] = key
            components.append(c)
        try:
            roles = api_get(s, EP.PROJECT_ROLES.format(key=key))
            for role_name, role_url in roles.items():
                for actor in api_get(s, role_url, quiet=True).get("actors", []):
                    role_rows.append({
                        "project_key": key, "role": role_name,
                        "actor": actor.get("displayName"),
                        "actor_type": actor.get("type"),
                        "account_id": (actor.get("actorUser") or {}).get("accountId"),
                    })
        except requests.HTTPError:
            pass
    write_json(out_dir, "versions.json", versions)
    write_csv(out_dir, "versions.csv", [{
        "project_key": v.get("_project_key"), "id": v.get("id"),
        "name": v.get("name"), "released": v.get("released"),
        "release_date": v.get("releaseDate"),
    } for v in versions])
    write_json(out_dir, "components.json", components)
    write_csv(out_dir, "components.csv", [{
        "project_key": c.get("_project_key"), "id": c.get("id"),
        "name": c.get("name"), "lead": _dn(c.get("lead")),
    } for c in components])
    write_json(out_dir, "project_roles.json", role_rows)
    write_csv(out_dir, "project_roles.csv", role_rows)


# --------------------------------------------------------------------------- #
# Watchers, attachment binaries
# --------------------------------------------------------------------------- #
def export_watchers(s, out_dir, issues, fetch_all=False):
    """Watcher identities. By default only issues with >1 watcher (a single
    watcher is almost always the reporter); --watchers fetches every issue."""
    rows = []
    targets = [i for i in issues
               if fetch_all
               or ((i.get("fields", {}).get("watches") or {}).get("watchCount", 0) > 1)]
    print(f"    fetching watchers for {len(targets)} issues...")
    for n, iss in enumerate(targets, 1):
        try:
            data = api_get(s, EP.ISSUE_WATCHERS.format(key=iss['key']), quiet=True)
        except requests.HTTPError:
            continue
        for w in data.get("watchers", []):
            rows.append({
                "issue_key": iss["key"], "account_id": w.get("accountId"),
                "display_name": w.get("displayName"),
            })
        if n % 100 == 0:
            print(f"      {n}/{len(targets)} done")
    write_json(out_dir, "watchers.json", rows)
    write_csv(out_dir, "watchers.csv", rows)


def _safe_filename(name):
    return re.sub(r"[^\w.\-]+", "_", name or "unnamed")[:150]


def download_attachments(s, out_dir, issues):
    files_dir = os.path.join(out_dir, "attachments")
    count = 0
    for iss in issues:
        for a in (iss.get("fields", {}) or {}).get("attachment") or []:
            url = a.get("content")
            if not url:
                continue
            dest_dir = os.path.join(files_dir, iss["key"])
            os.makedirs(dest_dir, exist_ok=True)
            dest = os.path.join(dest_dir,
                                f"{a.get('id')}_{_safe_filename(a.get('filename'))}")
            if os.path.exists(dest):
                continue
            try:
                r = s.get(url, stream=True, timeout=120)
                r.raise_for_status()
                with open(dest, "wb") as fh:
                    fh.writelines(r.iter_content(65536))
                count += 1
            except requests.HTTPError as e:
                print(f"      {iss['key']} {a.get('filename')}: skipped ({e})")
    print(f"    downloaded {count} attachment files to {files_dir}")


# --------------------------------------------------------------------------- #
# Entry point called from jira_export.main()
# --------------------------------------------------------------------------- #
def export_extras(s, out_dir, projects, issues, boards, sprints,
                  fetch_all_watchers=False, fetch_attachments=False,
                  fetch_remote_links=False):
    print("[10] Dashboards, gadgets, filters...")
    export_dashboards(s, out_dir)
    export_filters(s, out_dir)

    print("[11] Board configurations...")
    export_board_configs(s, out_dir, boards)

    print("[12] Sprint reports, velocity, burndowns (greenhopper)...")
    export_velocity(s, out_dir, boards)
    export_sprint_reports(s, out_dir, sprints)
    export_burndowns(s, out_dir, sprints)

    print("[13] Workflows and schemes...")
    export_workflows_and_schemes(s, out_dir)

    print("[14] Project versions, components, roles...")
    export_project_details(s, out_dir, projects)

    print("[15] Watchers...")
    export_watchers(s, out_dir, issues, fetch_all=fetch_all_watchers)

    if fetch_attachments:
        print("[16] Downloading attachment binaries...")
        download_attachments(s, out_dir, issues)

    print("[17] JPD views + insights (experimental GraphQL)...")
    from jira_export_jpd import export_jpd
    export_jpd(s, out_dir, projects, issues)

    import jira_export_admin as admin
    print("[18] Site config, schemes, labels, license roles...")
    admin.export_site_reference(s, out_dir)
    print("[19] Audit log...")
    admin.export_audit_log(s, out_dir)
    print("[20] Board settings (swimlanes, quick filters), epics, backlog order...")
    admin.export_board_settings(s, out_dir, boards)
    admin.export_board_epics_and_backlog(s, out_dir, boards)
    print("[21] Project features + voters...")
    admin.export_project_features(s, out_dir, projects)
    admin.export_voters(s, out_dir, issues)
    if fetch_remote_links:
        print("[22] Remote links...")
        admin.export_remote_links(s, out_dir, issues)
