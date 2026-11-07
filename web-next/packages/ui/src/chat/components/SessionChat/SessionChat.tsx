import {
  useCallback,
  useMemo,
  useState,
  useRef,
  useEffect,
  useLayoutEffect,
  type ReactNode,
} from 'react';
import {
  Wifi,
  WifiOff,
  BrainCircuitIcon,
  RotateCcwIcon,
  ArrowDownIcon,
  Eye,
  EyeOff,
  Trash2Icon,
  ChevronRight,
  ChevronDown,
  Loader2,
  BadgeCheck,
} from 'lucide-react';
import { LoadingState } from '../../../data/states/LoadingState';
import { ErrorState } from '../../../data/states/ErrorState';
import { cn } from '../../../utils/cn';
import { hideToolParts, useRoomState } from '../../hooks/useRoomState';
import {
  roomWorkers,
  workerOutcomes,
  workerStages,
  type Worker,
  type WorkerOutcome,
} from '../../workers';
import {
  UserMessage,
  AssistantMessage,
  StreamingMessage,
  SystemMessage,
  hasNativeMessageParts,
  messageRenderKey,
  messageModel,
  ModelSpeaker,
} from '../ChatMessages';
import { RoomMessage } from '../RoomMessage';
import { ThreadGroup } from '../ThreadGroup';
import { WorkersStrip } from '../WorkersStrip';
import { useSessionLinks, type SessionLink } from '../../sessionLinks';
import { ProgressLane } from '../ProgressLane';
import { WorkerSheet } from '../WorkerSheet';
import { HandoffRow, handoffParties } from '../HandoffRow';
import { AttentionRow } from '../AttentionRow';
import { isCoordinator } from '../RoomMessage/speaker';
import { ChatInput } from '../ChatInput';
import { PlanDock } from '../PlanDock';
import { QuestionBand } from '../QuestionBand';
import { SessionEmptyChat } from '../ChatEmptyStates';
import { WorkerOutcomeCard } from '../OutcomeCard';
import { resolveParticipantColor } from '../../utils/participantColor';
import type {
  AgentInternalEvent,
  AgentEventTarget,
  ChatMessage,
  ChatMessagePart,
  InputAnswer,
  InputRequest,
  RoomParticipant,
  MeshEvent,
  MeshDelegationEvent,
  MeshNotificationEvent,
  PermissionRequest,
  PermissionBehavior,
  FileEntry,
  SessionAgent,
  SessionCapabilities,
  SessionEffort,
  SessionPlan,
  SessionRuntimeOptions,
} from '../../types';
import type { FileAttachment } from '../../hooks/useFileAttachments';
import type { SlashCommand } from '../../utils/slashCommands';
import { useConversationView } from '../../compactUxPrefs';
import { ChatDisplayControls } from '../ChatDisplayControls';
import { ToolImageProvider } from '../ToolImages';
import { HistoryDetailsContext } from '../HistoryDetailsContext';
import './SessionChat.css';
import { ChatConnectionsButton } from '../../../ChatConnections';

const SCROLL_THRESHOLD = 150;

const THINKING_PRESETS = [
  { label: '4K', value: 4096 },
  { label: '8K', value: 8192 },
  { label: '16K', value: 16384 },
  { label: '32K', value: 32768 },
] as const;

function DefaultPermissionRequests({
  permissions,
  onRespond,
}: {
  permissions: PermissionRequest[];
  onRespond: (requestId: string, behavior: PermissionBehavior) => void;
}) {
  return (
    <div className="niuu-chat-request-list" role="region" aria-label="Pending approvals">
      {permissions.map((permission) => (
        <section className="niuu-chat-request" key={permission.requestId}>
          <div className="niuu-chat-request-copy">
            <strong className="niuu-chat-request-title">Approval required</strong>
            <span className="niuu-chat-request-tool">{permission.toolName}</span>
            <p className="niuu-chat-request-prompt">{permission.description}</p>
          </div>
          <div className="niuu-chat-request-actions">
            <button
              type="button"
              className="niuu-chat-request-button niuu-chat-request-button--deny"
              onClick={() => onRespond(permission.requestId, 'deny')}
            >
              Deny
            </button>
            <button
              type="button"
              className="niuu-chat-request-button"
              onClick={() => onRespond(permission.requestId, 'allow_once')}
            >
              Allow once
            </button>
            <button
              type="button"
              className="niuu-chat-request-button niuu-chat-request-button--primary"
              onClick={() => onRespond(permission.requestId, 'allow_always')}
            >
              Always allow
            </button>
          </div>
        </section>
      ))}
    </div>
  );
}

