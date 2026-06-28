# Integrity Rules

## No Fake Proofs Or Fake Functionality

No proof, test, helper, adapter, fixture, or implementation may fake, hardcode,
echo, precompute, force, or simulate the behavior that the system claims to have
produced.

This rule applies across the repo. It is not limited to any product area,
provider, model, adapter, command, or ticket.

Fixtures may provide input conditions. They must not contain or force the
semantic output being proven.

Schemas may validate shape, contract fields, enum domains, and safety
invariants. They must not encode the expected semantic answer.

Prompts may describe the task, context, constraints, and success criteria. They
must not tell the model which answer to produce.

## Proof Labels

Every PR Proof of Working must state what kind of proof it is:

- **Unit/mock proof**: exercises code with mocks, fakes, canned outputs, scripted
  helpers, or semantic constants. Valid for unit behavior only.
- **Contract proof**: proves an interface, schema, or adapter contract. Valid for
  the contract only.
- **Transport proof**: proves bytes move through a transport or command boundary.
  Valid for the transport only.
- **Local integration proof**: proves real local components work together. Any
  local-only helpers or constrained schemas must be disclosed.
- **Real local LLM proof**: proves a configured local model produced the claimed
  semantic output. Schemas may constrain shape, types, enums, and safety flags,
  but must not pre-fill the semantic answer.
- **Real-world dogfood proof**: proves the functionality worked in a real
  environment with real inputs and real side effects or explicitly bounded
  non-side effects.

Mocks, fakes, canned outputs, scripted helpers, and semantic constants are
allowed in unit/mock and contract proofs when labeled as such. They must not be
presented as proof that real functionality, real integration, real model
judgment, real autonomy, or real-world behavior works.

A proof that uses semantic constants, scripted answers, or answer-specific
prompt hints is not a real LLM proof.

If the system claims the LLM decided, the LLM must actually decide.

## Production Code

Production code must not contain fake pipelines, fake autonomy, placeholder
adapters, no-op services, scripted decisions, hardcoded "temporary" behavior, or
precomputed outcomes that are claimed as working functionality.

If a temporary stub is unavoidable, label it plainly as not implemented and make
callers fail clearly instead of silently pretending work happened.

## Review Note

Every PR Proof of Working must disclose any mocks, fakes, scripted wrappers,
constrained schemas, canned fixtures, semantic constants, local-only helpers, or
other limits on what the proof actually demonstrates.
