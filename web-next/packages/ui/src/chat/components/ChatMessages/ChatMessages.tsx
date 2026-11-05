import { useState, useCallback, useMemo, type CSSProperties } from 'react';
import { useCopyFeedback } from '../../hooks/useCopyFeedback';
import {
  Hammer,
  Copy,
  Check,
  RefreshCw,
  Forward,
  ChevronRight,
  ChevronDown,
  Loader2,
  Terminal,
  Paperclip,
  Bookmark,
  AlertTriangle,
  AlertCircle,
} from 'lucide-react';
import { cn } from '../../../utils/cn';
import { MarkdownContent } from '../MarkdownContent';
import { ToolBlock, ToolGroupBlock, groupContentBlocks } from '../ToolBlock';
import type { ChatMessage, ChatMessagePart } from '../../types';
import type { ContentBlock as ToolContentBlock } from '../ToolBlock';
import type { CompactionBlock } from '../ToolBlock/groupContentBlocks';
import { isSubagentTool } from '../ToolBlock/toolFamilies';
import {
  CompactionMarker,
  ForgeSessionCard,
  ProviderErrorCard,
  SubagentCard,
  compactionTokens,
  formatTokenCount,
} from '../TranscriptCards';
import { engineInk } from '../../utils/engineInk';
import { useCompactUxChatPrefs } from '../../compactUxPrefs';
import './ChatMessages.css';
import { ToolImageCard } from '../ToolImages';
import { PresentedFileCard } from '../ConversationResources';
import {
  isNotificationCardTool,
  isPresentedFileTool,
  isForgeSessionTool,
} from '../ToolBlock/groupContentBlocks';
import { useSessionLinks } from '../../sessionLinks';
import { RelayCard } from './RelayCard';
import { classifyRelayedTurn } from './relayedTurn';
import { NotificationCard } from '../NotificationCard';
import { isForgeNotifyCall, modelLabel, parseForgeNotificationPayload } from '@niuulabs/domain';

