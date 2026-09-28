"""One-off refresh: re-export ONLY JPD ideas + field options (not views).

Rewrites jpd_ideas.json, jpd_ideas.csv and jpd_field_options.json in place,
reusing projects.json from a previous full export. Read-only against Jira.

Run:  python refresh_jpd_ideas.py
"""
import json
import os

from dotenv import load_dotenv

load_dotenv()

from jira_export import build_session
from jira_export_jpd import _export_idea_fields

OUT = "./jira_export"


def main():
    s = build_session(os.environ["JIRA_URL"], os.environ["JIRA_EMAIL"],
                      os.environ["JIRA_API_TOKEN"])
    with open(os.path.join(OUT, "projects.json")) as f:
        projects = json.load(f)
    pd_projects = [p for p in projects
                   if p.get("projectTypeKey") == "product_discovery"]
    print(f"loaded {len(projects)} projects "
          f"({len(pd_projects)} product_discovery) from {OUT}")
    _export_idea_fields(s, OUT, pd_projects)
    print("Done — JPD ideas + field options refreshed.")


if __name__ == "__main__":
    main()
