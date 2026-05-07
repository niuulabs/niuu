# Issue Trackers

**Port:** IssueTrackerProvider

| Adapter | Description |
|---------|-------------|
| `LinearIssueTrackerProvider` | Linear.app integration |
| `JiraIssueTrackerProvider` | Jira integration |

Issue trackers connect Volundr sessions to project management. Features:

- Search issues by query or project
- Get recent issues
- Update issue status
- Map repos to tracker projects

## Linear

```yaml
linear:
  enabled: true
  api_key: "lin_api_xxx"  # or LINEAR_API_KEY env var
```

## Jira

Configure via the integration catalog:

```yaml
integrations:
  definitions:
    - slug: jira
      name: Jira
      integration_type: issue_tracker
      adapter: "volundr.adapters.outbound.integrations.jira.JiraAdapter"
      credential_schema:
        url:
          type: string
          required: true
        email:
          type: string
          required: true
        api_token:
          type: string
          required: true
```

Users provide their Jira credentials through the Volundr UI. The credentials are stored in the configured credential store.

## Project Mappings

Map git repositories to tracker projects so Volundr knows which project to associate with a session:

```
POST /api/v1/tracker/repo-mappings
{
  "repo_url": "https://github.com/org/repo",
  "project_id": "PROJ-123"
}
```

Once mapped, sessions created from that repository automatically link to the tracker project. Issue search is scoped to the mapped project by default.
