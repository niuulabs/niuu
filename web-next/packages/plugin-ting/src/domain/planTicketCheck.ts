import type { PhaseSpec } from '../ports';

/** Share of title words two tickets must have in common before one is flagged as a repeat. */
const DUPLICATE_TITLE_SIMILARITY = 0.7;

const STOP_WORDS = new Set(['a', 'an', 'and', 'for', 'in', 'of', 'on', 'the', 'to', 'with']);

export interface ExistingTicket {
  identifier: string;
  title: string;
}

export interface PlanTicketFlag {
  kind: 'vague' | 'duplicate';
  phaseIndex: number;
  runIndex: number;
  runName: string;
  message: string;
}

function repositoryKey(repo: string): string {
  const path = repo
    .trim()
    .toLocaleLowerCase()
    .replace(/\/+$/, '')
    .replace(/\.git$/, '');
  return path.split(/[/:]/).slice(-2).join('/');
}

/** Plans carry clone URLs while imported projects carry `org/name`; both name one repository. */
export function isSameRepository(left: string, right: string): boolean {
  return repositoryKey(left) === repositoryKey(right);
}

function titleWords(title: string): Set<string> {
  const words = title.toLocaleLowerCase().match(/[\p{L}\p{N}]+/gu) ?? [];
  return new Set(words.filter((word) => !STOP_WORDS.has(word)));
}

function titleSimilarity(left: string, right: string): number {
  const a = titleWords(left);
  const b = titleWords(right);
  if (a.size === 0 || b.size === 0) return 0;
  const shared = [...a].filter((word) => b.has(word)).length;
  return (2 * shared) / (a.size + b.size);
}

function looksLikeRepeat(left: string, right: string): boolean {
  return titleSimilarity(left, right) >= DUPLICATE_TITLE_SIMILARITY;
}

/**
 * Checks a draft before anything reaches the tracker. These are prompts for the reviewer, not a
 * verdict: titles are compared word by word, so a flag can be wrong and approval is never blocked.
 */
export function checkPlanTickets(
  phases: readonly PhaseSpec[],
  existing: readonly ExistingTicket[],
): PlanTicketFlag[] {
  const runs = phases.flatMap((phase, phaseIndex) =>
    phase.runs.map((run, runIndex) => ({ run, phaseIndex, runIndex })),
  );
  return runs.flatMap(({ run, phaseIndex, runIndex }, position): PlanTicketFlag[] => {
    const flag = (kind: PlanTicketFlag['kind'], message: string): PlanTicketFlag => ({
      kind,
      phaseIndex,
      runIndex,
      runName: run.name,
      message,
    });
    const earlierRepeat = runs
      .slice(0, position)
      .find((other) => looksLikeRepeat(run.name, other.run.name));
    const existingRepeat = existing.find((ticket) => looksLikeRepeat(run.name, ticket.title));
    return [
      ...(run.description.trim() ? [] : [flag('vague', 'No description')]),
      ...(run.acceptanceCriteria.length > 0 ? [] : [flag('vague', 'No acceptance criteria')]),
      ...(earlierRepeat
        ? [flag('duplicate', `Overlaps "${earlierRepeat.run.name}" in this plan`)]
        : []),
      ...(existingRepeat
        ? [flag('duplicate', `Looks like ${existingRepeat.identifier}: ${existingRepeat.title}`)]
        : []),
    ];
  });
}

export function buildRevisionRequest(flags: readonly PlanTicketFlag[]): string {
  const lines = flags.map((flag) => `- ${flag.runName}: ${flag.message}`);
  return ['Fix these tickets, and merge or drop any that repeat existing work:', ...lines].join(
    '\n',
  );
}
