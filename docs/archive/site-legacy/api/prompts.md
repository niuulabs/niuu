# Prompts API

Saved prompts are reusable prompt templates scoped globally or to a specific project.

All endpoints are prefixed with `/api/v1/forge`.

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/prompts` | List prompts (optional scope, repo filter) |
| `POST` | `/prompts` | Create a prompt |
| `PUT` | `/prompts/{id}` | Update a prompt |
| `DELETE` | `/prompts/{id}` | Delete a prompt |
| `GET` | `/prompts/search` | Search by name and content |

## Model

```json
{
  "id": "uuid",
  "name": "review-pr",
  "content": "Review this pull request for...",
  "scope": "global",
  "project_repo": null,
  "tags": ["review", "quality"]
}
```

Scope is either `global` (available everywhere) or `project` (tied to a specific `project_repo`).
