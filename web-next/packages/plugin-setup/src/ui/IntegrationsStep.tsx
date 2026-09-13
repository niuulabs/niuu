import { useEffect, useRef, useState } from 'react';
import {
  connectionLabel,
  connectionNeedsSignIn,
  credentialExpiryLabel,
  credentialProblemLabel,
  errorMessage,
  groupConnections,
  providerGroups,
  type CatalogEntry,
  type ConnectIntegrationInput,
  type IntegrationConnection,
  type IntegrationTestResult,
  type ProviderGroup,
  type WizardStep,
} from '../domain/setup';
import {
  AddProviderDialog,
  type AddProviderSelection,
  type ConnectMode,
} from './AddProviderDialog';
import { TestOutcome } from './TestOutcome';
import { AlertIcon, CheckIcon } from './icons';

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

const NOUNS: Record<string, { one: string; many: string }> = {
  ai_provider: { one: 'provider', many: 'providers' },
  source_control: { one: 'Git host', many: 'Git hosts' },
  issue_tracker: { one: 'tracker', many: 'trackers' },
};

export function nounFor(step: WizardStep): { one: string; many: string } {
  return NOUNS[step.integrationType ?? ''] ?? { one: 'integration', many: 'integrations' };
}

function methodLabel(group: ProviderGroup, connection: IntegrationConnection): string {
  const signIn = group.signInEntry;
  if (signIn) {
    const sharedSlug = group.keyEntry?.slug === signIn.slug;
    const signedIn = sharedSlug
      ? connection.credentialName === signIn.credentialEnrollment?.defaultCredentialName ||
        connection.credentialName.endsWith('-signin')
      : connection.slug === signIn.slug;
    if (signedIn) return 'Signed in';
  }
  return group.keyLabel.replace(/^Use an? /, '').replace(/^./, (c) => c.toUpperCase());
}

interface Adding extends AddProviderSelection {
  id: number;
  /** Connections already usable when the dialog opened; only a newer one closes it. */
  usableAtStart: readonly string[];
}

