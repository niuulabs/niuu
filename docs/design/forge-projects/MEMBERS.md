# Project members — people on a project, like agents

**10 November 2026 · branch `feat/project-members`**

A project is no longer one person's. Members of the same org join it with a role, chat in its
sessions, answer the agent's questions, steer it, and decide its gates. The agent can reach out to
the member who knows most about the question, the way it reaches another agent.

## Decisions

- **Every voice weighs the same.** The agent sees who said what and weighs content, never rank.
  There is no steering tier: anyone who can speak in a session can steer it.
- **Roles gate authority, not voice.** A role decides who may resolve a gate or permission prompt,
  edit workflows and tools, and manage members. A viewer writing "approved" in chat is not an
  approval.
- **Stay within the org.** Only members of the project's tenant can be added. No external invites.
- **A project owner grants any project role to any org member**, including `owner`. Platform roles
  (`volundr:admin`, `volundr:developer`, `volundr:viewer`) stay with the identity provider and are
  never granted by a project.
- **The provider owns who is in the org; Niuu owns sharing** (as in
  [the organizations design](../organizations/README.md)). Project roles are Niuu's.
- **One role vocabulary.** Rooms already enforce `viewer`, `approver` and `owner`
  (`niuu.room_access`). Session invites use the same words since migration 000118; the
  collaboration room's own unenforced roles and the `teacher`/`debugger` roles are gone.
- **Routing by expertise is the agent's judgment** and advisory: a member the agent picks is asked,
  but any member with the role can still answer or decide.

## What exists (audited on dev, 2026-11-10)

| Area | Today |
|---|---|
| Per-session invites | `session_participants` (000080): owner invites a same-tenant user as `viewer` or `approver`; Cedar `room_viewers` / `room_approvers` |
| Room roles | `viewer` chats and steers, `approver` also answers gates and permission prompts, `owner` everything (`niuu.room_access`) |
| Projects | `ForgeProject.owner_id` only; `ProjectService.get` refuses anyone but the owner, admins included |
| Message author | Not recorded. A user turn, its event-log frame and its trace span carry no user; the chat shows no name or avatar; the model sees bare text |
| Notifications | Gates and questions notify `session.owner_id` only |
| Decider | Permission and question answers record no user (Ting gates do: `decided_by`) |
| Credentials | A session runs on its owner's credentials; members act through them |
| Two people at once | Messages queue or steer per transport; merged steers read as "the user sent…" |
| Presence | Only agents show in the room roster; no human presence in the UI |

## Design

### 1. Project members and roles

`project_members(project_id, user_id, tenant_id, role, expertise, skills, added_by, created_at,
updated_at)`, unique on `(project_id, user_id)`. The project's `owner_id` is its creator and is
always an `owner` member.

| Project role | Can |
|---|---|
| `viewer` | See the project and its sessions, chat and steer in them |
| `contributor` | Also start sessions in the project, edit its workflows, tools and brief |
| `approver` | Also resolve gates and permission prompts, answer the agent's questions on the project's behalf |
| `owner` | Also manage members and the project's settings, archive it |

Roles are a ladder. Adding a member checks that the user is an active member of the project's
tenant (`tenant_memberships`); anyone else is refused. Only an `owner` (or a tenant admin) manages
members, and the last `owner` cannot be removed or demoted.

`ProjectService.get` and `list` admit members; `update` needs `contributor`; member management
needs `owner`.

### 2. Project members get room roles on the project's sessions

A session attached to a project (`SessionCoordination.project_id`) gives every project member a
room role on it, computed where room roles are computed today
(`SessionParticipantService.effective_room_role`, `compute_room_grants`):

| Project role | Room role on its sessions |
|---|---|
| `viewer`, `contributor` | `viewer` |
| `approver`, `owner` | `approver` |

The session's own owner stays its room `owner`. Per-session invites keep working for one-off
sharing; the more senior of the two grants applies. Members find the project's sessions on the
project page and in their session list.

### 3. Every message carries its author

