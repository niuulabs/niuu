import {
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type FormEvent,
  type ReactNode,
} from 'react';
import { ArrowLeft, ArrowUp } from 'lucide-react';
import { verdictLabel, type Worker } from '../../workers';
import type { AgentInternalEvent, ChatMessage, RoomParticipant } from '../../types';
import type { FileAttachment } from '../../hooks/useFileAttachments';
import { AssistantMessage } from '../ChatMessages';
import { OutcomeSpeakerProvider, WorkerOutcomeCard } from '../OutcomeCard';
import { VerdictPill } from '../VerdictPill';
import { WORKER_STATUS_LABELS, workerInitial } from '../WorkersStrip';
import './WorkerSheet.css';

export interface WorkerSheetProps {
  worker: Worker;
  participant: RoomParticipant;
  /** The room's messages; the sheet keeps this worker's. */
  messages: readonly ChatMessage[];
  /** The worker's own agent events (thoughts, tool calls and results). */
  events: readonly AgentInternalEvent[];
  /** Back to the coordinator's view of the whole room. */
  onClose: () => void;
  /** Send to this worker alone; without it the sheet has no composer. */
  onSendDirected?: (
    participants: RoomParticipant[],
    text: string,
    attachments: FileAttachment[],
  ) => void;
  /**
   * Show what the worker listens for and emits in the sheet's header. A host that shows them
   * elsewhere (behind the session's details) turns this off so the header stays short.
   */
  showEvents?: boolean;
}

/** How far from the end the sheet still follows new activity. */
const FOLLOW_DISTANCE_PX = 80;

function eventText(data: AgentInternalEvent['data']): string {
  return typeof data === 'string' ? data : JSON.stringify(data, null, 2);
}

function ToolDetails({ summary, body }: { summary: ReactNode; body: string }) {
  if (!body) return <div className="niuu-chat-sheet-tool">{summary}</div>;
  return (
    <details className="niuu-chat-sheet-tool">
      <summary>{summary}</summary>
      <pre>{body}</pre>
    </details>
  );
}

function AgentEventRow({ event }: { event: AgentInternalEvent }) {
  const tool = typeof event.metadata?.tool_name === 'string' ? event.metadata.tool_name : '';
  const data = eventText(event.data);

  if (event.frameType === 'thought') {
    return (
      <div className="niuu-chat-sheet-thought" data-frame="thought">
        {data}
      </div>
    );
  }
  if (event.frameType === 'tool_start') {
    const input = event.metadata?.input;
    const body = input === undefined ? '' : eventText(input as AgentInternalEvent['data']);
    return (
      <ToolDetails
        summary={
          <>
            <b>Running</b> {tool || data}
          </>
        }
        body={body}
      />
    );
  }
  if (event.frameType === 'tool_result') {
    return (
      <ToolDetails
        summary={
          <>
            <b>Result</b> {tool}
          </>
        }
        body={data}
      />
    );
  }
  return (
    <div className="niuu-chat-sheet-text" data-frame={event.frameType}>
      {data}
    </div>
  );
}

interface TimelineItem {
  key: string;
  at: Date;
  node: ReactNode;
}

/**
 * One worker, close up, in place of the room's conversation: what it listens for and emits,
 * only its own messages, outcomes and agent events in the order they happened, and a way to
 * message it directly. Going back shows the coordinator's view of everyone again.
 */
