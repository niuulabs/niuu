import { useCallback, useMemo, useState, useRef, useEffect, type ReactNode } from 'react';
import {
  Wifi,
  WifiOff,
  BrainCircuitIcon,
  RotateCcwIcon,
  ArrowDownIcon,
  Eye,
  EyeOff,
  Trash2Icon,
} from 'lucide-react';
import { cn } from '../../../utils/cn';
import { useRoomState } from '../../hooks/useRoomState';
import { UserMessage, AssistantMessage, StreamingMessage, SystemMessage } from '../ChatMessages';
import { RoomMessage } from '../RoomMessage';
import { ThreadGroup } from '../ThreadGroup';
import { MeshCascadePanel } from '../MeshCascadePanel';
import { MeshSidebar } from '../MeshSidebar';
import { AgentDetailPanel } from '../AgentDetailPanel';
import { ChatInput } from '../ChatInput';
import { SessionEmptyChat } from '../ChatEmptyStates';
import { MarkdownContent } from '../MarkdownContent';
import { extractOutcomeBlock } from '../OutcomeCard';
import { Dialog, DialogContent } from '../../../primitives/Dialog';
import type {
  AgentInternalEvent,
  ChatMessage,
  ChatMessagePart,
  RoomParticipant,
  MeshEvent,
  PermissionRequest,
  PermissionBehavior,
  FileEntry,
  SessionCapabilities,
} from '../../types';
import type { FileAttachment } from '../../hooks/useFileAttachments';
import type { SlashCommand } from '../../utils/slashCommands';
import './SessionChat.css';

const SCROLL_THRESHOLD = 150;
const SCROLL_LOCK_MS = 500;

const THINKING_PRESETS = [
  { label: '4K', value: 4096 },
  { label: '8K', value: 8192 },
  { label: '16K', value: 16384 },
  { label: '32K', value: 32768 },
] as const;

