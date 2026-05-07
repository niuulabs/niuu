# Issue Tracker API

Connects sessions to external issue trackers (Linear, Jira). Sessions can be linked to issues, and issue status can be updated from Volundr.

All endpoints are prefixed with `/api/v1/tracker`.

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/status` | Check tracker connection |
| `GET` | `/issues` | Search issues by query |
| `GET` | `/issues/recent` | Recent issues for a project |
| `PATCH` | `/issues/{id}` | Update issue status |
| `GET` | `/mappings` | List project mappings |
| `POST` | `/mappings` | Create mapping (repo URL → tracker project) |
| `DELETE` | `/mappings/{id}` | Delete mapping |

## Project mappings

Mappings link git repository URLs to issue tracker projects. When a session is created with a repo, the mapping tells Volundr which tracker project to query for related issues.

```json
{
  "repo_url": "github.com/org/repo",
  "project_id": "PRJ-123",
  "project_name": "My Project"
}
```

## Configuration

Enable via config:

```yaml
linear:
  enabled: true
  api_key: "lin_api_xxxx"
```

Or use the integrations system for dynamic adapter loading:

```yaml
integrations:
  definitions:
    - slug: "jira"
      name: "Jira"
      integration_type: "issue_tracker"
      adapter: "volundr.adapters.outbound.jira.JiraAdapter"
```