export function WorkerSheet({
  worker,
  participant,
  messages,
  events,
  onClose,
  onSendDirected,
  showEvents = true,
}: WorkerSheetProps) {
  const sheetRef = useRef<HTMLElement>(null);
  const bodyRef = useRef<HTMLDivElement>(null);
  const followRef = useRef(true);
  const [draft, setDraft] = useState('');
  const speaker = useMemo(() => ({ name: worker.name, color: worker.color }), [worker]);

  const timeline = useMemo<TimelineItem[]>(() => {
    const own = messages
      .filter(
        (message) =>
          message.participant?.peerId === worker.peerId &&
          message.role === 'assistant' &&
          message.metadata?.messageType !== 'system',
      )
      .map<TimelineItem>((message) => ({
        key: `message-${message.id}`,
        at: message.createdAt,
        node: (
          <div className="niuu-chat-sheet-message" data-testid="sheet-message">
            <AssistantMessage message={message} />
          </div>
        ),
      }));
    // An outcome that came in a message shows inside it; only live-event outcomes stand alone.
    const liveOutcomes = worker.outcomes
      .filter((outcome) => !outcome.messageId)
      .map<TimelineItem>((outcome, index) => ({
        key: `outcome-${index}-${outcome.at.getTime()}`,
        at: outcome.at,
        node: <WorkerOutcomeCard outcome={outcome} speaker={speaker} />,
      }));
    // Activity with a time stands where it happened; activity without one follows the rest.
    const activity = events.map<TimelineItem>((event, index) => ({
      key: `event-${event.id}-${index}`,
      at: event.timestamp ?? new Date(8.64e15),
      node: <AgentEventRow event={event} />,
    }));
    return [...own, ...liveOutcomes, ...activity].sort((a, b) => a.at.getTime() - b.at.getTime());
  }, [messages, worker, speaker, events]);

  useEffect(() => {
    sheetRef.current?.focus();
  }, []);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== 'Escape' || event.defaultPrevented) return;
      // Escape in a field elsewhere (a menu in the main composer) belongs to that field.
      const field = (event.target as HTMLElement | null)?.closest?.(
        'input, textarea, select, [contenteditable="true"]',
      );
      if (field && !sheetRef.current?.contains(field)) return;
      onClose();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose]);

  useLayoutEffect(() => {
    const body = bodyRef.current;
    if (body && followRef.current) body.scrollTop = body.scrollHeight;
  }, [timeline.length]);

  const onScroll = () => {
    const body = bodyRef.current;
    if (!body) return;
    followRef.current =
      body.scrollHeight - body.scrollTop - body.clientHeight <= FOLLOW_DISTANCE_PX;
  };

  const send = (event: FormEvent) => {
    event.preventDefault();
    const text = draft.trim();
    if (!text || !onSendDirected) return;
    onSendDirected([participant], text, []);
    setDraft('');
  };

  const latest = worker.latest;
  const empty = timeline.length === 0;

  return (
    <aside
      ref={sheetRef}
      className="niuu-chat-sheet"
      style={{ '--niuu-worker-color': worker.color } as CSSProperties}
      role="region"
      aria-label={`${worker.name} worker`}
      tabIndex={-1}
      data-testid="worker-sheet"
    >
      <header className="niuu-chat-sheet-head">
        <button
          type="button"
          className="niuu-chat-sheet-back"
          onClick={onClose}
          aria-label="Back to the coordinator"
        >
          <ArrowLeft className="niuu-chat-sheet-back-icon" aria-hidden="true" />
          Coordinator
        </button>
        <div className="niuu-chat-sheet-title">
          <span className="niuu-chat-sheet-avatar" aria-hidden="true">
            {workerInitial(worker)}
          </span>
          <div className="niuu-chat-sheet-who">
            <h4>{worker.name}</h4>
            <div className="niuu-chat-sheet-status" data-status={worker.status}>
              {WORKER_STATUS_LABELS[worker.status]} · worker
            </div>
          </div>
          {latest && <VerdictPill label={verdictLabel(latest.verdict)} tone={latest.tone} />}
        </div>
        {showEvents && (worker.consumes.length > 0 || worker.emits.length > 0) && (
          <div className="niuu-chat-sheet-events">
            {worker.consumes.length > 0 && (
              <span className="niuu-chat-sheet-event-group">
                Listens for
                {worker.consumes.map((type) => (
                  <code key={type}>{type}</code>
                ))}
              </span>
            )}
            {worker.emits.length > 0 && (
              <span className="niuu-chat-sheet-event-group">
                Emits
                {worker.emits.map((type) => (
                  <code key={type}>{type}</code>
                ))}
              </span>
            )}
          </div>
        )}
      </header>

      <div className="niuu-chat-sheet-body" ref={bodyRef} onScroll={onScroll}>
        {empty && <div className="niuu-chat-sheet-empty">Nothing from {worker.name} yet.</div>}
        <OutcomeSpeakerProvider value={speaker}>
          {timeline.map((item) => (
            <div key={item.key}>{item.node}</div>
          ))}
        </OutcomeSpeakerProvider>
      </div>

      {onSendDirected && (
        <form className="niuu-chat-sheet-composer" onSubmit={send}>
          <input
            type="text"
            className="niuu-chat-sheet-input"
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            placeholder={`Message ${worker.name} directly…`}
            aria-label={`Message ${worker.name} directly`}
          />
          <button
            type="submit"
            className="niuu-chat-sheet-send"
            disabled={!draft.trim()}
            aria-label={`Send to ${worker.name}`}
          >
            <ArrowUp className="niuu-chat-sheet-send-icon" />
          </button>
        </form>
      )}
    </aside>
  );
}