const formatTime = (date: Date): string =>
  date.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' });

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes}B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)}KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)}MB`;
}

function hasToolParts(parts?: readonly ChatMessagePart[]): boolean {
  return (
    parts?.some(
      (p) => p.type === 'tool_use' || p.type === 'tool_result' || p.type === 'tool_separator',
    ) ?? false
  );
}

function partsToContentBlocks(parts: readonly ChatMessagePart[]): ToolContentBlock[] {
  const blocks: ToolContentBlock[] = [];
  for (const part of parts) {
    if (part.type === 'tool_separator') {
      blocks.push(part);
    } else if (part.type === 'text' && part.text != null) {
      blocks.push({
        type: 'text',
        text: part.text,
        ...(part.id ? { id: part.id } : {}),
        ...(part.phase ? { phase: part.phase } : {}),
        ...(part.turn_id ? { turn_id: part.turn_id } : {}),
        ...(part.thread_id ? { thread_id: part.thread_id } : {}),
        ...(part.complete !== undefined ? { complete: part.complete } : {}),
      });
    } else if (part.type === 'tool_use' && part.id && part.name && part.input) {
      blocks.push({ type: 'tool_use', id: part.id, name: part.name, input: part.input });
    } else if (part.type === 'tool_result' && part.tool_use_id) {
      blocks.push(part as ToolContentBlock);
    } else if (part.type === 'compaction') {
      blocks.push({ ...part, type: 'compaction' } as CompactionBlock);
    }
  }
  return blocks;
}

function extractTokens(usage: Record<string, { inputTokens?: number; outputTokens?: number }>): {
  input: number;
  output: number;
} {
  let input = 0;
  let output = 0;
  for (const entry of Object.values(usage)) {
    input += entry.inputTokens ?? 0;
    output += entry.outputTokens ?? 0;
  }
  return { input, output };
}

function MessageAttachments({ attachments }: Pick<ChatMessage, 'attachments'>) {
  return (
    <>
      {' '}
      {attachments && attachments.length > 0 && (
        <div className="niuu-chat-attachment-row">
          {attachments.map((att, i) =>
            att.previewUrl ? (
              <img
                key={`${att.name}-${i}`}
                src={att.previewUrl}
                alt={att.name}
                className="niuu-chat-attachment-preview"
              />
            ) : (
              <span key={`${att.name}-${i}`} className="niuu-chat-attachment-badge">
                <Paperclip className="niuu-chat-attachment-icon" />
                <span>{att.name}</span>
                <span className="niuu-chat-attachment-size">{formatFileSize(att.size)}</span>
              </span>
            ),
          )}
        </div>
      )}
    </>
  );
}

export { formatTokenCount };

/** The engine-coloured dot and model name that head an assistant message. */
function ModelSpeaker({ model }: { model?: string }) {
  if (!model) return null;
  return (
    <span
      className="niuu-chat-speaker"
      style={{ '--niuu-chat-engine': engineInk(model) } as CSSProperties}
      data-testid="model-speaker"
      title={model}
    >
      <i className="niuu-chat-speaker-dot" aria-hidden="true" />
      {modelLabel(model)}
    </span>
  );
}

/* ── UserMessage ── */

interface UserMessageProps {
  message: ChatMessage;
}

export function UserMessage({ message }: UserMessageProps) {
  const uxPrefs = useCompactUxChatPrefs();
  const links = useSessionLinks();
  const delivery = message.delivery;
  const queued = delivery?.state === 'queued';
  // A turn this client sent has a delivery state and is the operator's own, whatever it says.
  const relay = useMemo(
    () => (delivery ? null : classifyRelayedTurn(message.content)),
    [delivery, message.content],
  );
  const sender = relay ? (relay.senderSessionId ?? relay.senderName) : null;
  const sessionLink = sender && links ? links.resolve(sender) : null;
  // "From Paris: …" is anyone's sentence; it is a relay only when Paris is a session.
  if (relay && (!relay.needsKnownSender || sessionLink)) {
    return (
      <RelayCard
        relay={relay}
        link={sessionLink}
        links={links}
        time={uxPrefs.timestamp === 'never' ? null : formatTime(message.createdAt)}
      />
    );
  }
  return (
    <div
      className="niuu-chat-user-wrapper"
      data-testid="user-message"
      data-timestamp={uxPrefs.timestamp}
      data-delivery={delivery?.state}
    >
      <div className={cn('niuu-chat-user-bubble', queued && 'niuu-chat-user-bubble--queued')}>
        <div className="niuu-chat-user-text">
          <MarkdownContent content={message.content} />
        </div>
        <MessageAttachments attachments={message.attachments} />
      </div>
      <div className="niuu-chat-msg-meta">
        {queued && (
          <span className="niuu-chat-delivery niuu-chat-delivery--queued" role="status">
            <span className="niuu-chat-delivery-pulse" aria-hidden="true" />
            Queued · waiting for the agent to take it
          </span>
        )}
        {delivery?.state === 'unconfirmed' && (
          <span className="niuu-chat-delivery niuu-chat-delivery--unconfirmed" role="status">
            <AlertTriangle className="niuu-chat-delivery-icon" aria-hidden="true" />
            Delivery not confirmed{delivery.error ? ` · ${delivery.error}` : ''}
          </span>
        )}
        {delivery?.state === 'failed' && (
          <span className="niuu-chat-delivery niuu-chat-delivery--failed" role="alert">
            <AlertCircle className="niuu-chat-delivery-icon" aria-hidden="true" />
            Not delivered{delivery.error ? ` · ${delivery.error}` : ''}
          </span>
        )}
        {uxPrefs.timestamp !== 'never' && (
          <span className="niuu-chat-timestamp">{formatTime(message.createdAt)}</span>
        )}
      </div>
    </div>
  );
}

/** Structured provider messages retain one component identity across running/settled states. */
export function hasNativeMessageParts(parts?: readonly ChatMessagePart[]): boolean {
  return (
    parts?.some((part) => Boolean(part.turn_id) || (part.type === 'text' && Boolean(part.id))) ??
    false
  );
}

/** REST may replace the broker's generated row id; the native turn remains the same row. */
export function messageRenderKey(message: ChatMessage): string {
  if (message.role !== 'assistant') return message.id;
  const native = message.parts?.find((part) => part.turn_id);
  const firstAnchor = message.parts?.find(
    (part) => (part.type === 'text' || part.type === 'tool_use') && part.id,
  );
  if (native && firstAnchor)
    return `native-fragment:${JSON.stringify([native.thread_id ?? '', native.turn_id, firstAnchor.id])}`;
  const text = message.parts?.find((part) => part.type === 'text' && part.id);
  return text ? `native-text:${JSON.stringify([text.thread_id ?? '', text.id])}` : message.id;
}

/* ── AssistantMessage ── */

interface AssistantMessageProps {
  message: ChatMessage;
  onCopy?: (text: string) => void;
  onRegenerate?: (messageId: string) => void;
  /** Hand this message to another session; the button shows only when this is given. */
  onForward?: (message: ChatMessage) => void;
  onBookmark?: (messageId: string, bookmarked: boolean) => void;
  bookmarked?: boolean;
  showTokenUsage?: boolean;
}

export function AssistantMessage({
  message,
  onCopy,
  onRegenerate,
  onForward,
  onBookmark,
  bookmarked = false,
  showTokenUsage = false,
}: AssistantMessageProps) {
  const [copied, handleCopyClick] = useCopyFeedback(message.content);
  const [reasoningOpen, setReasoningOpen] = useState(false);

  const reasoningParts = (message.parts?.filter((p) => p.type === 'reasoning') ?? []) as Array<{
    readonly type: 'reasoning';
    readonly text?: string;
  }>;
  const hasReasoning =
    reasoningParts.length > 0 && reasoningParts.some((p) => p.text && p.text.length > 0);

  const meta = message.metadata;
  const model = meta?.usage ? Object.keys(meta.usage)[0] : undefined;
  const tokens = meta?.usage ? extractTokens(meta.usage) : null;

  const handleCopy = useCallback(() => {
    if (onCopy) onCopy(message.content);
    handleCopyClick();
  }, [message.content, onCopy, handleCopyClick]);

  const uxPrefs = useCompactUxChatPrefs();

  const compactionOnly =
    message.parts !== undefined &&
    message.parts.length > 0 &&
    message.parts.every((part) => part.type === 'compaction');
  if (compactionOnly && message.parts) {
    return (
      <div className="niuu-chat-compaction-row" data-testid="compaction-message">
        {message.parts.map((part, i) => (
          <CompactionMarker
            key={part.id ?? i}
            {...compactionTokens(part as unknown as Record<string, unknown>)}
          />
        ))}
      </div>
    );
  }

  return (
    <div
      className="niuu-chat-assistant-wrapper"
      data-testid="assistant-message"
      data-timestamp={uxPrefs.timestamp}
    >
      {uxPrefs.showAgentAvatar && (
        <div className="niuu-chat-avatar">
          <Hammer className="niuu-chat-avatar-icon" />
        </div>
      )}
      <div className="niuu-chat-assistant-body">
        <div className="niuu-chat-assistant-header">
          <ModelSpeaker model={model} />
          {uxPrefs.timestamp !== 'never' && (
            <span className="niuu-chat-timestamp">{formatTime(message.createdAt)}</span>
          )}
          {message.status === 'running' && (
            <span className="niuu-chat-generating-label">
              <Loader2 className="niuu-chat-spinner-icon" />
              Generating...
            </span>
          )}
          {showTokenUsage && tokens && (
            <>
              <span className="niuu-chat-header-sep">&middot;</span>
              <span className="niuu-chat-token-info">
                {formatTokenCount(tokens.input)} → {formatTokenCount(tokens.output)} tokens
              </span>
            </>
          )}
        </div>

        {(!uxPrefs.showMessageActions || uxPrefs.copyMode === 'hover') && (
          <button
            type="button"
            className={cn(
              'niuu-chat-action-btn niuu-chat-copy',
              uxPrefs.copyMode === 'hover' && 'niuu-chat-copy--hover',
            )}
            onClick={handleCopy}
            title={copied ? 'Copied' : 'Copy'}
            aria-label="Copy message"
          >
            {copied ? <Check /> : <Copy />}
          </button>
        )}
        <MessageAttachments attachments={message.attachments} />
        {hasReasoning && (
          <div className="niuu-chat-reasoning">
            <button
              type="button"
              className="niuu-chat-reasoning-trigger"
              onClick={() => setReasoningOpen((prev) => !prev)}
            >
              {reasoningOpen ? (
                <ChevronDown className="niuu-chat-reasoning-chevron" />
              ) : (
                <ChevronRight className="niuu-chat-reasoning-chevron" />
              )}
              <Hammer className="niuu-chat-reasoning-icon" />
              <span className="niuu-chat-reasoning-label">Thinking</span>
            </button>
            {reasoningOpen && (
              <div className="niuu-chat-reasoning-content">
                {reasoningParts.map((part, i) => (
                  <div key={i} className="niuu-chat-reasoning-text">
                    {part.text}
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        <div className="niuu-chat-assistant-content">
          {message.parts &&
          (hasToolParts(message.parts) ||
            message.parts.some(
              (part) => part.type === 'compaction' || (part.type === 'text' && part.id),
            )) ? (
            <AssistantContentWithTools
              parts={message.parts}
              fallbackContent={message.content}
              isStreaming={message.status === 'running'}
            />
          ) : (
            <MarkdownContent content={message.content} />
          )}
        </div>

        {uxPrefs.showMessageActions && message.status !== 'running' && (
          <div className="niuu-chat-action-bar">
            {uxPrefs.copyMode === 'inline' && (
              <button
                type="button"
                className="niuu-chat-action-btn"
                onClick={handleCopy}
                title={copied ? 'Copied' : 'Copy'}
              >
                {copied ? (
                  <Check className="niuu-chat-action-icon" />
                ) : (
                  <Copy className="niuu-chat-action-icon" />
                )}
              </button>
            )}
            {onRegenerate && (
              <button
                type="button"
                className="niuu-chat-action-btn"
                onClick={() => onRegenerate(message.id)}
                title="Regenerate"
              >
                <RefreshCw className="niuu-chat-action-icon" />
              </button>
            )}
            {onForward && (
              <button
                type="button"
                className="niuu-chat-action-btn"
                onClick={() => onForward(message)}
                title="Forward…"
                aria-label="Forward…"
              >
                <Forward className="niuu-chat-action-icon" />
              </button>
            )}
            {onBookmark && (
              <button
                type="button"
                className={cn('niuu-chat-action-btn', bookmarked && 'niuu-chat-action-btn--active')}
                onClick={() => onBookmark(message.id, !bookmarked)}
                title={bookmarked ? 'Remove bookmark' : 'Bookmark'}
              >
                <Bookmark className="niuu-chat-action-icon" />
              </button>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

/* ── AssistantContentWithTools ── */

function AssistantContentWithTools({
  parts,
  fallbackContent,
  isStreaming = false,
}: {
  parts: readonly ChatMessagePart[];
  fallbackContent: string;
  isStreaming?: boolean;
}) {
  const grouped = useMemo(() => groupContentBlocks(partsToContentBlocks(parts), true), [parts]);
  // Older histories retain tool positions but only aggregate prose. Preserve that prose once;
  // its original position cannot be recovered here. Structured text parts remain authoritative.
  const hasText = grouped.some((item) => item.kind === 'text' && item.text.trim().length > 0);
  // A notification turn's content is its title; the card already shows it.
  const hasNotificationCard = grouped.some(
    (item) => item.kind === 'single' && isNotificationCardTool(item.block.name),
  );

  if (grouped.length === 0)
    return <MarkdownContent content={fallbackContent} isStreaming={isStreaming} />;

  return (
    <>
      {grouped.map((item, i) => {
        if (item.kind === 'image')
          return (
            <ToolImageCard
              key={`image:${item.image.toolUseId}:${item.image.index}`}
              image={item.image}
            />
          );
        if (item.kind === 'separator') {
          return (
            <hr
              key={`separator:${item.id ?? i}`}
              className="niuu-chat-tool-separator"
              aria-label="Hidden tool calls"
            />
          );
        }
        if (item.kind === 'compaction') {
          return <CompactionMarker key={`compaction:${i}`} {...compactionTokens(item.part)} />;
        }
        if (item.kind === 'text') {
          if (!item.text.trim()) return null;
          const key = item.id
            ? `text:${JSON.stringify([item.thread_id ?? '', item.turn_id ?? '', item.id])}`
            : `legacy-text:${i}`;
          // `phase` comes from the runtime (Codex: commentary / final_answer); runtimes that
          // send none render as plain text. The FINAL tag only helps when work precedes it.
          const commentary = item.phase === 'commentary';
          const final = item.phase === 'final_answer' && i > 0;
          return (
            <div
              key={key}
              data-text-id={item.id}
              data-text-phase={item.phase}
              className={cn(commentary && 'niuu-chat-commentary')}
            >
              {final && (
                <span className="niuu-chat-final-tag" data-testid="final-tag">
                  FINAL
                </span>
              )}
              <MarkdownContent
                content={item.text}
                isStreaming={isStreaming && item.complete !== true && i === grouped.length - 1}
              />
            </div>
          );
        }
        if (item.kind === 'single') {
          if (isPresentedFileTool(item.block.name))
            return <PresentedFileCard key={`file:${item.block.id}`} block={item.block} />;
          const notification = isNotificationCardTool(item.block.name)
            ? parseForgeNotificationPayload(item.block.input)
            : null;
          if (notification)
            return (
              <NotificationCard key={`notification:${item.block.id}`} notification={notification} />
            );
          // The raw notify call stays a quiet, expandable line beside its notification card,
          // and a notification whose payload cannot be read stays visible as a raw call.
          if (
            isNotificationCardTool(item.block.name) ||
            isForgeNotifyCall(item.block.name, item.block.input)
          )
            return (
              <ToolBlock key={`tool:${item.block.id}`} block={item.block} result={item.result} />
            );
          if (isForgeSessionTool(item.block.name))
            return (
              <ForgeSessionCard
                key={`forge-session:${item.block.id}`}
                block={item.block}
                result={item.result}
                isStreaming={isStreaming}
              />
            );
          if (isSubagentTool(item.block.name))
            return (
              <SubagentCard
                key={`subagent:${item.block.id}`}
                block={item.block}
                result={item.result}
                isStreaming={isStreaming}
              />
            );
          return (
            <ToolGroupBlock
              key={`tools:${item.block.id}`}
              toolName={item.block.name}
              blocks={[{ block: item.block, result: item.result }]}
              isStreaming={isStreaming}
            />
          );
        }
        if (item.kind === 'group') {
          return (
            <ToolGroupBlock
              key={`tools:${item.blocks[0]?.block.id ?? i}`}
              toolName={item.toolName}
              blocks={item.blocks}
              isStreaming={isStreaming}
            />
          );
        }
        return null;
      })}
      {!hasText && !hasNotificationCard && fallbackContent && (
        <MarkdownContent content={fallbackContent} isStreaming={isStreaming} />
      )}
    </>
  );
}

/* ── StreamingMessage ── */

interface StreamingMessageProps {
  content: string;
  parts?: readonly ChatMessagePart[];
  model?: string;
}

/** The pulsing agent avatar on an in-progress reply, shown only when avatars are on. */
function StreamingAvatar() {
  return (
    <div className={cn('niuu-chat-avatar', 'niuu-chat-avatar--pulsing')}>
      <Hammer className="niuu-chat-avatar-icon" />
    </div>
  );
}

export function StreamingMessage({ content, parts, model }: StreamingMessageProps) {
  const showAvatar = useCompactUxChatPrefs().showAgentAvatar;
  const reasoningParts = (parts?.filter((p) => p.type === 'reasoning' && p.text) ?? []) as Array<{
    readonly type: 'reasoning';
    readonly text?: string;
  }>;
  const hasReasoning = reasoningParts.length > 0;
  const hasTools = hasToolParts(parts);

  if (!content && hasTools && parts) {
    return (
      <div className="niuu-chat-streaming-wrapper" data-testid="streaming-message">
        {showAvatar && <StreamingAvatar />}
        <div className="niuu-chat-assistant-body">
          <div className="niuu-chat-assistant-header">
            <ModelSpeaker model={model} />
            <span className="niuu-chat-generating-label">
              <Loader2 className="niuu-chat-spinner-icon" />
              Using tools...
            </span>
          </div>
          {hasReasoning && (
            <div className="niuu-chat-reasoning-content">
              {reasoningParts.map((part, i) => (
                <div key={i} className="niuu-chat-reasoning-text">
                  {part.text}
                </div>
              ))}
            </div>
          )}
          <div className="niuu-chat-assistant-content">
            <AssistantContentWithTools parts={parts} fallbackContent="" isStreaming />
          </div>
        </div>
      </div>
    );
  }

  if (!content && hasReasoning) {
    return (
      <div className="niuu-chat-streaming-wrapper" data-testid="streaming-message">
        {showAvatar && <StreamingAvatar />}
        <div className="niuu-chat-assistant-body">
          <div className="niuu-chat-assistant-header">
            <ModelSpeaker model={model} />
            <span className="niuu-chat-generating-label">
              <Loader2 className="niuu-chat-spinner-icon" />
              Thinking...
            </span>
          </div>
          <div className="niuu-chat-reasoning-content">
            {reasoningParts.map((part, i) => (
              <div key={i} className="niuu-chat-reasoning-text">
                {part.text}
              </div>
            ))}
          </div>
        </div>
      </div>
    );
  }

  if (!content && !hasReasoning) {
    return (
      <div className="niuu-chat-streaming-wrapper" data-testid="streaming-message">
        {showAvatar && <StreamingAvatar />}
        <div className="niuu-chat-assistant-body">
          <div className="niuu-chat-assistant-header">
            <ModelSpeaker model={model} />
            <span className="niuu-chat-generating-label">
              <Loader2 className="niuu-chat-spinner-icon" />
              Thinking...
            </span>
          </div>
          <div className="niuu-chat-dots-container">
            <span className="niuu-chat-dot" />
            <span className="niuu-chat-dot" />
            <span className="niuu-chat-dot" />
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="niuu-chat-streaming-wrapper" data-testid="streaming-message">
      {showAvatar && <StreamingAvatar />}
      <div className="niuu-chat-assistant-body">
        <div className="niuu-chat-assistant-header">
          <ModelSpeaker model={model} />
          <span className="niuu-chat-generating-label">
            <Loader2 className="niuu-chat-spinner-icon" />
            Generating...
          </span>
        </div>
        <div className="niuu-chat-assistant-content">
          {parts && (hasTools || parts.some((part) => part.type === 'text' && part.id)) ? (
            <AssistantContentWithTools parts={parts} fallbackContent={content} isStreaming />
          ) : (
            <MarkdownContent content={content} isStreaming />
          )}
        </div>
      </div>
    </div>
  );
}

/* ── SystemMessage ── */

interface SystemMessageProps {
  message: ChatMessage;
}

export function SystemMessage({ message }: SystemMessageProps) {
  if (message.status === 'error') return <ProviderErrorCard message={message.content} />;
  return (
    <div className="niuu-chat-system-message" data-testid="system-message">
      <Terminal className="niuu-chat-system-icon" />
      <span>{message.content}</span>
    </div>
  );
}
