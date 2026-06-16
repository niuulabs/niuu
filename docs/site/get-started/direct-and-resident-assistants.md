# Direct And Resident Assistants

Install `ravn` when you want an assistant runtime outside the platform UI.

Use `ravn run` for direct conversations and `ravn daemon` when an assistant
should stay alive, respond to triggers, or maintain knowledge over time.

![Chronicle timeline](../images/chronicle-timeline.png)

## Direct assistant

Start with a direct assistant:

```bash
ravn run --config ~/.ravn/config.yaml
```

Or pass a prompt:

```bash
ravn run --config ~/.ravn/config.yaml "explain this repo"
```

This is the smallest Ravn shape: one operator, one assistant, one local config.

## Resident assistant

Move to daemon mode when the assistant should keep running:

```bash
ravn daemon --config ~/.ravn/config.yaml --persona autonomous-agent
```

Resident assistants are useful for:

- watching sources
- maintaining memory
- responding to events
- producing recaps
- investigating signals
- running scheduled or idle-time work

## Start from a setup profile

For local source development, `scripts/setups/ravn-setup` shows the available
setup profiles:

```bash
scripts/setups/ravn-setup list
scripts/setups/ravn-setup describe minimal
scripts/setups/ravn-setup describe daemon-http
```

The important progression is:

| Profile style | Use it for |
| --- | --- |
| `minimal` | direct assistant, no daemon |
| `mimir-local` | assistant with local memory |
| `daemon-http` | local resident assistant with an HTTP channel |
| `daemon-full` | resident assistant with triggers, wakefulness, recap, and trust rules |
| `flock-*` | several Ravn peers |

## Trust rules

Before leaving an assistant running, decide what it may do without approval.

Examples:

- reading is usually safe
- writing notes may be safe
- pushing branches may require approval
- pushing main should usually be disabled
- sending external messages should require approval
- spending beyond a cap should be blocked

Long-running assistants need boundaries more than one-off chats do.

## Dream cycles and wardens

Dream cycles are background reflection and maintenance passes. Wardens are a
specific resident-assistant shape focused on keeping Mímir knowledge healthy.

Use them after you have memory worth maintaining.

## What good looks like

You should be able to answer:

- Which persona is running?
- Which config file controls it?
- Where does state live?
- What triggers can wake it?
- What actions require approval?
- How do you stop it?

## Next

When you have multiple runtimes, add discovery and topology:

[Shared discovery and topology](shared-discovery-and-topology.md)
