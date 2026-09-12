# Agents, personas, and residents

An agent runtime executes work using models and tools. A persona describes how
that agent should approach its work. A resident is a Ravn configured to steward
an environment over time.

## A persona does not create a process

Selecting a persona supplies behavior to a runtime. It does not by itself deploy
an agent, grant credentials, or give it a workspace. The launch or deployment
still supplies those pieces. Several instances can use the same persona while
holding different state and working in different environments.

## Ordinary Ravn and resident Ravn

Use an ordinary Ravn for a direct conversation or bounded task. A resident adds
long-lived state and autonomous behavior: observing, judging whether action is
needed, acting within policy, waiting for input, and learning from outcomes.
The historical term **Valkyrie** still appears in APIs and UI areas.

Ravn owns these decisions. Niuu supplies identity, deployment, collaboration,
mesh, and other infrastructure. A running daemon is a process arrangement;
residency also requires an environment, purpose, and configured behavior.

## Keep the state understandable

For a resident, identify its persona, deployment target, persistent state,
knowledge mounts, triggers, and authority. When it pauses for input, inspect the
specific case it is waiting on. A new unrelated message is not necessarily an
answer to that suspended case.

A knowledge warden is a specialized assistant for curation. Give it a defined
knowledge scope before enabling ongoing maintenance. See
[direct agents and residents](../get-started/direct-and-resident-assistants.md).
