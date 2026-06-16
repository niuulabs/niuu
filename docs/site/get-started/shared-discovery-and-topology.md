# Shared Discovery And Topology

Add shared discovery when one local platform is no longer the whole world.

This is where Guild and Observatory become useful.

![Guild instances](../images/ui-guild-instances.png)

## What changes at this step

With one local platform, the UI can assume most things are nearby.

With several platforms, clusters, hosts, or resident assistants, the UI needs to
know:

- which instances exist
- what capabilities they expose
- where sessions are running
- which cluster or namespace owns a workload
- how services relate to each other

Guild is the runtime registry. Observatory is the topology and operations view.

## Register capabilities, not random processes

The registry should describe useful platform capabilities:

- a Volundr/Forge instance that owns sessions
- a Mímir instance that owns memory mounts
- a Ravn or warden runtime
- a Ting workflow service
- a Bifröst model gateway

Avoid registering low-level implementation details unless operators need to act
on them.

## Use labels for location

For Kubernetes deployments, cluster and namespace labels should come from
deployment configuration. That lets the UI group services by where they run
without hardcoding environment names.

Good topology answers questions such as:

- What runs in this cluster?
- Which namespace owns it?
- Which service or assistant reads from this memory?
- Which sessions belong to this remote Forge?

## Inspect topology

Use Observatory to check the platform shape.

![Observatory topology](../images/landing/landing-observatory.png)

The graph should show meaningful entities and relationships, not just a flat
list of deployments.

Look for:

- services
- agents
- sessions
- memory instances
- workflow runs
- cross-cluster relationships
- read/write/manage relationships where they are known

## What good looks like

You should be able to answer:

- Which instance owns this session?
- Which cluster is it in?
- Which memory instance does it use?
- Which agents are alive?
- Which services are connected by real relationships?

## Common mistake

Do not make every workload look like the same kind of thing. Services,
assistants, sessions, and deployments are different operator concepts.

## Next

When the stack needs durable infrastructure, move to Kubernetes:

[Kubernetes and GitOps](kubernetes-and-gitops-step.md)
