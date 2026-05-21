# Git Providers

Volundr supports multiple GitHub and GitLab instances simultaneously.

## Configuration

```yaml
git:
  validate_on_create: true  # Validate repo exists when creating sessions

  github:
    enabled: true
    instances:
      - name: "GitHub"
        base_url: "https://api.github.com"
        token: "ghp_xxxx"
        orgs: ["my-org"]
      - name: "GitHub Enterprise"
        base_url: "https://github.corp.com/api/v3"
        token_env: "GHE_TOKEN"  # Read token from env var
        orgs: ["internal"]

  gitlab:
    enabled: true
    instances:
      - name: "GitLab"
        base_url: "https://gitlab.com"
        token: "glpat_xxxx"
        orgs: ["my-group"]
```

### Token resolution

Per instance, tokens are resolved in order:

1. Explicit `token` field in the instance
2. Environment variable named by `token_env`
3. Top-level provider token (`git.github.token`)

## How providers work

The `GitProvider` port defines operations: `validate_repo`, `parse_repo`, `get_clone_url`, `list_repos`, `list_branches`.

The `GitWorkflowProvider` port extends this with write operations: `create_branch`, `create_pull_request`, `merge_pull_request`, `get_ci_status`.

Both GitHub and GitLab implement both ports. A `GitRegistry` aggregates all configured provider instances and routes requests to the right one based on the repo URL.

## Adapters

| Adapter | Port | Description |
|---------|------|-------------|
| `GitHubProvider` | `GitProvider` + `GitWorkflowProvider` | GitHub.com and GitHub Enterprise |
| `GitLabProvider` | `GitProvider` + `GitWorkflowProvider` | GitLab.com and self-hosted |

## Disabling

Set `enabled: false` or omit the provider config entirely. If no instances are configured and no token is set, the provider is not registered.
