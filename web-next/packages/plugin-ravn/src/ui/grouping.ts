import type { Ravn } from '../domain/ravn';
import type { BudgetState } from '@niuulabs/domain';

/** The available grouping keys for the Ravens split view. */
export type GroupKey = 'location' | 'persona' | 'state' | 'flock' | 'none';

/** Map a ravn status string to the DotState union type. */
export function ravnStatusToDotState(
  status: string,
): 'running' | 'attention' | 'failed' | 'unknown' {
  if (status === 'active') return 'running';
  if (status === 'suspended') return 'attention';
  if (status === 'failed') return 'failed';
  return 'unknown';
}

/**
 * Group a flat list of ravens by the given key.
 * Returns a record of group-label → ravens-in-group.
 * Within each group, ravens are sorted by personaName alphabetically.
 */
export function groupRavens(ravens: Ravn[], by: GroupKey): Record<string, Ravn[]> {
  const sorted = [...ravens].sort((a, b) =>
    (a.residentName || a.personaName).localeCompare(b.residentName || b.personaName),
  );

  if (by === 'none') return { all: sorted };

  const groups: Record<string, Ravn[]> = {};

  for (const r of sorted) {
    let key: string;

    if (by === 'flock') key = r.flockId ? `mesh ${r.flockId.slice(0, 8)}` : 'independent';
    else if (by === 'persona') key = r.personaName || r.residentName || 'unassigned';
    else if (by === 'state') key = r.status;
    else key = r.instanceName || r.location || 'unplaced';

    (groups[key] ??= []).push(r);
  }

  return groups;
}

/** Default number of top budget spenders to show. */
const TOP_BUDGET_SPENDERS_DEFAULT = 5;

/**
 * Return the top-N ravens ordered by USD spent today (descending).
 * Ravens with no budget entry are treated as $0 spent.
 */
export function topBudgetSpenders(
  ravens: Ravn[],
  budgets: Record<string, BudgetState>,
  n = TOP_BUDGET_SPENDERS_DEFAULT,
): Ravn[] {
  return [...ravens]
    .sort((a, b) => (budgets[b.id]?.spentUsd ?? 0) - (budgets[a.id]?.spentUsd ?? 0))
    .slice(0, n);
}
