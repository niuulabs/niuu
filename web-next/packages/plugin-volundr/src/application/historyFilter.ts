import type { Session, SessionState } from '../domain/session';

export type HistoryOutcome = 'terminated' | 'failed';

export interface HistoryFilters {
  ravnId?: string;
  personaName?: string;
  sagaId?: string;
  outcome?: HistoryOutcome;
  dateFrom?: string;
  dateTo?: string;
}

const TERMINAL_STATES: ReadonlySet<SessionState> = new Set(['terminated', 'failed']);

/**
 * Filters sessions to only include terminated/failed sessions that match
 * all the provided filter criteria.
 */
export function applyHistoryFilters(sessions: Session[], filters: HistoryFilters): Session[] {
  return sessions.filter((s) => {
    if (!TERMINAL_STATES.has(s.state)) return false;
    if (filters.ravnId && s.ravnId !== filters.ravnId) return false;
    if (filters.personaName && s.personaName !== filters.personaName) return false;
    if (filters.sagaId && s.sagaId !== filters.sagaId) return false;
    if (filters.outcome && s.state !== filters.outcome) return false;
    if (filters.dateFrom && s.terminatedAt && s.terminatedAt < filters.dateFrom) return false;
    if (filters.dateTo && s.terminatedAt && s.terminatedAt > filters.dateTo) return false;
    return true;
  });
}
