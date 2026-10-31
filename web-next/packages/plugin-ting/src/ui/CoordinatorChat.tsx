import { useMemo, useState, type FormEvent } from 'react';
import { useOptionalService } from '@niuulabs/plugin-sdk';
import { SessionChat, type ISessionHistoryLocator, useSkuldChat } from '@niuulabs/ui';
import { questionAboutQuote } from '../domain/review';
import type { TicketCoordinatorRef } from '../domain/work';

function QuotedQuestion({
  quote,
  onAsk,
  onCancel,
}: {
  quote: string;
  onAsk: (question: string) => void;
  onCancel: () => void;
}) {
  const [question, setQuestion] = useState('');

  function submit(event: FormEvent) {
    event.preventDefault();
    if (question.trim()) onAsk(question);
  }

  return (
    <form className="ting-coordinator-ask" onSubmit={submit}>
      <blockquote>{quote}</blockquote>
      <label>
        <span>Ask about this</span>
        <input autoFocus value={question} onChange={(event) => setQuestion(event.target.value)} />
      </label>
      <div className="ting-coordinator-ask__actions">
        <button type="button" onClick={onCancel}>
          Cancel
        </button>
        <button type="submit" disabled={!question.trim()}>
          Ask
        </button>
      </div>
    </form>
  );
}

function LiveCoordinatorChat({
  chatEndpoint,
  name,
  quote,
  onQuoteDone,
}: {
  chatEndpoint: string;
  name: string;
  quote?: string | null;
  onQuoteDone?: () => void;
}) {
  const historyLocator = useOptionalService<ISessionHistoryLocator>('forge.history');
  const historyEndpoint = useMemo(
    () => historyLocator?.historyEndpoint(chatEndpoint) ?? null,
    [chatEndpoint, historyLocator],
  );
  const chat = useSkuldChat(chatEndpoint, { historyMode: 'session', historyEndpoint });
  return (
    <div className="ting-board-chat" data-testid="ticket-coordinator-chat">
      {quote ? (
        <QuotedQuestion
          key={quote}
          quote={quote}
          onAsk={(question) => {
            chat.sendMessage(questionAboutQuote(quote, question), []);
            onQuoteDone?.();
          }}
          onCancel={() => onQuoteDone?.()}
        />
      ) : null}
      <SessionChat
        messages={chat.messages}
        streamingContent={chat.streamingContent}
        streamingParts={chat.streamingParts}
        streamingModel={chat.streamingModel}
        connected={chat.connected}
        historyLoaded={chat.historyLoaded}
        historyError={chat.historyError}
        onRetryHistory={chat.retryHistory}
        hasOlderHistory={chat.hasOlderHistory}
        loadingOlderHistory={chat.loadingOlderHistory}
        olderHistoryError={chat.olderHistoryError}
        onLoadOlderHistory={chat.loadOlderHistory}
        pendingPermissions={chat.pendingPermissions}
        pendingInputRequests={chat.pendingInputRequests}
        availableCommands={chat.availableCommands}
        capabilities={chat.capabilities}
        chatEndpoint={chatEndpoint}
        historyEndpoint={historyEndpoint}
        sessionName={name}
        showDisplayControls={false}
        onSend={chat.sendMessage}
        onSendDirected={chat.sendDirectedMessages}
        onPublishEvent={chat.publishEvent}
        onStop={chat.sendInterrupt}
        onPermissionRespond={chat.respondToPermission}
        onInputRespond={chat.respondToInput}
        onSetInternalVisibility={chat.sendSetInternalVisibility}
      />
    </div>
  );
}

/** A ticket's coordinator, wherever a person meets it: its panel, or its review. */
export function CoordinatorChat({
  coordinator,
  name,
  quote,
  onQuoteDone,
}: {
  coordinator: TicketCoordinatorRef | null | undefined;
  name: string;
  quote?: string | null;
  onQuoteDone?: () => void;
}) {
  if (!coordinator) {
    return (
      <p className="ting-board-panel__text">
        No coordinator yet. One starts when this ticket enters a workflow.
      </p>
    );
  }
  if (coordinator.status === 'closed') {
    return (
      <p className="ting-board-panel__text">This ticket is closed, and so is its coordinator.</p>
    );
  }
  if (!coordinator.chatEndpoint) {
    return <p className="ting-board-panel__text">The coordinator is starting up.</p>;
  }
  return (
    <LiveCoordinatorChat
      chatEndpoint={coordinator.chatEndpoint}
      name={name}
      quote={quote}
      onQuoteDone={onQuoteDone}
    />
  );
}
