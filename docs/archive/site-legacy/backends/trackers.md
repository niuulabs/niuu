# Issue Trackers

Volundr integrates with external issue trackers to link sessions to issues and update their status.

## Adapters

| Adapter | Provider | Description |
|---------|----------|-------------|
| `LinearAdapter` | Linear | Linear API integration |
| `JiraAdapter` | Jira | Jira REST API integration |

Both implement the `IssueTrackerProvider` port.

## Configuration

### Linear (direct config)

```yaml
linear:
  enabled: true
  api_key: "lin_api_xxxx"
```

### Dynamic adapter loading

For more flexibility, use the integration system:

```yaml
integrations:
  definitions:
    - slug: "jira"
      name: "Jira"
      integration_type: "issue_tracker"
      adapter: "volundr.adapters.outbound.jira.JiraAdapter"
      credential_schema:
        url: { type: "string", required: true }
        email: { type: "string", required: true }
        api_token: { type: "string", required: true }
```

Users then create connections via the [Integrations API](../api/integrations.md), providing their credentials.

## Adding a new tracker

1. Create an adapter class implementing `IssueTrackerProvider`
2. Add it to the integration catalog in config
3. No code changes needed in the container

The adapter must implement: `check_connection`, `search_issues`, `get_recent_issues`, `get_issue`, `update_issue_status`.