/**
 * What is connected for this step, one row per account, plus an "Add"
 * button that opens the provider → method → connect dialog. A provider
 * can be added as often as there are accounts.
 */
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
  const noun = nounFor(step);
  const [adding, setAdding] = useState<Adding | null>(null);
  const nextAddingId = useRef(0);

  const rows = groups.flatMap((group) =>
    groupConnections(group, connections).map((connection) => ({
      group,
      connection,
      pending: connectionNeedsSignIn(connection),
    })),
  );
  // The dialog closes itself the moment the account being added becomes
  // usable, and the new connection is checked right away so a wrong scope
  // or a dead key shows up here, not in a session.
  // Only a connection that became usable after the dialog opened counts:
  // an existing account that happens to carry the default name must not
  // close the dialog under the person's feet.
  const added =
    adding?.credentialName && connections
      ? connections.find(
          (connection) =>
            connection.enabled &&
            connection.credentialName === adding.credentialName &&
            !connectionNeedsSignIn(connection) &&
            !adding.usableAtStart.includes(connection.id),
        )
      : undefined;
  const dialogOpen = adding !== null && added === undefined;
  const addedId = added?.id ?? null;
  const checkedRef = useRef<string | null>(null);
  useEffect(() => {
    if (addedId === null || checkedRef.current === addedId) return;
    checkedRef.current = addedId;
    onTest(addedId);
  }, [addedId, onTest]);

  const startAdding = (selection: Partial<AddProviderSelection>) =>
    setAdding({
      id: (nextAddingId.current += 1),
      groupKey: selection.groupKey ?? null,
      mode: selection.mode ?? null,
      credentialName: selection.credentialName ?? null,
      usableAtStart: (connections ?? [])
        .filter((connection) => connection.enabled && !connectionNeedsSignIn(connection))
        .map((connection) => connection.id),
    });

  return (
    <div className="setup-col" data-testid={`setup-step-${step.id}`}>
      {error ? (
        <div className="setup-error" role="alert">
          Could not load the integrations catalog: {errorMessage(error)}
        </div>
      ) : null}
      {loading ? <div className="setup-note">Loading catalog…</div> : null}

      <div className="setup-card" data-testid="setup-provider-list">
        {rows.length === 0 && !loading ? (
          <div className="setup-row" data-testid="setup-provider-empty">
            <span className="setup-row__icon" />
            <div className="setup-row__body">
              <span className="setup-row__title">No {noun.many} yet</span>
              <span className="setup-row__detail">
                Add one to get started. You can add as many accounts as you use, and change them
                later in Settings.
              </span>
            </div>
          </div>
        ) : null}
        {rows.map(({ group, connection, pending }) => {
          // The method chip already tells a signed-in account from a keyed one;
          // only a name the person gave is worth repeating in the title.
          const label = connectionLabel(connection, group);
          return (
            <div
              className="setup-row"
              key={connection.id}
              data-testid={`setup-provider-row-${connection.id}`}
              data-provider={group.key}
            >
              <span
                className={`setup-row__icon ${pending ? 'setup-row__icon--warn' : 'setup-row__icon--ok'}`}
              >
                {pending ? <AlertIcon /> : <CheckIcon />}
              </span>
              <div className="setup-row__body">
                <span className="setup-row__title">
                  {group.title}
                  {label !== 'default' ? ` · ${label}` : ''}
                  <span className="setup-chip setup-chip--inline">
                    {pending ? 'Sign-in needed' : methodLabel(group, connection)}
                  </span>
                </span>
                <span className="setup-row__detail">
                  credential {connection.credentialName}
                  {credentialExpiryLabel(connection) ? (
                    <>
                      {' · '}
                      <span data-testid={`setup-provider-expiry-${connection.id}`}>
                        {credentialExpiryLabel(connection)}
                      </span>
                    </>
                  ) : null}
                </span>
                {pending && credentialProblemLabel(connection) ? (
                  <span
                    className="setup-row__detail setup-row__detail--warn"
                    data-testid={`setup-provider-problem-${connection.id}`}
                  >
                    {credentialProblemLabel(connection)}
                  </span>
                ) : null}
                <div className="setup-form__actions">
                  {pending ? (
                    <button
                      type="button"
                      className="setup-btn"
                      onClick={() =>
                        startAdding({
                          groupKey: group.key,
                          mode: 'signin',
                          credentialName: connection.credentialName,
                        })
                      }
                      data-testid={`setup-provider-finish-${connection.id}`}
                    >
                      Finish sign-in
                    </button>
                  ) : (
                    <button
                      type="button"
                      className="setup-btn"
                      onClick={() => onTest(connection.id)}
                      disabled={testingId === connection.id}
                      data-testid={`setup-test-${connection.id}`}
                    >
                      {testingId === connection.id ? 'Testing…' : 'Test connection'}
                    </button>
                  )}
                  {testResults[connection.id] ? (
                    <TestOutcome slug={group.key} result={testResults[connection.id]!} />
                  ) : null}
                </div>
              </div>
            </div>
          );
        })}
      </div>

      <div className="setup-form__actions">
        <button
          type="button"
          className="setup-btn setup-btn--primary"
          onClick={() => startAdding({})}
          disabled={loading || groups.length === 0}
          data-testid="setup-provider-add"
        >
          + Add {noun.one}
        </button>
      </div>

      {adding ? (
        <AddProviderDialog
          key={adding.id}
          open={dialogOpen}
          onOpenChange={(open) => {
            if (!open) setAdding(null);
          }}
          onSelection={(selection) =>
            setAdding((prev) => (prev ? { ...prev, ...selection } : prev))
          }
          noun={noun.one}
          groups={groups}
          initialGroupKey={adding.groupKey}
          initialMode={adding.mode as ConnectMode | null}
          initialCredentialName={
            adding.mode === 'signin' &&
            adding.credentialName &&
            rows.some(
              (row) => row.pending && row.connection.credentialName === adding.credentialName,
            )
              ? adding.credentialName
              : null
          }
          connections={connections}
          connectingSlug={connectingSlug}
          connectErrorSlug={connectErrorSlug}
          connectError={connectError}
          testingId={testingId}
          testResults={testResults}
          onConnect={onConnect}
          onTest={onTest}
        />
      ) : null}
    </div>
  );
}
