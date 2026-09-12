import { useState } from 'react';
import { Dialog, DialogContent } from '@niuulabs/ui';
import {
  connectionForSlug,
  signInUnavailableReason,
  type ConnectIntegrationInput,
  type IntegrationConnection,
  type IntegrationTestResult,
  type ProviderGroup,
} from '../domain/setup';
import { IntegrationCard } from './IntegrationCard';
import { SignInCard } from './SignInCard';
import { AlertIcon, BackIcon } from './icons';

export type ConnectMode = 'signin' | 'key';

export interface AddProviderDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** What the user is adding, in the step's words: "provider", "Git host", "tracker". */
  noun: string;
  /** Providers that can still be added (or whose sign-in is unfinished). */
  groups: ProviderGroup[];
  /** Pre-selected provider, e.g. when finishing a pending sign-in. */
  initialGroupKey?: string | null;
  initialMode?: ConnectMode | null;
  /** Called when the user picks a provider, so the parent knows what to watch. */
  onPick?: (groupKey: string | null) => void;
  connections: IntegrationConnection[] | undefined;
  connectingSlug: string | null;
  connectErrorSlug: string | null;
  connectError: Error | null;
  testingId: string | null;
  testResults: Record<string, IntegrationTestResult>;
  onConnect: (input: ConnectIntegrationInput) => void;
  onTest: (connectionId: string) => void;
}

/** Modes a provider offers, sign-in first unless it cannot run here. */
export function modesFor(group: ProviderGroup): ConnectMode[] {
  const modes: ConnectMode[] = [];
  if (group.signInEntry) modes.push('signin');
  if (group.keyEntry) modes.push('key');
  if (group.signInEntry?.signInAvailable === false && group.keyEntry) modes.reverse();
  return modes;
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
  onPick,
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
  const modes = group ? modesFor(group) : [];
  const mode: ConnectMode | null = group
    ? modes.length === 1
      ? (modes[0] ?? null)
      : chosenMode
    : null;

  const pick = (key: string | null) => {
    setChosenKey(key);
    onPick?.(key);
  };
  const reset = () => {
    pick(null);
    setChosenMode(null);
  };
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
              onClick={() => (mode && modes.length > 1 ? setChosenMode(null) : reset())}
              data-testid="setup-add-back"
            >
              <BackIcon /> Back
            </button>
          ) : null}

          {!group ? (
            groups.length === 0 ? (
              <div className="setup-note" data-testid="setup-add-none-left">
                Everything in the catalog for this step is already connected.
              </div>
            ) : (
              groups.map((candidate) => (
                <button
                  key={candidate.key}
                  type="button"
                  className="setup-option"
                  onClick={() => pick(candidate.key)}
                  data-testid={`setup-add-pick-${candidate.key}`}
                >
                  <span className="setup-option__body">
                    <span className="setup-option__title">{candidate.title}</span>
                    <span className="setup-option__desc">{candidate.description}</span>
                  </span>
                  <span className="setup-option__aside setup-chips">
                    {candidate.signInEntry ? (
                      <span className="setup-chip setup-chip--brand">Sign in</span>
                    ) : null}
                    {candidate.keyEntry ? (
                      <span className="setup-chip">{candidate.keyLabel}</span>
                    ) : null}
                  </span>
                </button>
              ))
            )
          ) : null}

          {group && !mode
            ? modes.map((candidate) => (
                <button
                  key={candidate}
                  type="button"
                  className="setup-option"
                  onClick={() => setChosenMode(candidate)}
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
            group.signInEntry.signInAvailable === false ? (
              <div
                className="setup-note setup-note--warn"
                data-testid={`setup-signin-unavailable-${group.signInEntry.slug}`}
              >
                <AlertIcon size={13} /> {signInUnavailableReason(group.signInEntry)}
              </div>
            ) : (
              <SignInCard
                entry={group.signInEntry}
                connection={
                  connections ? connectionForSlug(connections, group.signInEntry.slug) : undefined
                }
                headless
              />
            )
          ) : null}

          {group && mode === 'key' && group.keyEntry ? (
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
          ) : null}
        </div>
      </DialogContent>
    </Dialog>
  );
}
