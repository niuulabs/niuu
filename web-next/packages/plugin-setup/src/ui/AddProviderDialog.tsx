import { useState } from 'react';
import { Dialog, DialogContent } from '@niuulabs/ui';
import {
  availableModes,
  connectionForSlug,
  entryConnected,
  signInNeedsApp,
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

export interface AddProviderDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** What the user is adding, in the step's words: "provider", "Git host", "tracker". */
  noun: string;
  /** Every provider of the step; the dialog works out what each can still add. */
  groups: ProviderGroup[];
  /** Pre-selected provider, e.g. when finishing a pending sign-in. */
  initialGroupKey?: string | null;
  initialMode?: ConnectMode | null;
  /** Called whenever the chosen provider or method changes, so the parent knows what to watch. */
  onSelection?: (groupKey: string | null, mode: ConnectMode | null) => void;
  connections: IntegrationConnection[] | undefined;
  connectingSlug: string | null;
  connectErrorSlug: string | null;
  connectError: Error | null;
  testingId: string | null;
  testResults: Record<string, IntegrationTestResult>;
  onConnect: (input: ConnectIntegrationInput) => void;
  onTest: (connectionId: string) => void;
}

/** Modes a provider still offers, sign-in first. */
export function modesFor(
  group: ProviderGroup,
  connections: IntegrationConnection[] | undefined = undefined,
): ConnectMode[] {
  return availableModes(group, connections);
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
 * Add one provider in three small steps: which one, how (sign in or key),
 * then the sign-in card or the key form. The parent closes the dialog once
 * the connection shows up, so the flow never needs to know it succeeded.
 */
export function AddProviderDialog({
  open,
  onOpenChange,
  noun,
  groups,
  initialGroupKey = null,
  initialMode = null,
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
  const group = groups.find((candidate) => candidate.key === chosenKey) ?? null;
  const modes = group ? availableModes(group, connections) : [];
  const mode: ConnectMode | null = group
    ? modes.length === 1
      ? (modes[0] ?? null)
      : chosenMode
    : null;
  const addable = groups.filter((candidate) => availableModes(candidate, connections).length > 0);

  const select = (key: string | null, next: ConnectMode | null) => {
    setChosenKey(key);
    setChosenMode(next);
    onSelection?.(key, next);
  };
  const reset = () => select(null, null);
  const handleOpenChange = (next: boolean) => {
    if (!next) reset();
    onOpenChange(next);
  };

  const title = !group ? `Add ${noun}` : mode ? group.title : `${group.title}: how?`;

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

          {!group ? (
            addable.length === 0 ? (
              <div className="setup-note" data-testid="setup-add-none-left">
                Everything in the catalog for this step is already connected.
              </div>
            ) : (
              groups.map((candidate) => {
                const open = availableModes(candidate, connections);
                const signedIn = entryConnected(candidate.signInEntry, connections);
                const keyed = entryConnected(candidate.keyEntry, connections);
                return (
                  <button
                    key={candidate.key}
                    type="button"
                    className="setup-option"
                    onClick={() => select(candidate.key, null)}
                    disabled={open.length === 0}
                    data-testid={`setup-add-pick-${candidate.key}`}
                  >
                    <span className="setup-option__body">
                      <span className="setup-option__title">{candidate.title}</span>
                      <span className="setup-option__desc">{candidate.description}</span>
                    </span>
                    <span className="setup-option__aside setup-chips">
                      {candidate.signInEntry ? (
                        signedIn ? (
                          <span className="setup-chip setup-chip--ok">
                            <CheckIcon size={12} /> Signed in
                          </span>
                        ) : (
                          <span className="setup-chip setup-chip--brand">Sign in</span>
                        )
                      ) : null}
                      {candidate.keyEntry ? (
                        keyed ? (
                          <span className="setup-chip setup-chip--ok">
                            <CheckIcon size={12} /> {candidate.keyLabel.replace(/^Use an? /, '')}
                          </span>
                        ) : (
                          <span className="setup-chip">{candidate.keyLabel}</span>
                        )
                      ) : null}
                    </span>
                  </button>
                );
              })
            )
          ) : null}

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
                <SignInCard
                  entry={group.signInEntry}
                  connection={
                    connections ? connectionForSlug(connections, group.signInEntry.slug) : undefined
                  }
                  headless
                />
              </div>
            )
          ) : null}

          {group && mode === 'key' && group.keyEntry ? (
            <div className="setup-pane">
              {modeIntro(group, 'key')}
              <IntegrationCard
                entry={group.keyEntry}
                connection={
                  connections ? connectionForSlug(connections, group.keyEntry.slug) : undefined
                }
                connecting={connectingSlug === group.keyEntry.slug}
                connectError={connectErrorSlug === group.keyEntry.slug ? connectError : null}
                testResult={undefined}
                testing={false}
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
