# Platform Hardening Plan

Branch: `refactor/platform-hardening`

This plan turns the architectural review into incremental, testable cleanup work.
Playwright/E2E re-enablement is intentionally out of scope for this program.

## Working principles

- Keep changes small enough to review and verify independently.
- Preserve behavior with characterization tests before splitting large modules.
- Move contracts toward shared ports and models; move implementations outward to adapters.
- Remove obsolete code instead of preserving misleading compatibility paths.
- Fail explicitly when a live capability is unavailable.
- Keep configuration typed, documented, and visible in runtime schemas and deployment values.

## 1. Package boundaries

- [x] Remove every direct `ting -> volundr` production import.
- [x] Move shared session-definition contracts and defaults into `niuu`.
- [x] Move shared workload-identity configuration contracts into `niuu`.
- [x] Move browser-facing session endpoint normalization into a pure shared helper.
- [x] Add AST-based regression coverage for `ting <-> volundr` imports.
- [x] Freeze existing `niuu -> volundr` debt behind an explicit, shrinking allowlist.
- [ ] Extract shared identity, credential, tracker, audit, and feature contracts from Volundr.
- [x] Move shared-host route composition out of `niuu` package internals or invert it through plugins.
- [x] Reduce the Niuu boundary allowlist to empty and make the rule absolute.

Acceptance: `niuu` imports neither `volundr` nor `ting`; feature packages communicate
only through Niuu contracts and ports.

## 2. Explicit live, demo, and unavailable behavior

- [x] Inventory every mock adapter imported by production frontend composition.
- [x] Introduce an explicit runtime demo mode; never infer demo behavior from a missing URL.
- [x] Represent unavailable live services with typed unavailable/error adapters and clear UI states.
- [x] Reject invalid live configurations at startup instead of silently serving synthetic data.
- [x] Keep mock adapters in tests, Storybook, and explicitly selected demo environments only.
- [x] Add service-composition tests covering live, demo, unavailable, and malformed configurations.

Acceptance: a broken deployment cannot look healthy because a mock silently took over.

## 3. Configuration-first cleanup

- [ ] Inventory direct environment reads and classify OS bootstrap, secret indirection, and behavior knobs.
- [x] Move behavior knobs from domain/services into typed package settings.
- [x] Pass adapter configuration as plain constructor kwargs from composition roots.
- [ ] Preserve intentional legacy environment aliases through Pydantic settings.
- [x] Render new settings through Helm/config templates and public settings schemas.
- [ ] Add tests proving malformed settings fail loudly and documented defaults remain stable.

Acceptance: business behavior can be discovered, validated, and tested through configuration models.

## 4. Incomplete and misleading production paths

- [x] Prove whether legacy `cli/_commands/migrate.py` and `serve.py` are unreachable.
- [x] Delete unreachable handlers or implement their real supported behavior.
- [x] Replace hosted-service “stub” lifecycle objects with an honest hosted-app/service descriptor.
- [ ] Audit production TODOs, canned implementations, and broad `NotImplementedError` fallbacks.
- [ ] Distinguish intentional null-object adapters from incomplete implementations by name and contract.
- [ ] Add contract tests for every retained null/unavailable adapter.

Acceptance: production source contains no path that claims to work while doing nothing.

## 5. Incremental module decomposition

- [ ] Add characterization tests around each large module before extraction.
- [ ] Split `skuld/broker.py` by protocol routing, connection state, persistence, and transport lifecycle.
- [ ] Split `ravn/cli/commands.py` into bounded command modules and shared CLI composition.
- [ ] Split `ravn/api/valkyries.py` into routers, application services, and response mapping.
- [ ] Split Volundr and Niuu composition roots into focused builders without hiding wiring.
- [ ] Split `LaunchWizard.tsx` into use cases/hooks, step components, validation, and request mapping.
- [ ] Split `ResearchCampaignPage.tsx` into data hooks, panels, actions, and presentation.
- [ ] Track module size and dependency direction as review signals, not arbitrary runtime limits.

Acceptance: high-change modules have clear responsibilities and can be tested without booting an entire service.

## 6. Documentation and quality-gate alignment

- [ ] Update the root README for `web-next`, React 19, Tailwind/tokens, pnpm, and current commands.
- [ ] Remove stale branch-specific and deleted-`web/` guidance.
- [ ] Align the declared Python minimum with the Python 3.12 CI/container/release baseline.
- [ ] Raise frontend branch coverage from 84% to the documented 85%.
- [ ] Make Python format checking a hard CI failure.
- [ ] Make local verification commands mirror CI coverage gates.
- [ ] Document a reproducible offline-friendly bootstrap/toolchain workflow.
- [ ] Reconcile package version metadata with the release/tagging process.

Acceptance: the documented command, toolchain, and threshold is the one CI and releases actually use.

## 7. Verification and delivery

- [ ] Run targeted tests after every extraction.
- [ ] Run backend lint, formatting, unit coverage, and relevant integration suites.
- [ ] Run frontend typecheck, lint, formatting, unit coverage, build, and publish dry-run.
- [ ] Run chart tests/lint for configuration or deployment changes.
- [x] Keep `git diff --check` and package-boundary tests green throughout.
- [x] Record any environment-blocked proof without claiming success.
- [ ] Prepare cohesive conventional commits with review notes and residual risks.

Acceptance: each completed checklist item has code-level proof proportional to its risk.
