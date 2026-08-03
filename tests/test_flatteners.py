"""Offline unit tests — pure functions only, no network, no credentials.

Run:  pytest
"""

import jira_export as je
from jira_export_extras import _safe_filename
from jira_export_jpd import _issue_key_by_ari, _walk_views


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