export interface SessionChatProps {
  /** All completed messages */
  messages: readonly ChatMessage[];
  /** Currently streaming text (if any) */
  streamingContent?: string;
  /** Parts for the streaming message */
  streamingParts?: readonly ChatMessagePart[];
  /** Model name for the streaming message */
  streamingModel?: string;
  /** Whether the session is connected */
  connected?: boolean;
  /** Whether history has been loaded */
  historyLoaded?: boolean;
  historyError?: string | null;
  onRetryHistory?: () => void;
  hasOlderHistory?: boolean;
  loadingOlderHistory?: boolean;
  olderHistoryError?: string | null;
  onLoadOlderHistory?: () => Promise<void>;
  /** Room participants map (peerId → meta) */
  participants?: ReadonlyMap<string, RoomParticipant>;
  /**
   * Ids of the worker sessions this session coordinates. They join the room's agents in the
   * Workers strip, resolved through the page's session links.
   */
  workerSessionIds?: readonly string[];
  /**
   * What the host keeps by the composer, above it: a decision the session waits on, as Lexi
   * keeps its questions where the person answers.
   */
  aboveComposer?: ReactNode;
  /**
   * Draw the workers strip and progress lane over the conversation. A host that lists the
   * workers itself (a session list with the workers under their coordinator) leaves them out,
   * so the chat starts with the conversation, as Lexi's does.
   */
  showWorkersStrip?: boolean;
  /**
   * The room worker shown on its own, in place of the coordinator's view of everyone. Given
   * with `onFocusWorker`, the host owns it (a session list that selects workers); without
   * them the chat keeps it itself.
   */
  focusedWorkerId?: string | null;
  onFocusWorker?: (peerId: string | null) => void;
  /** The room's workers whenever who they are or what they are doing changes. */
  onWorkersChange?: (workers: readonly Worker[]) => void;
  /** Mesh events for the cascade panel */
  meshEvents?: readonly MeshEvent[];
  /** Per-agent internal event frames */
  agentEvents?: ReadonlyMap<string, readonly AgentInternalEvent[]>;
  /** Pending permission requests */
  pendingPermissions?: PermissionRequest[];
  /** Pending clarification requests */
  pendingInputRequests?: InputRequest[];
  /** Available slash commands */
  availableCommands?: readonly SlashCommand[];
  /** Which server-side capabilities are active */
  capabilities?: SessionCapabilities;
  /** The agent's plan; shown as a capsule above the composer when it has tasks. */
  plan?: SessionPlan | null;
  /** Subagents and teammates; the running ones are counted on the plan capsule. */
  agents?: readonly SessionAgent[];
  /** Reasoning effort choices for the composer's effort chip. */
  effort?: SessionEffort | null;
  /** Model catalog and current choice for the composer's model chip. */
  runtimeOptions?: SessionRuntimeOptions | null;
  /** The agent is mid-turn: the composer shows Stop and, when supported, queues messages. */
  working?: boolean;
  /** Pod hostname for file listing */
  sessionHost?: string | null;
  /** Full chat endpoint URL */
  chatEndpoint?: string | null;
  /** Resolved Forge history endpoint when the gateway is not the Forge API host */
  historyEndpoint?: string | null;
  /** Session name shown in empty state */
  sessionName?: string;
  /** Optional extra class on the outer wrapper */
  className?: string;
  /** Show the built-in toolbar row. */
  showToolbar?: boolean;
  /**
   * Render the display controls (and account reconnect) above the conversation
   * when there is no toolbar. Off when the host places `ChatDisplayControls` in
   * its own toolbar.
   */
  showDisplayControls?: boolean;
  /** Token counts are opt-in to keep the conversation uncluttered. */
  showTokenUsage?: boolean;
  /** Hide the built-in internal visibility toggle when the page owns it externally. */
  showInternalToggle?: boolean;
  /** Controlled internal visibility state for external toolbar integrations. */
  internalVisibility?: boolean;
  /** Route @ mentions through participant event subscriptions. */
  eventRouting?: boolean;

  /* ── Callbacks ── */
  onSend: (text: string, attachments: FileAttachment[]) => void;
  onSendDirected?: (
    participants: RoomParticipant[],
    text: string,
    attachments: FileAttachment[],
  ) => void;
  onPublishEvent?: (target: AgentEventTarget, text: string) => void;
  onStop?: () => void;
  onClear?: () => void;
  /**
   * Notify the backend whenever the user toggles internal-message visibility
   * so the server can stop streaming tool_use / tool_result blocks over the
   * wire (saves bandwidth and chronicle pollution). Optional — when omitted
   * the toggle still works as a client-side filter.
   */
  onSetInternalVisibility?: (visible: boolean) => void;
  onSetModel?: (model: string) => void;
  onSetEffort?: (effort: string) => void;
  /** Change the model from the composer chip (a runtime-options model id). */
  onSetRuntimeModel?: (model: string) => void;
  /** Why the runtime refused to list its models (shown on the composer's model chip). */
  runtimeOptionsError?: string | null;
  /** Why the runtime refused to report its effort levels (shown on the effort chip). */
  effortError?: string | null;
  onSetThinkingTokens?: (tokens: number) => void;
  onRewindFiles?: () => void;
  onCopy?: (text: string) => void;
  onRegenerate?: (messageId: string) => void;
  /** Offer "Forward…" on assistant messages; absent means no button. */
  onForwardMessage?: (message: ChatMessage) => void;
  onBookmark?: (messageId: string, bookmarked: boolean) => void;
  onPermissionRespond?: (requestId: string, behavior: PermissionBehavior) => void;
  onInputRespond?: (requestId: string, values: (string | InputAnswer)[]) => void;
  /** Remove a card no client can answer (read-only cards only). */
  onInputDismiss?: (requestId: string) => void;
  onFetchFiles?: (path: string, apiBase: string) => Promise<FileEntry[]>;
  onMessageCountChange?: (count: number) => void;

  /** Render slot for permission UI — receives pending list and respond callback */
  renderPermissions?: (
    permissions: PermissionRequest[],
    onRespond: (requestId: string, behavior: PermissionBehavior) => void,
  ) => ReactNode;
}

type InlineItem =
  | { kind: 'handoff'; key: string; at: Date; event: MeshDelegationEvent }
  | { kind: 'attention'; key: string; at: Date; event: MeshNotificationEvent }
  | { kind: 'outcome'; key: string; at: Date; outcome: WorkerOutcome };

const COORDINATOR_KEY = 'coordinator';

/** Who a message speaks as, for sharing a header; null for messages that have no speaker. */
function speakerKey(message: ChatMessage): string | null {
  if (message.metadata?.messageType === 'system') return null;
  if (message.participant) {
    return isCoordinator(message.participant) ? COORDINATOR_KEY : message.participant.peerId;
  }
  return message.role === 'assistant' ? COORDINATOR_KEY : null;
}

/** When a group of messages began. */
function groupStartedAt(
  group:
    | { type: 'single'; message: ChatMessage }
    | { type: 'thread'; messages: readonly ChatMessage[] }
    | {
        type: 'compact';
        turn: { intermediaries: readonly ChatMessage[]; final: ChatMessage | null };
      },
): number {
  if (group.type === 'single') return group.message.createdAt.getTime();
  if (group.type === 'thread') return group.messages[0]!.createdAt.getTime();
  return (group.turn.intermediaries[0] ?? group.turn.final)!.createdAt.getTime();
}

