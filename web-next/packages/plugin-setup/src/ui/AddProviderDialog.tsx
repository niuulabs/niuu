import { useState } from 'react';
import { Dialog, DialogContent, Field, Input } from '@niuulabs/ui';
import {
  accountCredentialName,
  availableModes,
  connectionLabel,
  groupConnections,
  signInNeedsApp,
  type CatalogEntry,
  type ConnectIntegrationInput,
  type ConnectMode,
  type IntegrationConnection,
  type IntegrationTestResult,
  type ProviderGroup,
} from '../domain/setup';
import { IntegrationCard } from './IntegrationCard';
import { OAuthAppForm } from './OAuthAppForm';
import { SignInCard } from './SignInCard';
import { BackIcon, CheckIcon } from './icons';

export type { ConnectMode } from '../domain/setup';

export interface AddProviderSelection {
  groupKey: string | null;
  mode: ConnectMode | null;
  /** The credential the connection being added will use; the parent watches for it. */
  credentialName: string | null;
}

export interface AddProviderDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** What the user is adding, in the step's words: "provider", "Git host", "tracker". */
  noun: string;
  /** Every provider of the step; each can be added as many times as there are accounts. */
  groups: ProviderGroup[];
  /** Pre-selected provider, e.g. when finishing a pending sign-in. */
  initialGroupKey?: string | null;
  initialMode?: ConnectMode | null;
  /** The existing connection to finish signing in; skips the account name. */
  initialCredentialName?: string | null;
  /** Called whenever the chosen provider, method or account changes. */
  onSelection?: (selection: AddProviderSelection) => void;
  connections: IntegrationConnection[] | undefined;
  connectingSlug: string | null;
  connectErrorSlug: string | null;
  connectError: Error | null;
  testingId: string | null;
  testResults: Record<string, IntegrationTestResult>;
  onConnect: (input: ConnectIntegrationInput) => void;
  onTest: (connectionId: string) => void;
}

/** Modes a provider offers, sign-in first. */
export function modesFor(group: ProviderGroup): ConnectMode[] {
  return availableModes(group);
}

function entryFor(group: ProviderGroup, mode: ConnectMode | null): CatalogEntry | undefined {
  if (!mode) return undefined;
  return mode === 'signin' ? group.signInEntry : group.keyEntry;
}

function modeIntro(group: ProviderGroup, mode: ConnectMode) {
  if (mode === 'signin') {
    return (
      <div className="setup-pane__intro" data-testid="setup-add-intro-signin">
        <p>
          Niuu starts {group.title}&apos;s own sign-in on this machine and shows you the link.
          Nothing to copy except a short code.
        </p>
        <ol className="setup-pane__steps">
          <li>Open the link and enter the code.</li>
          <li>Approve the sign-in in your browser.</li>
          <li>Come back here. This page updates by itself.</li>
        </ol>
      </div>
    );
  }
  return (
    <div className="setup-pane__intro" data-testid="setup-add-intro-key">
      <p>
        Paste a key you created in {group.title}&apos;s settings
        {group.keyHelpUrl ? (
          <>
            {' '}
            (
            <a href={group.keyHelpUrl} target="_blank" rel="noreferrer noopener">
              open them
            </a>
            )
          </>
        ) : null}
        . Niuu stores it encrypted on this machine and only ever sends it to {group.title}. You can
        test it right after.
      </p>
    </div>
  );
}

/**
 * Add one account in three small steps: which provider, how (sign in or key),
 * then the sign-in card or the key form. A provider can be added as often
 * as there are accounts; each one gets a name once the first exists. The
 * parent closes the dialog once the new connection shows up.
 */
