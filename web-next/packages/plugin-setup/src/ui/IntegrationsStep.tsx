import { useEffect, useRef, useState } from 'react';
import {
  availableModes,
  connectionForSlug,
  entryConnected,
  entryForMode,
  connectionNeedsSignIn,
  credentialExpiryLabel,
  credentialProblemLabel,
  groupConnection,
  providerGroups,
  type CatalogEntry,
  type ConnectIntegrationInput,
  type IntegrationConnection,
  type IntegrationTestResult,
  type ProviderGroup,
  type WizardStep,
} from '../domain/setup';
import { AddProviderDialog, type ConnectMode } from './AddProviderDialog';
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

/** The connection a row is about: the usable one, or the one waiting for sign-in. */
function rowConnection(
  group: ProviderGroup,
  connections: IntegrationConnection[] | undefined,
): { connection: IntegrationConnection; pending: boolean } | null {
  const usable = groupConnection(group, connections);
  if (usable) return { connection: usable, pending: false };
  if (!connections) return null;
  for (const entry of [group.signInEntry, group.keyEntry]) {
    if (!entry) continue;
    const connection = connectionForSlug(connections, entry.slug);
    if (connection && connectionNeedsSignIn(connection)) return { connection, pending: true };
  }
  return null;
}

function methodLabel(group: ProviderGroup, connection: IntegrationConnection): string {
  const signIn = group.signInEntry;
  if (signIn) {
    const sharedSlug = group.keyEntry?.slug === signIn.slug;
    const signedIn = sharedSlug
      ? connection.credentialName === signIn.credentialEnrollment?.defaultCredentialName
      : connection.slug === signIn.slug;
    if (signedIn) return 'Signed in';
  }
  return group.keyLabel.replace(/^Use an? /, '').replace(/^./, (c) => c.toUpperCase());
}

/**
 * What is connected for this step, one row per provider, plus an "Add"
 * button that opens the provider → method → connect dialog.
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
  const [adding, setAdding] = useState<{
    id: number;
    key: string | null;
    mode: ConnectMode | null;
  } | null>(null);

  const rows = groups
    .map((group) => ({ group, row: rowConnection(group, connections) }))
    .filter((item): item is { group: ProviderGroup; row: NonNullable<typeof item.row> } =>
      Boolean(item.row),
    );
  const addable = groups.filter((group) => availableModes(group, connections).length > 0);
  const addingGroup = adding?.key ? groups.find((g) => g.key === adding.key) : undefined;
  // The dialog closes itself the moment the method being added becomes usable,
  // and the new connection is checked right away so a wrong scope or a dead
  // key shows up here, not in a session.
  const target = addingGroup && adding?.mode ? entryForMode(addingGroup, adding.mode) : undefined;
  const added =
    target && connections && entryConnected(target, connections)
      ? connectionForSlug(connections, target.slug)
      : undefined;
  const dialogOpen = adding !== null && added === undefined;
  const addedId = added?.id ?? null;
  const checkedRef = useRef<string | null>(null);
  useEffect(() => {
    if (addedId === null || checkedRef.current === addedId) return;
    checkedRef.current = addedId;
    onTest(addedId);
  }, [addedId, onTest]);

  return (
    <div className="setup-col" data-testid={`setup-step-${step.id}`}>
      {error ? (
        <div className="setup-error" role="alert">
          Could not load the integrations catalog: {error.message}
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
                Add one to get started. You can add as many as you use, and change them later in
                Settings.
              </span>
            </div>
          </div>
        ) : null}
        {rows.map(({ group, row }) => (
          <div
            className="setup-row"
            key={group.key}
            data-testid={`setup-provider-row-${group.key}`}
          >
            <span
              className={`setup-row__icon ${row.pending ? 'setup-row__icon--warn' : 'setup-row__icon--ok'}`}
            >
              {row.pending ? <AlertIcon /> : <CheckIcon />}
            </span>
            <div className="setup-row__body">
              <span className="setup-row__title">
                {group.title}
                <span className="setup-chip setup-chip--inline">
                  {row.pending ? 'Sign-in needed' : methodLabel(group, row.connection)}
                </span>
              </span>
              <span className="setup-row__detail">
                credential {row.connection.credentialName}
                {credentialExpiryLabel(row.connection) ? (
                  <>
                    {' · '}
                    <span data-testid={`setup-provider-expiry-${group.key}`}>
                      {credentialExpiryLabel(row.connection)}
                    </span>
                  </>
                ) : null}
              </span>
              {row.pending && credentialProblemLabel(row.connection) ? (
                <span
                  className="setup-row__detail setup-row__detail--warn"
                  data-testid={`setup-provider-problem-${group.key}`}
                >
                  {credentialProblemLabel(row.connection)}
                </span>
              ) : null}
              <div className="setup-form__actions">
                {row.pending ? (
                  <button
                    type="button"
                    className="setup-btn"
                    onClick={() => setAdding({ id: Date.now(), key: group.key, mode: 'signin' })}
                    data-testid={`setup-provider-finish-${group.key}`}
                  >
                    Finish sign-in
                  </button>
                ) : (
                  <button
                    type="button"
                    className="setup-btn"
                    onClick={() => onTest(row.connection.id)}
                    disabled={testingId === row.connection.id}
                    data-testid={`setup-test-${group.key}`}
                  >
                    {testingId === row.connection.id ? 'Testing…' : 'Test connection'}
                  </button>
                )}
                {testResults[row.connection.id] ? (
                  <TestOutcome slug={group.key} result={testResults[row.connection.id]!} />
                ) : null}
              </div>
            </div>
          </div>
        ))}
      </div>

      <div className="setup-form__actions">
        <button
          type="button"
          className="setup-btn setup-btn--primary"
          onClick={() => setAdding({ id: Date.now(), key: null, mode: null })}
          disabled={loading || groups.length === 0}
          data-testid="setup-provider-add"
        >
          + Add {noun.one}
        </button>
        {!loading && groups.length > 0 && addable.length === 0 ? (
          <span className="setup-note">Every {noun.one} in the catalog is connected.</span>
        ) : null}
      </div>

      {adding ? (
        <AddProviderDialog
          key={adding.id}
          open={dialogOpen}
          onOpenChange={(open) => {
            if (!open) setAdding(null);
          }}
          onSelection={(key, mode) => setAdding((prev) => (prev ? { ...prev, key, mode } : prev))}
          noun={noun.one}
          groups={groups}
          initialGroupKey={adding.key}
          initialMode={adding.mode}
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
