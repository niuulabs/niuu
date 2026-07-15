/**
 * Human copy for every internal Valkyrie vocabulary term.
 *
 * The runtime speaks in snake_case enums (`using_adopted_learning`,
 * `human_review_required`). Nothing rendered to an operator may show those
 * raw values — every string crosses through this module first.
 */

import {
  decisionHasRealAction,
  decisionNeedsApproval,
  type AutonomyMode,
  type DecisionRecord,
} from '../domain';

interface TermCopy {
  label: string;
  description: string;
}

const OPERATIONAL_STATE_COPY: Record<string, TermCopy> = {
  watching: {
    label: 'Watching',
    description: 'Signals are flowing and nothing needs attention right now.',
  },
  investigating: {
    label: 'Investigating',
    description: 'A signal crossed the threshold and the resident is digging into it.',
  },
  incident: {
    label: 'Incident',
    description: 'Something is actively wrong in the environment.',
  },
  recovery: {
    label: 'Recovering',
    description: 'The environment is coming back from a problem.',
  },
  nominal: {
    label: 'Nominal',
    description: 'Everything the resident maintains matches its desired state.',
  },
  using_adopted_learning: {
    label: 'Handled with a learned skill',
    description: 'The resident recognized this signal and handled it with a skill it learned.',
  },
  adopted_learning_failed: {
    label: 'Learned skill failed',
    description: 'A learned skill matched this signal but its run failed.',
  },
  adopted_learning_regressed: {
    label: 'Learned skill rolled back',
    description: 'A learned skill kept failing and was automatically rolled back.',
  },
};

const ACTION_AUTHORITY_COPY: Record<string, TermCopy> = {
  autonomous: {
    label: 'Acted autonomously',
    description: 'Within the resident’s own authority; no human involved.',
  },
  yolo_allowed: {
    label: 'Allowed in yolo mode',
    description: 'Executed without review because yolo mode grants it.',
  },
  court_required: {
    label: 'Needs court approval',
    description: 'The ODIN court must weigh this before anything executes.',
  },
  human_review_required: {
    label: 'Needs your approval',
    description: 'Waiting in the review inbox for an operator decision.',
  },
  operator_approved: {
    label: 'Operator approved',
    description: 'A human approved this action from the review inbox.',
  },
  observe_only: {
    label: 'Observation only',
    description: 'No action was proposed; the resident only recorded what it saw.',
  },
};

const AUTONOMY_MODE_COPY: Record<AutonomyMode, TermCopy> = {
  guarded: {
    label: 'Guarded',
    description:
      'Observes and investigates on its own; every action and learned skill waits ' +
      'for your approval in the review inbox.',
  },
  autonomous: {
    label: 'Autonomous',
    description:
      'Acts on its own within its authority boundary; high-risk, destructive, or ' +
      'gated actions still route to the review inbox.',
  },
  yolo: {
    label: 'Yolo',
    description:
      'Acts without asking, including learned-skill rollouts. Automatic rollback ' +
      'on regression is the only safety net.',
  },
};

const SEVERITY_COPY: Record<string, TermCopy> = {
  info: { label: 'Info', description: 'Routine observation.' },
  notice: { label: 'Notice', description: 'Worth remembering, not worth acting on.' },
  warning: { label: 'Warning', description: 'Something drifted; usually triggers a task.' },
  critical: { label: 'Critical', description: 'Requires investigation now.' },
};

const WAKEFULNESS_COPY: Record<string, TermCopy> = {
  sleeping: { label: 'Sleeping', description: 'Not processing signals.' },
  watching: { label: 'Watching', description: 'Passively monitoring the environment.' },
  wakeful: { label: 'Wakeful', description: 'Actively working on something.' },
  dreaming: { label: 'Dreaming', description: 'Consolidating what it learned.' },
};

const OUTCOME_COPY: Record<string, TermCopy> = {
  executed: { label: 'Action executed', description: 'The resulting action completed.' },
  failed: { label: 'Action failed', description: 'The resulting action did not complete.' },
};

const REVIEW_KIND_COPY: Record<string, string> = {
  evolution_build: 'Self-built tool',
  skill_promotion: 'Skill promotion',
  flock_learning: 'Flock learning',
  court_escalation: 'Action approval',
  autonomy_change: 'Autonomy change',
  morning_brief: 'Morning brief',
};