export function AddProviderDialog({
  open,
  onOpenChange,
  noun,
  groups,
  initialGroupKey = null,
  initialMode = null,
  initialCredentialName = null,
  onSelection,
  connections,
  connectingSlug,
  connectErrorSlug,
  connectError,
  onConnect,
  onTest,
}: AddProviderDialogProps) {
  const [chosenKey, setChosenKey] = useState<string | null>(initialGroupKey);
  const [chosenMode, setChosenMode] = useState<ConnectMode | null>(initialMode);
  const [accountName, setAccountName] = useState('');
  const group = groups.find((candidate) => candidate.key === chosenKey) ?? null;
  const modes = group ? availableModes(group) : [];
  const mode: ConnectMode | null = group
    ? modes.length === 1
      ? (modes[0] ?? null)
      : chosenMode
    : null;
  const entry = group ? entryFor(group, mode) : undefined;
  const existing = group ? groupConnections(group, connections) : [];
  const finishing = initialCredentialName !== null;

  const nameFor = (candidate: CatalogEntry | undefined, next: ConnectMode | null, name: string) =>
    candidate
      ? finishing
        ? initialCredentialName
        : accountCredentialName(candidate, next ?? 'key', name)
      : null;
  const credentialName = nameFor(entry, mode, accountName);
  const taken =
    !finishing &&
    credentialName !== null &&
    existing.some((connection) => connection.credentialName === credentialName);
  const finishingConnection = finishing
    ? existing.find((connection) => connection.credentialName === initialCredentialName)
    : undefined;

  const select = (key: string | null, next: ConnectMode | null, name: string = accountName) => {
    setChosenKey(key);
    setChosenMode(next);
    setAccountName(name);
    const chosen = groups.find((candidate) => candidate.key === key);
    const chosenModes = chosen ? availableModes(chosen) : [];
    const effective = chosenModes.length === 1 ? (chosenModes[0] ?? null) : next;
    onSelection?.({
      groupKey: key,
      mode: effective,
      credentialName: nameFor(chosen ? entryFor(chosen, effective) : undefined, effective, name),
    });
  };
  const reset = () => select(null, null, '');
  const handleOpenChange = (next: boolean) => {
    if (!next) reset();
    onOpenChange(next);
  };

  const title = !group ? `Add ${noun}` : mode ? group.title : `${group.title}: how?`;
  const accountField =
    group && entry && !finishing ? (
      <Field
        label={existing.length > 0 ? 'Name for this account' : 'Name for this account (optional)'}
        hint={
          existing.length > 0
            ? `Already connected: ${[
                ...new Set(existing.map((connection) => connectionLabel(connection, group))),
              ].join(', ')}. Give this one its own name, like work or personal.`
            : 'Leave empty for the default. Handy when you add a second account later.'
        }
        error={taken ? 'That name is already in use for this provider.' : undefined}
      >
        <Input
          autoComplete="off"
          value={accountName}
          onChange={(event) => select(group.key, mode, event.target.value)}
          data-testid="setup-add-account-name"
        />
      </Field>
    ) : null;

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent title={title} className="setup-dialog">
        <div className="setup-col" data-testid="setup-add-dialog">
          {group ? (
            <button
              type="button"
              className="setup-btn setup-btn--ghost setup-dialog__back"
              onClick={() => (mode && modes.length > 1 ? select(group.key, null) : reset())}
              data-testid="setup-add-back"
            >
              <BackIcon /> Back
            </button>
          ) : null}

          {!group
            ? groups.map((candidate) => {
                const count = groupConnections(candidate, connections).length;
                return (
                  <button
                    key={candidate.key}
                    type="button"
                    className="setup-option"
                    onClick={() => select(candidate.key, null)}
                    data-testid={`setup-add-pick-${candidate.key}`}
                  >
                    <span className="setup-option__body">
                      <span className="setup-option__title">{candidate.title}</span>
                      <span className="setup-option__desc">{candidate.description}</span>
                    </span>
                    <span className="setup-option__aside setup-chips">
                      {count > 0 ? (
                        <span className="setup-chip setup-chip--ok">
                          <CheckIcon size={12} /> {count === 1 ? '1 account' : `${count} accounts`}
                        </span>
                      ) : null}
                      {candidate.signInEntry ? (
                        <span className="setup-chip setup-chip--brand">Sign in</span>
                      ) : null}
                      {candidate.keyEntry ? (
                        <span className="setup-chip">{candidate.keyLabel}</span>
                      ) : null}
                    </span>
                  </button>
                );
              })
            : null}

          {group && !mode
            ? modes.map((candidate) => (
                <button
                  key={candidate}
                  type="button"
                  className="setup-option"
                  onClick={() => select(group.key, candidate)}
                  data-testid={`setup-add-mode-${candidate}`}
                >
                  <span className="setup-option__body">
                    <span className="setup-option__title">
                      {candidate === 'signin' ? group.signInLabel : group.keyLabel}
                    </span>
                    <span className="setup-option__desc">
                      {candidate === 'signin'
                        ? 'Approve in your browser; nothing to copy except a short code.'
                        : 'Paste a key you created in the provider’s settings.'}
                    </span>
                  </span>
                </button>
              ))
            : null}

          {group && mode === 'signin' && group.signInEntry ? (
            signInNeedsApp(group.signInEntry) ? (
              <OAuthAppForm entry={group.signInEntry} />
            ) : (
              <div className="setup-pane">
                {modeIntro(group, 'signin')}
                {accountField}
                <SignInCard
                  entry={group.signInEntry}
                  connection={finishingConnection}
                  credentialName={credentialName ?? undefined}
                  disabled={taken}
                  headless
                />
              </div>
            )
          ) : null}

          {group && mode === 'key' && group.keyEntry ? (
            <div className="setup-pane">
              {modeIntro(group, 'key')}
              {accountField}
              <IntegrationCard
                entry={group.keyEntry}
                connection={undefined}
                credentialName={credentialName ?? undefined}
                connecting={connectingSlug === group.keyEntry.slug}
                connectError={connectErrorSlug === group.keyEntry.slug ? connectError : null}
                testResult={undefined}
                testing={false}
                disabled={taken}
                onConnect={onConnect}
                onTest={onTest}
                headless
              />
            </div>
          ) : null}
        </div>
      </DialogContent>
    </Dialog>
  );
}
