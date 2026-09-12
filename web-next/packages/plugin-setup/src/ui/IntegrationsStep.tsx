import {
  catalogForStep,
  connectionForSlug,
  needsInteractiveSignIn,
  type CatalogEntry,
  type ConnectIntegrationInput,
  type IntegrationConnection,
  type IntegrationTestResult,
  type WizardStep,
} from '../domain/setup';
import { IntegrationCard } from './IntegrationCard';
import { SignInCard } from './SignInCard';

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
  const entries = catalog ? catalogForStep(catalog, step) : [];
  return (
    <div className="setup-col" data-testid={`setup-step-${step.id}`}>
      {error ? (
        <div className="setup-error" role="alert">
          Could not load the integrations catalog: {error.message}
        </div>
      ) : null}
      {loading ? <div className="setup-note">Loading catalog…</div> : null}
      {!loading && !error && entries.length === 0 ? (
        <div className="setup-note" data-testid="setup-catalog-empty">
          Nothing in the catalog for this step. You can skip it.
        </div>
      ) : null}
      {entries.map((entry) => {
        const connection = connections ? connectionForSlug(connections, entry.slug) : undefined;
        if (needsInteractiveSignIn(entry)) {
          return <SignInCard key={entry.slug} entry={entry} connection={connection} />;
        }
        return (
          <IntegrationCard
            key={entry.slug}
            entry={entry}
            connection={connection}
            connecting={connectingSlug === entry.slug}
            connectError={connectErrorSlug === entry.slug ? connectError : null}
            testResult={connection ? testResults[connection.id] : undefined}
            testing={connection ? testingId === connection.id : false}
            onConnect={onConnect}
            onTest={onTest}
          />
        );
      })}
    </div>
  );
}
