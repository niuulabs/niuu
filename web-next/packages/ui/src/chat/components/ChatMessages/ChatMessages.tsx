import { useState, useCallback } from 'react';
import { useCopyFeedback } from '../../hooks/useCopyFeedback';
import {
  Hammer,
  Copy,
  Check,
  RefreshCw,
  ThumbsUp,
  ThumbsDown,
  ChevronRight,
  ChevronDown,
  Loader2,
  Terminal,
  Paperclip,
  Bookmark,
} from 'lucide-react';
import { cn } from '../../../utils/cn';
import { MarkdownContent } from '../MarkdownContent';
import { ToolBlock, ToolGroupBlock, groupContentBlocks } from '../ToolBlock';
import type { ChatMessage, ChatMessagePart } from '../../types';
import type { ContentBlock as ToolContentBlock } from '../ToolBlock';
import './ChatMessages.css';

const formatTime = (date: Date): string =>
  date.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' });

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes}B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)}KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)}MB`;
}

function hasToolParts(parts?: readonly ChatMessagePart[]): boolean {
  return parts?.some((p) => p.type === 'tool_use') ?? false;
}

function partsToContentBlocks(parts: readonly ChatMessagePart[]): ToolContentBlock[] {
  const blocks: ToolContentBlock[] = [];
  for (const part of parts) {
    if (part.type === 'text' && part.text != null) {
      blocks.push({ type: 'text', text: part.text });
    } else if (part.type === 'tool_use' && part.id && part.name && part.input) {
      blocks.push({ type: 'tool_use', id: part.id, name: part.name, input: part.input });
    } else if (part.type === 'tool_result' && part.tool_use_id) {
      blocks.push({ type: 'tool_result', tool_use_id: part.tool_use_id, content: part.content });
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

/* ── UserMessage ── */

interface UserMessageProps {
  message: ChatMessage;
}

export function UserMessage({ message }: UserMessageProps) {
  return (
    <div className="niuu-chat-user-wrapper" data-testid="user-message">
      <div className="niuu-chat-user-bubble">
        <div className="niuu-chat-user-text">
          <MarkdownContent content={message.content} />
        </div>
        {message.attachments && message.attachments.length > 0 && (
          <div className="niuu-chat-attachment-row">
            {message.attachments.map((att, i) => (
              <span key={`${att.name}-${i}`} className="niuu-chat-attachment-badge">
                <Paperclip className="niuu-chat-attachment-icon" />
                <span>{att.name}</span>
                <span className="niuu-chat-attachment-size">{formatFileSize(att.size)}</span>
              </span>
            ))}
          </div>
        )}
      </div>
      <div className="niuu-chat-msg-meta">
        <span className="niuu-chat-timestamp">{formatTime(message.createdAt)}</span>
      </div>
    </div>
  );
}

/* ── AssistantMessage ── */

interface AssistantMessageProps {
  message: ChatMessage;
  onCopy?: (text: string) => void;
  onRegenerate?: (messageId: string) => void;
  onBookmark?: (messageId: string, bookmarked: boolean) => void;
  bookmarked?: boolean;
}

export function AssistantMessage({
  message,
  onCopy,
  onRegenerate,
  onBookmark,
  bookmarked = false,
}: AssistantMessageProps) {
  const [copied, handleCopyClick] = useCopyFeedback(message.content);
  const [thumbState, setThumbState] = useState<'up' | 'down' | null>(null);
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

  return (
    <div className="niuu-chat-assistant-wrapper" data-testid="assistant-message">
      <div className="niuu-chat-avatar">
        <Hammer className="niuu-chat-avatar-icon" />
      </div>
      <div className="niuu-chat-assistant-body">
        <div className="niuu-chat-assistant-header">
          {model && <span className="niuu-chat-model-badge">{model}</span>}
          <span className="niuu-chat-timestamp">{formatTime(message.createdAt)}</span>
          {tokens && (
            <>
              <span className="niuu-chat-header-sep">&middot;</span>
              <span className="niuu-chat-token-info">
                {tokens.input}&rarr;{tokens.output} tok
              </span>
            </>
          )}
        </div>

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
          {hasToolParts(message.parts) && message.parts ? (
            <AssistantContentWithTools parts={message.parts} fallbackContent={message.content} />
          ) : (
            <MarkdownContent content={message.content} />
          )}
        </div>

        <div className="niuu-chat-action-bar">
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
          <div className="niuu-chat-action-divider" />
          <button
            type="button"
            className="niuu-chat-action-btn"
            data-active={thumbState === 'up'}
            onClick={() => setThumbState((prev) => (prev === 'up' ? null : 'up'))}
            title="Helpful"
          >
            <ThumbsUp className="niuu-chat-action-icon" />
          </button>
          <button
            type="button"
            className="niuu-chat-action-btn"
            data-active={thumbState === 'down'}
            onClick={() => setThumbState((prev) => (prev === 'down' ? null : 'down'))}
            title="Not helpful"
          >
            <ThumbsDown className="niuu-chat-action-icon" />
          </button>
          <div className="niuu-chat-action-divider" />
          <button
            type="button"
            className={cn('niuu-chat-action-btn', bookmarked && 'niuu-chat-action-btn--active')}
            onClick={() => onBookmark?.(message.id, !bookmarked)}
            title={bookmarked ? 'Remove bookmark' : 'Bookmark'}
          >
            <Bookmark className="niuu-chat-action-icon" />
          </button>
        </div>
      </div>
    </div>
  );
}

/* ── AssistantContentWithTools ── */

function AssistantContentWithTools({
  parts,
  fallbackContent,
}: {
  parts: readonly ChatMessagePart[];
  fallbackContent: string;
}) {
  const blocks = partsToContentBlocks(parts);
  const grouped = groupContentBlocks(blocks);

  if (grouped.length === 0) return <MarkdownContent content={fallbackContent} />;

  return (
    <>
      {grouped.map((item, i) => {
        if (item.kind === 'text') {
          if (!item.text.trim()) return null;
          return <MarkdownContent key={i} content={item.text} />;
        }
        if (item.kind === 'single') {
          return <ToolBlock key={i} block={item.block} result={item.result} />;
        }
        if (item.kind === 'group') {
          return <ToolGroupBlock key={i} toolName={item.toolName} blocks={item.blocks} />;
        }
        return null;
      })}
    </>
  );
}

/* ── StreamingMessage ── */

interface StreamingMessageProps {
  content: string;
  parts?: readonly ChatMessagePart[];
  model?: string;
}

export function StreamingMessage({ content, parts, model }: StreamingMessageProps) {
  const reasoningParts = (parts?.filter((p) => p.type === 'reasoning' && p.text) ?? []) as Array<{
    readonly type: 'reasoning';
    readonly text?: string;
  }>;
  const hasReasoning = reasoningParts.length > 0;
  const hasTools = hasToolParts(parts);

  if (!content && hasTools && parts) {
    return (
      <div className="niuu-chat-streaming-wrapper" data-testid="streaming-message">
        <div className={cn('niuu-chat-avatar', 'niuu-chat-avatar--pulsing')}>
          <Hammer className="niuu-chat-avatar-icon" />
        </div>
        <div className="niuu-chat-assistant-body">
          <div className="niuu-chat-assistant-header">
            {model && <span className="niuu-chat-model-badge">{model}</span>}
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
            <AssistantContentWithTools parts={parts} fallbackContent="" />
          </div>
        </div>
      </div>
    );
  }

  if (!content && hasReasoning) {
    return (
      <div className="niuu-chat-streaming-wrapper" data-testid="streaming-message">
        <div className={cn('niuu-chat-avatar', 'niuu-chat-avatar--pulsing')}>
          <Hammer className="niuu-chat-avatar-icon" />
        </div>
        <div className="niuu-chat-assistant-body">
          <div className="niuu-chat-assistant-header">
            {model && <span className="niuu-chat-model-badge">{model}</span>}
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
        <div className={cn('niuu-chat-avatar', 'niuu-chat-avatar--pulsing')}>
          <Hammer className="niuu-chat-avatar-icon" />
        </div>
        <div className="niuu-chat-assistant-body">
          <div className="niuu-chat-assistant-header">
            {model && <span className="niuu-chat-model-badge">{model}</span>}
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
      <div className={cn('niuu-chat-avatar', 'niuu-chat-avatar--pulsing')}>
        <Hammer className="niuu-chat-avatar-icon" />
      </div>
      <div className="niuu-chat-assistant-body">
        <div className="niuu-chat-assistant-header">
          {model && <span className="niuu-chat-model-badge">{model}</span>}
          <span className="niuu-chat-generating-label">
            <Loader2 className="niuu-chat-spinner-icon" />
            Generating...
          </span>
        </div>
        <div className="niuu-chat-assistant-content">
          {hasTools && parts ? (
            <AssistantContentWithTools parts={parts} fallbackContent={content} />
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
  return (
    <div className="niuu-chat-system-message" data-testid="system-message">
      <Terminal className="niuu-chat-system-icon" />
      <span>{message.content}</span>
    </div>
  );
}
