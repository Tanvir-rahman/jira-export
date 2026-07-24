#!/usr/bin/env python3
"""
jira_export_admin.py — site configuration, audit log, and board internals.

All verified working with a plain API token (site-admin account):
  - labels.json                  global label list
  - issue_link_types.json        Blocks / Cloners / Duplicates / Relates...
  - site_info.json               serverInfo + global configuration flags
  - project_categories.json      project categories (if any)
  - application_roles.json       products + license groups + seat info
  - screens.json, field_configurations.json, issue_type_schemes.json,
    priority_schemes.json        admin config schemes
  - audit_log.json/csv           Jira audit trail (user/group/project changes)
  - board_settings.json          swimlanes, quick filters, card colors,
                                 estimation, working days (greenhopper editmodel)
  - quickfilters.csv, swimlanes.csv
  - board_epics.json/csv         epic status/color/done per board
  - backlog_order.csv            exact backlog rank order per board
  - project_features.json/csv    enabled features per project
  - voters.json/csv              voter identities (issues with votes > 0)
  - remote_links.json/csv        web/Confluence links on issues (--remote-links)

NOT accessible with an API token: webhook registrations
(/rest/api/3/webhook is Connect/OAuth-app-only).
"""

import requests

import jira_endpoints as EP
from jira_export import _dn, api_get, paginate_startat, write_csv, write_json


# --------------------------------------------------------------------------- #
# Site-wide reference / config
# --------------------------------------------------------------------------- #
def export_site_reference(s, out_dir):
    write_json(out_dir, "labels.json",
               paginate_startat(s, EP.LABELS, "values", page_size=1000))
    write_json(out_dir, "issue_link_types.json",
               api_get(s, EP.ISSUE_LINK_TYPES).get("issueLinkTypes", []))
    write_json(out_dir, "site_info.json", {
        "server_info": api_get(s, EP.SERVER_INFO),
        "configuration": api_get(s, EP.CONFIGURATION),
    })
    write_json(out_dir, "project_categories.json",
               api_get(s, EP.PROJECT_CATEGORIES))
    for name, path in [
        ("application_roles.json", EP.APPLICATION_ROLES),
    ]:
        try:
            write_json(out_dir, name, api_get(s, path, quiet=True))
        except requests.HTTPError:
            print(f"    ({name}: needs admin, skipped)")
    for name, path in [
        ("screens.json", EP.SCREENS),
        ("field_configurations.json", EP.FIELD_CONFIGS),
        ("issue_type_schemes.json", EP.ISSUE_TYPE_SCHEMES),
        ("priority_schemes.json", EP.PRIORITY_SCHEMES),
    ]:
        try:
            write_json(out_dir, name,
                       paginate_startat(s, path, "values", quiet=True))
        except requests.HTTPError:
            print(f"    ({name}: needs admin, skipped)")


def export_audit_log(s, out_dir, page_size=1000):
    records, offset = [], 0
    while True:
        data = api_get(s, EP.AUDIT_RECORDS,
                       {"offset": offset, "limit": page_size})
        batch = data.get("records", [])
        records.extend(batch)
        if not batch or offset + len(batch) >= data.get("total", 0):
            break
        offset += len(batch)
    write_json(out_dir, "audit_log.json", records)
    write_csv(out_dir, "audit_log.csv", [{
        "created": r.get("created"), "category": r.get("category"),
        "summary": r.get("summary"),
        "object": (r.get("objectItem") or {}).get("name"),
        "object_type": (r.get("objectItem") or {}).get("typeName"),
        "changed": "; ".join(
            f"{c.get('fieldName')}: {c.get('from') or ''} -> {c.get('to') or ''}"
            for c in (r.get("changedValues") or [])),
    } for r in records])


