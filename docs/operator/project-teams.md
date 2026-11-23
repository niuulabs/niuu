# Project teams across clusters

A project's team is its members, its team integrations (Slack, Linear, Jira, …) and where
each kind of notification goes. A project has the same team on every cluster that has the
project, and nobody sets that up.

## One cluster

Nothing to configure. The Forge checks every change and keeps it.

## Several clusters

The Forge where a project was created is its **home**. It checks every change to the team.
The Guild keeps the newest team the home published and has every other Forge copy it.

- **Connect or change on any cluster.** On a cluster that only has a copy, the change is made
  at the home, with your own login, and copied back. A Slack or Linear install also starts at
  the home, and the provider returns to the home.
- **Notifications go out from where the session runs**, over the copied connection.
- **A new cluster** gets the team when the project is first used there, before its first
  session starts.
- **Ting** offers the team's trackers to the people on the team, whichever cluster is home.

No secret travels. A connection names its credential, and each cluster reads it from the
credential store they share. A cluster with a store of its own shows the connection as
**Not available here** and does not use it.

## When something is away

| What is away | What happens |
|---|---|
| The Guild, on the home | The change is kept and published at the next change or read. The project page says the other clusters are behind. |
| The Guild, on a copy | Reads show the last copy, and the page says it may be out of date. Changes and installs are refused: *Nothing was changed*. |
| The home | Copies read the Guild's record. Changes are refused. |
| Another cluster | It copies the team when the project is next read or launched there. |

Every failed exchange is logged at error and counted under `degraded` on `GET /health`:
`project_team.publish`, `project_team.copy`, `project_team.adopt`, `guild.project_team.copy`
and `guild.project_team.home`. `GET /api/v1/forge/projects/{id}/team` on a Forge says whether it is
the home, the version it holds and the last error.

## Requirements

- Each Forge has `guild` configured with its own instance, and is registered with the Guild for
  the org. A Forge registered for one person publishes no team: its owner decides what it
  answers.
- The Forges share one credential store (the same OpenBao mount), and each may read the
  project's credentials in it.
- The Forge whose connection store is the integrations service's database
  (`integrations.databaseName` without `integrations.repository`) also keeps every project's
  connections, because Ting reads them there and their tokens are renewed there.
- For Slack and Linear installs, set `oauth.redirectBaseUrl` on every Forge that is a project's
  home and register `<that URL>/api/v1/forge/integrations/oauth/callback` with the provider.
- Ting's credential for the Guild (`guild_registry.auth`) must act for the owner it works for.
  When it does not, team trackers are reported as unavailable rather than taken from another
  person.

## Limits

| Setting | Default | What bounds it | On a hit |
|---|---|---|---|
| `projects.team_refresh_after_seconds` | 15 | One project page makes several team reads at once | Reads within it use the copy, and do not wait again for a Guild that just failed |
| `projects.team_guild_timeout_seconds` | 60 | Publishing waits for every other Forge to copy | The change is kept, the error recorded and counted, and it is published later |

## What each cluster keeps to itself

- The use log of team integrations: each cluster shows the uses made there.
- Names and emails: a member who never signed in on a cluster is shown there by user id.

## A project on a second cluster

A cluster can come to have a project in three ways:

- **Through the Guild**, when someone launches into the project there: it is a copy of the
  home from the start.
- **Created there under the same id** (Ting does this on each cluster a run of the project
  runs on): the Forge asks the Guild first, and when the Guild holds the project for another
  Forge it is created as that Forge's copy and takes the team at once. When the Guild cannot be
  asked, nothing is created and the request fails with *Nothing was created* (503), so a second
  home is never made blind.
- **Registered there on its own** before this, or created at the same moment: the Forge that
  had the project first is the home. The other one's next publication is refused, the Guild
  names the home (the `Niuu-Project-Home` header), and the Forge becomes its copy.

It becomes a copy only when nothing would be lost. A member, a team integration or a route that
the home does not have is named on its project page, for example *This Forge also has member
cara as viewer, which Forge valhalla does not*, and nothing changes until someone adds it at
the home or removes it there. These refusals are counted as `project_team.adopt`.

A new project is published as it is created, so the Guild knows its home before any other
cluster has it.
