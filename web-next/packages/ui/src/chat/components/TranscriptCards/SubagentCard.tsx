import { useState } from 'react';
import { Users } from 'lucide-react';
import { cn } from '../../../utils/cn';
import { ToolCallsDrawer } from '../ToolBlock/ToolCallsDrawer';
import { toolResultText } from '../ToolBlock/toolFamilies';
import type { ToolResultBlock, ToolUseBlock } from '../ToolBlock/groupContentBlocks';
import './TranscriptCards.css';

/**
 * The result is a summary the subagent wrote; the card shows its opening lines and
 * says when it cut the rest. The whole result is one click away in the sheet.
 */
const RESULT_PREVIEW_LINES = 3;

export type SubagentStatus = 'running' | 'done' | 'failed';

export interface SubagentCardProps {
  block: ToolUseBlock;
  result?: ToolResultBlock;
  /**
   * The message is still being generated. A call without a result is running only
   * then; once the message is over a result that never came is not claimed as running.
   * Unset, the call counts as running while it has no result.
   */
  isStreaming?: boolean;
}

const STATUS_LABEL: Record<SubagentStatus, string> = {
  running: 'Running',
  done: 'Done',
  failed: 'Failed',
};

function text(value: unknown): string {
  return typeof value === 'string' ? value : '';
}

/** The subagent's status from its call: no result yet, a result, or an error result. */
export function subagentStatus(
  result: ToolResultBlock | undefined,
  isStreaming?: boolean,
): SubagentStatus | null {
  if (result?.is_error) return 'failed';
  if (result) return 'done';
  return isStreaming === false ? null : 'running';
}

/** Claude `Task` (`description`, `subagent_type`) and Codex `Agent` spawn (`prompt`, `model`). */
export function SubagentCard({ block, result, isStreaming }: SubagentCardProps) {
  const [open, setOpen] = useState(false);
  const { input } = block;
  const description = text(input.description);
  const kind = text(input.subagent_type);
  const prompt = text(input.prompt);
  const title = description || kind || prompt.split('\n')[0] || 'Subagent';
  const status = subagentStatus(result, isStreaming);
  const lines = toolResultText(result)
    .split('\n')
    .filter((line) => line.trim().length > 0);
  const shown = lines.slice(0, RESULT_PREVIEW_LINES);
  const cut = lines.length - shown.length;

  return (
    <div
      className="niuu-chat-subagent"
      data-testid="subagent-card"
      data-status={status ?? 'unfinished'}
    >
      <button
        type="button"
        className="niuu-chat-card-btn"
        onClick={() => setOpen(true)}
        aria-haspopup="dialog"
        aria-label={`Open subagent: ${title}`}
      >
        <span className="niuu-chat-card-head">
          <Users className="niuu-chat-card-icon" aria-hidden="true" />
          <span className="niuu-chat-card-kicker">Subagent</span>
          <b className="niuu-chat-card-title">{title}</b>
          {description && kind && <span className="niuu-chat-card-meta">{kind}</span>}
          {status && (
            <span
              className={cn('niuu-chat-pill', `niuu-chat-pill--${status}`)}
              data-testid="subagent-status"
            >
              {STATUS_LABEL[status]}
            </span>
          )}
        </span>
        {shown.length > 0 && (
          <span className="niuu-chat-subagent-result" data-testid="subagent-result">
            {shown.join('\n')}
            {cut > 0 && `\n… ${cut} more ${cut === 1 ? 'line' : 'lines'}`}
          </span>
        )}
      </button>
      <ToolCallsDrawer
        open={open}
        onOpenChange={setOpen}
        title={title}
        description="Subagent"
        calls={[{ block, result }]}
      />
    </div>
  );
}
