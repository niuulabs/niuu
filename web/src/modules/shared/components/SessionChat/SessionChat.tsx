import { useCallback, useMemo, useState, useRef, useEffect } from 'react';
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
import { PermissionStack } from '@/modules/shared/components/PermissionDialog';
import { useSkuldChat } from '@/modules/shared/hooks/useSkuldChat';
import { useRoomState } from '@/modules/shared/hooks/useRoomState';
import type {
  PermissionBehavior,
  ContentBlock,
  AttachmentMeta,
  ParticipantMeta,
  RoomParticipant,
  MeshEvent,
} from '@/modules/shared/hooks/useSkuldChat';
import { cn } from '@/utils';
import { UserMessage, AssistantMessage, StreamingMessage, SystemMessage } from './ChatMessages';
import { RoomMessage } from './RoomMessage';
import { ThreadGroup } from './ThreadGroup';
import { MeshCascadePanel } from './MeshCascadePanel';
import { MeshSidebar } from './MeshSidebar';
import { ChatInput } from './ChatInput';
import { SessionEmptyChat } from './ChatEmptyStates';
import type { FileAttachment } from './useFileAttachments';
import styles from './SessionChat.module.css';

const SCROLL_THRESHOLD = 150;
const SCROLL_LOCK_MS = 500;

interface SessionChatProps {
  /** WebSocket URL for the chat — e.g. wss://host/session */
  url: string | null;
  /** Optional class name for the outer wrapper */
  className?: string;
  /** Called when the visible message count changes */
  onMessageCountChange?: (count: number) => void;
  /** Skuld pod hostname for direct API calls (file listing, etc.) */
  sessionHost?: string | null;
  /** Full chat endpoint URL for gateway-routed sessions */
  chatEndpoint?: string | null;
}

const THINKING_PRESETS = [
  { label: '4K', value: 4096 },
  { label: '8K', value: 8192 },
  { label: '16K', value: 16384 },
  { label: '32K', value: 32768 },
] as const;

