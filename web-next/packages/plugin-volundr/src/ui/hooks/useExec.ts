import { useState, useCallback } from 'react';
import { appendExecEntry, updateExecEntry } from '../../domain/exec';
import type { ExecEntry } from '../../domain/exec';
import type { IPtyStream } from '../../ports/IPtyStream';

const EXEC_TIMEOUT_MS = 30_000;

/** Return value of useExec. */
export interface UseExecResult {
  history: ExecEntry[];
  isRunning: boolean;
  run: (command: string) => Promise<void>;
}

/**
 * Manages exec history for a single session.
 *
 * Sends the command over the PTY stream and collects output until the
 * shell prompt reappears (heuristic: line ending with `$ ` or `# `).
 * Falls back to a timeout to avoid hanging indefinitely.
 */
export function useExec(sessionId: string, stream: IPtyStream): UseExecResult {
  const [history, setHistory] = useState<ExecEntry[]>([]);
  const [isRunning, setIsRunning] = useState(false);

  const run = useCallback(
    async (command: string) => {
      if (isRunning) return;

      const id = `exec-${Date.now()}`;
      const startedAt = Date.now();

      setIsRunning(true);
      setHistory((prev) =>
        appendExecEntry(prev, { id, command, output: '', status: 'running', startedAt }),
      );

      let output = '';
      let unsubscribe: (() => void) | undefined;

      try {
        await new Promise<void>((resolve) => {
          const timer = setTimeout(resolve, EXEC_TIMEOUT_MS);

          unsubscribe = stream.subscribe(sessionId, (chunk) => {
            output += chunk;
            setHistory((prev) => updateExecEntry(prev, id, { output }));
            // Heuristic: stop collecting when we see a shell prompt.
            if (output.includes('\r\n$ ') || output.includes('\r\n# ')) {
              clearTimeout(timer);
              resolve();
            }
          });

          // Send the command followed by a newline (carriage return).
          stream.send(sessionId, `${command}\r`);
        });

        setHistory((prev) =>
          updateExecEntry(prev, id, { status: 'success', finishedAt: Date.now(), output }),
        );
      } catch {
        setHistory((prev) =>
          updateExecEntry(prev, id, { status: 'error', finishedAt: Date.now(), output }),
        );
      } finally {
        unsubscribe?.();
        setIsRunning(false);
      }
    },
    [isRunning, sessionId, stream],
  );

  return { history, isRunning, run };
}
