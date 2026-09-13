import { Link } from '@tanstack/react-router';
import { StateDot } from '@niuulabs/ui';
import { RECIPE_STEPS, type RecipeProgress } from '../application/useCreateRealm';

/** The create recipe as a checklist: same rhythm as the launch wizard's boot steps. */
export function RecipeChecklist({ progress }: { progress: RecipeProgress }) {
  const doneCount = Object.values(progress.states).filter((state) => state === 'done').length;
  const pct = Math.round((doneCount / RECIPE_STEPS.length) * 100);
  return (
    <div className="niuu:flex niuu:flex-col niuu:gap-3" data-testid="recipe-checklist">
      <div className="niuu:h-1.5 niuu:overflow-hidden niuu:rounded-full niuu:bg-bg-elevated">
        <div className="niuu:h-full niuu:bg-brand niuu:transition-all" style={{ width: `${pct}%` }} />
      </div>
      <ol className="niuu:m-0 niuu:flex niuu:list-none niuu:flex-col niuu:gap-1.5 niuu:p-0">
        {RECIPE_STEPS.map((step, index) => {
          const state = progress.states[step.id] ?? 'todo';
          const color =
            state === 'done'
              ? 'niuu:text-text-muted'
              : state === 'running'
                ? 'niuu:text-brand'
                : state === 'failed'
                  ? 'niuu:text-critical-fg'
                  : 'niuu:text-text-faint';
          return (
            <li
              key={step.id}
              className={`niuu:flex niuu:items-center niuu:gap-2 niuu:text-xs ${color}`}
              data-testid={`recipe-step-${step.id}`}
              data-state={state}
            >
              <span className="niuu:w-4 niuu:font-mono niuu:text-[10px]">{index + 1}</span>
              <StateDot
                state={
                  state === 'done'
                    ? 'healthy'
                    : state === 'running'
                      ? 'processing'
                      : state === 'failed'
                        ? 'failed'
                        : 'idle'
                }
                pulse={state === 'running'}
                size={6}
              />
              <span>{step.label}</span>
            </li>
          );
        })}
      </ol>
      {progress.error && progress.failedStep ? (
        <div
          className="niuu:flex niuu:flex-col niuu:gap-1 niuu:rounded-md niuu:border niuu:border-critical-bo niuu:bg-critical-bg niuu:p-3 niuu:text-xs"
          role="alert"
        >
          <span className="niuu:font-medium niuu:text-critical-fg">
            Stopped at “{progress.failedStep.label}”.
          </span>
          <span className="niuu:text-text-secondary">{progress.error}</span>
          <span className="niuu:text-text-muted">
            Nothing was undone. What was created so far is visible in{' '}
            <Link to={progress.failedStep.advancedPath as never} className="niuu:text-brand-300">
              Advanced mode
            </Link>
            .
          </span>
        </div>
      ) : null}
    </div>
  );
}
