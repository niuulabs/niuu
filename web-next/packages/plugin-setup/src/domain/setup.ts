/**
 * Domain types and pure helpers for the first-launch wizard.
 *
 * Mirrors the backend contract of `/api/v1/niuu/setup` (progress + host facts)
 * and the integrations catalog the wizard connects providers, git and
 * trackers through. No framework imports here.
 */

export interface SetupStepRecord {
  step: string;
  completedAt: string;
  data: Record<string, unknown>;
}

export interface SetupState {
  enabled: boolean;
  mode: string;
  completed: boolean;
  completedAt: string | null;
  steps: string[];
  completedSteps: SetupStepRecord[];
}

export interface GpuFacts {
  name: string;
  memory_total_mib: number;
  driver_version: string;
}

export interface HostFacts {
  hostname: string;
  os_name: string;
  os_version: string;
  arch: string;
  cpu_count: number;
  memory_total_bytes: number;
  docker_version: string;
  compose_version: string;
  nvidia_runtime: boolean;
  gpus: GpuFacts[];
  data_dir: string;
  disk_free_bytes: number;
  disk_total_bytes: number;
  /** How `niuu up` published the platform; empty/0 when not started through the CLI. */
  bind_host?: string;
  external_host?: string;
  port?: number;
  skuld_image?: string;
  checks?: HostCheck[];
}

// ---------------------------------------------------------------------------
// Stack changes (docker mode): what the wizard may stage and apply.
// ---------------------------------------------------------------------------

export interface VllmSettings {
  enabled: boolean;
  model: string;
  image: string;
  maxModelLen: number;
  gpuMemoryUtilization: number;
}

export interface StackSettings {
  bindHost: string;
  externalHost: string;
  port: number;
  projectName: string;
  skuldImage: string;
  vllm: VllmSettings;
  accessUrls: string[];
}

export interface ModelOption {
  id: string;
  model: string;
  name: string;
  description: string;
  weightGib: number;
  recommended: boolean;
  /** null when the host's accelerator memory is unknown. */
  fits: boolean | null;
  memoryNeededGib: number;
}

export interface StackView {
  current: StackSettings;
  /** Nested settings overrides not applied yet, e.g. `{docker: {bind_host: '0.0.0.0'}}`. */
  staged: Record<string, unknown>;
  effective: StackSettings;
  models: ModelOption[];
  acceleratorMemoryGib: number;
  hasStagedChanges: boolean;
}

/** Wizard-level keys accepted by `PUT /setup/stack`. */
export interface StackChanges {
  bind_host?: string;
  vllm_enabled?: boolean;
  vllm_model?: string;
  vllm_max_model_len?: number;
  vllm_gpu_memory_utilization?: number;
}

export type ApplyState = 'idle' | 'applying' | 'applied' | 'failed';

export interface VllmStatus {
  state: 'absent' | 'starting' | 'ready' | 'failed' | string;
  detail: string;
}

export interface ApplyStatus {
  state: ApplyState;
  startedAt: string;
  detail: string;
  changes: Record<string, unknown>;
  vllm: VllmStatus | null;
}

export const LOCAL_BIND_HOST = '127.0.0.1';
export const NETWORK_BIND_HOST = '0.0.0.0';

/** Memory kept free for the platform and sandboxes when judging what fits (GiB). */
export const SANDBOX_HEADROOM_GIB = 12;

export interface MemoryPlan {
  totalGib: number;
  modelGib: number;
  sandboxGib: number;
  freeGib: number;
  /** Percent widths for the meter, summing to at most 100. */
  modelPct: number;
  sandboxPct: number;
}

