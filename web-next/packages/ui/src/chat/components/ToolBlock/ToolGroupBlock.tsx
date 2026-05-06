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
  const label = getToolLabel(toolName);

  return (
    <div className="niuu-chat-tool-group" data-testid="tool-group-block">
      <button
        type="button"
        className="niuu-chat-tool-group-header"
        onClick={() => setIsOpen((prev) => !prev)}
        aria-expanded={isOpen}
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
          {blocks.map((item, i) => (
            <ToolBlock key={i} block={item.block} result={item.result} defaultOpen />
          ))}
        </div>
      )}
    </div>
  );
}
