import {
  providerGroups,
  type CatalogEntry,
  type ConnectIntegrationInput,
  type IntegrationConnection,
  type IntegrationTestResult,
  type WizardStep,
} from '../domain/setup';
import { ProviderPane } from './ProviderPane';

export interface IntegrationsStepProps {
  step: WizardStep;
  catalog: CatalogEntry[] | undefined;
  connections: IntegrationConnection[] | undefined;
  loading: boolean;
  error: Error | null;
  connectingSlug: string | null;
  connectErrorSlug: string | null;
  connectError: Error | null;
  testingId: string | null;
  testResults: Record<string, IntegrationTestResult>;
  onConnect: (input: ConnectIntegrationInput) => void;
  onTest: (connectionId: string) => void;
}

/** One pane per provider (Anthropic, OpenAI, GitHub, ...), each with its ways to connect. */
export function IntegrationsStep({
  step,
  catalog,
  connections,
  loading,
  error,
  connectingSlug,
  connectErrorSlug,
  connectError,
  testingId,
  testResults,
  onConnect,
  onTest,
}: IntegrationsStepProps) {
  const groups = catalog ? providerGroups(catalog, step) : [];
  return (
    <div className="setup-col" data-testid={`setup-step-${step.id}`}>
      {error ? (
        <div className="setup-error" role="alert">
          Could not load the integrations catalog: {error.message}
        </div>
      ) : null}
      {loading ? <div className="setup-note">Loading catalog…</div> : null}
      {!loading && !error && groups.length === 0 ? (
        <div className="setup-note" data-testid="setup-catalog-empty">
          Nothing in the catalog for this step. You can skip it.
        </div>
      ) : null}
      <div className="setup-two">
        {groups.map((group) => (
          <ProviderPane
            key={group.key}
            group={group}
            connections={connections}
            connectingSlug={connectingSlug}
            connectErrorSlug={connectErrorSlug}
            connectError={connectError}
            testingId={testingId}
            testResults={testResults}
            onConnect={onConnect}
            onTest={onTest}
          />
        ))}
      </div>
    </div>
  );
}