/** How the host's accelerator memory splits if *modelGib* of weights are loaded. */
export function memoryPlan(totalGib: number, modelGib: number): MemoryPlan {
  const model = Math.max(0, modelGib);
  const sandbox = model > 0 ? SANDBOX_HEADROOM_GIB : 0;
  const free = Math.max(0, totalGib - model - sandbox);
  const pct = (value: number) =>
    totalGib > 0 ? Math.min(100, Math.round((value / totalGib) * 100)) : 0;
  return {
    totalGib,
    modelGib: model,
    sandboxGib: sandbox,
    freeGib: free,
    modelPct: pct(model),
    sandboxPct: pct(sandbox),
  };
}

/** Human summary of a staged change set, for the finish step. */
export function describeStagedChanges(view: StackView | undefined): string[] {
  if (!view || !view.hasStagedChanges) return [];
  const lines: string[] = [];
  if (view.effective.bindHost !== view.current.bindHost) {
    lines.push(
      view.effective.bindHost === LOCAL_BIND_HOST
        ? 'Reachable from this machine only'
        : `Reachable from your network at ${view.effective.accessUrls[1] ?? view.effective.accessUrls[0]}`,
    );
  }
  const before = view.current.vllm;
  const after = view.effective.vllm;
  if (after.enabled !== before.enabled || after.model !== before.model) {
    lines.push(
      after.enabled ? `Serve ${after.model} locally with vLLM` : 'Stop serving a local model',
    );
  }
  return lines;
}

/** True when the page's own origin stops being served after the change. */
export function originLostAfterApply(view: StackView | undefined, origin: string): boolean {
  if (!view || !view.hasStagedChanges) return false;
  if (view.effective.bindHost === view.current.bindHost) return false;
  return !view.effective.accessUrls.some((url) => origin.startsWith(url));
}

// ---------------------------------------------------------------------------
// Provider panes: one pane per provider, with the ways to connect it.
// ---------------------------------------------------------------------------

export interface ProviderGroup {
  key: string;
  title: string;
  description: string;
  /** Entry connected with a pasted key or token, when the catalog has one. */
  keyEntry?: CatalogEntry;
  /** Entry connected by signing in through the provider, when the catalog has one. */
  signInEntry?: CatalogEntry;
  /** Ways to connect that exist as a product but not on this install. */
  unavailable: Array<{ label: string; reason: string }>;
}

interface GroupSpec {
  key: string;
  title: string;
  description: string;
  keySlug?: string;
  signInSlug?: string;
  unavailable?: Array<{ label: string; reason: string }>;
}

const GROUP_SPECS: readonly GroupSpec[] = [
  {
    key: 'anthropic',
    title: 'Anthropic · Claude',
    description: 'Claude Code sessions, Ravn judgment',
    keySlug: 'anthropic',
    signInSlug: 'claude-code',
  },
  {
    key: 'openai',
    title: 'OpenAI · Codex',
    description: 'Codex sessions and GPT models',
    keySlug: 'openai',
    signInSlug: 'codex',
  },
  {
    key: 'xai',
    title: 'xAI · Grok',
    description: 'Grok models through the model gateway',
    keySlug: 'xai',
  },
  {
    key: 'github',
    title: 'GitHub',
    description: 'Clone, push, open pull requests, MCP server',
    keySlug: 'github',
    unavailable: [
      {
        label: 'GitHub App',
        reason:
          'Scoped to the repos you pick, no long-lived token. Needs a published app with a callback for this install; not shipped in the single-host bundle yet.',
      },
    ],
  },
  {
    key: 'gitlab',
    title: 'GitLab',
    description: 'Clone, push, open merge requests, MCP server',
    keySlug: 'gitlab',
  },
];

