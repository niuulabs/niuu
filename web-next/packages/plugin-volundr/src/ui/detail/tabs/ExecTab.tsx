import { useState, useRef } from 'react';
import type { UseExecResult } from '../../hooks/useExec';
import type { ExecEntry } from '../../../domain/exec';

interface ExecEntryRowProps {
  entry: ExecEntry;
}

function ExecEntryRow({ entry }: ExecEntryRowProps) {
  const elapsed =
    entry.finishedAt !== undefined
      ? `${((entry.finishedAt - entry.startedAt) / 1000).toFixed(2)}s`
      : '…';

  return (
    <div
      className="niuu-flex niuu-flex-col niuu-gap-1 niuu-rounded-md niuu-border niuu-border-border-subtle niuu-bg-bg-secondary niuu-p-3"
      data-testid="exec-entry"
      data-status={entry.status}
    >
      {/* Command header */}
      <div className="niuu-flex niuu-items-center niuu-gap-2">
        <span
          className={
            entry.status === 'running'
              ? 'niuu-text-brand'
              : entry.status === 'error'
                ? 'niuu-text-critical'
                : 'niuu-text-text-muted'
          }
          aria-label={`status: ${entry.status}`}
        >
          {entry.status === 'running' ? '⟳' : entry.status === 'error' ? '✗' : '✓'}
        </span>
        <span className="niuu-flex-1 niuu-font-mono niuu-text-sm niuu-text-text-primary">
          $ {entry.command}
        </span>
        <span className="niuu-font-mono niuu-text-xs niuu-text-text-muted">{elapsed}</span>
      </div>

      {/* Output */}
      {entry.output && (
        <pre
          className="niuu-mt-1 niuu-max-h-48 niuu-overflow-auto niuu-rounded niuu-bg-bg-primary niuu-p-2 niuu-font-mono niuu-text-xs niuu-text-text-secondary"
          data-testid="exec-output"
        >
          {entry.output}
        </pre>
      )}
    </div>
  );
}

interface ExecTabProps {
  exec: UseExecResult;
}

/** Exec tab — run-and-wait command input with ordered history. */
export function ExecTab({ exec }: ExecTabProps) {
  const [cmd, setCmd] = useState('');
  const inputRef = useRef<HTMLInputElement>(null);

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const command = cmd.trim();
    if (!command || exec.isRunning) return;
    setCmd('');
    void exec.run(command);
  }

  return (
    <div className="niuu-flex niuu-h-full niuu-flex-col niuu-gap-3 niuu-p-4" data-testid="exec-tab">
      {/* Command input */}
      <form onSubmit={handleSubmit} className="niuu-flex niuu-items-center niuu-gap-2">
        <span className="niuu-font-mono niuu-text-sm niuu-text-text-muted">$</span>
        <input
          ref={inputRef}
          type="text"
          value={cmd}
          onChange={(e) => setCmd(e.target.value)}
          placeholder="run a command…"
          disabled={exec.isRunning}
          className="niuu-flex-1 niuu-rounded-md niuu-border niuu-border-border niuu-bg-bg-secondary niuu-px-3 niuu-py-1.5 niuu-font-mono niuu-text-sm niuu-text-text-primary niuu-outline-none focus:niuu-border-brand disabled:niuu-opacity-50"
          data-testid="exec-input"
          aria-label="Command to run"
        />
        <button
          type="submit"
          disabled={!cmd.trim() || exec.isRunning}
          className="niuu-py-1 niuu-px-3 niuu-bg-brand niuu-text-bg-primary niuu-border niuu-border-brand niuu-rounded-sm niuu-cursor-pointer niuu-font-mono niuu-text-xs disabled:niuu-cursor-not-allowed disabled:niuu-opacity-50"
          data-testid="exec-run-btn"
        >
          {exec.isRunning ? 'running…' : 'Run'}
        </button>
      </form>

      {/* History */}
      <div className="niuu-flex niuu-min-h-0 niuu-flex-1 niuu-flex-col niuu-gap-2 niuu-overflow-auto">
        {exec.history.length === 0 ? (
          <p
            className="niuu-py-8 niuu-text-center niuu-text-sm niuu-text-text-muted"
            data-testid="exec-empty"
          >
            No commands run yet.
          </p>
        ) : (
          [...exec.history].reverse().map((entry) => <ExecEntryRow key={entry.id} entry={entry} />)
        )}
      </div>
    </div>
  );
}
