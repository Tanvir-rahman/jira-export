"""Offline unit tests — pure functions only, no network, no credentials.

Run:  pytest
"""

import jira_export as je
from jira_export_extras import _safe_filename
from jira_export_jpd import _cell, _issue_key_by_ari, _view_row, _walk_views


# --------------------------------------------------------------------------- #
# adf_to_text
# --------------------------------------------------------------------------- #
def test_adf_to_text_extracts_nested_paragraphs_and_marks():
    # Arrange
    adf = {"type": "doc", "content": [
        {"type": "paragraph", "content": [
            {"type": "text", "text": "Hello ", "marks": [{"type": "em"}]},
            {"type": "text", "text": "world"},
        ]},
        {"type": "paragraph", "content": [{"type": "text", "text": "second"}]},
    ]}

    # Act
    text = je.adf_to_text(adf)

    # Assert
    assert text == "Hello world\nsecond\n"


def test_adf_to_text_handles_hardbreak_none_and_plain_string():
    assert je.adf_to_text(None) == ""
    assert je.adf_to_text("already plain") == "already plain"
    adf = {"type": "paragraph", "content": [
        {"type": "text", "text": "a"}, {"type": "hardBreak"},
        {"type": "text", "text": "b"},
    ]}
    assert je.adf_to_text(adf) == "a\nb\n"


# --------------------------------------------------------------------------- #
# flatten_issue
# --------------------------------------------------------------------------- #
def _issue_fixture():
    return {
        "key": "PHX-1", "id": "10001",
        "fields": {
            "summary": "Fix login",
            "description": {"type": "doc", "content": [
                {"type": "paragraph",
                 "content": [{"type": "text", "text": "desc"}]}]},
            "issuetype": {"name": "Bug", "subtask": False},
            "status": {"name": "Done", "statusCategory": {"name": "Done"}},
            "project": {"key": "PHX", "name": "Phoenix"},
            "priority": {"name": "High"},
            "assignee": {"displayName": "Alice", "accountId": "acc-1"},
            "labels": ["backend", "auth"],
            "comment": {"total": 3, "comments": []},
            "attachment": [{"id": "1"}, {"id": "2"}],
            "customfield_10001": 5.0,
            "customfield_10002": [{"name": "Sprint 1"}, {"name": "Sprint 2"}],
            "votes": {"votes": 2},
            "watches": {"watchCount": 4},
        },
    }


def test_flatten_issue_maps_core_fields_and_detects_story_points():
    row = je.flatten_issue(_issue_fixture(), ["customfield_10001"],
                           "customfield_10002", None)
    assert row["key"] == "PHX-1"
    assert row["issuetype"] == "Bug"
    assert row["status_category"] == "Done"
    assert row["story_points"] == 5.0
    assert row["sprints"] == "Sprint 1, Sprint 2"
    assert row["labels"] == "backend, auth"
    assert row["num_comments"] == 3
    assert row["num_attachments"] == 2
    assert row["votes"] == 2
    assert row["watchers"] == 4
    assert row["description"] == "desc"


def test_flatten_issue_survives_empty_fields():
    row = je.flatten_issue({"key": "X-1", "id": "1", "fields": {}}, [], None, None)
    assert row["key"] == "X-1"
    assert row["story_points"] is None
    assert row["sprints"] == ""


# --------------------------------------------------------------------------- #
# pagination (fake session, no network)
# --------------------------------------------------------------------------- #
class _FakeResp:
    def __init__(self, payload):
        import json as _json
        self._payload = payload
        self.status_code = 200
        self.text = _json.dumps(payload)

    def json(self):
        return self._payload

    def raise_for_status(self):
        pass


class _FakeSession:
    """Serves startAt-paginated pages of 2 items from a 3-item dataset."""
    base_url = "https://fake"

    def get(self, url, params=None, **kwargs):
        start = params["startAt"]
        items = [{"id": i} for i in range(3)][start:start + 2]
        return _FakeResp({"startAt": start, "total": 3, "values": items})


def test_paginate_startat_walks_all_pages_until_total():
    out = je.paginate_startat(_FakeSession(), "/fake", "values", page_size=2)
    assert [i["id"] for i in out] == [0, 1, 2]


# --------------------------------------------------------------------------- #
# JPD helpers
# --------------------------------------------------------------------------- #
def test_walk_views_flattens_nested_viewsets_with_path():
    viewsets = [{"name": "Prioritize",
                 "views": [{"name": "All ideas"}],
                 "viewsets": [{"name": "More",
                               "views": [{"name": "Custom"}]}]}]
    got = [(path, v["name"]) for path, v in _walk_views(viewsets)]
    assert got == [("Prioritize", "All ideas"), ("Prioritize/More", "Custom")]


