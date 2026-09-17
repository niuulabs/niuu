# Agents, personas, and residents

An agent runtime executes work using models and tools. A persona describes how
that agent should approach its work. A resident is an autonomous agent configured to steward
an environment over time, with a mandate and the authority to pursue it.
Residents participate in the distributed ecosystem: they can call on peers,
commission work, and share knowledge and capabilities.

## A persona does not create a process

Selecting a persona supplies behavior to a runtime. It does not by itself deploy
an agent, grant credentials, or give it a workspace. The launch or deployment
still supplies those pieces. Several instances can use the same persona while
holding different state and working in different environments.

## Direct agents and residents

Niuu implements this resident behavior in its Ravn runtime. The same runtime
can also handle direct conversations and bounded tasks. A resident adds
long-lived state and autonomous behavior: observing, judging whether action is
needed, acting within policy, waiting for input, and learning from outcomes.
The historical term **Valkyrie** still appears in APIs and UI areas.

The agent runtime owns these decisions. Niuu supplies identity, deployment, collaboration,
mesh, and other infrastructure. A running daemon is a process arrangement;
residency also requires an environment, purpose, and configured behavior.

## Mandates and environments

A mandate defines the resident's ongoing responsibility. The environment supplies
its observations, available capabilities, and operational context. Authority
bounds what it may do. A persona shapes its approach; the mandate explains what
it is responsible for.

For example, a cluster resident may be responsible for investigating health
signals. It judges whether a warning needs action, what evidence is missing,
whether an existing tool or peer can help, and when it needs a person's input.
A signal is an observation for that judgment, not a predetermined command.

## From a capability gap to a reusable tool

A resident can discover that it lacks a capability, research the gap, or commission a
team or workflow to build a tool. Construction, verification, adoption, and reuse
are distinct steps: generated output alone is not an acquired capability.
Configured policy and authority govern what may be installed and used.

Niuu supplies execution, communication, identity, and sharing infrastructure.
The resident decides whether a result is useful, what it has learned, and how that changes
its next action. Other agents can reuse shared knowledge or capabilities when
their configuration and access permit it. See [memory and knowledge](memory-and-knowledge.md).

## Keep the state understandable

For a resident, identify its persona, deployment target, persistent state,
knowledge mounts, triggers, and authority. When it pauses for input, inspect the
specific case it is waiting on. A new unrelated message is not necessarily an
answer to that suspended case.

A knowledge warden is a specialized assistant for curation. Give it a defined
knowledge scope before enabling ongoing maintenance. See
[direct agents and residents](../get-started/direct-and-resident-assistants.md).
