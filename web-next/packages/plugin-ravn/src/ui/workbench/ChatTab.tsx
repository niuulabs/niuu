import { useMemo, useState } from 'react';
import { Play, Plus, RotateCw, X } from 'lucide-react';
import { Dialog, DialogContent, LoadingState, normalizeSessionUrl, relTime } from '@niuulabs/ui';
import type { Ravn } from '../../domain/ravn';
import type { Session } from '../../domain/session';
import {
  canCreateResidentSession,
  canDeleteResidentSession,
  canListResidentSessions,
  canRestartResident,
  canResumeResident,
  nameForRavn,
} from '../../domain/residentActions';
import { ravnLifeState, type RavnLifeState } from '../../application/ravnWorkbench';
import {
  belongsToRavn,
  conversationTitle,
  pickConversation,
  sortConversations,
} from '../../application/conversations';
import {
  useCreateResidentSession,
  useDeleteResidentSession,
  useResidentProfiles,
  useResidentSessions,
} from '../hooks/useResidentControl';
import { useMessages, useSessions } from '../hooks/useSessions';
import { ResidentModelSelect } from '../ResidentModelSelect';
import { MessageRow } from '../MessageRow';
import { LiveChat } from './LiveChat';
import { errorText } from './errorText';
import { RavnMark } from './RavnMark';

export interface ChatTabProps {
  ravn: Ravn;
  requestedSessionId: string | null;
  onSelectSession: (sessionId: string | null) => void;
  onLifecycle: (action: 'restart' | 'resume') => void;
  onShowActivity: () => void;
  lifecyclePending: boolean;
}

const NOT_RUNNING_COPY: Partial<Record<RavnLifeState, { title: string; text: string }>> = {
  failed: {
    title: "isn't running",
    text: 'There is nobody on the other end until the runtime is healthy again. The banner above says why.',
  },
  starting: {
    title: 'is starting',
    text: 'Chat opens here on its own as soon as the runtime reports ready.',
  },
  removing: { title: 'is being removed', text: 'Its conversations go with it.' },
  suspended: { title: 'is suspended', text: 'Resume it to talk again.' },
  stopped: {
    title: 'has stopped',
    text: 'Earlier conversations stay readable below when there are any.',
  },
  idle: { title: 'is idle', text: 'It has no live conversation right now.' },
};