function stringifyOutcomeValue(value: unknown): string {
  if (value == null) return '';
  if (typeof value === 'string') return value;
  if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

function pushOutcomeField(lines: string[], key: string, value: unknown): void {
  const text = stringifyOutcomeValue(value);
  if (!text) return;
  if (text.includes('\n')) {
    lines.push(`${key}: |`);
    for (const line of text.split('\n')) {
      lines.push(`  ${line}`);
    }
    return;
  }
  lines.push(`${key}: ${text}`);
}

function formatOutcomeMarkdown(event: Extract<MeshEvent, { type: 'outcome' }>): string {
  const fields = event.fields ?? {};
  const lines: string[] = [];
  pushOutcomeField(lines, 'verdict', event.verdict ?? fields.verdict);
  pushOutcomeField(lines, 'summary', event.summary ?? fields.summary);

  for (const [key, value] of Object.entries(fields)) {
    if (key === 'verdict' || key === 'summary' || key === 'success') continue;
    pushOutcomeField(lines, key, value);
  }

  if (lines.length === 0) {
    pushOutcomeField(lines, 'event_type', event.eventType);
  }

  return `### ${event.persona}\n\n\`\`\`outcome\n${lines.join('\n')}\n\`\`\``;
}

function isOutcomeMessageContent(content: string): boolean {
  return (
    content.includes('```outcome') ||
    content.includes('---outcome---') ||
    content.includes('<outcome>')
  );
}

function formatOutcomeDialogContent(
  messageContent: string | undefined,
  event: Extract<MeshEvent, { type: 'outcome' }>,
): string {
  if (messageContent) {
    const extracted = extractOutcomeBlock(messageContent);
    if (extracted) {
      return `\`\`\`outcome\n${extracted.raw}\n\`\`\``;
    }
  }
  return formatOutcomeMarkdown(event);
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
  /** Room participants map (peerId → meta) */
  participants?: ReadonlyMap<string, RoomParticipant>;
  /** Mesh events for the cascade panel */
  meshEvents?: readonly MeshEvent[];
  /** Per-agent internal event frames */
  agentEvents?: ReadonlyMap<string, readonly AgentInternalEvent[]>;
  /** Pending permission requests */
  pendingPermissions?: PermissionRequest[];
  /** Available slash commands */
  availableCommands?: readonly SlashCommand[];
  /** Which server-side capabilities are active */
  capabilities?: SessionCapabilities;
  /** Pod hostname for file listing */
  sessionHost?: string | null;
  /** Full chat endpoint URL */
  chatEndpoint?: string | null;
  /** Session name shown in empty state */
  sessionName?: string;
  /** Optional extra class on the outer wrapper */
  className?: string;

  /* ── Callbacks ── */
  onSend: (text: string, attachments: FileAttachment[]) => void;
  onSendDirected?: (
    participants: RoomParticipant[],
    text: string,
    attachments: FileAttachment[],
  ) => void;
  onStop?: () => void;
  onClear?: () => void;
  onSetModel?: (model: string) => void;
  onSetThinkingTokens?: (tokens: number) => void;
  onRewindFiles?: () => void;
  onCopy?: (text: string) => void;
  onRegenerate?: (messageId: string) => void;
  onBookmark?: (messageId: string, bookmarked: boolean) => void;
  onPermissionRespond?: (requestId: string, behavior: PermissionBehavior) => void;
  onFetchFiles?: (path: string, apiBase: string) => Promise<FileEntry[]>;
  onMessageCountChange?: (count: number) => void;

  /** Render slot for permission UI — receives pending list and respond callback */
  renderPermissions?: (
    permissions: PermissionRequest[],
    onRespond: (requestId: string, behavior: PermissionBehavior) => void,
  ) => ReactNode;
}

type SelectedOutcomeDetail = {
  event: Extract<MeshEvent, { type: 'outcome' }>;
  content: string;
};

export function SessionChat({
  messages,
  streamingContent,
  streamingParts,
  streamingModel,
  connected = false,
  historyLoaded = true,
  participants = new Map(),
  meshEvents = [],
  agentEvents = new Map(),
  pendingPermissions = [],
  availableCommands,
  capabilities = {},
  sessionHost = null,
  chatEndpoint = null,
  sessionName = 'Session',
  className,
  onSend,
  onSendDirected,
  onStop,
  onClear,
  onSetModel,
  onSetThinkingTokens,
  onRewindFiles,
  onCopy,
  onRegenerate,
  onBookmark,
  onPermissionRespond,
  onFetchFiles,
  onMessageCountChange,
  renderPermissions,
}: SessionChatProps) {
  const {
    isRoomMode,
    activeFilter,
    setActiveFilter,
    showInternal,
    toggleInternal,
    visibleMessages,
    collapsedThreads,
    toggleThread,
  } = useRoomState(messages, participants);

  const [modelInput, setModelInput] = useState('');
  const [showModelInput, setShowModelInput] = useState(false);
  const [showThinkingMenu, setShowThinkingMenu] = useState(false);
  const [showScrollBtn, setShowScrollBtn] = useState(false);
  const [newMessageCount, setNewMessageCount] = useState(0);
  const [selectedOutcomeDetail, setSelectedOutcomeDetail] = useState<SelectedOutcomeDetail | null>(
    null,
  );
  const [peerSidebarCollapsed, setPeerSidebarCollapsed] = useState(false);
  const [cascadePanelCollapsed, setCascadePanelCollapsed] = useState(false);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const scrollContainerRef = useRef<HTMLDivElement>(null);
  const isNearBottomRef = useRef(true);
  const userSentRef = useRef(false);
  const prevMessageCountRef = useRef(0);
  const scrollLockUntilRef = useRef(0);

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

  const selectedAgentId: string | null = activeFilter !== 'all' ? activeFilter : null;
  const [detailPeerId, setDetailPeerId] = useState<string | null>(null);
  const effectiveRightPanelMode = detailPeerId
    ? 'detail'
    : meshEvents.length > 0
      ? 'cascade'
      : null;

  const [highlightedMsgId, setHighlightedMsgId] = useState<string | null>(null);
  const findClosestParticipantMessage = useCallback(
    (event: MeshEvent, outcomeOnly = false) => {
      const targetTime = event.timestamp.getTime();
      const participantMsgs = messages.filter(
        (message) =>
          message.participant?.peerId === event.participantId && message.role === 'assistant',
      );
      const scopedMessages =
        outcomeOnly || event.type === 'outcome'
          ? participantMsgs.filter((message) => isOutcomeMessageContent(message.content))
          : participantMsgs;
      const candidateMessages = scopedMessages.length > 0 ? scopedMessages : participantMsgs;
      if (candidateMessages.length === 0) return null;
      return candidateMessages.reduce((best, message) => {
        const dt = Math.abs(message.createdAt.getTime() - targetTime);
        const bestDt = Math.abs(best.createdAt.getTime() - targetTime);
        return dt < bestDt ? message : best;
      });
    },
    [messages],
  );

  const handleOutcomeClick = useCallback(
    (event: MeshEvent) => {
      const closest = findClosestParticipantMessage(event);
      if (!closest) return;
      const el = document.getElementById(`msg-${closest.id}`);
      if (el) {
        el.scrollIntoView({ behavior: 'smooth', block: 'center' });
        setHighlightedMsgId(closest.id);
        setTimeout(() => setHighlightedMsgId(null), 2000);
      }
    },
    [findClosestParticipantMessage],
  );

  const handleOutcomeShowDetails = useCallback(
    (event: Extract<MeshEvent, { type: 'outcome' }>) => {
      const closest = findClosestParticipantMessage(event, true);
      setSelectedOutcomeDetail({
        event,
        content: formatOutcomeDialogContent(closest?.content, event),
      });
    },
    [findClosestParticipantMessage],
  );

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

  const scrollToBottom = useCallback((behavior: ScrollBehavior = 'smooth') => {
    messagesEndRef.current?.scrollIntoView?.({ behavior });
    setNewMessageCount(0);
  }, []);

  useEffect(() => {
    const el = scrollContainerRef.current;
    if (!el) return;
    const handleScroll = () => {
      const distanceFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
      isNearBottomRef.current = distanceFromBottom <= SCROLL_THRESHOLD;
      setShowScrollBtn(distanceFromBottom > SCROLL_THRESHOLD * 2);
      if (isNearBottomRef.current) setNewMessageCount(0);
    };
    el.addEventListener('scroll', handleScroll, { passive: true });
    let prevHeight = el.scrollHeight;
    const resizeObserver = new ResizeObserver(() => {
      const newHeight = el.scrollHeight;
      if (newHeight !== prevHeight) {
        prevHeight = newHeight;
        scrollLockUntilRef.current = Date.now() + SCROLL_LOCK_MS;
      }
    });
    resizeObserver.observe(el);
    return () => {
      el.removeEventListener('scroll', handleScroll);
      resizeObserver.disconnect();
    };
  }, [hasConversation]);

  useEffect(() => {
    const messageCount = visibleMessages.length;
    const countDelta = messageCount - prevMessageCountRef.current;
    prevMessageCountRef.current = messageCount;
    if (userSentRef.current) {
      userSentRef.current = false;
      messagesEndRef.current?.scrollIntoView?.({ behavior: 'smooth' });
      return;
    }
    if (countDelta === 0) return;
    if (Date.now() < scrollLockUntilRef.current) return;
    if (isNearBottomRef.current) {
      messagesEndRef.current?.scrollIntoView?.({ behavior: 'smooth' });
      return;
    }
    setNewMessageCount((prev) => prev + countDelta);
  }, [visibleMessages.length]);

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

  const handlePermissionRespond = useCallback(
    (requestId: string, behavior: PermissionBehavior) => {
      onPermissionRespond?.(requestId, behavior);
    },
    [onPermissionRespond],
  );

  const handleSelectAgent = useCallback(
    (peerId: string) => {
      setDetailPeerId(null);
      setActiveFilter(activeFilter === peerId ? 'all' : peerId);
    },
    [activeFilter, setActiveFilter],
  );

  const handleShowDetail = useCallback((peerId: string) => {
    setDetailPeerId(peerId);
  }, []);

  const handleCloseDetail = useCallback(() => {
    setDetailPeerId(null);
  }, []);

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

  const handleRegenerate = useCallback(
    (messageId: string) => {
      if (onRegenerate) {
        onRegenerate(messageId);
        return;
      }
      const idx = messages.findIndex((m) => m.id === messageId);
      if (idx < 0) return;
      for (let i = idx - 1; i >= 0; i--) {
        const m = messages[i];
        if (m && m.role === 'user') {
          onSend(m.content, []);
          return;
        }
      }
    },
    [messages, onRegenerate, onSend],
  );

  const handleBookmark = useCallback(
    (id: string, bookmarked: boolean) => {
      if (onBookmark) {
        onBookmark(id, bookmarked);
        return;
      }
      const key = `bookmark:${id}`;
      try {
        if (bookmarked) {
          localStorage.setItem(key, '1');
        } else {
          localStorage.removeItem(key);
        }
      } catch {
        // localStorage may not be available
      }
    },
    [onBookmark],
  );

  const hasSidebar = Array.from(participants.values()).some(
    (participant) => participant.participantType === 'ravn',
  );
  const showRightPanel = effectiveRightPanelMode !== null;
  const hasRunningAssistantMessage = visibleMessages.some(
    (message) => message.role === 'assistant' && message.status === 'running',
  );
  const isStreaming =
    !hasRunningAssistantMessage &&
    (!!streamingContent || (streamingParts && streamingParts.length > 0));

  return (
    <div
      className={cn('niuu-chat-outer-grid', className)}
      data-has-sidebar={hasSidebar || undefined}
      data-right-panel={showRightPanel || undefined}
      data-right-panel-collapsed={
        showRightPanel && effectiveRightPanelMode === 'cascade' && cascadePanelCollapsed
          ? true
          : undefined
      }
      data-testid="session-chat"
    >
      {hasSidebar && (
        <MeshSidebar
          participants={participants}
          selectedPeerId={selectedAgentId}
          onSelectPeer={handleSelectAgent}
          collapsed={peerSidebarCollapsed}
          onToggleCollapsed={() => setPeerSidebarCollapsed((value) => !value)}
        />
      )}

      <div className="niuu-chat-wrapper">
        {/* ── Toolbar ── */}
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

                {isRoomMode && (
                  <button
                    type="button"
                    className={cn(
                      'niuu-chat-control-btn',
                      showInternal && 'niuu-chat-control-btn--active',
                    )}
                    onClick={toggleInternal}
                    title={showInternal ? 'Hide internal messages' : 'Show internal messages'}
                    aria-pressed={showInternal}
                    data-testid="internal-toggle"
                  >
                    {showInternal ? (
                      <Eye className="niuu-chat-control-icon" />
                    ) : (
                      <EyeOff className="niuu-chat-control-icon" />
                    )}
                    <span className="niuu-chat-control-label">Internal</span>
                  </button>
                )}
              </div>
            </div>
          )}
        </div>

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

        {/* ── History loading ── */}
        {!historyLoaded && connected && (
          <div className="niuu-chat-history-loading" data-testid="history-loading">
            Loading conversation...
          </div>
        )}

        {/* ── Messages ── */}
        {hasConversation || isStreaming ? (
          <div className="niuu-chat-messages-container" ref={scrollContainerRef}>
            <div className="niuu-chat-messages-inner">
              {renderedGroups.map((group) => {
                if (group.type === 'thread') {
                  return (
                    <ThreadGroup
                      key={group.threadId}
                      messages={group.messages}
                      isCollapsed={collapsedThreads.has(group.threadId)}
                      onToggle={() => toggleThread(group.threadId)}
                    />
                  );
                }

                const msg = group.message;
                if (msg.metadata?.messageType === 'system') {
                  return <SystemMessage key={msg.id} message={msg} />;
                }

                if ((isRoomMode && msg.participant) || isRoomSession) {
                  return (
                    <div
                      key={msg.id}
                      id={`msg-${msg.id}`}
                      data-highlighted={highlightedMsgId === msg.id || undefined}
                    >
                      <RoomMessage
                        message={msg}
                        onSelectAgent={handleSelectAgent}
                        selectedAgentId={selectedAgentId}
                        onShowDetail={msg.participant ? handleShowDetail : undefined}
                        onCopy={handleCopy}
                        onRegenerate={handleRegenerate}
                        onBookmark={handleBookmark}
                        bookmarked={(() => {
                          try {
                            return localStorage.getItem(`bookmark:${msg.id}`) === '1';
                          } catch {
                            return false;
                          }
                        })()}
                      />
                    </div>
                  );
                }

                if (msg.role === 'user') {
                  return <UserMessage key={msg.id} message={msg} />;
                }
                if (msg.status === 'running') {
                  return <StreamingMessage key={msg.id} content={msg.content} parts={msg.parts} />;
                }
                return (
                  <AssistantMessage
                    key={msg.id}
                    message={msg}
                    onCopy={handleCopy}
                    onRegenerate={handleRegenerate}
                    onBookmark={handleBookmark}
                    bookmarked={(() => {
                      try {
                        return localStorage.getItem(`bookmark:${msg.id}`) === '1';
                      } catch {
                        return false;
                      }
                    })()}
                  />
                );
              })}

              {/* Streaming indicator */}
              {isStreaming && (
                <StreamingMessage
                  content={streamingContent ?? ''}
                  parts={streamingParts}
                  model={streamingModel}
                />
              )}

              <div ref={messagesEndRef} />
            </div>

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
          />
        )}

        {/* ── Input area ── */}
        <div className="niuu-chat-input-area-outer">
          <div className="niuu-chat-input-area-inner">
            {pendingPermissions.length > 0 && renderPermissions
              ? renderPermissions(pendingPermissions, handlePermissionRespond)
              : null}
            <ChatInput
              onSend={handleSend}
              onSendDirected={handleSendDirected}
              isLoading={false}
              onStop={onStop ?? (() => undefined)}
              disabled={!connected}
              stopDisabled={!capabilities.interrupt}
              sessionHost={sessionHost}
              chatEndpoint={chatEndpoint}
              availableCommands={availableCommands}
              participants={participants}
              onFetchFiles={onFetchFiles}
            />
          </div>
        </div>
      </div>

      <Dialog
        open={selectedOutcomeDetail !== null}
        onOpenChange={(open) => {
          if (!open) setSelectedOutcomeDetail(null);
        }}
      >
        {selectedOutcomeDetail && (
          <DialogContent
            title={`${selectedOutcomeDetail.event.persona} outcome`}
            description={selectedOutcomeDetail.event.eventType}
            className="niuu-chat-outcome-dialog"
          >
            <MarkdownContent content={selectedOutcomeDetail.content} />
          </DialogContent>
        )}
      </Dialog>

      {showRightPanel && effectiveRightPanelMode === 'cascade' && meshEvents.length > 0 && (
        <MeshCascadePanel
          events={meshEvents}
          onEventClick={handleOutcomeClick}
          onOutcomeShowDetails={handleOutcomeShowDetails}
          collapsed={cascadePanelCollapsed}
          onToggleCollapsed={() => setCascadePanelCollapsed((value) => !value)}
        />
      )}

      {showRightPanel &&
        effectiveRightPanelMode === 'detail' &&
        detailPeerId &&
        participants.get(detailPeerId) && (
          <AgentDetailPanel
            participant={participants.get(detailPeerId)!}
            events={agentEvents.get(detailPeerId) ?? []}
            onClose={handleCloseDetail}
          />
        )}
    </div>
  );
}
