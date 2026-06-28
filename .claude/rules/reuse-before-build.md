# Reuse Before Build

Before adding a new port, adapter, runner, executor, transport, registry,
gateway, service, workflow layer, scheduler, or framework, inspect the existing
primitives that may already solve the problem.

A new primitive is allowed only when the PR proves existing primitives cannot be
reused or extended cleanly.

Every PR that adds such a primitive must include a **Reuse Before Build** section
that names:

- existing primitives inspected
- why each was insufficient
- why the new boundary is genuinely different
- why the change is not a parallel implementation
- how the new code composes with the existing architecture

Prefer reshaping or composing existing code over adding a parallel boundary.
If the existing primitive is messy but usable, use it and document the mess
instead of creating a second version.

See `docs/developer/architecture-primitives.md` for the current canonical
primitive map.
