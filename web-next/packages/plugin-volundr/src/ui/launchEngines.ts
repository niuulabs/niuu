import type { RepoRecord } from '@niuulabs/ui';
import type {
  CatalogEntry,
  IntegrationConnection,
  SessionDefinition,
} from '../models/volundr.model';

/**
 * Which engines (session definitions) a person can actually launch, given the
 * AI providers they have connected.
 *
 * A session definition names the model vendors it accepts in
 * `compatibleProviders`; every AI-provider catalog entry says which vendor a
 * connection of it unlocks in `modelVendor`. Joining the two through the
 * person's connections is what turns the full engine list into "the ones that
 * will work here".
 */

/** Where providers are connected, disconnected and signed in again. */
export const PROVIDER_SETTINGS_PATH = '/settings/integrations';

/** A connected AI provider together with the catalog entry that describes it. */
export interface ConnectedProvider {
  connection: IntegrationConnection;
  entry: CatalogEntry;
  vendor: string;
}

/** An engine the person can launch, and the connected providers that power it. */
export interface EngineOption {
  definition: SessionDefinition;
  providers: ConnectedProvider[];
}

/** Statuses under which a stored credential cannot run a session right now. */
const UNUSABLE_CREDENTIAL_STATUSES = new Set(['missing', 'enrolling']);

/**
 * Session-definition labels that keep an engine out of the launch dialogs:
 * `batch` engines exist for Ting workflows, `remote-control` ones are driven
 * from the Claude app rather than launched here.
 */
export const HIDDEN_ENGINE_LABELS: ReadonlySet<string> = new Set(['batch', 'remote-control']);

/** Vendor aliases, kept in step with `niuu.domain.model_runtime.normalize_model_vendor`. */
const VENDOR_ALIASES: Record<string, string> = {
  claude: 'anthropic',
  codex: 'openai',
  ollama: 'local',
  vllm: 'local',
};

export function normalizeVendor(value: string | undefined): string {
  const vendor = (value ?? '').trim().toLowerCase();
  return VENDOR_ALIASES[vendor] ?? vendor;
}

/** The AI providers the person has connected and can use right now. */
export function connectedProviders(
  integrations: IntegrationConnection[],
  catalog: CatalogEntry[],
): ConnectedProvider[] {
  const entries = new Map(
    catalog.filter((entry) => entry.slug).map((entry) => [entry.slug as string, entry]),
  );
  const providers: ConnectedProvider[] = [];
  for (const connection of integrations) {
    if (connection.enabled === false) continue;
    if (
      connection.credentialStatus &&
      UNUSABLE_CREDENTIAL_STATUSES.has(connection.credentialStatus)
    )
      continue;
    const entry = connection.slug ? entries.get(connection.slug) : undefined;
    if (!entry || entry.integrationType !== 'ai_provider' || !entry.modelVendor) continue;
    providers.push({ connection, entry, vendor: normalizeVendor(entry.modelVendor) });
  }
  return providers;
}

/**
 * The engines that at least one connected provider powers, in catalog order.
 * Provider-neutral engines (no `compatibleProviders`) are launchable as soon
 * as any AI provider is connected.
 */
export function availableEngines(
  definitions: SessionDefinition[],
  integrations: IntegrationConnection[],
  catalog: CatalogEntry[],
): EngineOption[] {
  const providers = connectedProviders(integrations, catalog);
  const engines: EngineOption[] = [];
  for (const definition of definitions) {
    if (definition.labels.some((label) => HIDDEN_ENGINE_LABELS.has(label))) continue;
    const vendors = definition.compatibleProviders.map(normalizeVendor);
    const powering =
      vendors.length === 0
        ? providers
        : providers.filter((provider) => vendors.includes(provider.vendor));
    if (powering.length === 0) continue;
    engines.push({ definition, providers: powering });
  }
  return engines;
}

/** "Claude Code (subscription) · claude-code-setup" style label for one connected provider. */
export function describeProvider(provider: ConnectedProvider): string {
  const name = provider.entry.name || provider.entry.slug || provider.connection.id;
  const account = provider.connection.credentialName;
  const expired = provider.connection.credentialStatus === 'auth_required';
  const label = account && account !== name ? `${name} · ${account}` : name;
  return expired ? `${label} (sign-in expired)` : label;
}

/** Every provider that powers an engine, comma-separated. */
export function describeEngineProviders(option: EngineOption): string {
  return Array.from(new Set(option.providers.map(describeProvider))).join(', ');
}

