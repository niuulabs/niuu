/**
 * Reading a session's launch stages. The stages and their times come from Forge,
 * which records what the runtime backend reported; the only thing computed here
 * is how long ago a real timestamp was.
 */

import type { SessionStartup, StartupStage } from '../models/volundr.model';

/** `42s`, `1:42`, `1:02:05`. */
export function formatDuration(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000));
  if (total < 60) return `${total}s`;
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor(total / 60) % 60;
  const seconds = String(total % 60).padStart(2, '0');
  if (hours === 0) return `${minutes}:${seconds}`;
  return `${hours}:${String(minutes).padStart(2, '0')}:${seconds}`;
}

/** How long a stage took, or has taken so far; null for a stage still to come. */
export function stageDurationMs(stage: StartupStage, now: number): number | null {
  const started = Date.parse(stage.startedAt ?? '');
  if (!Number.isFinite(started)) return null;
  const finished = Date.parse(stage.finishedAt ?? '');
  return (Number.isFinite(finished) ? finished : now) - started;
}

/** The stage the launch is on now: the last one active or failed. */
export function currentStage(stages: readonly StartupStage[]): StartupStage | null {
  for (let index = stages.length - 1; index >= 0; index -= 1) {
    const stage = stages[index]!;
    if (stage.state === 'active' || stage.state === 'failed') return stage;
  }
  return null;
}

/**
 * The stages to show for a session in `status`.
 *
 * Forge closes a launch when the session leaves starting. A session that failed
 * while no Forge was watching it (a restart) still has its stage open; that stage
 * is where it failed, for the session's own reason.
 */
export function stagesForStatus(
  startup: SessionStartup,
  status: string | null | undefined,
  error: string | null | undefined,
): StartupStage[] {
  if (status !== 'failed' && status !== 'error') return startup.stages;
  const open = currentStage(startup.stages);
  if (!open || open.state !== 'active') return startup.stages;
  return startup.stages
    .filter((stage) => stage.state !== 'pending')
    .map((stage) =>
      stage === open ? { ...stage, state: 'failed', detail: error ?? stage.detail } : stage,
    );
}

/** How long the launch has taken: until its last stage ended, or until now while one is open. */
export function launchElapsedMs(
  startup: SessionStartup,
  stages: readonly StartupStage[],
  now: number,
): number {
  const started = Date.parse(startup.startedAt);
  if (!Number.isFinite(started)) return 0;
  if (stages.some((stage) => stage.state === 'active')) return now - started;
  const ends = stages
    .map((stage) => Date.parse(stage.finishedAt ?? ''))
    .filter((end) => Number.isFinite(end));
  return (ends.length ? Math.max(...ends) : now) - started;
}

/** One line for a session row: the current stage and what the backend says about it. */
export function startupHeadline(stage: StartupStage | null): string | null {
  if (!stage) return null;
  const label = stage.state === 'failed' ? `${stage.label} failed` : stage.label;
  return stage.detail ? `${label} · ${stage.detail}` : label;
}
