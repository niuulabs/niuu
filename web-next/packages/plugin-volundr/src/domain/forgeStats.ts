import type { VolundrStats } from '../models/volundr.model';

/** Sum only received host snapshots; callers identify missing or stale hosts. */
export function mergeForgeStats(snapshots: VolundrStats[]): VolundrStats {
  const totals: VolundrStats = {
    activeSessions: 0,
    totalSessions: 0,
    sessionsToday: 0,
    tokensToday: 0,
    localTokens: 0,
    cloudTokens: 0,
    costToday: 0,
  };
  for (const snapshot of snapshots) {
    for (const field of [
      'activeSessions',
      'totalSessions',
      'sessionsToday',
      'tokensToday',
      'localTokens',
      'cloudTokens',
      'costToday',
    ] as const) {
      totals[field] += snapshot[field];
    }
    for (const key of ['tokensToday', 'activePods', 'sessionsToday', 'costToday'] as const) {
      const points = snapshot.sparklines?.[key];
      if (!points) continue;
      totals.sparklines ??= {};
      const merged = (totals.sparklines[key] ??= []);
      points.forEach((point, index) => {
        merged[index] = (merged[index] ?? 0) + point;
      });
    }
  }
  return totals;
}
