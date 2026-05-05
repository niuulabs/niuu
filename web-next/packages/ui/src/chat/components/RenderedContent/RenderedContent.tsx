import { Copy, Check } from 'lucide-react';
import { OutcomeCard, extractOutcomeBlock } from '../OutcomeCard';
import { useCopyFeedback } from '../../hooks/useCopyFeedback';
import './RenderedContent.css';

interface RenderedContentProps {
  content: string;
  className?: string;
}

function InlineCode({ children }: { children: string }) {
  const [copied, handleCopy] = useCopyFeedback(children);

  return (
    <div className="niuu-chat-rc-codeblock" data-testid="rendered-code-block">
      <div className="niuu-chat-rc-codeblock-header">
        <button
          type="button"
          className="niuu-chat-rc-copy-btn"
          onClick={handleCopy}
          title={copied ? 'Copied!' : 'Copy'}
        >
          {copied ? (
            <Check className="niuu-chat-rc-copy-icon" />
          ) : (
            <Copy className="niuu-chat-rc-copy-icon" />
          )}
        </button>
      </div>
      <pre className="niuu-chat-rc-pre">{children}</pre>
    </div>
  );
}

export function RenderedContent({ content, className }: RenderedContentProps) {
  // Check for outcome blocks first
  const outcome = extractOutcomeBlock(content);
  if (outcome) {
    return (
      <div className={className}>
        {outcome.before && <p className="niuu-chat-rc-p">{outcome.before}</p>}
        <OutcomeCard raw={outcome.raw} />
        {outcome.after && <p className="niuu-chat-rc-p">{outcome.after}</p>}
      </div>
    );
  }

  const parts = splitContentBlocks(content);

  return (
    <div className={className} data-testid="rendered-content">
      {parts.map((part, i) => {
        if (part.type === 'code') {
          return <InlineCode key={i}>{part.content}</InlineCode>;
        }
        if (!part.content.trim()) return null;
        return (
          <p key={i} className="niuu-chat-rc-p">
            {part.content}
          </p>
        );
      })}
    </div>
  );
}

function splitContentBlocks(content: string): Array<{ type: 'text' | 'code'; content: string }> {
  const parts: Array<{ type: 'text' | 'code'; content: string }> = [];
  let cursor = 0;

  while (cursor < content.length) {
    const fenceStart = content.indexOf('```', cursor);
    if (fenceStart === -1) break;

    if (fenceStart > cursor) {
      parts.push({ type: 'text', content: content.slice(cursor, fenceStart) });
    }

    const languageStart = fenceStart + 3;
    const newlineIndex = content.indexOf('\n', languageStart);
    if (newlineIndex === -1) break;

    const fenceEnd = content.indexOf('```', newlineIndex + 1);
    if (fenceEnd === -1) break;

    parts.push({ type: 'code', content: content.slice(newlineIndex + 1, fenceEnd) });
    cursor = fenceEnd + 3;
  }

  if (cursor < content.length) {
    parts.push({ type: 'text', content: content.slice(cursor) });
  }

  return parts;
}
