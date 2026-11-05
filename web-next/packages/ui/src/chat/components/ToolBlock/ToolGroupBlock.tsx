import { useState } from 'react';
import { AlertTriangle, Check, ChevronRight, Loader2 } from 'lucide-react';
import { cn } from '../../../utils/cn';
import { useCompactUxChatPrefs } from '../../compactUxPrefs';
import { ToolBlock } from './ToolBlock';
import { ToolCallsDrawer, type ToolCall } from './ToolCallsDrawer';
import {
  familyInfo,
  runFamilies,
  summarizeRun,
  sumDiffstat,
  toolFamilyInfo,
  toolSubject,
  toolTicker,
} from './toolFamilies';
import './ToolBlock.css';
import './ToolRun.css';

/** The row shows this many family chips; any further families are counted as "+N". */
const MAX_FAMILY_CHIPS = 3;
/** Calls rendered per page in "every call" mode; the rest sit behind a visible button. */
const CARDS_PAGE_SIZE = 25;

interface ToolGroupBlockProps {
  /** The name of the burst's first call; kept for callers and tests that identify a run by it. */
  toolName?: string;
  blocks: ToolCall[];
  /** The message is still being generated: a call without a result is then in flight. */
  isStreaming?: boolean;
}

function plural(count: number, one: string, many: string): string {
  return `${count} ${count === 1 ? one : many}`;
}

/**
 * One burst of consecutive tool calls. By default a single row (family chips, a
 * humanised summary, the diffstat, a status glyph) that opens a right-hand sheet
 * with every call; with the `toolRuns: 'cards'` display preference the calls are
 * rendered inline as `ToolBlock`s instead.
 */
export function ToolGroupBlock({ toolName, blocks, isStreaming = false }: ToolGroupBlockProps) {
  const { toolRuns } = useCompactUxChatPrefs();
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [visibleCount, setVisibleCount] = useState(CARDS_PAGE_SIZE);

  if (toolRuns === 'cards') {
    return (
      <div
        className="niuu-chat-toolrun-cards"
        data-testid="tool-group-block"
        data-tool-name={toolName}
      >
        {blocks.slice(0, visibleCount).map((item) => (
          <ToolBlock key={item.block.id} block={item.block} result={item.result} />
        ))}
        {visibleCount < blocks.length && (
          <button
            type="button"
            className="niuu-chat-tool-show-more"
            onClick={() => setVisibleCount((count) => count + CARDS_PAGE_SIZE)}
          >
            Show {Math.min(CARDS_PAGE_SIZE, blocks.length - visibleCount)} more calls (
            {blocks.length - visibleCount} remaining)
          </button>
        )}
      </div>
    );
  }

  const uses = blocks.map(({ block }) => block);
  const pending = blocks.filter(({ result }) => !result);
  const running = isStreaming && pending.length > 0;
  const failed = blocks.some(({ result }) => result?.is_error);
  const families = runFamilies(uses);
  const chips = families.slice(0, MAX_FAMILY_CHIPS);
  const hiddenFamilies = families.length - chips.length;
  const summary = summarizeRun(uses);
  const summaryText = summary.map((segment) => segment.phrase).join(' · ');
  const diff = sumDiffstat(uses);
  const [only] = blocks;
  const single = blocks.length === 1 && only ? only.block : null;
  const live = pending.at(-1)?.block;

  const status = running ? 'Running' : failed ? 'A call failed' : 'Done';
  const title = plural(blocks.length, 'tool call', 'tool calls');

  return (
    <div
      className={cn('niuu-chat-toolrun', running && 'niuu-chat-toolrun--live')}
      data-testid="tool-group-block"
      data-tool-name={toolName}
      data-status={running ? 'running' : failed ? 'failed' : 'done'}
    >
      <button
        type="button"
        className="niuu-chat-toolrun-row"
        onClick={() => setDrawerOpen(true)}
        aria-haspopup="dialog"
        aria-label={`Open ${title}: ${summaryText}. ${status}.`}
      >
        <span className="niuu-chat-toolrun-line">
          <span className="niuu-chat-toolrun-chips" aria-hidden="true">
            {chips.map((family) => {
              const Icon = familyInfo(family).icon;
              return (
                <span key={family} className="niuu-chat-toolrun-chip" data-family={family}>
                  <Icon className="niuu-chat-toolrun-chip-icon" />
                </span>
              );
            })}
            {hiddenFamilies > 0 && (
              <span className="niuu-chat-toolrun-chip niuu-chat-toolrun-chip--more">
                +{hiddenFamilies}
              </span>
            )}
          </span>
          <span className="niuu-chat-toolrun-summary">
            {single ? (
              <>
                <b>
                  {running ? toolFamilyInfo(single.name).present : toolFamilyInfo(single.name).past}
                </b>{' '}
                <span className="niuu-chat-toolrun-subject">{toolSubject(single)}</span>
              </>
            ) : (
              summary.map((segment, index) => (
                <span key={segment.key}>
                  {index > 0 && <span className="niuu-chat-toolrun-sep"> · </span>}
                  {index === 0 ? <b>{segment.phrase}</b> : segment.phrase}
                </span>
              ))
            )}
          </span>
          {diff.added + diff.removed > 0 && (
            <span className="niuu-chat-toolrun-diff" data-testid="tool-run-diffstat">
              <span className="niuu-chat-toolrun-diff-add">+{diff.added}</span>{' '}
              <span className="niuu-chat-toolrun-diff-del">−{diff.removed}</span>
            </span>
          )}
          <span
            className="niuu-chat-toolrun-state"
            role="img"
            aria-label={status}
            data-state={running ? 'running' : failed ? 'failed' : 'done'}
          >
            {running ? (
              <Loader2 className="niuu-chat-toolrun-spinner" />
            ) : failed ? (
              <AlertTriangle className="niuu-chat-toolrun-glyph" />
            ) : (
              <Check className="niuu-chat-toolrun-glyph" />
            )}
          </span>
          <ChevronRight className="niuu-chat-toolrun-chevron" aria-hidden="true" />
        </span>
        {running && live && !single && (
          <span className="niuu-chat-toolrun-ticker" data-testid="tool-run-ticker">
            <span>{toolTicker(live)}</span>
          </span>
        )}
      </button>
      <ToolCallsDrawer
        open={drawerOpen}
        onOpenChange={setDrawerOpen}
        title={title}
        description={summaryText}
        calls={blocks}
      />
    </div>
  );
}
