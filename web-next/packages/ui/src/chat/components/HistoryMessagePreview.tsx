import { useContext, useEffect, useState } from 'react';
import { Dialog, DialogContent } from '../../primitives/Dialog';
import { HistoryDetailsContext } from './HistoryDetailsContext';
import { fetchHistoryItem } from '../hooks/historyPaging';
import { transformTurns } from '../hooks/useSkuldChat';
import { AssistantMessage, UserMessage } from './ChatMessages';
import type { ChatMessage } from '../types';

export function HistoryMessagePreview({ message }: { message: ChatMessage }) {
  const socketUrl = useContext(HistoryDetailsContext);
  const [open, setOpen] = useState(false);
  const [detail, setDetail] = useState<ChatMessage>();
  const [error, setError] = useState<string>();
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    if (!open || !socketUrl) return;
    const controller = new AbortController();
    void fetchHistoryItem(socketUrl, message.id, controller.signal)
      .then((turn) => {
        if (!controller.signal.aborted) setDetail(transformTurns([turn])[0]);
      })
      .catch((failure: unknown) => {
        if (!controller.signal.aborted)
          setError(failure instanceof Error ? failure.message : 'Could not load this message.');
      });
    return () => controller.abort();
  }, [open, socketUrl, message.id, attempt]);
  return (
    <section className="niuu-chat-history-preview">
      <p>
        {message.historyMetadataPreview ? 'Message details available' : 'Large message — preview'}
      </p>
      {!message.historyMetadataPreview && (
        <p className="niuu-chat-history-excerpt">{message.content}</p>
      )}
      <button
        className="niuu-chat-retry"
        type="button"
        onClick={() => setOpen(true)}
        disabled={!socketUrl}
      >
        Open full message
      </button>
      <Dialog
        open={open}
        onOpenChange={(value) => {
          setOpen(value);
          if (!value) {
            setDetail(undefined);
            setError(undefined);
          }
        }}
      >
        <DialogContent title="Message" className="niuu-chat-history-reader">
          {error ? (
            <div role="alert">
              {error}{' '}
              <button
                className="niuu-chat-retry"
                onClick={() => {
                  setError(undefined);
                  setAttempt((value) => value + 1);
                }}
              >
                Try again
              </button>
            </div>
          ) : !detail ? (
            <p role="status">Loading message…</p>
          ) : detail.role === 'user' ? (
            <UserMessage message={detail} />
          ) : (
            <AssistantMessage message={detail} />
          )}
        </DialogContent>
      </Dialog>
    </section>
  );
}
