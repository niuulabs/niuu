import { useMemo, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useOptionalService, useService } from '@niuulabs/plugin-sdk';
import { SessionChat, type ISessionHistoryLocator } from '@niuulabs/ui';
import { ExternalLink, GitFork, MessageSquarePlus } from 'lucide-react';
import type { IVolundrService } from '../../ports/IVolundrService';
import type { VolundrSession } from '../../models/volundr.model';
import { SessionForkError } from '../../domain/session';
import { useSkuldChat } from '../hooks/useSkuldChat';
import { normalizeSessionUrl } from '../liveSessionTransport';
import { slugifySessionName } from '../launchWizardModel';
import { errorText } from '../errorText';
import { isSessionWorking } from './sessionHeaderModel';
import './SideChatPane.css';

/** The longest name Forge accepts for a session (an RFC 1123 label). */
const SESSION_NAME_MAX = 63;
const SIDE_SUFFIX = '-side';

/** How a side chat reads in the dock and the session list, as Lexi's: "↳ <name> · side". */
export function sideChatLabel(parentName: string): string {
  return `↳ ${parentName} · side`;
}

/** The side chat's session name: Forge names are RFC 1123 labels, so "<name>-side". */
export function sideChatName(parentName: string): string {
  const stem = slugifySessionName(parentName)
    .slice(0, SESSION_NAME_MAX - SIDE_SUFFIX.length)
    .replace(/-+$/, '');
  return `${stem || 'session'}${SIDE_SUFFIX}`;
}

/** Polls the child while it boots; a running or settled child changes through its own events. */
const BOOT_POLL_MS = 3_000;

/**
 * Lexi's side chat in the right dock: a second conversation beside this one. "Fork with
 * context" reforges this session into a child on the same host (same repository, branch and
 * model) and shows it here; "New chat" opens the launch flow in the same workspace. Either way
 * it is a session of its own, also in the list.
 */
export function SideChatPane({
  session,
  childId,
  readOnly = false,
  onForked,
  onNewChat,
  onOpenSession,
}: {
  /** The session this side chat sits beside. */
  session: VolundrSession;
  /** The side chat's session, once forked. */
  childId: string | null;
  readOnly?: boolean;
  onForked: (childId: string) => void;
  /** Opens the launch flow in this workspace; absent when the launch flow cannot take it. */
  onNewChat?: () => void;
  onOpenSession: (id: string) => void;
}) {
  const volundr = useService<IVolundrService>('volundr');
  const queryClient = useQueryClient();
  const [forking, setForking] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fork = async () => {
    setForking(true);
    setError(null);
    try {
      const child = await volundr.forkSession(session.id, sideChatName(session.name));
      onForked(child.id);
    } catch (failure) {
      // A child that exists is still shown, with what failed after it was created.
      if (failure instanceof SessionForkError) onForked(failure.childId);
      setError(errorText(failure, 'The side chat could not be forked'));
    } finally {
      setForking(false);
      void queryClient.invalidateQueries({ queryKey: ['volundr', 'session-list'] });
    }
  };

  return (
    <div className="forge-side-chat" data-testid="side-chat-pane">
      <div className="forge-side-chat__head">
        <span className="forge-side-chat__name">
          <b>{sideChatLabel(session.name)}</b> — its own session, also in the list
        </span>
        {childId ? (
          <button
            type="button"
            className="forge-side-chat__open"
            onClick={() => onOpenSession(childId)}
            title="Open the side chat as the main session"
          >
            <ExternalLink aria-hidden="true" />
            Open
          </button>
        ) : null}
      </div>
      {error ? (
        <p role="alert" className="forge-side-chat__error">
          {error}
        </p>
      ) : null}
      {childId ? (
        <SideChatThread childId={childId} instanceId={session.instanceId} />
      ) : (
        <div className="forge-side-chat__start">
          <p>
            Fork this conversation into a child session on the same host, or start a fresh session
            in the same workspace.
          </p>
          <div className="forge-side-chat__actions">
            <button
              type="button"
              className="forge-side-chat__action forge-side-chat__action--primary"
              disabled={readOnly || forking}
              onClick={() => void fork()}
              title="A child session with this one's repository, branch and model"
            >
              <GitFork aria-hidden="true" />
              {forking ? 'Forking…' : 'Fork with context'}
            </button>
            <button
              type="button"
              className="forge-side-chat__action"
              disabled={readOnly || !onNewChat}
              onClick={onNewChat}
              title={
                onNewChat
                  ? 'Open the launch flow in this workspace'
                  : 'The launch flow takes a Git workspace; this session works in a local folder'
              }
            >
              <MessageSquarePlus aria-hidden="true" />
              New chat
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

function SideChatThread({ childId, instanceId }: { childId: string; instanceId?: string }) {
  const volundr = useService<IVolundrService>('volundr');
  const childQuery = useQuery({
    queryKey: ['volundr', 'raw-session', childId, instanceId ?? null],
    queryFn: ({ signal }) => volundr.getSession(childId, { instanceId, signal }),
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status === 'running' || status === 'stopped' || status === 'failed'
        ? false
        : BOOT_POLL_MS;
    },
  });
  const child = childQuery.data;
  const chatEndpoint = normalizeSessionUrl(
    child?.status === 'running' ? (child.chatEndpoint ?? null) : null,
  );
  const historyLocator = useOptionalService<ISessionHistoryLocator>('forge.history');
  const historyEndpoint = useMemo(
    () => historyLocator?.historyEndpoint(chatEndpoint) ?? null,
    [chatEndpoint, historyLocator],
  );
  const chat = useSkuldChat(chatEndpoint, { historyEndpoint });

  if (childQuery.isError) {
    return (
      <p role="alert" className="forge-side-chat__error">
        {errorText(childQuery.error, 'The side chat could not be read')}
      </p>
    );
  }
  if (!child) return <p className="forge-side-chat__status">Loading the side chat…</p>;
  if (child.status === 'failed' || child.status === 'error') {
    return (
      <p role="alert" className="forge-side-chat__error">
        The side chat failed{child.error ? `: ${child.error}` : '.'}
      </p>
    );
  }
  if (!chatEndpoint) {
    return (
      <p className="forge-side-chat__status" role="status">
        {child.status === 'stopped' || child.status === 'archived'
          ? 'The side chat has stopped. Open it to start it again.'
          : 'The side chat is starting…'}
      </p>
    );
  }
  return (
    <SessionChat
      className="forge-side-chat__chat"
      showToolbar={false}
      showDisplayControls={false}
      showWorkersStrip={false}
      showInternalToggle={false}
      messages={chat.messages}
      streamingContent={chat.streamingContent}
      streamingParts={chat.streamingParts}
      streamingModel={chat.streamingModel}
      connected={chat.connected}
      historyLoaded={chat.historyLoaded}
      historyError={chat.historyError}
      onRetryHistory={chat.retryHistory}
      participants={chat.participants}
      pendingPermissions={chat.pendingPermissions}
      pendingInputRequests={chat.pendingInputRequests}
      onPermissionRespond={chat.respondToPermission}
      onInputRespond={chat.respondToInput}
      onInputDismiss={chat.dismissInputRequest}
      availableCommands={chat.availableCommands}
      capabilities={chat.capabilities}
      chatEndpoint={chatEndpoint}
      historyEndpoint={historyEndpoint}
      sessionName={child.name}
      onSend={chat.sendMessage}
      onStop={chat.sendInterrupt}
      working={isSessionWorking(child.activityState)}
    />
  );
}