function NotRunning({
  ravn,
  onLifecycle,
  onShowActivity,
  lifecyclePending,
}: Pick<ChatTabProps, 'ravn' | 'onLifecycle' | 'onShowActivity' | 'lifecyclePending'>) {
  const state = ravnLifeState(ravn);
  const copy = NOT_RUNNING_COPY[state] ?? {
    title: 'has no chat',
    text: 'This runtime reports no chat endpoint.',
  };
  const showLogs = Boolean(ravn.managed && ravn.capabilities?.includes('logs'));
  return (
    <div className="rw-empty" data-testid="ravn-chat-unavailable">
      <div className="rw-empty__inner">
        <RavnMark ravn={ravn} size="lg" />
        <h3 className="rw-empty__title">
          {nameForRavn(ravn)} {copy.title}
        </h3>
        <p className="rw-empty__text">{copy.text}</p>
        <div className="rw-empty__acts">
          {state === 'failed' && showLogs && (
            <button type="button" className="rw-btn" onClick={onShowActivity}>
              View logs
            </button>
          )}
          {state === 'failed' && canRestartResident(ravn) && (
            <button
              type="button"
              className="rw-btn rw-btn--primary"
              onClick={() => onLifecycle('restart')}
              disabled={lifecyclePending}
            >
              <RotateCw size={14} aria-hidden="true" />
              Restart
            </button>
          )}
          {canResumeResident(ravn) && (
            <button
              type="button"
              className="rw-btn rw-btn--primary"
              onClick={() => onLifecycle('resume')}
              disabled={lifecyclePending}
            >
              <Play size={14} aria-hidden="true" />
              Resume
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

function Transcript({ ravn, session }: { ravn: Ravn; session: Session }) {
  const messages = useMessages(session.id, true, session.instanceId, ravn.id);
  if (messages.isLoading) return <LoadingState label="Loading conversation…" />;
  if (messages.isError) {
    return (
      <p className="rw-inline-error" role="alert">
        {errorText(messages.error, 'Failed to load the conversation')}
      </p>
    );
  }
  return (
    <>
      <div className="rw-transcript__note">
        This conversation is {session.status} — the transcript is read-only.
      </div>
      <div className="rw-transcript" role="log" aria-label="Conversation transcript">
        {(messages.data ?? []).length === 0 ? (
          <p className="rw-muted">No messages recorded.</p>
        ) : (
          (messages.data ?? []).map((message) => <MessageRow key={message.id} message={message} />)
        )}
      </div>
    </>
  );
}

function NewConversationDialog({
  ravn,
  open,
  onOpenChange,
  onCreated,
}: {
  ravn: Ravn;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onCreated: (session: Session) => void;
}) {
  const [title, setTitle] = useState('');
  const [model, setModel] = useState(ravn.model);
  const profiles = useResidentProfiles(open);
  const create = useCreateResidentSession(ravn);
  const profile = profiles.data?.find(
    (candidate) => candidate.id === ravn.profileId && candidate.instanceId === ravn.instanceId,
  );
  const allowedModels = profile?.allowedModels ?? [];
  const selectedModel = allowedModels.includes(model) ? model : (profile?.defaultModel ?? '');

  async function submit() {
    if (!title.trim()) return;
    let session: Session;
    try {
      session = await create.mutateAsync({
        title: title.trim(),
        ...(selectedModel && { model: selectedModel }),
      });
    } catch {
      return;
    }
    setTitle('');
    onOpenChange(false);
    onCreated(session);
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent title="New conversation">
        <form
          className="rw-form"
          onSubmit={(event) => {
            event.preventDefault();
            void submit();
          }}
        >
          <label className="rw-field">
            <span className="rw-field__label">Title</span>
            <input
              className="rw-input"
              value={title}
              onChange={(event) => setTitle(event.target.value)}
              maxLength={255}
              placeholder={`What to talk with ${nameForRavn(ravn)} about`}
              autoFocus
              data-testid="ravn-conversation-title"
            />
          </label>
          {allowedModels.length > 0 && (
            <label className="rw-field">
              <span className="rw-field__label">Model</span>
              <ResidentModelSelect
                allowedModels={allowedModels}
                modelPrefix={profile?.modelPrefix ?? ''}
                value={selectedModel}
                onChange={setModel}
                testId="ravn-conversation-model"
              />
            </label>
          )}
          {create.isError && (
            <div className="rw-form-error" role="alert">
              {errorText(create.error, 'Creating the conversation failed')}
            </div>
          )}
          <div className="rw-dialog-foot">
            <span className="rw-dialog-foot__sum" />
            <button type="button" className="rw-btn" onClick={() => onOpenChange(false)}>
              Cancel
            </button>
            <button
              type="submit"
              className="rw-btn rw-btn--primary"
              disabled={!title.trim() || create.isPending}
              data-testid="ravn-conversation-create"
            >
              {create.isPending ? 'Starting…' : 'Start conversation'}
            </button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function CloseConversationDialog({
  ravn,
  session,
  onOpenChange,
  onClosed,
}: {
  ravn: Ravn;
  session: Session | null;
  onOpenChange: (open: boolean) => void;
  onClosed: () => void;
}) {
  const remove = useDeleteResidentSession(ravn);
  async function confirm() {
    if (!session) return;
    try {
      await remove.mutateAsync(session.id);
    } catch {
      return;
    }
    onOpenChange(false);
    onClosed();
  }
  return (
    <Dialog open={Boolean(session)} onOpenChange={onOpenChange}>
      <DialogContent
        title="Close conversation"
        description={
          session
            ? `“${conversationTitle(session)}” and the history the engine keeps for it are removed.`
            : undefined
        }
      >
        {remove.isError && (
          <div className="rw-form-error" role="alert">
            {errorText(remove.error, 'Closing the conversation failed')}
          </div>
        )}
        <div className="rw-dialog-foot">
          <span className="rw-dialog-foot__sum" />
          <button type="button" className="rw-btn" onClick={() => onOpenChange(false)}>
            Cancel
          </button>
          <button
            type="button"
            className="rw-btn rw-btn--danger"
            disabled={remove.isPending}
            onClick={() => void confirm()}
            data-testid="ravn-conversation-close-confirm"
          >
            {remove.isPending ? 'Closing…' : 'Close conversation'}
          </button>
        </div>
      </DialogContent>
    </Dialog>
  );
}

export function ChatTab({
  ravn,
  requestedSessionId,
  onSelectSession,
  onLifecycle,
  onShowActivity,
  lifecyclePending,
}: ChatTabProps) {
  const [creating, setCreating] = useState(false);
  const [closing, setClosing] = useState<Session | null>(null);
  const state = ravnLifeState(ravn);
  const listable = canListResidentSessions(ravn);
  const canCreate = canCreateResidentSession(ravn);
  const canClose = canDeleteResidentSession(ravn);
  const residentSessions = useResidentSessions(ravn, listable);
  const fleetSessions = useSessions();

  const conversations = useMemo(() => {
    const source = listable
      ? (residentSessions.data ?? [])
      : (fleetSessions.data ?? []).filter((session) => belongsToRavn(session, ravn));
    return sortConversations(source);
  }, [listable, residentSessions.data, fleetSessions.data, ravn]);

  const selected = pickConversation(conversations, requestedSessionId);
  const sessionEndpoint =
    selected?.status === 'running' ? normalizeSessionUrl(selected.chatEndpoint ?? null) : null;
  const ravnEndpoint =
    !selected && state === 'running' ? normalizeSessionUrl(ravn.chatEndpoint ?? null) : null;
  const endpoint = sessionEndpoint ?? ravnEndpoint;
  const showList = listable || conversations.length > 1;
  const loading = listable ? residentSessions.isLoading : fleetSessions.isLoading;
  const loadError = listable ? residentSessions.error : fleetSessions.error;

  let main;
  if (loading) {
    main = <LoadingState label="Loading conversations…" />;
  } else if (endpoint) {
    main = (
      <LiveChat
        key={endpoint}
        chatEndpoint={endpoint}
        name={nameForRavn(ravn)}
        socketHistory={ravn.kind === 'resident'}
        eventRouting={Boolean(selected?.flockId ?? ravn.flockId)}
      />
    );
  } else if (selected && state !== 'starting') {
    main = <Transcript ravn={ravn} session={selected} />;
  } else if (state === 'running' && canCreate) {
    main = (
      <div className="rw-empty">
        <div className="rw-empty__inner">
          <RavnMark ravn={ravn} size="lg" />
          <h3 className="rw-empty__title">No conversations yet</h3>
          <p className="rw-empty__text">Start one to talk with {nameForRavn(ravn)}.</p>
          <div className="rw-empty__acts">
            <button
              type="button"
              className="rw-btn rw-btn--primary"
              onClick={() => setCreating(true)}
            >
              <Plus size={14} aria-hidden="true" />
              New conversation
            </button>
          </div>
        </div>
      </div>
    );
  } else {
    main = (
      <NotRunning
        ravn={ravn}
        onLifecycle={onLifecycle}
        onShowActivity={onShowActivity}
        lifecyclePending={lifecyclePending}
      />
    );
  }

  return (
    <div className={showList ? 'rw-chat' : 'rw-chat rw-chat--solo'} data-testid="ravn-chat-tab">
      {showList && (
        <div className="rw-convs" aria-label="Conversations">
          <div className="rw-convs__head">
            <span>Conversations</span>
            {canCreate && (
              <button
                type="button"
                className="rw-btn rw-btn--small"
                onClick={() => setCreating(true)}
                data-testid="ravn-conversation-new"
              >
                <Plus size={13} aria-hidden="true" />
                New
              </button>
            )}
          </div>
          <div className="rw-convs__list">
            {loadError && (
              <p className="rw-inline-error" role="alert">
                {errorText(loadError, 'Failed to load conversations')}
              </p>
            )}
            {conversations.map((session) => (
              <div key={session.id} className="rw-conv-row">
                <button
                  type="button"
                  className="rw-conv"
                  aria-current={selected?.id === session.id}
                  onClick={() => onSelectSession(session.id)}
                  data-testid="ravn-conversation"
                >
                  <span className="rw-conv__title">{conversationTitle(session)}</span>
                  <span className="rw-conv__meta">
                    {session.status === 'running' && (
                      <span className="rw-conv__live">● live · </span>
                    )}
                    {session.messageCount != null && `${session.messageCount} msgs · `}
                    {relTime(session.createdAt)}
                  </span>
                </button>
                {canClose && (
                  <button
                    type="button"
                    className="rw-conv__close"
                    onClick={() => setClosing(session)}
                    aria-label={`Close ${conversationTitle(session)}`}
                    title="Close conversation"
                  >
                    <X size={14} aria-hidden="true" />
                  </button>
                )}
              </div>
            ))}
            {!loading && conversations.length === 0 && !loadError && (
              <p className="rw-rail__empty">No conversations.</p>
            )}
          </div>
        </div>
      )}
      <div className="rw-chat__main">{main}</div>

      <NewConversationDialog
        ravn={ravn}
        open={creating}
        onOpenChange={setCreating}
        onCreated={(session) => onSelectSession(session.id)}
      />
      <CloseConversationDialog
        ravn={ravn}
        session={closing}
        onOpenChange={(open) => !open && setClosing(null)}
        onClosed={() => onSelectSession(null)}
      />
    </div>
  );
}