/** "54s", "2m 5s", "1h 12m": how long a turn worked, as Lexi writes it. */
function workedDuration(seconds: number): string {
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return seconds % 60 ? `${minutes}m ${seconds % 60}s` : `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  return minutes % 60 ? `${hours}h ${minutes % 60}m` : `${hours}h`;
}

export function SessionChat({
  messages,
  streamingContent,
  streamingParts,
  streamingModel,
  connected = false,
  historyLoaded = true,
  historyError,
  onRetryHistory,
  hasOlderHistory = false,
  loadingOlderHistory = false,
  olderHistoryError,
  onLoadOlderHistory,
  participants = new Map(),
  workerSessionIds,
  showWorkersStrip = true,
  aboveComposer,
  focusedWorkerId,
  onFocusWorker,
  onWorkersChange,
  meshEvents = [],
  agentEvents = new Map(),
  pendingPermissions = [],
  pendingInputRequests = [],
  availableCommands,
  capabilities = {},
  plan = null,
  agents,
  effort = null,
  runtimeOptions = null,
  working = false,
  sessionHost = null,
  chatEndpoint = null,
  historyEndpoint = null,
  sessionName = 'Session',
  className,
  showToolbar = true,
  showDisplayControls = true,
  showTokenUsage = false,
  showInternalToggle = true,
  internalVisibility,
  eventRouting = false,
  onSend,
  onSendDirected,
  onPublishEvent,
  onStop,
  onClear,
  onSetInternalVisibility,
  onSetModel,
  onSetEffort,
  onSetRuntimeModel,
  runtimeOptionsError,
  effortError,
  onSetThinkingTokens,
  onRewindFiles,
  onCopy,
  onRegenerate,
  onForwardMessage,
  onBookmark,
  onPermissionRespond,
  onInputRespond,
  onInputDismiss,
  onFetchFiles,
  onMessageCountChange,
  renderPermissions,
}: SessionChatProps) {
  const {
    isRoomMode,
    showInternal,
    setShowInternal,
    toggleInternal: toggleInternalLocal,
    visibleMessages,
    collapsedThreads,
    toggleThread,
  } = useRoomState(messages, participants, internalVisibility ?? false);

  useEffect(() => {
    if (internalVisibility === undefined) return;
    setShowInternal(internalVisibility);
  }, [internalVisibility, setShowInternal]);

  // Tool visibility is presentation-only. Keep receiving tool results so images
  // can enter the conversation even while their execution details are hidden.
  // useRoomState still applies room participant/internal-message visibility.
  useEffect(() => {
    if (connected) onSetInternalVisibility?.(true);
  }, [connected, onSetInternalVisibility]);
  const toggleInternal = toggleInternalLocal;

  const [modelInput, setModelInput] = useState('');
  const [showModelInput, setShowModelInput] = useState(false);
  const [showThinkingMenu, setShowThinkingMenu] = useState(false);
  const [showScrollBtn, setShowScrollBtn] = useState(false);
  const [newMessageCount, setNewMessageCount] = useState(0);
  const conversationView = useConversationView();
  const [expandedTurns, setExpandedTurns] = useState<ReadonlySet<string>>(new Set());

  const toggleTurn = useCallback((turnId: string) => {
    setExpandedTurns((prev) => {
      const next = new Set(prev);
      if (next.has(turnId)) {
        next.delete(turnId);
      } else {
        next.add(turnId);
      }
      return next;
    });
  }, []);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const scrollContainerRef = useRef<HTMLDivElement>(null);
  const isNearBottomRef = useRef(true);
  const olderPendingRef = useRef(false);
  const prependAnchorRef = useRef<{ id: string; top: number; firstId?: string } | null>(null);
  const loadOlder = useCallback(() => {
    const el = scrollContainerRef.current;
    if (
      !el ||
      !onLoadOlderHistory ||
      !hasOlderHistory ||
      olderPendingRef.current ||
      loadingOlderHistory
    )
      return;
    const top = el.getBoundingClientRect().top;
    const anchor = Array.from(el.querySelectorAll<HTMLElement>('[data-history-id]')).find(
      (row) => row.getBoundingClientRect().bottom > top,
    );
    if (anchor)
      prependAnchorRef.current = {
        id: anchor.dataset.historyId!,
        top: anchor.getBoundingClientRect().top,
        firstId: visibleMessages[0]?.id,
      };
    isNearBottomRef.current = false;
    olderPendingRef.current = true;
    void onLoadOlderHistory().finally(() => {
      olderPendingRef.current = false;
    });
  }, [hasOlderHistory, loadingOlderHistory, onLoadOlderHistory, visibleMessages]);
  const userSentRef = useRef(false);
  const prevMessageCountRef = useRef(0);
  const initialScrollRef = useRef<string | null | undefined>(undefined);

  const participantsMap = useMemo<Map<string, RoomParticipant>>(() => {
    const map = new Map<string, RoomParticipant>();
    for (const [k, v] of participants) {
      map.set(k, v);
    }
    return map;
  }, [participants]);

  const isRoomSession = Array.from(participantsMap.values()).some(
    (participant) => participant.participantType && participant.participantType !== 'skuld',
  );

  // The coordinator's workers: the room's agents, their outcomes and the order of their flow.
  const workers = useMemo(
    () => roomWorkers(participantsMap, meshEvents, messages),
    [participantsMap, meshEvents, messages],
  );
  const stages = useMemo(() => workerStages(workers), [workers]);
  const hasWorkers = workers.length > 0;
  const sessionLinks = useSessionLinks();
  const workerSessions = useMemo(
    () =>
      (workerSessionIds ?? []).flatMap((id): SessionLink[] => {
        const link = sessionLinks?.resolve(id);
        return link ? [link] : [];
      }),
    [sessionLinks, workerSessionIds],
  );
  const roomView = isRoomMode || isRoomSession || hasWorkers;
  const [ownFocus, setOwnFocus] = useState<string | null>(null);
  const focusControlled = focusedWorkerId !== undefined && onFocusWorker !== undefined;
  const sheetPeerId = focusControlled ? focusedWorkerId : ownFocus;
  const focusWorker = focusControlled ? onFocusWorker : setOwnFocus;
  const sheetWorker = workers.find((worker) => worker.peerId === sheetPeerId);
  const sheetParticipant = sheetPeerId ? participantsMap.get(sheetPeerId) : undefined;
  const workerIds = useMemo(() => new Set(workers.map((worker) => worker.peerId)), [workers]);

  const openWorker = useCallback(
    (peerId: string) => focusWorker(sheetPeerId === peerId ? null : peerId),
    [focusWorker, sheetPeerId],
  );
  const closeWorker = useCallback(() => focusWorker(null), [focusWorker]);

  // Tell the host who the workers are only when that changes, not on every message.
  const workersKey = workers
    .map(
      (worker) =>
        `${worker.peerId}:${worker.name}:${worker.status}:${worker.latest?.verdict ?? ''}`,
    )
    .join('|');
  const reportedWorkersKey = useRef<string | null>(null);
  useEffect(() => {
    if (!onWorkersChange || reportedWorkersKey.current === workersKey) return;
    reportedWorkersKey.current = workersKey;
    onWorkersChange(workers);
  }, [workers, workersKey, onWorkersChange]);

  const hasConversation = useMemo(
    () =>
      messages.some(
        (m) => m.role === 'user' || (m.role === 'assistant' && !m.metadata?.messageType),
      ),
    [messages],
  );

  type MessageGroup =
    | { type: 'single'; message: (typeof visibleMessages)[number] }
    | { type: 'thread'; threadId: string; messages: typeof visibleMessages };

  const renderedGroups = useMemo((): MessageGroup[] => {
    if (!isRoomMode || !showInternal) {
      return visibleMessages.map((m) => ({ type: 'single', message: m }));
    }
    const result: MessageGroup[] = [];
    let i = 0;
    while (i < visibleMessages.length) {
      const msg = visibleMessages[i];
      if (!msg) {
        i++;
        continue;
      }
      if (msg.visibility === 'internal' && msg.threadId) {
        const threadId = msg.threadId;
        const threadMsgs: (typeof visibleMessages)[number][] = [msg];
        let j = i + 1;
        while (j < visibleMessages.length) {
          const next = visibleMessages[j];
          if (next && next.visibility === 'internal' && next.threadId === threadId) {
            threadMsgs.push(next);
            j++;
          } else {
            break;
          }
        }
        if (threadMsgs.length > 1) {
          result.push({ type: 'thread', threadId, messages: threadMsgs });
          i = j;
          continue;
        }
      }
      result.push({ type: 'single', message: msg });
      i++;
    }
    return result;
  }, [visibleMessages, isRoomMode, showInternal]);

  // ── Compact (Codex-style) turn folding ──
  // A turn opens at a user message and runs until the next user message. We
  // render: the user message, an optional "Worked" disclosure for any
  // intermediary assistant/tool/system steps, and the final assistant reply.
  // Folding is a pure view concern; it does not change what is fetched.
  type ChatMsg = (typeof visibleMessages)[number];
  type CompactTurn = {
    id: string;
    user: ChatMsg | null;
    intermediaries: ChatMsg[];
    final: ChatMsg | null;
    leading: ChatMsg[];
  };

  // Never fold across speakers, threads, visibility changes, or system notices.
  // Consecutive contributions remain in transcript order, including when a
  // participant returns after another participant has spoken.
  const compactRoomGroups = useMemo(() => {
    const groups: (MessageGroup | { type: 'compact'; turn: CompactTurn })[] = [];
    let run: ChatMsg[] = [];
    const flush = () => {
      const final = run.at(-1);
      if (!final) return;
      groups.push({
        type: 'compact',
        turn: {
          id: `room-turn-${run[0]!.id}`,
          user: null,
          leading: [],
          intermediaries: run.slice(0, -1),
          final,
        },
      });
      run = [];
    };
    for (const group of renderedGroups) {
      if (
        group.type === 'thread' ||
        group.message.role !== 'assistant' ||
        group.message.metadata?.messageType
      ) {
        flush();
        groups.push(group);
        continue;
      }
      const message = group.message;
      const previous = run.at(-1);
      if (
        previous &&
        (previous.participant?.peerId !== message.participant?.peerId ||
          previous.threadId !== message.threadId ||
          previous.visibility !== message.visibility)
      )
        flush();
      run.push(message);
    }
    flush();
    return groups;
  }, [renderedGroups]);

  type RoomGroup = (typeof compactRoomGroups)[number];
  type TimelineEntry = { kind: 'group'; group: RoomGroup } | { kind: 'inline'; item: InlineItem };

  const compactTurns = useMemo((): CompactTurn[] => {
    const turns: CompactTurn[] = [];
    // Messages before the first user message (e.g. a resumed session opening
    // with assistant output) are rendered as-is, ahead of the first turn.
    const leadingPreamble: ChatMsg[] = [];
    // While building a turn we keep every non-user message in `members`; on
    // close we split off the last assistant message as the turn's answer.
    let members: ChatMsg[] = [];
    let currentUser: ChatMsg | null = null;
    let sawUser = false;

    const pushCurrent = () => {
      if (!currentUser) return;
      const intermediaries = [...members];
      let finalMsg: ChatMsg | null = null;
      for (let k = intermediaries.length - 1; k >= 0; k--) {
        const candidate = intermediaries[k];
        if (candidate && candidate.role === 'assistant') {
          finalMsg = candidate;
          intermediaries.splice(k, 1);
          break;
        }
      }
      turns.push({
        id: `turn-${currentUser.id}`,
        user: currentUser,
        intermediaries,
        final: finalMsg,
        leading: [],
      });
      currentUser = null;
      members = [];
    };

    for (const msg of visibleMessages) {
      if (msg.role === 'user') {
        pushCurrent();
        sawUser = true;
        currentUser = msg;
        continue;
      }
      if (!sawUser) {
        leadingPreamble.push(msg);
        continue;
      }
      members.push(msg);
    }
    pushCurrent();

    if (leadingPreamble.length > 0) {
      turns.unshift({
        id: 'turn-preamble',
        user: null,
        intermediaries: [],
        final: null,
        leading: leadingPreamble,
      });
    }
    return turns;
  }, [visibleMessages]);

  const useCompact = conversationView === 'compact';

  // Every tool call the transcript holds, by message, for the folded turns' "N tools".
  const messageOrder = useMemo(
    () => new Map(messages.map((message, index) => [message.id, index])),
    [messages],
  );
  const toolUses = (message: ChatMessage | undefined): number =>
    message?.parts?.filter((part) => part.type === 'tool_use').length ?? 0;
  const turnToolCount = (turn: (typeof compactTurns)[number]): number => {
    const own = [...turn.intermediaries, ...(turn.final ? [turn.final] : [])];
    // A question bounds its turn: everything up to the answer is that turn's work, tool-only
    // messages included. A room fold has no question; it counts its speaker's own messages.
    if (!turn.user) {
      return own.reduce((count, message) => {
        const index = messageOrder.get(message.id);
        return count + toolUses(index === undefined ? message : messages[index]);
      }, 0);
    }
    const from = (messageOrder.get(turn.user.id) ?? -1) + 1;
    const last = own.at(-1);
    const to = last ? (messageOrder.get(last.id) ?? messages.length - 1) : messages.length - 1;
    return messages.slice(from, to + 1).reduce((count, message) => count + toolUses(message), 0);
  };

  // What the workers did that no transcript message carries: handoffs between them, outcomes
  // that arrived only as live events, and their calls for attention. Each stands in the
  // conversation where it happened.
  const inlineItems = useMemo((): InlineItem[] => {
    if (!hasWorkers) return [];
    const handoffs = meshEvents.filter(
      (event): event is MeshDelegationEvent => event.type === 'mesh_message',
    );
    const notices = meshEvents.filter(
      (event): event is MeshNotificationEvent => event.type === 'notification',
    );
    // An outcome that came in a message renders inside it; only the live-event ones stand alone.
    const outcomes = workerOutcomes(meshEvents, messages).filter((outcome) => !outcome.messageId);
    return [
      ...handoffs.map((event): InlineItem => ({
        kind: 'handoff',
        key: event.id,
        at: event.timestamp,
        event,
      })),
      ...notices.map((event): InlineItem => ({
        kind: 'attention',
        key: event.id,
        at: event.timestamp,
        event,
      })),
      ...outcomes.map((outcome, index): InlineItem => ({
        kind: 'outcome',
        key: `outcome-${outcome.peerId}-${outcome.at.getTime()}-${index}`,
        at: outcome.at,
        outcome,
      })),
    ].sort((a, b) => a.at.getTime() - b.at.getTime());
  }, [hasWorkers, meshEvents, messages]);

  // The conversation in order: message groups with the workers' inline items between them,
  // an item landing before the first group that is later than it.
  const timeline = useMemo((): TimelineEntry[] => {
    const groups = useCompact ? compactRoomGroups : renderedGroups;
    const entries: TimelineEntry[] = [];
    let next = 0;
    for (const group of groups) {
      const groupAt = groupStartedAt(group);
      while (next < inlineItems.length && inlineItems[next]!.at.getTime() < groupAt) {
        entries.push({ kind: 'inline', item: inlineItems[next]! });
        next++;
      }
      entries.push({ kind: 'group', group });
    }
    for (; next < inlineItems.length; next++) {
      entries.push({ kind: 'inline', item: inlineItems[next]! });
    }
    return entries;
  }, [useCompact, compactRoomGroups, renderedGroups, inlineItems]);

  // Consecutive messages from one speaker share the first one's header; a handoff, an
  // outcome, a thread or another speaker starts a new run.
  const hiddenHeaders = useMemo((): ReadonlySet<string> => {
    const hidden = new Set<string>();
    if (!hasWorkers) return hidden;
    let previous: string | null = null;
    const visit = (message: ChatMsg) => {
      const key = speakerKey(message);
      if (key && key === previous) hidden.add(message.id);
      previous = key;
    };
    for (const entry of timeline) {
      if (entry.kind === 'inline') {
        previous = null;
        continue;
      }
      const { group } = entry;
      if (group.type === 'thread') {
        previous = null;
        continue;
      }
      if (group.type === 'single') {
        visit(group.message);
        continue;
      }
      // A folded turn: its steps sit under the disclosure, its answer shows the speaker.
      for (const step of group.turn.intermediaries) hidden.add(step.id);
      if (group.turn.intermediaries.length > 0) previous = null;
      if (group.turn.final) visit(group.turn.final);
    }
    return hidden;
  }, [hasWorkers, timeline]);

  const hasRunningAssistantMessage = visibleMessages.some(
    (message) => message.role === 'assistant' && message.status === 'running',
  );
  const isStreaming =
    !hasRunningAssistantMessage &&
    (!!streamingContent || (streamingParts && streamingParts.length > 0));

  const scrollToBottom = useCallback((behavior: ScrollBehavior = 'smooth') => {
    const container = scrollContainerRef.current;
    if (container) {
      if (behavior === 'smooth' && container.scrollTo)
        container.scrollTo({ top: container.scrollHeight, behavior });
      else container.scrollTop = container.scrollHeight;
    }
    setNewMessageCount(0);
  }, []);

  // Place the hydrated transcript before the browser paints it, without traversing its history.
  useLayoutEffect(() => {
    if (!historyLoaded) {
      initialScrollRef.current = undefined;
      return;
    }
    const container = scrollContainerRef.current;
    if (!container || initialScrollRef.current === chatEndpoint) return;
    container.scrollTop = container.scrollHeight;
    isNearBottomRef.current = true;
    prevMessageCountRef.current = visibleMessages.length;
    initialScrollRef.current = chatEndpoint;
  }, [historyLoaded, chatEndpoint, visibleMessages.length, streamingContent, streamingParts]);

  useEffect(() => {
    const el = scrollContainerRef.current;
    if (!el) return;
    let lastTop = el.scrollTop;
    const handleScroll = () => {
      const scrollingUp = el.scrollTop < lastTop;
      lastTop = el.scrollTop;
      if (scrollingUp && el.scrollTop <= SCROLL_THRESHOLD && !olderHistoryError && !historyError)
        loadOlder();
      const distance = el.scrollHeight - el.scrollTop - el.clientHeight;
      isNearBottomRef.current = distance <= SCROLL_THRESHOLD;
      setShowScrollBtn(distance > SCROLL_THRESHOLD * 2);
      if (isNearBottomRef.current) setNewMessageCount(0);
    };
    el.addEventListener('scroll', handleScroll, { passive: true });
    // Observe content too: images, code highlighting and streamed text can grow without resizing
    // the scroll viewport. Preserve a reader's position once they have scrolled away from the end.
    const observer = new ResizeObserver(() => {
      if (isNearBottomRef.current) el.scrollTop = el.scrollHeight;
    });
    observer.observe(el);
    if (el.firstElementChild) observer.observe(el.firstElementChild);
    return () => {
      el.removeEventListener('scroll', handleScroll);
      observer.disconnect();
    };
  }, [hasConversation, isStreaming, historyLoaded, loadOlder, olderHistoryError, historyError]);

  useLayoutEffect(() => {
    if (!historyLoaded) return;
    const anchor = prependAnchorRef.current;
    if (anchor && visibleMessages[0]?.id !== anchor.firstId) {
      const el = scrollContainerRef.current;
      const row = Array.from(el?.querySelectorAll<HTMLElement>('[data-history-id]') ?? []).find(
        (item) => item.dataset.historyId === anchor.id,
      );
      if (el && row) el.scrollTop += row.getBoundingClientRect().top - anchor.top;
      prependAnchorRef.current = null;
      prevMessageCountRef.current = visibleMessages.length;
      return;
    }
    if (!loadingOlderHistory && !olderPendingRef.current) prependAnchorRef.current = null;
    const countDelta = visibleMessages.length - prevMessageCountRef.current;
    prevMessageCountRef.current = visibleMessages.length;
    if (userSentRef.current || (countDelta !== 0 && isNearBottomRef.current)) {
      userSentRef.current = false;
      const el = scrollContainerRef.current;
      if (el) el.scrollTop = el.scrollHeight;
      return;
    }
    if (countDelta > 0) setNewMessageCount((prev) => prev + countDelta);
  }, [visibleMessages, historyLoaded, loadingOlderHistory]);

  useEffect(() => {
    onMessageCountChange?.(visibleMessages.length);
  }, [visibleMessages.length, onMessageCountChange]);

  const handleModelSubmit = useCallback(() => {
    const trimmed = modelInput.trim();
    if (!trimmed) return;
    onSetModel?.(trimmed);
    setModelInput('');
    setShowModelInput(false);
  }, [modelInput, onSetModel]);

  const handleThinkingSelect = useCallback(
    (tokens: number) => {
      onSetThinkingTokens?.(tokens);
      setShowThinkingMenu(false);
    },
    [onSetThinkingTokens],
  );

  const handleSend = useCallback(
    (text: string, fileAttachments: FileAttachment[]) => {
      userSentRef.current = true;
      onSend(text, fileAttachments);
    },
    [onSend],
  );

  const handleSendDirected = useCallback(
    (agentParticipants: RoomParticipant[], text: string, fileAttachments: FileAttachment[]) => {
      userSentRef.current = true;
      onSendDirected?.(agentParticipants, text, fileAttachments);
    },
    [onSendDirected],
  );

  const handlePublishEvent = useCallback(
    (target: AgentEventTarget, text: string) => {
      userSentRef.current = true;
      onPublishEvent?.(target, text);
    },
    [onPublishEvent],
  );

  const handlePermissionRespond = useCallback(
    (requestId: string, behavior: PermissionBehavior) => {
      onPermissionRespond?.(requestId, behavior);
    },
    [onPermissionRespond],
  );

  const handleInputRespond = useCallback(
    (requestId: string, values: (string | InputAnswer)[]) => {
      onInputRespond?.(requestId, values);
    },
    [onInputRespond],
  );

  const handleCopy = useCallback(
    (text: string) => {
      if (onCopy) {
        onCopy(text);
        return;
      }
      navigator.clipboard?.writeText(text).catch(() => undefined);
    },
    [onCopy],
  );

  if (!historyLoaded) {
    return (
      <div className={cn('niuu-chat-session-loading', className)} data-testid="history-loading">
        {historyError ? (
          <ErrorState
            title="Could not load conversation"
            message={historyError}
            action={
              onRetryHistory && (
                <button type="button" className="niuu-chat-retry" onClick={onRetryHistory}>
                  Try again
                </button>
              )
            }
          />
        ) : (
          <LoadingState label="Loading conversation…" />
        )}
      </div>
    );
  }

  const isBookmarked = (id: string): boolean => {
    try {
      return localStorage.getItem(`bookmark:${id}`) === '1';
    } catch {
      return false;
    }
  };

  // Render a single visible message exactly as the expanded loop does. Shared
  // by the expanded view and the compact "Worked" disclosure / final answer.
  const renderMessageBody = (msg: (typeof visibleMessages)[number]): ReactNode => {
    if (msg.metadata?.messageType === 'system') {
      return <SystemMessage key={messageRenderKey(msg)} message={msg} />;
    }
    if ((isRoomMode && msg.participant) || isRoomSession || hasWorkers) {
      const worker = msg.participant && workerIds.has(msg.participant.peerId);
      return (
        <div key={messageRenderKey(msg)} id={`msg-${msg.id}`}>
          <RoomMessage
            message={msg}
            workerRoom={hasWorkers}
            showHeader={!hiddenHeaders.has(msg.id)}
            onShowDetail={worker ? openWorker : undefined}
            onCopy={handleCopy}
            onRegenerate={onRegenerate}
            onBookmark={onBookmark}
            bookmarked={isBookmarked(msg.id)}
          />
        </div>
      );
    }
    if (msg.role === 'user') {
      return <UserMessage key={messageRenderKey(msg)} message={msg} />;
    }
    if (msg.status === 'running' && !hasNativeMessageParts(msg.parts)) {
      return (
        <StreamingMessage key={messageRenderKey(msg)} content={msg.content} parts={msg.parts} />
      );
    }
    return (
      <AssistantMessage
        key={messageRenderKey(msg)}
        message={msg}
        showTokenUsage={showTokenUsage}
        onCopy={handleCopy}
        onRegenerate={onRegenerate}
        onForward={onForwardMessage}
        onBookmark={onBookmark}
        bookmarked={isBookmarked(msg.id)}
      />
    );
  };

  const renderSingleMessage = (msg: (typeof visibleMessages)[number]): ReactNode => (
    // Every row is a scroll anchor for history paging, in compact and expanded views alike.
    <div key={messageRenderKey(msg)} data-history-id={msg.id}>
      {renderMessageBody(msg)}
    </div>
  );

  // Compact rendering of one folded turn: question → "Worked" disclosure → answer.
  const renderCompactTurn = (turn: (typeof compactTurns)[number]): ReactNode => {
    const stepCount = turn.intermediaries.length;
    // The "show tool calls and results" eye (showInternal) reveals the work
    // inline: when it is on, every turn's intermediary steps (tool calls/results)
    // are expanded without needing to click each "Worked" disclosure.
    const expanded =
      expandedTurns.has(turn.id) ||
      showInternal ||
      turn.intermediaries.some((message) => message.status === 'error');
    const turnRunning =
      turn.final?.status === 'running' || turn.intermediaries.some((m) => m.status === 'running');

    // Lexi's "Worked · 5 tools · 54s": the tools the turn ran and how long it took. Tool
    // calls are hidden from the folded messages, so they are counted in the transcript.
    const toolCount = turnToolCount(turn);
    const seconds =
      turn.user && turn.final
        ? Math.round((turn.final.createdAt.getTime() - turn.user.createdAt.getTime()) / 1000)
        : NaN;
    const workedFacts = [
      toolCount > 0
        ? `${toolCount} tool${toolCount === 1 ? '' : 's'}`
        : `${stepCount} step${stepCount === 1 ? '' : 's'}`,
      Number.isFinite(seconds) && seconds > 0 ? workedDuration(seconds) : null,
    ].filter(Boolean);
    const workedLabel = turnRunning ? 'Working…' : 'Worked';
    // The answer's speaker heads the turn, above its work, as in Lexi.
    const speakerModel = turn.final ? messageModel(turn.final) : undefined;

    return (
      <div
        key={turn.id}
        className="niuu-chat-compact-turn"
        data-testid="compact-turn"
        data-headed={speakerModel && stepCount > 0 ? true : undefined}
      >
        {turn.leading.map((m) => renderSingleMessage(m))}
        {turn.user && renderSingleMessage(turn.user)}
        {speakerModel && stepCount > 0 && (
          <div className="niuu-chat-turn-speaker">
            <ModelSpeaker model={speakerModel} />
          </div>
        )}
        {stepCount > 0 && (
          <div className="niuu-chat-worked">
            <button
              type="button"
              className="niuu-chat-worked-trigger"
              onClick={() => toggleTurn(turn.id)}
              aria-expanded={expanded}
              data-testid="worked-toggle"
            >
              {expanded ? (
                <ChevronDown className="niuu-chat-control-icon" aria-hidden />
              ) : (
                <ChevronRight className="niuu-chat-control-icon" aria-hidden />
              )}
              {turnRunning ? (
                <Loader2 className="niuu-chat-spinner-icon" aria-hidden />
              ) : (
                <BadgeCheck className="niuu-chat-worked-badge" aria-hidden />
              )}
              <span className="niuu-chat-worked-label">
                {(isRoomMode || isRoomSession) && turn.final?.participant
                  ? `${turn.final.participant.displayName || turn.final.participant.persona}: ${workedLabel}`
                  : workedLabel}
              </span>
              {!turnRunning &&
                workedFacts.map((fact) => (
                  <span key={fact} className="niuu-chat-worked-fact">
                    {fact}
                  </span>
                ))}
            </button>
            {expanded && (
              <div className="niuu-chat-worked-steps" data-testid="worked-steps">
                {turn.intermediaries.map((m) => renderSingleMessage(m))}
              </div>
            )}
          </div>
        )}
        {turn.final && renderSingleMessage(turn.final)}
      </div>
    );
  };

  const renderInlineItem = (item: InlineItem): ReactNode => {
    if (item.kind === 'handoff') {
      return (
        <HandoffRow
          key={item.key}
          event={item.event}
          parties={handoffParties(item.event, participantsMap, workers)}
          onOpenWorker={openWorker}
        />
      );
    }
    if (item.kind === 'attention') {
      const worker = workers.find((candidate) => candidate.peerId === item.event.participantId);
      return (
        <AttentionRow
          key={item.key}
          event={item.event}
          worker={worker && { peerId: worker.peerId, name: worker.name, color: worker.color }}
          onOpenWorker={openWorker}
        />
      );
    }
    const { outcome } = item;
    const worker = workers.find((candidate) => candidate.peerId === outcome.peerId);
    return (
      <WorkerOutcomeCard
        key={item.key}
        outcome={outcome}
        speaker={{
          name: worker?.name ?? outcome.persona,
          color: worker?.color ?? resolveParticipantColor(outcome.peerId),
        }}
      />
    );
  };

  const renderGroup = (group: RoomGroup): ReactNode => {
    if (group.type === 'compact') return renderCompactTurn(group.turn);
    if (group.type === 'thread') {
      return (
        <div key={group.threadId} data-history-id={group.threadId}>
          <ThreadGroup
            messages={group.messages}
            isCollapsed={collapsedThreads.has(group.threadId)}
            onToggle={() => toggleThread(group.threadId)}
          />
        </div>
      );
    }
    return renderSingleMessage(group.message);
  };

  const displayControls = (
    <>
      <ChatConnectionsButton />
      <ChatDisplayControls />
    </>
  );

  return (
    <div className={cn('niuu-chat-outer-grid', className)} data-testid="session-chat">
      <div className="niuu-chat-wrapper">
        {!showToolbar && showDisplayControls && (
          <div className="niuu-chat-display-controls">{displayControls}</div>
        )}
        {/* ── Toolbar ── */}
        {showToolbar && (
          <div className="niuu-chat-toolbar">
            <div className="niuu-chat-toolbar-left">
              <div className="niuu-chat-status-indicator" data-connected={connected}>
                {connected ? (
                  <Wifi className="niuu-chat-status-icon" />
                ) : (
                  <WifiOff className="niuu-chat-status-icon" />
                )}
                <span>{connected ? 'Connected' : 'Disconnected'}</span>
              </div>
              <span className="niuu-chat-message-count">
                {visibleMessages.length} message{visibleMessages.length !== 1 ? 's' : ''}
              </span>
              {visibleMessages.length > 0 && onClear && (
                <button
                  type="button"
                  className="niuu-chat-control-btn"
                  onClick={onClear}
                  title="Clear chat"
                  data-testid="clear-chat"
                >
                  <Trash2Icon className="niuu-chat-control-icon" />
                </button>
              )}
              {showInternalToggle && (
                <button
                  type="button"
                  className={cn(
                    'niuu-chat-control-btn',
                    showInternal && 'niuu-chat-control-btn--active',
                  )}
                  onClick={toggleInternal}
                  title={
                    showInternal ? 'Hide tool calls and results' : 'Show tool calls and results'
                  }
                  aria-pressed={showInternal}
                  data-testid="internal-toggle"
                >
                  {showInternal ? (
                    <Eye className="niuu-chat-control-icon" />
                  ) : (
                    <EyeOff className="niuu-chat-control-icon" />
                  )}
                </button>
              )}
              {displayControls}
            </div>

            {connected && (
              <div className="niuu-chat-toolbar-right">
                <div className="niuu-chat-control-group">
                  {capabilities.set_model && onSetModel && (
                    <button
                      type="button"
                      className="niuu-chat-control-btn"
                      onClick={() => setShowModelInput((prev) => !prev)}
                      title="Switch model"
                      data-testid="model-switch-toggle"
                    >
                      <BrainCircuitIcon className="niuu-chat-control-icon" />
                    </button>
                  )}

                  {capabilities.set_thinking_tokens && onSetThinkingTokens && (
                    <div className="niuu-chat-thinking-wrapper">
                      <button
                        type="button"
                        className="niuu-chat-control-btn"
                        onClick={() => setShowThinkingMenu((prev) => !prev)}
                        title="Set thinking budget"
                        data-testid="thinking-budget-toggle"
                      >
                        <span className="niuu-chat-control-label">Thinking</span>
                      </button>
                      {showThinkingMenu && (
                        <div className="niuu-chat-thinking-menu" data-testid="thinking-menu">
                          {THINKING_PRESETS.map((preset) => (
                            <button
                              key={preset.value}
                              type="button"
                              className="niuu-chat-thinking-option"
                              onClick={() => handleThinkingSelect(preset.value)}
                              data-testid={`thinking-${preset.label}`}
                            >
                              {preset.label}
                            </button>
                          ))}
                        </div>
                      )}
                    </div>
                  )}

                  {capabilities.rewind_files && onRewindFiles && (
                    <button
                      type="button"
                      className="niuu-chat-control-btn"
                      onClick={onRewindFiles}
                      title="Rewind files"
                      data-testid="rewind-files"
                    >
                      <RotateCcwIcon className="niuu-chat-control-icon" />
                    </button>
                  )}
                </div>
              </div>
            )}
          </div>
        )}

        {/* ── Model input bar ── */}
        {showModelInput && connected && capabilities.set_model && (
          <div className="niuu-chat-model-input-bar" data-testid="model-input-bar">
            <input
              type="text"
              className="niuu-chat-model-input"
              value={modelInput}
              onChange={(e) => setModelInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') handleModelSubmit();
                if (e.key === 'Escape') setShowModelInput(false);
              }}
              placeholder="Model ID (e.g. claude-opus-4-6)"
              autoFocus
              aria-label="Model ID input"
            />
            <button
              type="button"
              className="niuu-chat-model-submit-btn"
              onClick={handleModelSubmit}
              data-testid="model-submit"
            >
              Switch
            </button>
          </div>
        )}

        {showWorkersStrip && (hasWorkers || workerSessions.length > 0) && (
          <WorkersStrip
            workers={workers}
            sessions={workerSessions}
            activePeerId={sheetPeerId}
            onSelect={openWorker}
          />
        )}
        {showWorkersStrip && hasWorkers && <ProgressLane stages={stages} onSelect={openWorker} />}
        {historyError && (
          <div className="niuu-chat-history-status" role="alert">
            {historyError}{' '}
            <button className="niuu-chat-retry" onClick={onRetryHistory}>
              Try again
            </button>
          </div>
        )}
        {/* ── Messages ── */}
        {hasConversation || isStreaming || hasOlderHistory ? (
          <div className="niuu-chat-messages-container" ref={scrollContainerRef}>
            <HistoryDetailsContext.Provider value={historyEndpoint ?? chatEndpoint}>
              <ToolImageProvider endpoint={historyEndpoint ?? chatEndpoint}>
                <div className="niuu-chat-messages-inner">
                  {(hasOlderHistory || loadingOlderHistory || olderHistoryError) && (
                    <div className="niuu-chat-history-status">
                      {olderHistoryError && <p role="alert">{olderHistoryError}</p>}
                      <button
                        type="button"
                        className="niuu-chat-retry"
                        disabled={loadingOlderHistory}
                        onClick={loadOlder}
                      >
                        {loadingOlderHistory
                          ? 'Loading earlier messages…'
                          : olderHistoryError
                            ? 'Retry earlier messages'
                            : 'Load earlier messages'}
                      </button>
                    </div>
                  )}
                  {useCompact && !roomView
                    ? compactTurns.map((turn) => renderCompactTurn(turn))
                    : timeline.map((entry) =>
                        entry.kind === 'inline'
                          ? renderInlineItem(entry.item)
                          : renderGroup(entry.group),
                      )}

                  {/* Streaming indicator */}
                  {isStreaming && (
                    <StreamingMessage
                      content={streamingContent ?? ''}
                      parts={
                        showInternal
                          ? streamingParts
                          : streamingParts && hideToolParts(streamingParts)
                      }
                      model={streamingModel}
                    />
                  )}

                  <div ref={messagesEndRef} />
                </div>
              </ToolImageProvider>
            </HistoryDetailsContext.Provider>

            {showScrollBtn && (
              <button
                type="button"
                className="niuu-chat-scroll-to-bottom"
                onClick={() => scrollToBottom('smooth')}
                aria-label="Scroll to bottom"
              >
                <ArrowDownIcon className="niuu-chat-scroll-to-bottom-icon" />
                {newMessageCount > 0 && (
                  <span className="niuu-chat-scroll-to-bottom-badge">
                    {newMessageCount > 99 ? '99+' : newMessageCount}
                  </span>
                )}
              </button>
            )}
          </div>
        ) : (
          <SessionEmptyChat
            sessionName={sessionName}
            onSuggestionClick={(text) => handleSend(text, [])}
            suggestionsEnabled={!eventRouting}
          />
        )}

        {/* ── Input area ── */}
        <div className="niuu-chat-input-area-outer">
          <div className="niuu-chat-input-area-inner">
            {pendingPermissions.length > 0 &&
              (renderPermissions ? (
                renderPermissions(pendingPermissions, handlePermissionRespond)
              ) : (
                <DefaultPermissionRequests
                  permissions={pendingPermissions}
                  onRespond={handlePermissionRespond}
                />
              ))}
            <PlanDock plan={plan} agents={agents} />
            {pendingInputRequests.length > 0 && (
              <div
                className="niuu-chat-request-list"
                role="region"
                aria-label="Pending input requests"
              >
                {pendingInputRequests.map((request) => (
                  <QuestionBand
                    key={request.requestId}
                    request={request}
                    onRespond={handleInputRespond}
                    onDismiss={onInputDismiss}
                  />
                ))}
              </div>
            )}
            {aboveComposer}
            <ChatInput
              onSend={handleSend}
              onSendDirected={handleSendDirected}
              onPublishEvent={handlePublishEvent}
              eventRouting={eventRouting}
              isLoading={working}
              onStop={onStop ?? (() => undefined)}
              disabled={!connected}
              stopDisabled={!capabilities.interrupt}
              capabilities={capabilities}
              effort={effort}
              runtimeOptions={runtimeOptions}
              onSetEffort={onSetEffort}
              onSetRuntimeModel={onSetRuntimeModel}
              runtimeOptionsError={runtimeOptionsError}
              effortError={effortError}
              sessionHost={sessionHost}
              chatEndpoint={chatEndpoint}
              availableCommands={availableCommands}
              participants={participants}
              onFetchFiles={onFetchFiles}
            />
          </div>
        </div>
        {sheetWorker && sheetParticipant && (
          <HistoryDetailsContext.Provider value={historyEndpoint ?? chatEndpoint}>
            <ToolImageProvider endpoint={historyEndpoint ?? chatEndpoint}>
              <WorkerSheet
                worker={sheetWorker}
                participant={sheetParticipant}
                messages={messages}
                events={agentEvents.get(sheetWorker.peerId) ?? []}
                onClose={closeWorker}
                onSendDirected={onSendDirected ? handleSendDirected : undefined}
              />
            </ToolImageProvider>
          </HistoryDetailsContext.Provider>
        )}
      </div>
    </div>
  );
}
