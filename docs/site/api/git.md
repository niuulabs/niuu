# Git Workflows API

Git workflow endpoints manage branches, pull requests, CI status, and merge confidence across GitHub and GitLab.

All endpoints are prefixed with `/api/v1/forge/repos`.

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/prs` | Create a pull request from a session |
| `GET` | `/prs` | List pull requests for a repo |
| `GET` | `/prs/{number}` | Get a pull request |
| `POST` | `/prs/{number}/merge` | Merge a pull request |
| `GET` | `/prs/{number}/ci` | Get CI status for a branch |
| `POST` | `/confidence` | Calculate merge confidence |

## Repository endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/repos/providers` | List configured git providers |
| `GET` | `/repos/{provider}/orgs/{org}` | List repos in an org |
| `GET` | `/repos/validate` | Validate a repo URL |
| `GET` | `/repos/branches` | List branches for a repo |

## Create a pull request

```
POST /repos/prs
```

```json
{
  "session_id": "uuid",
  "title": "Fix auth flow",
  "description": "Updated token refresh logic",
  "target_branch": "main",
  "labels": ["bug-fix"]
}
```

Uses the session's repo and branch as the source. The PR is created on the git provider (GitHub or GitLab).

## Merge confidence

```
POST /repos/confidence
```

Calculates a confidence score (0.0 - 1.0) for merging based on CI status, review status, and configured thresholds:

- Score >= `auto_merge_threshold` (default 0.9): auto-merge recommended
- Score >= `notify_merge_threshold` (default 0.6): notify then merge
- Score < 0.6: require manual approval
