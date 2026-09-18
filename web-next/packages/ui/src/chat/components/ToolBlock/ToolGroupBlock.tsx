import { useState } from 'react';
import { ChevronRight, ChevronDown } from 'lucide-react';
import { ToolIcon } from './ToolIcon';
import { ToolBlock } from './ToolBlock';
import { getToolLabel } from './toolLabels';
import type { ToolUseBlock, ToolResultBlock } from './groupContentBlocks';
import './ToolBlock.css';

interface ToolGroupBlockProps {
  toolName: string;
  blocks: Array<{ block: ToolUseBlock; result?: ToolResultBlock }>;
}

export function ToolGroupBlock({ toolName, blocks }: ToolGroupBlockProps) {
  const [isOpen, setIsOpen] = useState(false);
  const [visibleCount, setVisibleCount] = useState(25);
  const summary = [...new Set(blocks.map(({ block }) => getToolLabel(block.name)))]
    .map(
      (label) =>
        `${blocks.filter(({ block }) => getToolLabel(block.name) === label).length} ${label.toLowerCase()}`,
    )
    .join(' · ');
  const label = blocks.every(({ block }) => block.name === toolName)
    ? getToolLabel(toolName)
    : summary;

  return (
    <div className="niuu-chat-tool-group" data-testid="tool-group-block">
      <button
        type="button"
        className="niuu-chat-tool-group-header"
        onClick={() => setIsOpen((prev) => !prev)}
        aria-expanded={isOpen}
        aria-label={`${isOpen ? 'Collapse' : 'Expand'} ${blocks.length} tool calls: ${summary}`}
        onKeyDown={(event) => {
          if (event.key === 'ArrowRight') {
            event.preventDefault();
            setIsOpen(true);
          }
          if (event.key === 'ArrowLeft' || event.key === 'Escape') {
            event.preventDefault();
            setIsOpen(false);
          }
        }}
      >
        <ToolIcon toolName={toolName} className="niuu-chat-tool-icon" />
        <span className="niuu-chat-tool-label">{label}</span>
        <span className="niuu-chat-tool-group-count">{blocks.length}</span>
        {isOpen ? (
          <ChevronDown className="niuu-chat-tool-chevron-icon" />
        ) : (
          <ChevronRight className="niuu-chat-tool-chevron-icon" />
        )}
      </button>
      {isOpen && (
        <div className="niuu-chat-tool-group-items">
          {blocks.slice(0, visibleCount).map((item) => (
            <ToolBlock key={item.block.id} block={item.block} result={item.result} />
          ))}
          {visibleCount < blocks.length && (
            <button
              type="button"
              className="niuu-chat-tool-show-more"
              onClick={() => setVisibleCount((count) => count + 25)}
            >
              Show {Math.min(25, blocks.length - visibleCount)} more calls (
              {blocks.length - visibleCount} remaining)
            </button>
          )}
        </div>
      )}
    </div>
  );
}