/** Panes for a step: known pairings first, then any other catalog entry on its own. */
export function providerGroups(entries: CatalogEntry[], step: WizardStep): ProviderGroup[] {
  const forStep = catalogForStep(entries, step);
  const used = new Set<string>();
  const groups: ProviderGroup[] = [];
  for (const spec of GROUP_SPECS) {
    const keyEntry = spec.keySlug ? forStep.find((e) => e.slug === spec.keySlug) : undefined;
    const signInEntry = spec.signInSlug
      ? forStep.find((e) => e.slug === spec.signInSlug)
      : undefined;
    if (!keyEntry && !signInEntry) continue;
    if (keyEntry) used.add(keyEntry.slug);
    if (signInEntry) used.add(signInEntry.slug);
    groups.push({
      key: spec.key,
      title: spec.title,
      description: spec.description,
      keyEntry,
      signInEntry,
      unavailable: spec.unavailable ?? [],
    });
  }
  for (const entry of forStep) {
    if (used.has(entry.slug)) continue;
    const interactive = needsInteractiveSignIn(entry);
    groups.push({
      key: entry.slug,
      title: entry.name,
      description: entry.description,
      keyEntry: interactive ? undefined : entry,
      signInEntry: interactive ? entry : undefined,
      unavailable: [],
    });
  }
  return groups;
}

/** The connection that makes a pane "connected", if any. */
export function groupConnection(
  group: ProviderGroup,
  connections: IntegrationConnection[] | undefined,
): IntegrationConnection | undefined {
  if (!connections) return undefined;
  for (const entry of [group.signInEntry, group.keyEntry]) {
    if (!entry) continue;
    const connection = connectionForSlug(connections, entry.slug);
    if (connection && !connectionNeedsSignIn(connection)) return connection;
  }
  return undefined;
}

export type AccessMode = 'local' | 'lan' | 'unknown';

/** Who can reach the web app, derived from the bind address `niuu up` used. */
export function accessMode(facts: HostFacts | null): AccessMode {
  const bind = (facts?.bind_host ?? '').trim();
  if (!bind) return 'unknown';
  if (bind === '127.0.0.1' || bind === 'localhost' || bind === '::1') return 'local';
  return 'lan';
}

/** Addresses the web app answers on, given the bind address and detected LAN host. */
export function accessUrls(facts: HostFacts | null): string[] {
  if (!facts || !facts.port) return [];
  const urls = [`http://127.0.0.1:${facts.port}`];
  if (accessMode(facts) === 'lan' && facts.external_host) {
    urls.push(`http://${facts.external_host}:${facts.port}`);
  }
  return urls;
}

export interface SystemCheck {
  name: string;
  passed: boolean;
  warnOnly: boolean;
  message: string;
}

/** A preflight result recorded by `niuu up` (snake_case, as written to host-facts.json). */
export interface HostCheck {
  name: string;
  passed: boolean;
  warn_only: boolean;
  message: string;
}

export function hostChecks(facts: HostFacts | null): SystemCheck[] {
  return (facts?.checks ?? []).map((check) => ({
    name: check.name,
    passed: check.passed,
    warnOnly: check.warn_only,
    message: check.message,
  }));
}

export interface SystemReport {
  host: HostFacts | null;
  checks: SystemCheck[];
  healthy: boolean;
}

export interface CatalogFieldSchema {
  label: string;
  type: string;
  default?: string;
}

export interface CatalogSchema {
  required?: string[];
  properties?: Record<string, CatalogFieldSchema>;
}

export interface CatalogEntry {
  slug: string;
  name: string;
  description: string;
  integrationType: string;
  authType: string;
  credentialSchema: CatalogSchema;
  configSchema: CatalogSchema;
}

export interface IntegrationConnection {
  id: string;
  slug: string;
  integrationType: string;
  credentialName: string;
  enabled: boolean;
  config: Record<string, unknown>;
  credentialStatus: string;
}

export interface IntegrationTestResult {
  success: boolean;
  provider: string;
  workspace: string | null;
  user: string | null;
  error: string | null;
}

export interface ConnectIntegrationInput {
  slug: string;
  credentialName: string;
  credential: Record<string, string>;
  config: Record<string, unknown>;
}

/** Lifecycle of one interactive provider sign-in (Claude subscription, Codex device code). */
export type EnrollmentState =
  'pending' | 'awaiting_user' | 'complete' | 'failed' | 'expired' | 'cancelled';

