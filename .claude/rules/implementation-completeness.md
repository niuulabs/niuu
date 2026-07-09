# Implementation Completeness

## No Placeholders Outside Tests

Production code, docs, charts, scripts, and examples must not introduce placeholder
or fake implementations. This includes stubs that return canned data, TODO-shaped
control flow, partial adapters, mock credentials, fake endpoints, and code paths that
only work for a narrow demo while presenting themselves as complete.

Allowed exceptions:

- Unit and integration tests may use placeholders, fakes, fixtures, and canned values
  when the test makes that explicit.
- Existing placeholders outside the touched scope should be reported when relevant,
  not silently expanded.

## Completion Standard

Before calling work done:

1. Implement the real path or state that the task is blocked.
2. Verify the behavior with tests or live proof appropriate to the change.
3. If proof fails, report the concrete failure and stop short of claiming success.
4. Do not add repo validation, runtime checks, or product behavior solely to enforce
   this agent rule unless the user explicitly asks for that product behavior.
