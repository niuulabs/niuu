import { useState } from 'react';
import { Users } from 'lucide-react';
import { modelLabel } from '@niuulabs/domain';
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

export interface TeammateSpawn {
  name: string;
  type?: string;
  model?: string;
}

/**
 * With agent teams, Claude answers a Task call with the teammate's spawn record (JSON with
 * its id and name), not a result: the teammate keeps working in its own pane. Any other
 * result, including plain text that is not JSON, is not a spawn record.
 */
export function teammateSpawn(result: ToolResultBlock | undefined): TeammateSpawn | null {
  const raw = toolResultText(result).trim();
  if (!raw.startsWith('{')) return null;
  let record: unknown;
  try {
    record = JSON.parse(raw);
  } catch {
    return null; // A result that merely starts with a brace is ordinary text.
  }
  if (!record || typeof record !== 'object') return null;
  const fields = record as Record<string, unknown>;
  const id = text(fields.agentId) || text(fields.agent_id);
  const name = text(fields.name);
  if (!id || !name) return null;
  const type = text(fields.agent_type);
  const model = text(fields.model);
  return { name, ...(type ? { type } : {}), ...(model ? { model } : {}) };
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
  const spawn = status === 'done' ? teammateSpawn(result) : null;
  const lines = spawn
    ? [
        [`Teammate ${spawn.name}`, spawn.type, spawn.model && modelLabel(spawn.model)]
          .filter(Boolean)
          .join(' · '),
      ]
    : toolResultText(result)
        .split('\n')
        .filter((line) => line.trim().length > 0);
  const shown = lines.slice(0, RESULT_PREVIEW_LINES);
  const cut = lines.length - shown.length;

  return (
    <div
      className="niuu-chat-subagent"
      data-testid="subagent-card"
      data-status={spawn ? 'started' : (status ?? 'unfinished')}
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
              className={cn('niuu-chat-pill', `niuu-chat-pill--${spawn ? 'running' : status}`)}
              data-testid="subagent-status"
              title={
                spawn
                  ? 'The teammate works in its own pane; its result is not in this call'
                  : undefined
              }
            >
              {spawn ? 'Started' : STATUS_LABEL[status]}
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