/**
 * The models a provider serves itself: the Model server's `config.models`,
 * registered from Settings → Runtime. Empty for cloud providers, whose engine
 * picks its own default model.
 */
export function providerModels(provider: ConnectedProvider | undefined): string[] {
  const models = provider?.connection.config?.models;
  if (!Array.isArray(models)) return [];
  return models.filter(
    (model): model is string => typeof model === 'string' && model.trim() !== '',
  );
}

/**
 * The model a launch uses: the person's pick when the provider serves it, else
 * the first model the provider serves, else the engine's default model.
 */
export function launchModel(
  engine: EngineOption | undefined,
  provider: ConnectedProvider | undefined,
  picked: string,
): string {
  const served = providerModels(provider);
  if (served.length === 0) return engine?.definition.defaultModel ?? '';
  return served.includes(picked) ? picked : (served[0] ?? '');
}

/**
 * The provider a launch will use for an engine: the one already chosen among
 * `selectedIntegrationIds`, otherwise the first that powers the engine.
 */
export function selectedEngineProvider(
  engine: EngineOption | undefined,
  selectedIntegrationIds: readonly string[],
): ConnectedProvider | undefined {
  if (!engine) return undefined;
  return (
    engine.providers.find((provider) => selectedIntegrationIds.includes(provider.connection.id)) ??
    engine.providers[0]
  );
}

/**
 * The integration selection with exactly one of the engine's providers in it:
 * `providerId` when given, otherwise whichever is already selected, otherwise
 * the first. Providers of other engines the person chose explicitly stay.
 */
export function withEngineProvider(
  selectedIntegrationIds: readonly string[],
  engine: EngineOption | undefined,
  providerId?: string,
): string[] {
  if (!engine || engine.providers.length === 0) return [...selectedIntegrationIds];
  const chosen =
    (providerId && engine.providers.find((provider) => provider.connection.id === providerId)) ||
    selectedEngineProvider(engine, selectedIntegrationIds);
  if (!chosen) return [...selectedIntegrationIds];
  const others = new Set(engine.providers.map((provider) => provider.connection.id));
  return [...selectedIntegrationIds.filter((id) => !others.has(id)), chosen.connection.id];
}

/**
 * The source-control connections a repository should be cloned with: the
 * account that listed it when the catalog knows, otherwise every enabled
 * Git account (a pasted URL could belong to any of them).
 */
export function sourceControlIdsForRepo(
  integrations: IntegrationConnection[],
  repos: readonly RepoRecord[],
  repoUrl: string,
): string[] {
  const sources = integrations.filter(
    (connection) => connection.integrationType === 'source_control' && connection.enabled !== false,
  );
  const account = repos.find((repo) => repo.cloneUrl === repoUrl)?.account;
  const owner = account && sources.find((connection) => connection.credentialName === account);
  if (owner) return [owner.id];
  return sources.map((connection) => connection.id);
}

/**
 * The integration selection with its source-control part replaced by the
 * accounts that should clone `repoUrl` (see `sourceControlIdsForRepo`).
 */
export function withRepoSourceControl(
  selectedIntegrationIds: readonly string[],
  integrations: IntegrationConnection[],
  repos: readonly RepoRecord[],
  repoUrl: string,
): string[] {
  const sources = new Set(
    integrations
      .filter((connection) => connection.integrationType === 'source_control')
      .map((connection) => connection.id),
  );
  return [
    ...selectedIntegrationIds.filter((id) => !sources.has(id)),
    ...sourceControlIdsForRepo(integrations, repos, repoUrl),
  ];
}

/**
 * What the quick launch attaches to a session: the chosen AI provider, the
 * Git account that owns the repository (none for a local folder), and every
 * other enabled non-AI integration (trackers, messaging) the person has.
 */
export function quickLaunchIntegrationIds(input: {
  provider: ConnectedProvider | undefined;
  integrations: IntegrationConnection[];
  repos: readonly RepoRecord[];
  repoUrl: string;
  local: boolean;
}): string[] {
  const ids: string[] = [];
  if (input.provider) ids.push(input.provider.connection.id);
  if (!input.local)
    ids.push(...sourceControlIdsForRepo(input.integrations, input.repos, input.repoUrl));
  for (const connection of input.integrations) {
    if (connection.enabled === false) continue;
    if (connection.integrationType === 'ai_provider') continue;
    if (connection.integrationType === 'source_control') continue;
    ids.push(connection.id);
  }
  return Array.from(new Set(ids));
}
