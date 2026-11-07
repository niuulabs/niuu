import { useId, useState } from 'react';
import { Check, ChevronDown, ChevronUp, Users } from 'lucide-react';
import { cn } from '../../../utils/cn';
import type { SessionAgent, SessionPlan, SessionPlanTask } from '../../types';
import './PlanDock.css';

// The progress ring is drawn in a 16x16 viewBox; these are its geometry, not tunables.
const RING_CENTER = 8;
const RING_RADIUS = 6.5;
const RING_STROKE = 2;
const RING_CIRCUMFERENCE = 2 * Math.PI * RING_RADIUS;

type TaskState = 'done' | 'active' | 'pending';

function taskState(task: SessionPlanTask): TaskState {
  if (task.status === 'completed') return 'done';
  if (task.status === 'in_progress') return 'active';
  return 'pending';
}

export interface PlanDockProps {
  plan: SessionPlan | null | undefined;
  agents?: readonly SessionAgent[];
  className?: string;
}

/** What the agent is doing now: the running task, else the next one, else that it is done. */
function currentLabel(tasks: readonly SessionPlanTask[]): string {
  const active = tasks.find((task) => taskState(task) === 'active');
  if (active) return active.activeForm ?? active.content;
  const next = tasks.find((task) => taskState(task) === 'pending');
  return next ? next.content : 'Plan complete';
}

/**
 * The agent's plan as a capsule above the composer: progress, what is running now and how
 * many subagents are working. Click to open the checklist. Not shown without tasks.
 */
export function PlanDock({ plan, agents = [], className }: PlanDockProps) {
  const [open, setOpen] = useState(false);
  const listId = useId();
  if (!plan || plan.tasks.length === 0) return null;

  const { tasks } = plan;
  const done = tasks.filter((task) => taskState(task) === 'done').length;
  const running = agents.filter((agent) => agent.status === 'running').length;
  const Chevron = open ? ChevronDown : ChevronUp;

  return (
    <div className={cn('niuu-plan-dock', className)} data-open={open || undefined}>
      <button
        type="button"
        className="niuu-plan-dock__cap"
        aria-expanded={open}
        aria-controls={listId}
        onClick={() => setOpen((value) => !value)}
      >
        <svg className="niuu-plan-dock__ring" viewBox="0 0 16 16" aria-hidden="true">
          <circle
            className="niuu-plan-dock__ring-track"
            cx={RING_CENTER}
            cy={RING_CENTER}
            r={RING_RADIUS}
            strokeWidth={RING_STROKE}
          />
          <circle
            className="niuu-plan-dock__ring-fill"
            cx={RING_CENTER}
            cy={RING_CENTER}
            r={RING_RADIUS}
            strokeWidth={RING_STROKE}
            strokeDasharray={`${(RING_CIRCUMFERENCE * done) / tasks.length} ${RING_CIRCUMFERENCE}`}
            transform={`rotate(-90 ${RING_CENTER} ${RING_CENTER})`}
          />
        </svg>
        <span className="niuu-plan-dock__summary">
          <b>
            {done}/{tasks.length}
          </b>{' '}
          · {currentLabel(tasks)}
        </span>
        {running > 0 && (
          <span className="niuu-plan-dock__agents">
            · <Users className="niuu-plan-dock__agents-icon" aria-hidden="true" /> {running}{' '}
            {running === 1 ? 'agent' : 'agents'} running
          </span>
        )}
        <Chevron className="niuu-plan-dock__chevron" aria-hidden="true" />
      </button>
      {open && (
        <ul id={listId} className="niuu-plan-dock__list" aria-label="Plan tasks">
          {tasks.map((task, index) => {
            const state = taskState(task);
            return (
              <li
                key={`${index}-${task.content}`}
                className={cn('niuu-plan-dock__item', `niuu-plan-dock__item--${state}`)}
                aria-current={state === 'active' ? 'step' : undefined}
              >
                <span className="niuu-plan-dock__box" aria-hidden="true">
                  {state === 'done' && <Check className="niuu-plan-dock__check" />}
                </span>
                {task.content}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
