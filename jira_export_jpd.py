#!/usr/bin/env python3
"""
jira_export_jpd.py — Jira Product Discovery (polaris) views + insights export.

Uses Atlassian's experimental GraphQL API (header `X-ExperimentalApi:
polaris-v0`) on the site gateway (<site>/gateway/api/graphql). Works with a
plain email + API token (Basic auth) — verified live.

Exports per product_discovery project:
  - jpd_viewsets.json            view sets (sections in the sidebar)
  - jpd_views.json / .csv        every view: type, filter JQL, emoji, ranks
  - jpd_insights.json / .csv     insights with snippets, mapped to idea keys
  - jpd_ideas.csv                ideas with JPD fields (RICE, impact, effort,
                                 confidence, value, reach, insight counts...)

Known gap: the API is read-only and experimental — Atlassian may change it.
Field *values* of type formula (e.g. RICE score) are computed client-side and
may come back null over the REST issue API.
"""

import requests

import jira_endpoints as EP
from jira_export import adf_to_text, api_get, search_all_issues, write_csv, write_json

VIEW_FIELDS = ("id uuid name emoji visualizationType containsArchived jql "
               "rank createdAt updatedAt viewSetId")

JPD_QUERY = """query jpdExport($id: ID!) {
  polarisProject(id: $id) {
    key name
    viewsets {
      id name type rank
      views { %s }
      viewsets { id name type rank views { %s } }
    }
    insights {
      id container created updated
      account { name }
      description
      snippets { id data url properties }
    }
  }
}""" % (VIEW_FIELDS, VIEW_FIELDS)


def gql(session, query, variables):
    r = session.post(session.base_url + EP.GRAPHQL,
                     json={"query": query, "variables": variables},
                     headers={"X-ExperimentalApi": "polaris-v0"})
    r.raise_for_status()
    data = r.json()
    if data.get("errors"):
        raise RuntimeError(f"GraphQL errors: {data['errors'][:3]}")
    return data["data"]


def _walk_views(viewsets, parent=""):
    """Yield (viewset_path, view) for nested viewsets (one level deep)."""
    for vs in viewsets or []:
        path = f"{parent}/{vs.get('name')}" if parent else vs.get("name")
        for v in vs.get("views") or []:
            yield path, v
        for sub, v in _walk_views(vs.get("viewsets"), parent=path):
            yield sub, v


def _issue_key_by_ari(issues):
    """Map issue ARI id suffix -> issue key (insight.container is an ARI)."""
    return {str(i.get("id")): i.get("key") for i in issues}


def export_jpd(s, out_dir, projects, issues):
    pd_projects = [p for p in projects
                   if p.get("projectTypeKey") == "product_discovery"]
    if not pd_projects:
        print("    no product_discovery projects — skipping JPD export")
        return

    cloud_id = api_get(s, EP.TENANT_INFO).get("cloudId")
    key_by_id = _issue_key_by_ari(issues)

    all_viewsets, view_rows, all_insights, insight_rows = [], [], [], []
    for p in pd_projects:
        ari = f"ari:cloud:jira:{cloud_id}:project/{p['id']}"
        try:
            data = gql(s, JPD_QUERY, {"id": ari})["polarisProject"]
        except (requests.HTTPError, RuntimeError) as e:
            print(f"    {p['key']}: JPD GraphQL failed ({e})")
            continue

        for vs in data.get("viewsets") or []:
            vs["_project_key"] = p["key"]
            all_viewsets.append(vs)
        for vs_path, v in _walk_views(data.get("viewsets")):
            view_rows.append({
                "project_key": p["key"], "viewset": vs_path,
                "view": v.get("name"), "emoji": v.get("emoji"),
                "type": v.get("visualizationType"), "jql": v.get("jql"),
                "created": v.get("createdAt"), "updated": v.get("updatedAt"),
            })
        for ins in data.get("insights") or []:
            ins["_project_key"] = p["key"]
            all_insights.append(ins)
            container_id = (ins.get("container") or "").rsplit("/", 1)[-1]
            snippets = ins.get("snippets") or []
            insight_rows.append({
                "project_key": p["key"], "insight_id": ins.get("id"),
                "idea_key": key_by_id.get(container_id),
                "author": (ins.get("account") or {}).get("name"),
                "created": ins.get("created"), "updated": ins.get("updated"),
                "text": adf_to_text(ins.get("description")).strip(),
                "urls": ", ".join(sn.get("url") or "" for sn in snippets if sn.get("url")),
            })

    write_json(out_dir, "jpd_viewsets.json", all_viewsets)
    write_csv(out_dir, "jpd_views.csv", view_rows)
    write_json(out_dir, "jpd_views.json", view_rows)
    write_json(out_dir, "jpd_insights.json", all_insights)
    write_csv(out_dir, "jpd_insights.csv", insight_rows)

    _export_idea_fields(s, out_dir, pd_projects)


def _export_idea_fields(s, out_dir, pd_projects):
    """Ideas with JPD custom field values (RICE, impact, effort, ...)."""
    jpd_fields = [f for f in api_get(s, EP.FIELDS)
                  if "polaris" in ((f.get("schema") or {}).get("custom") or "")]
    fids = [f["id"] for f in jpd_fields]
    names = {f["id"]: f["name"] for f in jpd_fields}
    keys = ", ".join(p["key"] for p in pd_projects)
    ideas = search_all_issues(s, f"project in ({keys}) order by created ASC",
                              ["summary", "status", "created"] + fids)
    rows = []
    for iss in ideas:
        f = iss.get("fields") or {}
        row = {"key": iss.get("key"), "summary": f.get("summary"),
               "status": ((f.get("status") or {}).get("name"))}
        for fid in fids:
            val = f.get(fid)
            if isinstance(val, dict):
                val = val.get("value") or val.get("name") or val
            row[names[fid]] = val
        rows.append(row)
    write_json(out_dir, "jpd_ideas.json", rows)
    write_csv(out_dir, "jpd_ideas.csv", rows)