export function SessionChat({
  url,
  className,
  onMessageCountChange,
  sessionHost = null,
  chatEndpoint = null,
}: SessionChatProps) {
  const {
    messages,
    participants,
    meshEvents,
    connected,
    isRunning,
    historyLoaded,
    pendingPermissions,
    sendMessage,
    sendDirectedMessages,
    respondToPermission,
    sendInterrupt,
    sendSetModel,
    sendSetMaxThinkingTokens,
    sendRewindFiles,
    clearMessages,
    availableCommands,
    capabilities,
  } = useSkuldChat(url);

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
  const [rightPanelMode, _setRightPanelMode] = useState<'cascade' | null>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const scrollContainerRef = useRef<HTMLDivElement>(null);
  const isNearBottomRef = useRef(true);
  const userSentRef = useRef(false);
  const prevMessageCountRef = useRef(0);
  const scrollLockUntilRef = useRef(0);

  // Build a map of peerId → ParticipantMeta from all messages that carry participant data
  const participantsMap = useMemo<Map<string, ParticipantMeta>>(() => {
    const map = new Map<string, ParticipantMeta>();
    for (const msg of messages) {
      if (msg.participant) {
        map.set(msg.participant.peerId, msg.participant);
      }
    }
    return map;
  }, [messages]);

  const isRoomSession = participantsMap.size > 0;

  const handleSelectAgent = useCallback(
    (peerId: string) => {
      setActiveFilter(activeFilter === peerId ? 'all' : peerId);
    },
    [activeFilter, setActiveFilter]
  );

  // Auto-show cascade panel when mesh events exist
  const effectiveRightPanelMode = rightPanelMode ?? (meshEvents.length > 0 ? 'cascade' : null);
  const selectedAgentId: string | null = activeFilter !== 'all' ? activeFilter : null;

  // Scroll to message closest to an outcome event
  const [highlightedMsgId, setHighlightedMsgId] = useState<string | null>(null);
  const handleOutcomeClick = useCallback(
    (event: MeshEvent) => {
      if (event.type !== 'outcome') return;
      // Find the closest room_message from this participant by timestamp
      const targetTime = event.timestamp.getTime();
      const participantMsgs = messages.filter(
        m => m.participant?.peerId === event.participantId && m.role === 'assistant'
      );
      if (participantMsgs.length === 0) return;
      // Find closest by time (last message before or at outcome time)
      const closest = participantMsgs.reduce((best, m) => {
        const dt = Math.abs(m.createdAt.getTime() - targetTime);
        const bestDt = Math.abs(best.createdAt.getTime() - targetTime);
        return dt < bestDt ? m : best;
      });
      // Scroll to it
      const el = document.getElementById(`msg-${closest.id}`);
      if (el) {
        el.scrollIntoView({ behavior: 'smooth', block: 'center' });
        setHighlightedMsgId(closest.id);
        setTimeout(() => setHighlightedMsgId(null), 2000);
      }
    },
    [messages]
  );

  // Show welcome when only system messages exist (no real user/assistant conversation)
  const hasConversation = useMemo(
    () =>
      messages.some(m => m.role === 'user' || (m.role === 'assistant' && !m.metadata?.messageType)),
    [messages]
  );

  // Group consecutive internal messages by threadId for ThreadGroup rendering
  type MessageGroup =
    | { type: 'single'; message: (typeof visibleMessages)[number] }
    | { type: 'thread'; threadId: string; messages: typeof visibleMessages };

  // Thread grouping: consecutive internal messages with same threadId collapse into ThreadGroup
  const renderedGroups = useMemo((): MessageGroup[] => {
    if (!isRoomMode || !showInternal)
      return visibleMessages.map(m => ({ type: 'single', message: m }));

    const result: MessageGroup[] = [];
    let i = 0;
    while (i < visibleMessages.length) {
      const msg = visibleMessages[i];
      if (msg.visibility === 'internal' && msg.threadId) {
        const threadId = msg.threadId;
        const threadMsgs: (typeof visibleMessages)[number][] = [msg];
        let j = i + 1;
        while (j < visibleMessages.length) {
          const next = visibleMessages[j];
          if (next.visibility === 'internal' && next.threadId === threadId) {
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

  // Track scroll position with passive listener
  useEffect(() => {
    const el = scrollContainerRef.current;
    if (!el) return;

    const handleScroll = () => {
      const distanceFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
      isNearBottomRef.current = distanceFromBottom <= SCROLL_THRESHOLD;
      setShowScrollBtn(distanceFromBottom > SCROLL_THRESHOLD * 2);

      if (isNearBottomRef.current) {
        setNewMessageCount(0);
      }
    };

    el.addEventListener('scroll', handleScroll, { passive: true });

    // Watch for content height changes (code block expand/collapse, image load)
    // and suppress auto-scroll briefly so the viewport doesn't jump
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

  // Auto-scroll only on new messages or user send — never on DOM resize
  useEffect(() => {
    const messageCount = visibleMessages.length;
    const countDelta = messageCount - prevMessageCountRef.current;
    prevMessageCountRef.current = messageCount;

    // User just sent a message — always scroll
    if (userSentRef.current) {
      userSentRef.current = false;
      messagesEndRef.current?.scrollIntoView?.({ behavior: 'smooth' });
      return;
    }

    // No new messages — don't scroll (prevents scroll on code block expand)
    if (countDelta === 0) return;

    // Scroll lock active (code block expand/collapse just happened)
    if (Date.now() < scrollLockUntilRef.current) return;

    // New messages arrived while near bottom — auto-scroll
    if (isNearBottomRef.current) {
      messagesEndRef.current?.scrollIntoView?.({ behavior: 'smooth' });
      return;
    }

    // User is scrolled up — show count of new messages
    setNewMessageCount(prev => prev + countDelta);
  }, [visibleMessages.length]);

  const handlePermissionRespond = useCallback(
    (requestId: string, behavior: PermissionBehavior) => {
      respondToPermission(requestId, behavior);
    },
    [respondToPermission]
  );

  const handleModelSubmit = useCallback(() => {
    const trimmed = modelInput.trim();
    if (!trimmed) {
      return;
    }
    sendSetModel(trimmed);
    setModelInput('');
    setShowModelInput(false);
  }, [modelInput, sendSetModel]);

  const handleThinkingSelect = useCallback(
    (tokens: number) => {
      sendSetMaxThinkingTokens(tokens);
      setShowThinkingMenu(false);
    },
    [sendSetMaxThinkingTokens]
  );

  const handleSend = useCallback(
    (text: string, fileAttachments: FileAttachment[]) => {
      userSentRef.current = true;

      // Only image files with compressed blobs can be transmitted as content blocks.
      // Non-image files are filtered out so metadata stays consistent with actual
      // content blocks sent over the wire.
      const imageAttachments = fileAttachments.filter(
        (fa): fa is FileAttachment & { compressed: Blob } =>
          fa.file.type.startsWith('image/') && fa.compressed !== null
      );

      if (imageAttachments.length === 0) {
        sendMessage(text);
        return;
      }

      const attachmentMeta: AttachmentMeta[] = imageAttachments.map(fa => ({
        name: fa.name,
        type: 'image' as const,
        size: fa.compressed.size,
        contentType: 'image/jpeg',
      }));

      // Pre-allocate to preserve ordering: contentBlocks[i] matches attachmentMeta[i]
      const contentBlocks: (ContentBlock | null)[] = new Array(imageAttachments.length).fill(null);
      let processedCount = 0;

      const checkComplete = () => {
        processedCount += 1;
        if (processedCount < imageAttachments.length) return;
        // Filter out failed reads, keep meta in sync
        const finalBlocks: ContentBlock[] = [];
        const finalMeta: AttachmentMeta[] = [];
        for (let i = 0; i < contentBlocks.length; i++) {
          const block = contentBlocks[i];
          if (block) {
            finalBlocks.push(block);
            finalMeta.push(attachmentMeta[i]);
          }
        }
        sendMessage(text, finalBlocks, finalMeta);
      };

      imageAttachments.forEach((fa, index) => {
        const reader = new FileReader();
        reader.onload = () => {
          const base64 = (reader.result as string).split(',')[1];
          contentBlocks[index] = {
            type: 'image',
            source: {
              type: 'base64',
              media_type: 'image/jpeg',
              data: base64,
            },
          };
          checkComplete();
        };
        reader.onerror = () => {
          // Skip failed image but still send the message
          checkComplete();
        };
        reader.readAsDataURL(fa.compressed);
      });
    },
    [sendMessage]
  );

  const handleSendDirected = useCallback(
    (
      agentParticipants: RoomParticipant[],
      text: string,
      // TODO(NIU-607): directed_message does not support file attachments yet;
      // the binary payload transport is tracked separately. File paths are already
      // prepended as @{path} prefixes in `text` by ChatInput, so context is not lost.
      _fileAttachments: FileAttachment[]
    ) => {
      userSentRef.current = true;
      sendDirectedMessages(
        agentParticipants.map(p => p.peerId),
        text
      );
    },
    [sendDirectedMessages]
  );

  const handleStop = useCallback(() => {
    sendInterrupt();
  }, [sendInterrupt]);

  const handleCopy = useCallback((text: string) => {
    navigator.clipboard?.writeText(text);
  }, []);

  const handleBookmark = useCallback((id: string, bookmarked: boolean) => {
    const key = `bookmark:${id}`;
    if (bookmarked) {
      localStorage.setItem(key, '1');
    } else {
      localStorage.removeItem(key);
    }
  }, []);

  const handleRegenerate = useCallback(
    (messageId: string) => {
      const idx = messages.findIndex(m => m.id === messageId);
      if (idx < 0) {
        return;
      }
      for (let i = idx - 1; i >= 0; i--) {
        if (messages[i].role === 'user') {
          sendMessage(messages[i].content);
          return;
        }
      }
    },
    [messages, sendMessage]
  );

  // Report visible message count to parent for sidebar sync
  useEffect(() => {
    onMessageCountChange?.(visibleMessages.length);
  }, [visibleMessages.length, onMessageCountChange]);

  const hasSidebar = participants.size > 0;
  const showRightPanel = effectiveRightPanelMode !== null;

  return (
    <div
      className={cn(styles.outerGrid, className)}
      data-has-sidebar={hasSidebar ? 'true' : undefined}
      data-right-panel={showRightPanel ? 'true' : undefined}
    >
      {hasSidebar && (
        <MeshSidebar
          participants={participants}
          selectedPeerId={selectedAgentId}
          onSelectPeer={handleSelectAgent}
        />
      )}
      <div className={styles.wrapper}>
        <div className={styles.toolbar}>
          <div className={styles.toolbarLeft}>
            <div className={styles.statusIndicator} data-connected={connected}>
              {connected ? (
                <Wifi className={styles.statusIcon} />
              ) : (
                <WifiOff className={styles.statusIcon} />
              )}
              <span>{connected ? 'Connected' : 'Disconnected'}</span>
            </div>
            <span className={styles.messageCount}>
              {visibleMessages.length} message{visibleMessages.length !== 1 ? 's' : ''}
            </span>
            {visibleMessages.length > 0 && (
              <button
                type="button"
                className={styles.controlBtn}
                onClick={clearMessages}
                title="Clear chat"
                data-testid="clear-chat"
              >
                <Trash2Icon className={styles.controlIcon} />
              </button>
            )}
          </div>

          {connected && (
            <div className={styles.toolbarRight}>
              <div className={styles.controlGroup}>
                {capabilities.set_model && (
                  <button
                    type="button"
                    className={styles.controlBtn}
                    onClick={() => setShowModelInput(prev => !prev)}
                    title="Switch model"
                    data-testid="model-switch-toggle"
                  >
                    <BrainCircuitIcon className={styles.controlIcon} />
                  </button>
                )}

                {capabilities.set_thinking_tokens && (
                  <div className={styles.thinkingWrapper}>
                    <button
                      type="button"
                      className={styles.controlBtn}
                      onClick={() => setShowThinkingMenu(prev => !prev)}
                      title="Set thinking budget"
                      data-testid="thinking-budget-toggle"
                    >
                      <span className={styles.controlLabel}>Thinking</span>
                    </button>
                    {showThinkingMenu && (
                      <div className={styles.thinkingMenu} data-testid="thinking-menu">
                        {THINKING_PRESETS.map(preset => (
                          <button
                            key={preset.value}
                            type="button"
                            className={styles.thinkingOption}
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

                {capabilities.rewind_files && (
                  <button
                    type="button"
                    className={styles.controlBtn}
                    onClick={sendRewindFiles}
                    title="Rewind files"
                    data-testid="rewind-files"
                  >
                    <RotateCcwIcon className={styles.controlIcon} />
                  </button>
                )}

                {isRoomMode && (
                  <button
                    type="button"
                    className={cn(styles.controlBtn, showInternal && styles.controlBtnActive)}
                    onClick={toggleInternal}
                    title={showInternal ? 'Hide internal messages' : 'Show internal messages'}
                    aria-pressed={showInternal}
                    data-testid="internal-toggle"
                  >
                    {showInternal ? (
                      <Eye className={styles.controlIcon} />
                    ) : (
                      <EyeOff className={styles.controlIcon} />
                    )}
                    <span className={styles.controlLabel}>Internal</span>
                  </button>
                )}
              </div>
            </div>
          )}
        </div>

        {showModelInput && connected && capabilities.set_model && (
          <div className={styles.modelInputBar} data-testid="model-input-bar">
            <input
              type="text"
              className={styles.modelInput}
              value={modelInput}
              onChange={e => setModelInput(e.target.value)}
              onKeyDown={e => {
                if (e.key === 'Enter') handleModelSubmit();
                if (e.key === 'Escape') setShowModelInput(false);
              }}
              placeholder="Model ID (e.g. claude-opus-4-6)"
              autoFocus
              aria-label="Model ID input"
            />
            <button
              type="button"
              className={styles.modelSubmitBtn}
              onClick={handleModelSubmit}
              data-testid="model-submit"
            >
              Switch
            </button>
          </div>
        )}

        {!historyLoaded && connected && (
          <div className={styles.historyLoading} data-testid="history-loading">
            Loading conversation...
          </div>
        )}

        {hasConversation ? (
          <div className={styles.messagesContainer} ref={scrollContainerRef}>
            <div className={styles.messagesInner}>
              {renderedGroups.map(group => {
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

                // System messages rendered as compact inline notifications
                if (msg.metadata?.messageType === 'system') {
                  return <SystemMessage key={msg.id} message={msg} />;
                }

                // Room messages (participant present or room session) — render as RoomMessage
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

                // Streaming assistant message
                if (msg.status === 'running') {
                  return <StreamingMessage key={msg.id} content={msg.content} parts={msg.parts} />;
                }

                // Complete assistant message
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
              <div ref={messagesEndRef} />
            </div>
            {showScrollBtn && (
              <button
                type="button"
                className={styles.scrollToBottom}
                onClick={() => scrollToBottom('smooth')}
                aria-label="Scroll to bottom"
              >
                <ArrowDownIcon className={styles.scrollToBottomIcon} />
                {newMessageCount > 0 && (
                  <span className={styles.scrollToBottomBadge}>
                    {newMessageCount > 99 ? '99+' : newMessageCount}
                  </span>
                )}
              </button>
            )}
          </div>
        ) : (
          <SessionEmptyChat
            sessionName="Volundr"
            onSuggestionClick={text => handleSend(text, [])}
          />
        )}

        <div className={styles.inputArea}>
          <div className={styles.inputInner}>
            {pendingPermissions.length > 0 && (
              <PermissionStack
                permissions={pendingPermissions}
                onRespond={handlePermissionRespond}
              />
            )}
            <ChatInput
              onSend={handleSend}
              onSendDirected={handleSendDirected}
              isLoading={isRunning}
              onStop={handleStop}
              disabled={!connected}
              stopDisabled={!capabilities.interrupt}
              sessionHost={sessionHost}
              chatEndpoint={chatEndpoint}
              availableCommands={availableCommands}
              participants={participants}
            />
          </div>
        </div>
      </div>

      {showRightPanel && effectiveRightPanelMode === 'cascade' && meshEvents.length > 0 && (
        <MeshCascadePanel events={meshEvents} onEventClick={handleOutcomeClick} />
      )}
    </div>
  );
}
