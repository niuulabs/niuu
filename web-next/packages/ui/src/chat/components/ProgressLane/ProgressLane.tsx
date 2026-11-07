import type { CSSProperties } from 'react';
import { RotateCcw } from 'lucide-react';
import { cn } from '../../../utils/cn';
import { verdictLabel, type WorkerStage } from '../../workers';
import { VerdictPill, type VerdictPillTone } from '../VerdictPill';
import './ProgressLane.css';

export interface StageStatus {
  label: string;
  tone: VerdictPillTone;
}

/**
 * A worker that is running again reads "Working"; its earlier verdict is the loop count.
 * One that has reported shows what it concluded; one that has not is waiting.
 */
export function stageStatus({ worker }: WorkerStage): StageStatus {
  if (worker.status === 'working') return { label: 'Working', tone: 'working' };
  if (!worker.latest) {
    return { label: worker.status === 'left' ? 'Left' : 'Waiting', tone: 'waiting' };
  }
  return { label: verdictLabel(worker.latest.verdict), tone: worker.latest.tone };
}

const COUNT_WORDS: Record<number, string> = { 1: 'once', 2: 'twice' };

function times(count: number): string {
  return COUNT_WORDS[count] ?? `${count} times`;
}

function joinNames(names: readonly string[]): string {
  if (names.length < 2) return names.join('');
  return `${names.slice(0, -1).join(', ')} and ${names.at(-1)}`;
}

/**
 * One line on how the flow is going, read from the stages alone: who sent work back and how
 * often, and who is on it now. Null when the stages say nothing yet.
 */
export function progressSummary(stages: readonly WorkerStage[]): string | null {
  const parts: string[] = [];
  for (const { worker } of stages) {
    const sentBack = worker.outcomes.filter((outcome) => outcome.tone === 'changes').length;
    if (sentBack > 0) parts.push(`${worker.name} sent the work back ${times(sentBack)}`);
  }

  const current = stages.filter((stage) => stage.current);
  const active = current.filter((stage) => stage.worker.status === 'working');
  if (active.length > 0) {
    const names = joinNames(active.map((stage) => stage.worker.name));
    parts.push(`${names} ${active.length === 1 ? 'is' : 'are'} on it`);
  }
  const blocked = current.filter(
    (stage) => stage.worker.status !== 'working' && stage.worker.latest?.tone === 'blocked',
  );
  if (blocked.length > 0) {
    const names = joinNames(blocked.map((stage) => stage.worker.name));
    parts.push(`${names} ${blocked.length === 1 ? 'is' : 'are'} blocked`);
  }

  const settled = stages.every((stage) => stage.worker.latest?.tone === 'pass');
  if (parts.length === 0 && settled) parts.push('every worker has passed');
  return parts.length > 0 ? parts.join(' · ') : null;
}

export interface ProgressLaneProps {
  stages: readonly WorkerStage[];
  onSelect: (peerId: string) => void;
}

/** The workers' flow as stages in order; with fewer than two workers there is no flow to show. */
export function ProgressLane({ stages, onSelect }: ProgressLaneProps) {
  if (stages.length < 2) return null;
  const summary = progressSummary(stages);

  return (
    <section className="niuu-chat-lane" aria-label="Progress" data-testid="progress-lane">
      <div className="niuu-chat-lane-head">
        <RotateCcw className="niuu-chat-lane-icon" aria-hidden="true" />
        <b>Progress</b>
        {summary && <span data-testid="progress-summary">· {summary}</span>}
      </div>
      <ol className="niuu-chat-lane-stages">
        {stages.map((stage) => {
          const status = stageStatus(stage);
          const { worker } = stage;
          return (
            <li key={worker.peerId}>
              <button
                type="button"
                className={cn('niuu-chat-stage', stage.current && 'is-current')}
                style={{ '--niuu-worker-color': worker.color } as CSSProperties}
                aria-current={stage.current ? 'step' : undefined}
                data-testid="progress-stage"
                onClick={() => onSelect(worker.peerId)}
              >
                <span className="niuu-chat-stage-name">
                  <i aria-hidden="true" />
                  {worker.name}
                </span>
                <span className="niuu-chat-stage-state">
                  <VerdictPill label={status.label} tone={status.tone} />
                  {stage.loops > 0 && (
                    <span className="niuu-chat-stage-loops" title="Times it ran again">
                      ↺ {stage.loops}
                    </span>
                  )}
                </span>
              </button>
            </li>
          );
        })}
      </ol>
    </section>
  );
}