def test_view_row_flattens_field_refs_and_handles_single_or_list():
    v = {"name": "Board", "visualizationType": "BOARD",
         "containsArchived": False, "sortMode": "PROJECT_RANK",
         "verticalGroupBy": {"id": "x", "jiraFieldKey": "customfield_10761"},
         # experimental schema may hand back a single object instead of a list
         "filter": {"kind": "FIELD_NUMERIC",
                    "field": {"jiraFieldKey": "customfield_13165"},
                    "values": [{"numericValue": 1, "operator": "EQ"}]},
         "sort": [{"field": {"jiraFieldKey": "created"}, "order": "DESC"}],
         "fields": [{"jiraFieldKey": "summary"},
                    {"jiraFieldKey": "customfield_13165"}]}
    row = _view_row("PCP", "Prioritize", v)
    assert row["project_key"] == "PCP"
    assert row["contains_archived"] is False
    assert row["group_by"] is None
    assert row["vertical_group_by"] == "customfield_10761"
    assert row["filter"] == [{"kind": "FIELD_NUMERIC",
                              "field": "customfield_13165",
                              "values": [{"numericValue": 1, "operator": "EQ"}]}]
    assert row["sort"] == [{"field": "created", "order": "DESC"}]
    assert row["sort_mode"] == "PROJECT_RANK"
    assert row["columns"] == ["summary", "customfield_13165"]
    assert row["hidden"] == []


def test_issue_key_by_ari_maps_numeric_id_to_key():
    issues = [{"id": "10005", "key": "PD-1"}, {"id": 10006, "key": "PD-2"}]
    mapping = _issue_key_by_ari(issues)
    assert mapping["10005"] == "PD-1"
    assert mapping["10006"] == "PD-2"


# --------------------------------------------------------------------------- #
# misc
# --------------------------------------------------------------------------- #
def test_safe_filename_strips_path_separators_and_odd_chars():
    assert _safe_filename("../../etc/passwd") == ".._.._etc_passwd"
    assert _safe_filename("report Q1 2026.pdf") == "report_Q1_2026.pdf"
    assert _safe_filename(None) == "unnamed"


def test_flatten_changelog_one_row_per_changed_field():
    histories = [{"created": "2026-01-01", "author": {"displayName": "A"},
                  "items": [{"field": "status", "fromString": "To Do",
                             "toString": "Done"},
                            {"field": "assignee", "fromString": None,
                             "toString": "Alice"}]}]
    rows = je.flatten_changelog("PHX-1", histories)
    assert len(rows) == 2
    assert rows[0] == {"issue_key": "PHX-1", "changed_at": "2026-01-01",
                       "author": "A", "field": "status", "from": "To Do",
                       "to": "Done"}


# --------------------------------------------------------------------------- #
# JPD idea field cells (_cell)
# --------------------------------------------------------------------------- #
def test_cell_flattens_option_user_and_doc_values():
    assert _cell({"value": "P0"}) == "P0"
    assert _cell({"name": "Ready to Go-live"}) == "Ready to Go-live"
    assert _cell({"displayName": "Md. Rafiul Islam"}) == "Md. Rafiul Islam"
    doc = {"type": "doc", "content": [{"type": "paragraph", "content": [
        {"type": "text", "text": "short description"}]}]}
    assert _cell(doc) == "short description"
    assert _cell("2026-07-15") == "2026-07-15"
    assert _cell(3.5) == 3.5
    assert _cell(None) is None


def test_cell_flattens_multiselect_lists_and_drops_empties():
    val = [{"value": "Integrations"}, {"value": "Platform focused"}]
    assert _cell(val) == ["Integrations", "Platform focused"]
    assert _cell([]) is None
    assert _cell({"unknown": "shape"}) is None


def test_cell_unwraps_cascading_select_parent_value():
    assert _cell({"value": "Payments", "child": {"value": "Wallet"}}) == "Payments"


def test_idea_row_first_nonnull_wins_for_duplicate_field_names():
    # Six site fields share the name "Product Area"; a null from the wrong
    # project's id must never clobber the real value, in either order.
    names = {"customfield_1": "Product Area", "customfield_2": "Product Area"}
    for order in (["customfield_1", "customfield_2"],
                  ["customfield_2", "customfield_1"]):
        row = {}
        fields = {"customfield_1": None, "customfield_2": {"value": "Integrations"}}
        for fid in order:
            if row.get(names[fid]) is None:
                row[names[fid]] = _cell(fields[fid])
        assert row["Product Area"] == "Integrations"
