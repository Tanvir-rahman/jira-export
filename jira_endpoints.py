#!/usr/bin/env python3
"""
jira_endpoints.py — every API endpoint used by the exporter, in one place.

When Atlassian moves or deprecates an endpoint, fix it here — no other file
should contain a URL path. Placeholders use str.format():

    EP.ISSUE_COMMENTS.format(key="ABC-1")

Stability tiers:
  [stable]       documented Jira Cloud REST API v3 / Agile 1.0
  [internal]     greenhopper — undocumented but stable for years; same data
                 the Jira UI renders
  [experimental] polaris GraphQL — Atlassian may change without notice
"""

# --- Auth / site ----------------------------------------------------- [stable]
MYSELF = "/rest/api/3/myself"
SERVER_INFO = "/rest/api/3/serverInfo"
CONFIGURATION = "/rest/api/3/configuration"
TENANT_INFO = "/_edge/tenant_info"  # cloudId for ARIs

# --- Projects -------------------------------------------------------- [stable]
PROJECT_SEARCH = "/rest/api/3/project/search"
PROJECT_VERSIONS = "/rest/api/3/project/{key}/versions"
PROJECT_COMPONENTS = "/rest/api/3/project/{key}/components"
PROJECT_ROLES = "/rest/api/3/project/{key}/role"
PROJECT_FEATURES = "/rest/api/3/project/{key}/features"
PROJECT_CATEGORIES = "/rest/api/3/projectCategory"

# --- People ---------------------------------------------------------- [stable]
USERS_SEARCH = "/rest/api/3/users/search"
GROUP_BULK = "/rest/api/3/group/bulk"
GROUP_MEMBER = "/rest/api/3/group/member"
TEAMS = "/gateway/api/public/teams/v1/org/{org_id}/teams"
TEAM_MEMBERS = "/gateway/api/public/teams/v1/org/{org_id}/teams/{team_id}/members"

# --- Reference data -------------------------------------------------- [stable]
FIELDS = "/rest/api/3/field"
ISSUE_TYPES = "/rest/api/3/issuetype"
STATUSES = "/rest/api/3/status"
PRIORITIES = "/rest/api/3/priority"
RESOLUTIONS = "/rest/api/3/resolution"
LABELS = "/rest/api/3/label"
ISSUE_LINK_TYPES = "/rest/api/3/issueLinkType"
APPLICATION_ROLES = "/rest/api/3/applicationrole"

# --- Issues ---------------------------------------------------------- [stable]
SEARCH_JQL = "/rest/api/3/search/jql"  # POST; nextPageToken pagination
ISSUE_COMMENTS = "/rest/api/3/issue/{key}/comment"
ISSUE_WORKLOGS = "/rest/api/3/issue/{key}/worklog"
ISSUE_CHANGELOG = "/rest/api/3/issue/{key}/changelog"
ISSUE_WATCHERS = "/rest/api/3/issue/{key}/watchers"
ISSUE_VOTES = "/rest/api/3/issue/{key}/votes"
ISSUE_REMOTE_LINKS = "/rest/api/3/issue/{key}/remotelink"

# --- Agile (boards / sprints) ---------------------------------------- [stable]
BOARDS = "/rest/agile/1.0/board"
BOARD_SPRINTS = "/rest/agile/1.0/board/{board_id}/sprint"
BOARD_CONFIG = "/rest/agile/1.0/board/{board_id}/configuration"
BOARD_EPICS = "/rest/agile/1.0/board/{board_id}/epic"
BOARD_BACKLOG = "/rest/agile/1.0/board/{board_id}/backlog"

# --- Dashboards / filters -------------------------------------------- [stable]
DASHBOARDS = "/rest/api/3/dashboard"
DASHBOARD_GADGETS = "/rest/api/3/dashboard/{dashboard_id}/gadget"
FILTER_SEARCH = "/rest/api/3/filter/search"

# --- Workflows / schemes / admin ------------------------------------- [stable]
WORKFLOW_SEARCH = "/rest/api/3/workflow/search"
WORKFLOW_SCHEMES = "/rest/api/3/workflowscheme"
PERMISSION_SCHEMES = "/rest/api/3/permissionscheme"
NOTIFICATION_SCHEMES = "/rest/api/3/notificationscheme"
ISSUE_SECURITY_SCHEMES = "/rest/api/3/issuesecurityschemes"
SCREENS = "/rest/api/3/screens"
FIELD_CONFIGS = "/rest/api/3/fieldconfiguration"
ISSUE_TYPE_SCHEMES = "/rest/api/3/issuetypescheme"
PRIORITY_SCHEMES = "/rest/api/3/priorityscheme"
AUDIT_RECORDS = "/rest/api/3/auditing/record"  # offset/limit pagination

# --- Greenhopper charts (sprint report, velocity, burndown) -------- [internal]
GH_SPRINT_REPORT = "/rest/greenhopper/1.0/rapid/charts/sprintreport"
GH_VELOCITY = "/rest/greenhopper/1.0/rapid/charts/velocity"
GH_BURNDOWN = "/rest/greenhopper/1.0/rapid/charts/scopechangeburndownchart"
GH_RAPIDVIEW_CONFIG = "/rest/greenhopper/1.0/rapidviewconfig/editmodel"

# --- Jira Product Discovery (polaris) --------------------------- [experimental]
GRAPHQL = "/gateway/api/graphql"  # needs header X-ExperimentalApi: polaris-v0