# --------------------------------------------------------------------------- #
# Board internals: swimlanes, quick filters, backlog rank, epics
# --------------------------------------------------------------------------- #
def export_board_settings(s, out_dir, boards):
    settings, qf_rows, swim_rows = [], [], []
    for b in boards:
        try:
            cfg = api_get(s, EP.GH_RAPIDVIEW_CONFIG,
                          {"rapidViewId": b["id"]}, quiet=True)
        except requests.HTTPError:
            continue
        cfg["_board_id"] = b["id"]
        settings.append(cfg)
        for qf in cfg.get("quickFilterConfig", {}).get("quickFilters", []):
            qf_rows.append({"board_id": b["id"], "board_name": b.get("name"),
                            "name": qf.get("name"), "jql": qf.get("query")})
        for sw in cfg.get("swimlanesConfig", {}).get("swimlanes", []):
            swim_rows.append({"board_id": b["id"], "board_name": b.get("name"),
                              "name": sw.get("name"), "jql": sw.get("query")})
    write_json(out_dir, "board_settings.json", settings)
    write_csv(out_dir, "quickfilters.csv", qf_rows)
    write_csv(out_dir, "swimlanes.csv", swim_rows)


def export_board_epics_and_backlog(s, out_dir, boards):
    epic_rows, backlog_rows = [], []
    for b in boards:
        try:
            for e in paginate_startat(s, EP.BOARD_EPICS.format(board_id=b['id']),
                                      "values", quiet=True):
                epic_rows.append({
                    "board_id": b["id"], "epic_key": e.get("key"),
                    "name": e.get("name"), "done": e.get("done"),
                    "color": (e.get("color") or {}).get("key"),
                })
        except requests.HTTPError:
            pass
        try:
            start, pos = 0, 1
            while True:
                data = api_get(s, EP.BOARD_BACKLOG.format(board_id=b['id']),
                               {"startAt": start, "maxResults": 100,
                                "fields": "summary"}, quiet=True)
                issues = data.get("issues", [])
                for iss in issues:
                    backlog_rows.append({
                        "board_id": b["id"], "rank_position": pos,
                        "issue_key": iss.get("key"),
                        "summary": (iss.get("fields") or {}).get("summary"),
                    })
                    pos += 1
                start += len(issues)
                if not issues or start >= data.get("total", 0):
                    break
        except requests.HTTPError:
            pass
    write_json(out_dir, "board_epics.json", epic_rows)
    write_csv(out_dir, "board_epics.csv", epic_rows)
    write_csv(out_dir, "backlog_order.csv", backlog_rows)


# --------------------------------------------------------------------------- #
# Project features, voters, remote links
# --------------------------------------------------------------------------- #
def export_project_features(s, out_dir, projects):
    rows = []
    for p in projects:
        try:
            for f in api_get(s, EP.PROJECT_FEATURES.format(key=p['key']),
                             quiet=True).get("features", []):
                rows.append({"project_key": p["key"], "feature": f.get("feature"),
                             "state": f.get("state")})
        except requests.HTTPError:
            pass
    write_json(out_dir, "project_features.json", rows)
    write_csv(out_dir, "project_features.csv", rows)


def export_voters(s, out_dir, issues):
    rows = []
    targets = [i for i in issues
               if ((i.get("fields", {}).get("votes") or {}).get("votes", 0)) > 0]
    print(f"    fetching voters for {len(targets)} issues...")
    for iss in targets:
        try:
            data = api_get(s, EP.ISSUE_VOTES.format(key=iss['key']), quiet=True)
        except requests.HTTPError:
            continue
        for v in data.get("voters", []):
            rows.append({"issue_key": iss["key"],
                         "account_id": v.get("accountId"),
                         "display_name": v.get("displayName")})
    write_json(out_dir, "voters.json", rows)
    write_csv(out_dir, "voters.csv", rows)


def export_remote_links(s, out_dir, issues):
    rows = []
    print(f"    fetching remote links for {len(issues)} issues "
          f"(1 request each)...")
    for n, iss in enumerate(issues, 1):
        try:
            links = api_get(s, EP.ISSUE_REMOTE_LINKS.format(key=iss['key']),
                            quiet=True)
        except requests.HTTPError:
            continue
        for ln in links:
            obj = ln.get("object") or {}
            rows.append({
                "issue_key": iss["key"], "title": obj.get("title"),
                "url": obj.get("url"),
                "relationship": ln.get("relationship"),
                "application": (ln.get("application") or {}).get("name"),
            })
        if n % 100 == 0:
            print(f"      {n}/{len(issues)} done")
    write_json(out_dir, "remote_links.json", rows)
    write_csv(out_dir, "remote_links.csv", rows)