export interface Enrollment {
  id: string;
  connectionId: string;
  providerSlug: string;
  credentialName: string;
  state: EnrollmentState;
  /** Where the user signs in; empty until the CLI has produced it. */
  verificationUri: string;
  /** Device code to enter on the provider's page (device-code logins only). */
  userCode: string;
  expiresAt: string;
  errorCode: string;
  /** True when the provider hands the user a code to paste back (Claude). */
  inputRequired: boolean;
}

export function isEnrollmentActive(enrollment: Enrollment | undefined): boolean {
  return enrollment?.state === 'pending' || enrollment?.state === 'awaiting_user';
}

/** Human explanation for a failed, expired or cancelled sign-in. */
export function enrollmentFailureMessage(enrollment: Enrollment): string {
  if (enrollment.state === 'expired') return 'The sign-in timed out. Start it again.';
  if (enrollment.state === 'cancelled') return 'Sign-in cancelled.';
  const code = enrollment.errorCode;
  if (code === 'runner_start_failed' || code === 'login_worker_missing') {
    return 'The sign-in helper could not be started on this host. Check the platform logs.';
  }
  if (code === 'provider_login_rejected') return 'The provider rejected the sign-in.';
  if (code === 'claude_token_not_found') {
    return 'Claude finished without handing back a token. Try again.';
  }
  if (code === 'unexpected_login_url') {
    return 'The sign-in helper was sent to an unexpected address and stopped for safety.';
  }
  return code ? `Sign-in failed (${code}).` : 'Sign-in failed.';
}

/** Wizard screens in presentation order. Only screens with a live backend appear. */
export type WizardStepId =
  'welcome' | 'system' | 'model' | 'providers' | 'git' | 'tracker' | 'runtime' | 'finish';

export interface WizardStep {
  id: WizardStepId;
  label: string;
  /** Integration category this step connects, when it is an integrations step. */
  integrationType?: 'ai_provider' | 'source_control' | 'issue_tracker';
}

export const WIZARD_STEPS: readonly WizardStep[] = [
  { id: 'welcome', label: 'Welcome' },
  { id: 'system', label: 'System check' },
  { id: 'model', label: 'Local model' },
  { id: 'providers', label: 'AI providers', integrationType: 'ai_provider' },
  { id: 'git', label: 'Git', integrationType: 'source_control' },
  { id: 'tracker', label: 'Tickets', integrationType: 'issue_tracker' },
  { id: 'runtime', label: 'Runtime & access' },
  { id: 'finish', label: 'Finish' },
];

/** Backend step id recorded for a wizard screen (the backend uses `launch` for finish). */
export function backendStepId(step: WizardStepId): string {
  return step === 'finish' ? 'launch' : step;
}

export function stepIndex(step: WizardStepId): number {
  return WIZARD_STEPS.findIndex((candidate) => candidate.id === step);
}

export function nextStep(step: WizardStepId): WizardStepId | null {
  const next = WIZARD_STEPS[stepIndex(step) + 1];
  return next ? next.id : null;
}

export function previousStep(step: WizardStepId): WizardStepId | null {
  const index = stepIndex(step);
  const previous = index > 0 ? WIZARD_STEPS[index - 1] : undefined;
  return previous ? previous.id : null;
}

export function isStepDone(state: SetupState | undefined, step: WizardStepId): boolean {
  if (!state) return false;
  const id = backendStepId(step);
  return state.completedSteps.some((record) => record.step === id);
}

/** Entries connected with a pasted key or token (a form in the wizard). */
export function isConnectableFromWizard(entry: CatalogEntry): boolean {
  return !needsInteractiveSignIn(entry);
}

/** Entries connected by signing in through the provider (browser or device code). */
export function needsInteractiveSignIn(entry: CatalogEntry): boolean {
  return entry.authType === 'browser_login' || entry.authType === 'device_code';
}

