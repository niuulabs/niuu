import { useState } from 'react';
import { ChevronRight, ChevronDown } from 'lucide-react';
import { cn } from '../../../utils/cn';
import { ToolIcon } from './ToolIcon';
import { getToolLabel, getToolCategory } from './toolLabels';
import type { ToolUseBlock, ToolResultBlock } from './groupContentBlocks';
import './ToolBlock.css';

const MAX_OUTPUT_LINES = 20;

function extractPreview(block: ToolUseBlock): string {
  const { name, input } = block;
  switch (name) {
    case 'Bash':
      return (String(input.command ?? '').split('\n')[0] ?? '').slice(0, 80);
    case 'Read':
      return String(input.file_path ?? input.path ?? '');
    case 'Write':
      return String(input.file_path ?? input.path ?? '');
    case 'Edit':
      return String(input.file_path ?? input.path ?? '');
    case 'Glob':
      return String(input.pattern ?? '');
    case 'Grep':
      return String(input.pattern ?? '');
    case 'WebSearch':
      return String(input.query ?? '');
    case 'WebFetch':
      return String(input.url ?? '');
    case 'Agent':
      return String(input.description ?? input.prompt ?? '').slice(0, 80);
    default:
      return Object.values(input)[0] != null ? String(Object.values(input)[0]).slice(0, 80) : '';
  }
}

interface ToolDetailProps {
  block: ToolUseBlock;
  result?: ToolResultBlock;
}

function ToolDetail({ block, result }: ToolDetailProps) {
  const [showFull, setShowFull] = useState(false);
  const { name, input } = block;
  const output = result?.content ?? '';
  const outputLines = output.split('\n');
  const isTruncated = outputLines.length > MAX_OUTPUT_LINES && !showFull;
  const displayedOutput = isTruncated
    ? outputLines.slice(0, MAX_OUTPUT_LINES).join('\n') + '\n...'
    : output;

  if (name === 'Bash') {
    return (
      <div className="niuu-chat-tool-detail">
        <div className="niuu-chat-tool-command">
          <span className="niuu-chat-tool-prompt">$</span>
          <pre className="niuu-chat-tool-cmd-text">{String(input.command ?? '')}</pre>
        </div>
        {input.description != null && (
          <p className="niuu-chat-tool-desc">{String(input.description)}</p>
        )}
        {output && (
          <div className="niuu-chat-tool-output">
            <pre className="niuu-chat-tool-output-text">{displayedOutput}</pre>
            {isTruncated && (
              <button
                type="button"
                className="niuu-chat-tool-show-more"
                onClick={() => setShowFull(true)}
              >
                Show full output
              </button>
            )}
          </div>
        )}
      </div>
    );
  }

  if (name === 'Edit') {
    return (
      <div className="niuu-chat-tool-detail">
        <p className="niuu-chat-tool-filepath">{String(input.file_path ?? input.path ?? '')}</p>
        {input.old_string != null && (
          <pre className="niuu-chat-tool-diff niuu-chat-tool-diff--old">
            {String(input.old_string)}
          </pre>
        )}
        {input.new_string != null && (
          <pre className="niuu-chat-tool-diff niuu-chat-tool-diff--new">
            {String(input.new_string)}
          </pre>
        )}
      </div>
    );
  }

  if (name === 'Write') {
    return (
      <div className="niuu-chat-tool-detail">
        <p className="niuu-chat-tool-filepath">{String(input.file_path ?? input.path ?? '')}</p>
        {input.content != null && (
          <pre className="niuu-chat-tool-output-text">{String(input.content).slice(0, 200)}</pre>
        )}
      </div>
    );
  }

  if (name === 'Read') {
    return (
      <div className="niuu-chat-tool-detail">
        <p className="niuu-chat-tool-filepath">{String(input.file_path ?? input.path ?? '')}</p>
        {output && (
          <div className="niuu-chat-tool-output">
            <pre className="niuu-chat-tool-output-text">{displayedOutput}</pre>
            {isTruncated && (
              <button
                type="button"
                className="niuu-chat-tool-show-more"
                onClick={() => setShowFull(true)}
              >
                Show full output
              </button>
            )}
          </div>
        )}
      </div>
    );
  }

  // Generic: list all input params
  return (
    <div className="niuu-chat-tool-detail">
      {Object.entries(input).map(([key, val]) => (
        <div key={key} className="niuu-chat-tool-param">
          <span className="niuu-chat-tool-param-key">{key}:</span>
          <pre className="niuu-chat-tool-param-val">
            {typeof val === 'string' ? val : JSON.stringify(val, null, 2)}
          </pre>
        </div>
      ))}
      {output && (
        <div className="niuu-chat-tool-output">
          <pre className="niuu-chat-tool-output-text">{displayedOutput}</pre>
          {isTruncated && (
            <button
              type="button"
              className="niuu-chat-tool-show-more"
              onClick={() => setShowFull(true)}
            >
              Show full output
            </button>
          )}
        </div>
      )}
    </div>
  );
}

interface ToolBlockProps {
  block: ToolUseBlock;
  result?: ToolResultBlock;
  defaultOpen?: boolean;
}

export function ToolBlock({ block, result, defaultOpen = false }: ToolBlockProps) {
  const [isOpen, setIsOpen] = useState(defaultOpen);
  const label = getToolLabel(block.name);
  const category = getToolCategory(block.name);
  const preview = extractPreview(block);

  return (
    <div
      className={cn('niuu-chat-tool-block', `niuu-chat-tool-block--${category}`)}
      data-testid="tool-block"
    >
      <button
        type="button"
        className="niuu-chat-tool-header"
        onClick={() => setIsOpen((prev) => !prev)}
        aria-expanded={isOpen}
      >
        <ToolIcon toolName={block.name} className="niuu-chat-tool-icon" />
        <span className="niuu-chat-tool-label">{label}</span>
        {!isOpen && preview && <span className="niuu-chat-tool-preview">{preview}</span>}
        <span className="niuu-chat-tool-chevron">
          {isOpen ? (
            <ChevronDown className="niuu-chat-tool-chevron-icon" />
          ) : (
            <ChevronRight className="niuu-chat-tool-chevron-icon" />
          )}
        </span>
      </button>
      {isOpen && <ToolDetail block={block} result={result} />}
    </div>
  );
}