The session proxy already passes the verified user to the broker on the WebSocket
(`x-auth-user-id`, email). The broker keeps it per connection and stamps every user turn with
`author: {user_id, display_name}`:

- on the conversation turn, the event-log frame (so replay keeps it), the `user_confirmed`
  broadcast and the trace span (`actor_id` = user id);
- the chat shows the author's avatar and name on user messages;
- the model sees the author: once a session has more than one person, each human message opens
  with `[<display name>]`, and merged steers list each author instead of "the user".

### 4. Members are told, and decisions are signed

- Gate and question notifications go to every member who may act on them (`approver` and up),
  not only the session owner. A question the agent addressed to one person goes to that person.
- Permission answers, question answers and gate decisions record the deciding user.

### 5. A2H: people in the directory, and a tool to reach them

A member's profile is their expertise (free text) and skills, in the shape of an A2A
`AgentSkill` (`niuu.domain.agent_directory`), so people and agents can be listed and ranked the
same way. A person is a directory entry of kind `member`; they are reached through the session's
room and notifications, never as an A2A task.

The Forge MCP gains two tools for a session's agent:

- `find_people(topic)` — the project's members with their role, skills and expertise, so the
  model can judge who knows most. Niuu does not rank them.
- `ask_person(user_id, question)` — puts the question in the room addressed to that member and
  notifies them. The answer comes back to the exact question that asked it. A person who is not a
  member of the session's project is refused.

### 6. Credentials stay the owner's

A member's message makes the agent act on the session owner's credentials. This is stated in the
invite and Members UI. A session a contributor starts in the project is theirs and runs on their
own credentials. Per-member credentials inside one session are out of scope.

### 7. Team integrations

A project owner connects integrations for the whole team, the same way they manage members.
A team connection is owned by the project, not a person: its `owner_id` is `project:<id>`, its
`project_id` is set, and its credential is stored under the `project` credential owner
(`credential_owner()` in `niuu.domain.models`). No per-person lookup finds it, and a missing
team connection is an error, never a switch to someone's personal one.

- **It acts as the team's bot or service account.** The catalog's `team_install` says how:
  `oauth` is the provider's app install (a Slack bot; Linear with `actor=app`),
  `client_credentials` is a service account minted into short-lived tokens and renewed by the
  token refresher (an Atlassian service account for Jira). `team_only` integrations (Slack)
  cannot be personal connections.
- **Owners manage, members read** (`/api/v1/forge/projects/{id}/integrations`). An app install
  starts at `POST /api/v1/integrations/oauth/{slug}/authorize-team`, and the callback checks
  again that the installer still owns the project.
- **Team routes** forward a project's notifications through its messaging connection: one
  notification rule per event, owned by the project (`needs_decision`, `person_question`,
  `progress`, `errors`), each to a channel (one post per item) or to each person it is for (a
  direct message to every recipient's copy, found by their identity-provider email).
- **Ting** uses a project's team trackers for every member of it (the projects a member is on
  come from Forge) and wraps each in a recorder.
- **Every use is recorded** in `integration_usage`: connect, reconnect, disconnect, each team
  post, and each tracker write Ting makes, with who it acted for.

Not yet: team connections are not mounted into session pods (agents reach team trackers through
Ting, not an MCP token in their pod), Forge's own tracker routes still list personal trackers
only, and GitHub Issues has no team tracker because Ting has no GitHub Issues adapter.

## Phases

1. **Members and access:** table, roles, REST, project and session access for members.
2. **Authorship:** author on turns, frames, replay, UI and model input.
3. **Reach:** notifications to members, decider recorded, member skills, `find_people` /
   `ask_person`.
4. **Web:** Members tab on the project page (after the mockup review), author on messages,
   shared sessions in the list.

## Not in scope

- People outside the org.
- Per-member credentials.
- Members on another Forge host's replica of a project: membership lives with the project's
  home copy.

## Open questions

- Human presence ("who's here") in the session header — worth adding with phase 4?
- Should members be able to edit their own expertise, or only owners?
