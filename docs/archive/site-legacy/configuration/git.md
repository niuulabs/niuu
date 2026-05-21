# Git Providers

**Port:** GitProvider / GitWorkflowProvider

## Adapters

| Adapter | Description |
|---------|-------------|
| `GitHubProvider` | github.com and GitHub Enterprise |
| `GitLabProvider` | gitlab.com and self-hosted GitLab |

Both adapters support multiple instances. You can connect to several GitHub or GitLab servers simultaneously.

## Configuration (config.yaml)

```yaml
git:
  validate_on_create: true
  github:
    instances:
      - name: GitHub
        base_url: https://api.github.com
        token: ghp_xxx          # or use token_env
        token_env: GITHUB_TOKEN # env var name
        orgs: [my-org]
      - name: GitHub Enterprise
        base_url: https://github.company.com/api/v3
        token: ghp_enterprise
        orgs: [engineering, platform]
  gitlab:
    instances:
      - name: GitLab.com
        base_url: https://gitlab.com
        token: glpat_xxx
        orgs: [my-group]
```

## Configuration (Helm)

```yaml
git:
  github:
    enabled: true
    existingSecret: github-token
    instances:
      - name: GitHub Enterprise
        baseUrl: https://github.company.com/api/v3
        existingSecret: github-enterprise-secret
        orgs: [engineering]
  gitlab:
    enabled: false
```

## Token Resolution

Each instance resolves its token in this order:

1. Explicit `token` field on the instance
2. Environment variable named in `token_env`
3. Top-level token for that provider

## Workflow Settings

Available in both config.yaml (snake_case) and Helm (camelCase):

| config.yaml | Helm | Description |
|-------------|------|-------------|
| `auto_branch` | `autoBranch` | Auto-create branches for sessions |
| `branch_prefix` | `branchPrefix` | Branch name prefix |
| `protect_main` | `protectMain` | Prevent direct pushes to main |
| `default_merge_method` | `defaultMergeMethod` | squash, merge, or rebase |
| `auto_merge_threshold` | `autoMergeThreshold` | Confidence score for auto-merge |
| `notify_merge_threshold` | `notifyMergeThreshold` | Confidence score for notify-then-merge |
