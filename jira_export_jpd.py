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

VIEW_FIELDS = (
    "id uuid name emoji visualizationType containsArchived jql "
    "rank createdAt updatedAt viewSetId groupOrder sortMode "
    "groupBy { id jiraFieldKey } verticalGroupBy { id jiraFieldKey } "
    "sort { field { id jiraFieldKey } order } "
    "filter { kind field { id jiraFieldKey } "
    "values { stringValue numericValue operator enumValue } } "
    "fields { id jiraFieldKey } hidden { id jiraFieldKey }"
)

# Nested lists/dicts don't belong in CSV cells — the CSV keeps the flat
# subset; jpd_views.json carries the full rows (the Tori importer reads JSON).
CSV_VIEW_KEYS = ("project_key", "viewset", "view", "emoji", "type", "jql",
                 "created", "updated")

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
}""" % (VIEW_FIELDS, VIEW_FIELDS)  # noqa: UP031 — GraphQL braces clash with str.format


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


def _fkey(field):
    """PolarisIdeaField ref -> its jiraFieldKey string (None-safe)."""
    return (field or {}).get("jiraFieldKey")


def _aslist(v):
    """Experimental schema: sort/filter may come back single or list."""
    return v if isinstance(v, list) else ([] if v is None else [v])


def _view_row(project_key, vs_path, v):
    return {
        "project_key": project_key, "viewset": vs_path,
        "view": v.get("name"), "emoji": v.get("emoji"),
        "type": v.get("visualizationType"), "jql": v.get("jql"),
        "created": v.get("createdAt"), "updated": v.get("updatedAt"),
        "contains_archived": v.get("containsArchived"),
        "filter": [{"kind": f.get("kind"), "field": _fkey(f.get("field")),
                    "values": f.get("values") or []}
                   for f in _aslist(v.get("filter"))],
        "group_by": _fkey(v.get("groupBy")),
        "vertical_group_by": _fkey(v.get("verticalGroupBy")),
        "group_order": v.get("groupOrder"),
        "sort": [{"field": _fkey(sf.get("field")), "order": sf.get("order")}
                 for sf in _aslist(v.get("sort"))],
        "sort_mode": v.get("sortMode"),
        "columns": [_fkey(f) for f in _aslist(v.get("fields"))],
        "hidden": [_fkey(f) for f in _aslist(v.get("hidden"))],
    }


def export_jpd_views(s, out_dir, pd_projects):
    """Viewsets + views only. Returns [(project, polarisProject data)] so
    export_jpd can reuse the same GraphQL responses for insights."""
    cloud_id = api_get(s, EP.TENANT_INFO).get("cloudId")
    all_viewsets, view_rows, project_data = [], [], []
    for p in pd_projects:
        ari = f"ari:cloud:jira:{cloud_id}:project/{p['id']}"
        try:
            data = gql(s, JPD_QUERY, {"id": ari})["polarisProject"]
        except (requests.HTTPError, RuntimeError) as e:
            print(f"    {p['key']}: JPD GraphQL failed ({e})")
            continue
        project_data.append((p, data))
        for vs in data.get("viewsets") or []:
            vs["_project_key"] = p["key"]
            all_viewsets.append(vs)
        for vs_path, v in _walk_views(data.get("viewsets")):
            view_rows.append(_view_row(p["key"], vs_path, v))
    write_json(out_dir, "jpd_viewsets.json", all_viewsets)
    write_csv(out_dir, "jpd_views.csv",
              [{k: r[k] for k in CSV_VIEW_KEYS} for r in view_rows])
    write_json(out_dir, "jpd_views.json", view_rows)
    return project_data


def export_jpd(s, out_dir, projects, issues):
    pd_projects = [p for p in projects
                   if p.get("projectTypeKey") == "product_discovery"]
    if not pd_projects:
        print("    no product_discovery projects — skipping JPD export")
        return

    key_by_id = _issue_key_by_ari(issues)

    all_insights, insight_rows = [], []
    for p, data in export_jpd_views(s, out_dir, pd_projects):
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

    write_json(out_dir, "jpd_insights.json", all_insights)
    write_csv(out_dir, "jpd_insights.csv", insight_rows)

    _export_idea_fields(s, out_dir, pd_projects)


def _cell(val):
    """Flatten a REST field value to a JSON-friendly cell.

    Option dict -> its label, user dict -> display name, ADF doc -> plain
    text, list -> list of flattened members (empty -> None), None -> None.
    Dicts with none of the known label keys flatten to None rather than
    leaking raw API objects into the export.
    """
    if isinstance(val, list):
        flat = [c for c in (_cell(v) for v in val) if c is not None]
        return flat or None
    if isinstance(val, dict):
        if val.get("type") == "doc":
            return adf_to_text(val).strip() or None
        out = val.get("value") or val.get("name") or val.get("displayName")
        return _cell(out) if isinstance(out, (dict, list)) else out
    return val


def _harvest_options(options, fid, val):
    """Record select-option {id, value} pairs seen in a raw field value.

    View filters (jpd_views.json) reference options by numeric id; nothing
    else in the export maps those ids to labels, so they are collected here
    while the raw values are still unflattened.
    """
    if isinstance(val, list):
        for v in val:
            _harvest_options(options, fid, v)
    elif isinstance(val, dict) and "value" in val and val.get("id") is not None:
        options.setdefault(fid, {})[str(val["id"])] = val["value"]


def _export_idea_fields(s, out_dir, pd_projects):
    """Ideas with every custom field value, JPD-native and standard alike.

    JPD boards mix polaris fields with plain Jira custom fields placed on
    the idea screens (selects, multicheckboxes, text...), so ALL custom
    fields are requested — filtering on "polaris" silently dropped the
    standard ones. Several site fields can share one display name (this
    site has six "Product Area"s); first non-null value wins so a null
    from an unrelated same-named field never clobbers a real one.

    Also writes jpd_field_options.json ({field id: {option id: label}}) so
    the importer can resolve view filters that reference options by id.
    """
    custom = [f for f in api_get(s, EP.FIELDS)
              if f.get("id", "").startswith("customfield_")]
    fids = [f["id"] for f in custom]
    names = {f["id"]: f["name"] for f in custom}
    keys = ", ".join(p["key"] for p in pd_projects)
    ideas = search_all_issues(s, f"project in ({keys}) order by created ASC",
                              ["summary", "status", "created"] + fids)
    rows, options = [], {}
    for iss in ideas:
        f = iss.get("fields") or {}
        row = {"key": iss.get("key"), "summary": f.get("summary"),
               "status": ((f.get("status") or {}).get("name"))}
        for fid in fids:
            _harvest_options(options, fid, f.get(fid))
            name = names[fid]
            if row.get(name) is None:
                row[name] = _cell(f.get(fid))
        rows.append(row)
    write_json(out_dir, "jpd_ideas.json", rows)
    write_csv(out_dir, "jpd_ideas.csv", rows)
    write_json(out_dir, "jpd_field_options.json", options)
