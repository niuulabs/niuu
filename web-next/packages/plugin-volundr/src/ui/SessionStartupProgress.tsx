import { useSyncExternalStore } from 'react';
import { Circle, CircleCheck, CircleX, LoaderCircle } from 'lucide-react';
import type { SessionStartup, StartupStage } from '../models/volundr.model';
import {
  currentStage,
  formatDuration,
  launchElapsedMs,
  stageDurationMs,
  stagesForStatus,
  startupHeadline,
} from '../domain/sessionStartup';

// One clock for every mounted panel and row; elapsed time only moves from a real
// timestamp Forge recorded, it is never a guess at progress.
const listeners = new Set<() => void>();
let timer: ReturnType<typeof setInterval> | undefined;
const clock = () => Math.floor(Date.now() / 1000) * 1000;
function subscribe(listener: () => void) {
  listeners.add(listener);
  timer ??= setInterval(() => listeners.forEach((notify) => notify()), 1000);
  return () => {
    listeners.delete(listener);
    if (!listeners.size) {
      clearInterval(timer);
      timer = undefined;
    }
  };
}

function useNow(): number {
  return useSyncExternalStore(subscribe, clock, clock);
}

function StageIcon({ stage }: { stage: StartupStage }) {
  const size = 'niuu:h-4 niuu:w-4 niuu:shrink-0 niuu:mt-0.5';
  switch (stage.state) {
    case 'done':
      return <CircleCheck aria-hidden className={`${size} niuu:text-state-ok`} />;
    case 'failed':
      return <CircleX aria-hidden className={`${size} niuu:text-critical`} />;
    case 'active':
      return <LoaderCircle aria-hidden className={`${size} niuu:animate-spin niuu:text-brand`} />;
    default:
      return <Circle aria-hidden className={`${size} niuu:text-text-faint`} />;
  }
}

const STATE_NAMES: Record<StartupStage['state'], string> = {
  pending: 'to come',
  active: 'in progress',
  done: 'done',
  failed: 'failed',
};

interface SessionStartupProgressProps {
  startup: SessionStartup;
  /** The session's lifecycle status, so a launch that failed unwatched reads as failed. */
  status: string | null | undefined;
  error?: string | null;
}

/** What a starting session is waiting on, stage by stage, as its backend reported it. */
export function SessionStartupProgress({ startup, status, error }: SessionStartupProgressProps) {
  const now = useNow();
  const stages = stagesForStatus(startup, status, error);
  const failed = stages.some((stage) => stage.state === 'failed');
  return (
    <section
      aria-label="Session startup"
      data-testid="session-startup-progress"
      className="niuu:mx-auto niuu:w-full niuu:max-w-xl niuu:rounded-lg niuu:border niuu:border-border-subtle niuu:bg-bg-secondary niuu:px-4 niuu:py-3"
    >
      <header className="niuu:mb-2 niuu:flex niuu:items-center niuu:gap-2 niuu:text-sm">
        <span className="niuu:font-medium niuu:text-text-primary">
          {failed ? 'Startup failed' : 'Starting'}
        </span>
        <span
          className="niuu:ml-auto niuu:font-mono niuu:text-xs niuu:text-text-secondary"
          data-testid="session-startup-elapsed"
        >
          {formatDuration(launchElapsedMs(startup, stages, now))}
        </span>
      </header>
      <ol className="niuu:flex niuu:flex-col niuu:gap-1">
        {stages.map((stage) => {
          const duration = stageDurationMs(stage, now);
          return (
            <li
              key={stage.key}
              data-testid={`startup-stage-${stage.key}`}
              data-state={stage.state}
              className="niuu:flex niuu:items-start niuu:gap-2 niuu:py-0.5 niuu:text-sm"
            >
              <StageIcon stage={stage} />
              <div className="niuu:min-w-0 niuu:flex-1">
                <div
                  className={
                    stage.state === 'pending'
                      ? 'niuu:text-text-faint'
                      : stage.state === 'failed'
                        ? 'niuu:font-medium niuu:text-critical'
                        : stage.state === 'active'
                          ? 'niuu:font-medium niuu:text-text-primary'
                          : 'niuu:text-text-secondary'
                  }
                >
                  {stage.label}
                  <span className="niuu:sr-only">, {STATE_NAMES[stage.state]}</span>
                </div>
                {stage.detail ? (
                  <div
                    className={`niuu:font-mono niuu:text-xs niuu:break-words ${
                      stage.state === 'failed' ? 'niuu:text-critical' : 'niuu:text-text-muted'
                    }`}
                  >
                    {stage.detail}
                  </div>
                ) : null}
              </div>
              <span className="niuu:shrink-0 niuu:font-mono niuu:text-xs niuu:text-text-muted">
                {duration === null ? '' : formatDuration(duration)}
              </span>
            </li>
          );
        })}
      </ol>
      {startup.progressError ? (
        <p
          role="status"
          className="niuu:mt-2 niuu:text-xs niuu:text-state-warn"
          data-testid="session-startup-progress-error"
        >
          Forge could not read this launch&apos;s progress: {startup.progressError}
        </p>
      ) : null}
    </section>
  );
}

interface SessionStartupHeadlineProps {
  startup: SessionStartup;
  status: string | null | undefined;
  error?: string | null;
  className?: string;
}

/** A session row's one line: the current launch stage and how long the launch has taken. */
export function SessionStartupHeadline({
  startup,
  status,
  error,
  className,
}: SessionStartupHeadlineProps) {
  const now = useNow();
  const stages = stagesForStatus(startup, status, error);
  const headline = startupHeadline(currentStage(stages));
  if (!headline) return null;
  const elapsed = formatDuration(launchElapsedMs(startup, stages, now));
  // The row may cut the backend's words short; the launch time always stays visible.
  return (
    <span
      className={`${className ?? ''} niuu:flex niuu:min-w-0 niuu:gap-1`}
      title={`${headline} (${elapsed})`}
      data-testid="session-startup-headline"
    >
      <span className="niuu:min-w-0 niuu:truncate">{headline}</span>
      <span className="niuu:shrink-0 niuu:font-mono"> · {elapsed}</span>
    </span>
  );
}
