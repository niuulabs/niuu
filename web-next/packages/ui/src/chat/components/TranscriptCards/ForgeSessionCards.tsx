import { useState, type ComponentType } from 'react';
import { ArrowRight, GitBranch, Square } from 'lucide-react';
import { modelLabel } from '@niuulabs/domain';
import { cn } from '../../../utils/cn';
import {
  sessionOpenHandler,
  useSessionLinks,
  type SessionLink,
  type SessionLinksValue,
} from '../../sessionLinks';
import { ToolCallsDrawer } from '../ToolBlock/ToolCallsDrawer';
import { toolResultText } from '../ToolBlock/toolFamilies';
import {
  forgeSessionAction,
  type ForgeSessionAction,
  type ToolResultBlock,
  type ToolUseBlock,
} from '../ToolBlock/groupContentBlocks';
import './TranscriptCards.css';

/** A session id is a UUID; the card names a session by its first group when nothing else does. */
const SHORT_ID_LENGTH = 8;

export interface ForgeSessionCardProps {
  block: ToolUseBlock;
  result?: ToolResultBlock;
  /** The message is still being generated, so a call without a result is still running. */
  isStreaming?: boolean;
}

function text(value: unknown): string {
  return typeof value === 'string' ? value : '';
}

function record(value: unknown): Record<string, unknown> | null {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

/**
 * The tool's answer is the JSON object Forge MCP wrote. An error is plain text, and a
 * long answer may have been cut mid-object; neither is an object, and the card then
 * shows only what the call itself said.
 */
function resultObject(result: ToolResultBlock | undefined): Record<string, unknown> | null {
  if (!result || result.is_error) return null;
  const raw = toolResultText(result).trim();
  if (!raw.startsWith('{')) return null;
  try {
    return record(JSON.parse(raw));
  } catch {
    return null;
  }
}

function failureText(result: ToolResultBlock | undefined): string {
  return (
    toolResultText(result)
      .split('\n')
      .find((line) => line.trim().length > 0) ?? ''
  );
}

type DeliveryState = 'sending' | 'delivered' | 'pending' | 'failed';

const DELIVERY_LABEL: Record<DeliveryState, string> = {
  sending: 'Sending',
  delivered: 'Delivered',
  pending: 'Pending',
  failed: 'Failed',
};

const DELIVERY_PILL: Record<DeliveryState, string> = {
  sending: 'niuu-chat-pill--running',
  delivered: 'niuu-chat-pill--ok',
  pending: 'niuu-chat-pill--warn',
  failed: 'niuu-chat-pill--failed',
};

/** Forge's answer to `send_message`: delivered, or pending while its retry still drives it. */
function deliveryState(
  result: ToolResultBlock | undefined,
  isStreaming: boolean | undefined,
): DeliveryState | null {
  if (result?.is_error) return 'failed';
  if (!result) return isStreaming === false ? null : 'sending';
  const answer = resultObject(result);
  const state = text(answer?.status) || text(answer?.delivery);
  if (state === 'delivered' || state === 'pending' || state === 'failed') return state;
  return null;
}

interface Target {
  id: string;
  name: string;
  link: SessionLink | null;
}

function target(links: SessionLinksValue | null, id: string, name: string): Target {
  const link = (id && links?.resolve(id)) || (name && links?.resolve(name)) || null;
  return {
    id: link?.id ?? id,
    name: link?.name ?? (name || id.slice(0, SHORT_ID_LENGTH)),
    link,
  };
}

function SessionName({ target: who, links }: { target: Target; links: SessionLinksValue | null }) {
  if (!who.link || !links) return <span className="niuu-chat-worker-name">{who.name}</span>;
  return (
    <a
      className="niuu-chat-worker-name niuu-chat-worker-name--link"
      href={who.link.href}
      onClick={sessionOpenHandler(links, who.link)}
      aria-label={`Open session ${who.name}`}
    >
      {who.name}
    </a>
  );
}

function StatePill({ link }: { link: SessionLink }) {
  return (
    <span
      className={cn('niuu-chat-pill', `niuu-chat-pill--${link.tone}`)}
      data-testid="worker-state"
      data-tone={link.tone}
    >
      {link.stateLabel}
    </span>
  );
}

function OpenButton({ link, links }: { link: SessionLink; links: SessionLinksValue }) {
  return (
    <a
      className="niuu-chat-worker-open"
      href={link.href}
      onClick={sessionOpenHandler(links, link)}
      aria-label={`Open ${link.name}`}
    >
      Open
    </a>
  );
}

/** `create_session` / `start_session`: a worker joins the coordinator's work. */
function StartedWorker({ block, result, isStreaming }: ForgeSessionCardProps) {
  const links = useSessionLinks();
  const [open, setOpen] = useState(false);
  const { input } = block;
  const session = record(resultObject(result)?.session);
  const who = target(
    links,
    text(input.session_id) || text(session?.id),
    text(input.name) || text(session?.name),
  );
  const objective = text(input.objective);
  const model = text(input.model) || text(session?.model);
  const detail = [objective, model && modelLabel(model)].filter(Boolean).join(' · ');
  const failed = result?.is_error === true;
  const running = !result && isStreaming !== false;

  return (
    <div
      className={cn('niuu-chat-worker', failed && 'niuu-chat-worker--failed')}
      data-testid="worker-card"
      data-action={forgeSessionAction(block.name)}
      data-status={failed ? 'failed' : running ? 'running' : 'done'}
    >
      <span className="niuu-chat-worker-icon" aria-hidden="true">
        <GitBranch className="niuu-chat-card-icon" />
      </span>
      <div className="niuu-chat-worker-text">
        <div className="niuu-chat-worker-title">
          {failed ? 'Could not start worker ' : running ? 'Starting worker ' : 'Started worker '}
          <SessionName target={who} links={links} />
        </div>
        {(detail || failed) && (
          <div className="niuu-chat-worker-detail">{failed ? failureText(result) : detail}</div>
        )}
      </div>
      <span className="niuu-chat-worker-end">
        {who.link && <StatePill link={who.link} />}
        {who.link && links && <OpenButton link={who.link} links={links} />}
        <button type="button" className="niuu-chat-worker-details" onClick={() => setOpen(true)}>
          Details
        </button>
      </span>
      <ToolCallsDrawer
        open={open}
        onOpenChange={setOpen}
        title={who.name}
        description="Worker session"
        calls={[{ block, result }]}
      />
    </div>
  );
}

/** `send_message`: one line saying who was told what, and whether it arrived. */
function MessageSent({ block, result, isStreaming }: ForgeSessionCardProps) {
  const links = useSessionLinks();
  const [open, setOpen] = useState(false);
  const { input } = block;
  const who = target(links, text(input.session_id), '');
  const preview =
    text(input.content)
      .split('\n')
      .find((line) => line.trim()) ?? '';
  const state = deliveryState(result, isStreaming);

  return (
    <div className="niuu-chat-worker-row" data-testid="worker-message" data-delivery={state}>
      <ArrowRight className="niuu-chat-card-icon" aria-hidden="true" />
      <SessionName target={who} links={links} />
      <button
        type="button"
        className="niuu-chat-worker-preview"
        onClick={() => setOpen(true)}
        aria-haspopup="dialog"
        aria-label={`Open message to ${who.name}`}
      >
        {preview}
      </button>
      {state && (
        <span className={cn('niuu-chat-pill', DELIVERY_PILL[state])} data-testid="worker-delivery">
          {DELIVERY_LABEL[state]}
        </span>
      )}
      <ToolCallsDrawer
        open={open}
        onOpenChange={setOpen}
        title={`Message to ${who.name}`}
        description="Session message"
        calls={[{ block, result }]}
      />
    </div>
  );
}

/** `stop_session`: the worker is stopped, its workspace and transcript kept. */
function StoppedWorker({ block, result, isStreaming }: ForgeSessionCardProps) {
  const links = useSessionLinks();
  const who = target(links, text(block.input.session_id), '');
  const failed = result?.is_error === true;
  const running = !result && isStreaming !== false;

  return (
    <div
      className={cn('niuu-chat-worker-row', failed && 'niuu-chat-worker-row--failed')}
      data-testid="worker-stopped"
      data-status={failed ? 'failed' : running ? 'running' : 'done'}
    >
      <Square className="niuu-chat-card-icon" aria-hidden="true" />
      <span>{failed ? 'Could not stop' : running ? 'Stopping' : 'Stopped'}</span>
      <SessionName target={who} links={links} />
      {failed && <span className="niuu-chat-worker-preview">{failureText(result)}</span>}
    </div>
  );
}

const CARDS: Record<ForgeSessionAction, ComponentType<ForgeSessionCardProps>> = {
  create_session: StartedWorker,
  start_session: StartedWorker,
  send_message: MessageSent,
  stop_session: StoppedWorker,
};

/**
 * A coordinator's call on another session, as Lexi words it: it started a worker, told a
 * worker something, or stopped one. Claude names the tools `mcp__forge__<tool>`, Codex
 * `forge.<tool>`. Any other Forge tool keeps the ordinary tool row.
 */
export function ForgeSessionCard(props: ForgeSessionCardProps) {
  const action = forgeSessionAction(props.block.name);
  if (!action) return null;
  const Card = CARDS[action];
  return <Card {...props} />;
}
