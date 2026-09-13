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

/** Vendor aliases, kept in step with `niuu.domain.model_runtime.normalize_model_vendor`. */
const VENDOR_ALIASES: Record<string, string> = {
  claude: 'anthropic',
  codex: 'openai',
  ollama: 'local',
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

/** "Claude Code (subscription) · claude-code-setup" style summary of what powers an engine. */
export function describeEngineProviders(option: EngineOption): string {
  const parts = option.providers.map((provider) => {
    const name = provider.entry.name || provider.entry.slug || provider.connection.id;
    const account = provider.connection.credentialName;
    const expired = provider.connection.credentialStatus === 'auth_required';
    const label = account && account !== name ? `${name} · ${account}` : name;
    return expired ? `${label} (sign-in expired)` : label;
  });
  return Array.from(new Set(parts)).join(', ');
}
