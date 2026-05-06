/**
 * MessageRow — renders a single transcript message with per-kind idioms.
 *
 * Kinds:
 *   user        — operator input, indigo left border
 *   asst        — assistant reply, neutral
 *   system      — injected context, muted/italic
 *   tool_call   — tool invocation, amber left border + monospace
 *   tool_result — tool response, emerald left border + monospace
 *   emit        — domain event emitted, cyan badge
 *   think       — extended thinking scratchpad, purple, collapsed by default
 */

import { useState } from 'react';
import type { Message } from '../domain/message';
import { formatTime } from './formatTime';

interface MessageRowProps {
  message: Message;
}

function parseEmitContent(content: string): { event: string; payload: string } {
  try {
    const obj = JSON.parse(content) as { event?: string; payload?: unknown };
    return {
      event: obj.event ?? 'event',
      payload: JSON.stringify(obj.payload ?? {}, null, 2),
    };
  } catch {
    return { event: 'event', payload: content };
  }
}

export function MessageRow({ message }: MessageRowProps) {
  const [thinkExpanded, setThinkExpanded] = useState(false);

  const time = formatTime(message.ts);

  if (message.kind === 'user') {
    return (
      <div className="rv-msg rv-msg--user" data-kind="user">
        <span className="rv-msg__time">{time}</span>
        <div className="rv-msg__body">{message.content}</div>
      </div>
    );
  }

  if (message.kind === 'asst') {
    return (
      <div className="rv-msg rv-msg--asst" data-kind="asst">
        <span className="rv-msg__time">{time}</span>
        <div className="rv-msg__body">{message.content}</div>
      </div>
    );
  }

  if (message.kind === 'system') {
    return (
      <div className="rv-msg rv-msg--system" data-kind="system">
        <span className="rv-msg__time">{time}</span>
        <div className="rv-msg__body rv-msg__body--italic">{message.content}</div>
      </div>
    );
  }

  if (message.kind === 'tool_call') {
    return (
      <div className="rv-msg rv-msg--tool-call" data-kind="tool_call">
        <span className="rv-msg__time">{time}</span>
        <div className="rv-msg__tool-header">
          <span className="rv-msg__tool-badge rv-msg__tool-badge--call">call</span>
          <span className="rv-msg__tool-name">{message.toolName ?? 'tool'}</span>
        </div>
        <pre className="rv-msg__code">{message.content}</pre>
      </div>
    );
  }

  if (message.kind === 'tool_result') {
    return (
      <div className="rv-msg rv-msg--tool-result" data-kind="tool_result">
        <span className="rv-msg__time">{time}</span>
        <div className="rv-msg__tool-header">
          <span className="rv-msg__tool-badge rv-msg__tool-badge--result">result</span>
          <span className="rv-msg__tool-name">{message.toolName ?? 'tool'}</span>
        </div>
        <pre className="rv-msg__code">{message.content}</pre>
      </div>
    );
  }

  if (message.kind === 'emit') {
    const { event, payload } = parseEmitContent(message.content);
    return (
      <div className="rv-msg rv-msg--emit" data-kind="emit">
        <span className="rv-msg__time">{time}</span>
        <div className="rv-msg__emit-header">
          <span className="rv-msg__emit-badge">emit</span>
          <span className="rv-msg__emit-event">{event}</span>
        </div>
        <pre className="rv-msg__code rv-msg__code--emit">{payload}</pre>
      </div>
    );
  }

  if (message.kind === 'think') {
    return (
      <div className="rv-msg rv-msg--think" data-kind="think">
        <span className="rv-msg__time">{time}</span>
        <button
          type="button"
          className="rv-msg__think-toggle"
          aria-expanded={thinkExpanded}
          onClick={() => setThinkExpanded((v) => !v)}
        >
          <span className="rv-msg__think-badge">think</span>
          <span className="rv-msg__think-label">{thinkExpanded ? 'hide' : 'show'} reasoning</span>
        </button>
        {thinkExpanded && <pre className="rv-msg__code rv-msg__code--think">{message.content}</pre>}
      </div>
    );
  }

  return null;
}