export function catalogForStep(entries: CatalogEntry[], step: WizardStep): CatalogEntry[] {
  if (!step.integrationType) return [];
  return entries.filter((entry) => entry.integrationType === step.integrationType);
}

/** Credential states in which a connection exists but cannot be used yet. */
const UNUSABLE_CREDENTIAL_STATUSES = new Set(['auth_required', 'enrolling']);

/** True when the connection's credential still has to be (re)established. */
export function connectionNeedsSignIn(connection: IntegrationConnection): boolean {
  return UNUSABLE_CREDENTIAL_STATUSES.has(connection.credentialStatus);
}

export function connectionForSlug(
  connections: IntegrationConnection[],
  slug: string,
): IntegrationConnection | undefined {
  return connections.find((connection) => connection.slug === slug && connection.enabled);
}

const GIB = 1024 ** 3;

export function formatGib(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return '0 GiB';
  return `${Math.round(bytes / GIB)} GiB`;
}

export function formatGpu(gpu: GpuFacts): string {
  const gib = Math.round(gpu.memory_total_mib / 1024);
  return gib > 0 ? `${gpu.name} · ${gib} GiB` : gpu.name;
}

/** Presentation only: the reference target gets its name on the welcome screen. */
export function hostFlavor(facts: HostFacts | null): string {
  if (!facts) return 'this machine';
  const gpuNames = facts.gpus.map((gpu) => gpu.name.toLowerCase()).join(' ');
  if (gpuNames.includes('gb10') || gpuNames.includes('dgx')) return 'this DGX Spark';
  if (facts.gpus.length > 0) return 'this GPU host';
  return 'this machine';
}

export interface HostChip {
  label: string;
  tone: 'brand' | 'neutral' | 'ok';
}

export function hostChips(facts: HostFacts | null): HostChip[] {
  if (!facts) return [];
  const chips: HostChip[] = [{ label: facts.hostname, tone: 'brand' }];
  chips.push({ label: `${facts.os_name} ${facts.os_version} · ${facts.arch}`, tone: 'neutral' });
  if (facts.memory_total_bytes > 0) {
    chips.push({ label: `${formatGib(facts.memory_total_bytes)} memory`, tone: 'neutral' });
  }
  for (const gpu of facts.gpus) {
    chips.push({ label: formatGpu(gpu), tone: 'ok' });
  }
  if (facts.docker_version) {
    chips.push({ label: `Docker ${facts.docker_version}`, tone: 'neutral' });
  }
  return chips;
}

/** Default credential name for a connection made by the wizard. */
export function credentialNameFor(slug: string): string {
  return `${slug}-setup`.toLowerCase().replace(/[^a-z0-9_-]/g, '-');
}

export function requiredCredentialKeys(entry: CatalogEntry): string[] {
  const required = entry.credentialSchema.required ?? [];
  if (required.length > 0) return required;
  return Object.keys(entry.credentialSchema.properties ?? {});
}

export function missingCredentialKeys(
  entry: CatalogEntry,
  values: Record<string, string>,
): string[] {
  return requiredCredentialKeys(entry).filter((key) => !(values[key] ?? '').trim());
}

/** Config values to send: typed inputs, with schema defaults filled in when blank. */
export function buildConfigPayload(
  entry: CatalogEntry,
  values: Record<string, string>,
): Record<string, unknown> {
  const payload: Record<string, unknown> = {};
  for (const [key, schema] of Object.entries(entry.configSchema.properties ?? {})) {
    const raw = (values[key] ?? '').trim() || schema.default?.trim() || '';
    if (!raw) continue;
    if (schema.type === 'string[]') {
      payload[key] = raw
        .split(',')
        .map((item) => item.trim())
        .filter(Boolean);
      continue;
    }
    payload[key] = raw;
  }
  return payload;
}
