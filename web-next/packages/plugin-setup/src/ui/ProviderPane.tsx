import { useState } from 'react';
import {
  connectionForSlug,
  connectionNeedsSignIn,
  groupConnection,
  type ConnectIntegrationInput,
  type IntegrationConnection,
  type IntegrationTestResult,
  type ProviderGroup,
} from '../domain/setup';
import { IntegrationCard } from './IntegrationCard';
import { SignInCard } from './SignInCard';
import { AlertIcon, CheckIcon } from './icons';

export interface ProviderPaneProps {
  group: ProviderGroup;
  connections: IntegrationConnection[] | undefined;
  connectingSlug: string | null;
  connectErrorSlug: string | null;
  connectError: Error | null;
  testingId: string | null;
  testResults: Record<string, IntegrationTestResult>;
  onConnect: (input: ConnectIntegrationInput) => void;
  onTest: (connectionId: string) => void;
}

type Mode = 'signin' | 'key';

/**
 * One provider, all the ways to connect it: sign in with a subscription,
 * paste an API key, or a way that exists but not on this install.
 */
export function ProviderPane({
  group,
  connections,
  connectingSlug,
  connectErrorSlug,
  connectError,
  testingId,
  testResults,
  onConnect,
  onTest,
}: ProviderPaneProps) {
  const connected = groupConnection(group, connections);
  const modes: Mode[] = [];
  if (group.signInEntry) modes.push('signin');
  if (group.keyEntry) modes.push('key');
  const [chosen, setChosen] = useState<Mode | null>(null);
  const connectedMode: Mode | null =
    connected && group.signInEntry && connected.slug === group.signInEntry.slug
      ? 'signin'
      : connected && group.keyEntry && connected.slug === group.keyEntry.slug
        ? 'key'
        : null;
  const mode: Mode | null = connectedMode ?? chosen ?? modes[0] ?? null;

  const keyConnection =
    group.keyEntry && connections ? connectionForSlug(connections, group.keyEntry.slug) : undefined;
  const signInConnection =
    group.signInEntry && connections
      ? connectionForSlug(connections, group.signInEntry.slug)
      : undefined;
  const signInPending = signInConnection ? connectionNeedsSignIn(signInConnection) : false;

  return (
    <section className="setup-card" data-testid={`setup-provider-${group.key}`}>
      <div className="setup-card__head">
        <div>
          <h3 className="setup-card__title">{group.title}</h3>
          <p className="setup-card__desc">{group.description}</p>
        </div>
        {connected ? (
          <span className="setup-chip setup-chip--ok">
            <CheckIcon size={12} /> Connected
          </span>
        ) : signInPending ? (
          <span className="setup-chip setup-chip--warn">Sign-in needed</span>
        ) : (
          <span className="setup-chip">Not connected</span>
        )}
      </div>

      {modes.length > 1 && !connected ? (
        <div
          className="setup-segment"
          role="tablist"
          data-testid={`setup-provider-modes-${group.key}`}
        >
          {modes.map((candidate) => (
            <button
              key={candidate}
              type="button"
              role="tab"
              aria-selected={mode === candidate}
              className={`setup-segment__btn ${mode === candidate ? 'setup-segment__btn--active' : ''}`}
              onClick={() => setChosen(candidate)}
              data-testid={`setup-provider-mode-${group.key}-${candidate}`}
            >
              {candidate === 'signin' ? 'Sign in with your subscription' : 'Use an API key'}
            </button>
          ))}
        </div>
      ) : null}

      {mode === 'signin' && group.signInEntry ? (
        <SignInCard entry={group.signInEntry} connection={signInConnection} headless />
      ) : null}
      {mode === 'key' && group.keyEntry ? (
        <IntegrationCard
          entry={group.keyEntry}
          connection={keyConnection}
          connecting={connectingSlug === group.keyEntry.slug}
          connectError={connectErrorSlug === group.keyEntry.slug ? connectError : null}
          testResult={keyConnection ? testResults[keyConnection.id] : undefined}
          testing={keyConnection ? testingId === keyConnection.id : false}
          onConnect={onConnect}
          onTest={onTest}
          headless
        />
      ) : null}

      {group.unavailable.map((item) => (
        <div
          className="setup-note"
          key={item.label}
          data-testid={`setup-provider-unavailable-${group.key}`}
        >
          <AlertIcon size={13} /> {item.label}: {item.reason}
        </div>
      ))}
    </section>
  );
}
