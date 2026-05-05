/**
 * Exec domain — run-and-wait command entries for the Exec tab.
 *
 * Each ExecEntry records a single command invocation with its output and
 * terminal status. History is accumulated by the useExec hook.
 */

export type ExecStatus = 'running' | 'success' | 'error';

export interface ExecEntry {
  id: string;
  command: string;
  output: string;
  status: ExecStatus;
  startedAt: number;
  finishedAt?: number;
}

/** Append a new entry to the exec history list. */
export function appendExecEntry(history: ExecEntry[], entry: ExecEntry): ExecEntry[] {
  return [...history, entry];
}

/** Replace an existing entry by id (e.g. to update output / status). */
export function updateExecEntry(
  history: ExecEntry[],
  id: string,
  updates: Partial<ExecEntry>,
): ExecEntry[] {
  return history.map((e) => (e.id === id ? { ...e, ...updates } : e));
}