function fallbackCopy(value: string): TermCopy {
  const label = value.replace(/[_.]/g, ' ').trim();
  return {
    label: label ? label.charAt(0).toUpperCase() + label.slice(1) : 'Unknown',
    description: '',
  };
}

export function operationalStateCopy(state: string): TermCopy {
  return OPERATIONAL_STATE_COPY[state] ?? fallbackCopy(state);
}

export function actionAuthorityCopy(authority: string): TermCopy {
  return ACTION_AUTHORITY_COPY[authority] ?? fallbackCopy(authority);
}

/**
 * The truthful status for a decision row. Only a judgment that actually
 * reaches the review inbox (see `decisionNeedsApproval`) reads as "Needs your
 * approval"; ambient/observational/autonomous decisions get a neutral status
 * ("Observation only" / "Acted autonomously" / the raw authority copy) so we
 * never mislabel background work as waiting on the operator.
 */
export function decisionStatusCopy(
  decision: Pick<DecisionRecord, 'actionAuthority' | 'tier' | 'recommendedAction'>,
): TermCopy {
  if (decisionNeedsApproval(decision)) {
    return ACTION_AUTHORITY_COPY.human_review_required!;
  }
  if (!decisionHasRealAction(decision)) {
    // No real action was proposed — whatever the authority says, the resident
    // only recorded what it saw.
    return ACTION_AUTHORITY_COPY.observe_only!;
  }
  if (decision.actionAuthority === 'human_review_required') {
    // Authority says review, but the tier gate means it never surfaces to the
    // inbox — it is observational, not pending.
    return ACTION_AUTHORITY_COPY.observe_only!;
  }
  return actionAuthorityCopy(decision.actionAuthority);
}

export function autonomyModeCopy(mode: AutonomyMode): TermCopy {
  return AUTONOMY_MODE_COPY[mode] ?? fallbackCopy(mode);
}

/** One-line boundary reminder for the autonomy stat card. */
const AUTONOMY_MODE_HINT: Record<AutonomyMode, string> = {
  guarded: 'every action waits for review',
  autonomous: 'court above high risk',
  yolo: 'no review, rollback only',
};

export function autonomyModeHint(mode: AutonomyMode): string {
  return AUTONOMY_MODE_HINT[mode];
}

/**
 * The most useful message an error carries: the API error `detail` when
 * present (ApiClientError), else the Error message, else the fallback.
 */
export function errorMessage(error: unknown, fallback: string): string {
  if (error && typeof error === 'object' && 'detail' in error) {
    const detail = (error as { detail?: unknown }).detail;
    if (typeof detail === 'string' && detail) return detail;
  }
  if (error instanceof Error && error.message) return error.message;
  return fallback;
}

export function severityCopy(severity: string): TermCopy {
  return SEVERITY_COPY[severity] ?? fallbackCopy(severity);
}

export function wakefulnessCopy(state: string): TermCopy {
  return WAKEFULNESS_COPY[state] ?? fallbackCopy(state);
}

export function outcomeCopy(outcome: string): TermCopy | null {
  if (!outcome) return null;
  return OUTCOME_COPY[outcome] ?? fallbackCopy(outcome);
}

export function reviewKindLabel(kind: string): string {
  return REVIEW_KIND_COPY[kind] ?? fallbackCopy(kind).label;
}

const LEARNED_SKILL_STATES = new Set([
  'using_adopted_learning',
  'adopted_learning_failed',
  'adopted_learning_regressed',
]);

/** True when a judgment state means "a learned skill was exercised". */
export function isLearnedSkillState(state: string): boolean {
  return LEARNED_SKILL_STATES.has(state);
}

/**
 * Explain a quiet environment instead of showing an empty panel: how many
 * signals were seen and why none of them produced a task.
 */
export function describeIdleSituation(
  signalCount: number,
  taskSeverities: string[] | undefined,
): string {
  if (signalCount === 0) {
    return (
      'No signals have been observed from this environment yet. Signals appear ' +
      'here as soon as a configured source reports one.'
    );
  }
  const severities = taskSeverities?.length
    ? taskSeverities.map((entry) => severityCopy(entry).label.toLowerCase()).join(' or ')
    : 'warning or critical';
  return (
    `Watching — ${signalCount} signal(s) observed, none crossed the task ` +
    `threshold (only ${severities} signals start an investigation).`
  );
}
