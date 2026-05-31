# Git Workflows

Volundr integrates with GitHub and GitLab for branch management and pull requests.

## Branch creation

Sessions auto-create branches by default. Branch naming follows the `volundr/session/<name>` convention. You can disable this or change the prefix in config.

## Pull requests

Create PRs from sessions via the API or the web UI. The PR description is populated from the chronicle — a summary of what happened plus key changes.

## Merge confidence scoring

Volundr calculates a confidence score for each PR based on several factors:

| Factor | Weight |
|--------|--------|
| Test results | 30% |
| Change size | 20% |
| Change category (safe vs risky) | 15% |
| Dependency changes | 15% |
| Coverage delta | 10% |
| Files changed count | 10% |

What happens next depends on the score:

- **>= 0.9** — Auto-merge. No human needed.
- **0.6 – 0.9** — Notify reviewers, then merge after approval window.
- **< 0.6** — Require manual approval before merging.

All thresholds are configurable.

## Configuration

```yaml
git:
  workflow:
    auto_branch: true
    branch_prefix: "volundr/session"
    protect_main: true
    default_merge_method: squash
    auto_merge_threshold: 0.9
    notify_merge_threshold: 0.6
```

Set `auto_branch: false` to skip automatic branch creation. Change `default_merge_method` to `merge` or `rebase` if you prefer those strategies.

## Multiple git instances

You can configure multiple GitHub and GitLab instances side by side. For example: github.com alongside a GitHub Enterprise instance, or gitlab.com alongside a self-hosted GitLab. Each instance gets its own token and org list.

This uses the dynamic adapter pattern — each provider is a separate entry in config with its own credentials and base URL.

## CI status

Volundr monitors CI status on branches and factors it into the merge confidence calculation. If CI is failing, the confidence score drops accordingly.
