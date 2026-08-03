"""One-off resume: finish an interrupted export from step [20] onward.

Reuses the already-written files in ./jira_export (projects/boards/issues) and
only runs the remaining API work:
  [20b] board epics + backlog order   (this is where the run hung)
  [21]  project features + voters

Run:  python resume_export.py
"""
import json
import os

from dotenv import load_dotenv

load_dotenv()

from jira_export import build_session  # noqa: E402
import jira_export_admin as admin  # noqa: E402

OUT = "./jira_export"


def load(name):
    with open(os.path.join(OUT, name)) as f:
        return json.load(f)


def main():
    s = build_session(os.environ["JIRA_URL"], os.environ["JIRA_EMAIL"],
                      os.environ["JIRA_API_TOKEN"])
    projects = load("projects.json")
    boards = load("boards.json")
    issues = load("issues.json")
    print(f"loaded {len(projects)} projects, {len(boards)} boards, "
          f"{len(issues)} issues from {OUT}")

    # board_settings.json + swimlanes.csv were already written before the hang —
    # skipping straight to the part that didn't finish.
    print("[20b] Board epics + backlog order...")
    admin.export_board_epics_and_backlog(s, OUT, boards)
    print("[21] Project features + voters...")
    admin.export_project_features(s, OUT, projects)
    admin.export_voters(s, OUT, issues)
    print("Done — export complete.")


if __name__ == "__main__":
    main()
