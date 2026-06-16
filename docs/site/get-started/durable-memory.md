# Durable Memory

Add memory when useful information should survive a single session.

This is where Mímir becomes useful. Treat it as shared knowledge for operators
and assistants: sources, pages, research outputs, curated notes, and wardens.

![Mímir knowledge overview](../images/landing/landing-memory.png)

## What belongs in memory

Good candidates:

- project decisions
- architecture notes
- research summaries
- postmortems
- recurring operator preferences
- durable facts that future sessions should recall

Bad candidates:

- raw tokens
- private keys
- broad home-directory dumps
- temporary terminal output
- anything you would not want another assistant to read later

## Start with one mount

Begin with one local or shared knowledge mount. Make it obvious what it is for.

For example:

- personal project notes
- team wiki
- research outputs
- operational runbooks

Avoid creating many mounts before you have a habit for what should go where.

## Add sources

Sources are raw material that can be compiled, summarized, or curated into
knowledge pages.

Use sources for:

- imported documents
- research material
- transcripts
- external notes
- session outputs that need curation

The goal is not to hoard raw material. The goal is to turn useful raw material
into durable knowledge.

## Add a warden later

A warden is a resident assistant focused on maintaining knowledge. It can watch
sources, compile missing pages, curate stale material, and keep a mount healthy.

Do this after a mount is useful. A warden with no clear knowledge boundary just
creates noise.

## What good looks like

You should be able to answer:

- What is this mount for?
- Who or what writes to it?
- Who or what reads from it?
- Which source material is waiting to be compiled?
- Which assistant is responsible for curation, if any?

## Common mistake

Do not use memory as a secret store. Use the platform credential and secret
systems for sensitive values.

## Next

When work needs several stages or roles, add workflows:

[Workflows and teams](workflows-and-teams-step.md)
