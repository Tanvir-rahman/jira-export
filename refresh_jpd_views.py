"""One-off refresh: re-export ONLY JPD viewsets + views (not insights/ideas).

Rewrites jpd_viewsets.json, jpd_views.json, jpd_views.csv in place, reusing
projects.json from a previous full export. Read-only against Jira.

Run:  python refresh_jpd_views.py
"""
import json
import os

from dotenv import load_dotenv

load_dotenv()

from jira_export import build_session
from jira_export_jpd import export_jpd_views

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
    export_jpd_views(s, OUT, pd_projects)
    print("Done — JPD views refreshed.")


if __name__ == "__main__":
    main()
