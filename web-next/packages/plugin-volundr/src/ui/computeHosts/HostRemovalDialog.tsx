import { useState } from 'react';
import { Dialog, DialogContent } from '@niuulabs/ui';
import {
  HOST_LEAVE_COMMAND,
  type ComputeHost,
  type RemoveHostResult,
} from '../../domain/computeHosts';
import { errorText } from '../errorText';
import { CopyButton } from './CopyButton';
import { RadioCard } from './RadioCard';

/** Which confirmation opens: take a host out (drain, optionally removing it) or remove an empty one. */
export type RemovalMode = 'drain' | 'drain_remove' | 'remove';

export interface HostRemovalDialogProps {
  host: ComputeHost;
  mode: RemovalMode;
  onDrain: (remove: boolean) => Promise<unknown>;
  onRemove: () => Promise<RemoveHostResult>;
  onClose: () => void;
}

const BUTTON =
  'niuu:rounded-md niuu:border niuu:border-border niuu:bg-bg-tertiary niuu:px-3 niuu:py-1.5 niuu:text-sm niuu:text-text-primary';
const PRIMARY =
  'niuu:rounded-md niuu:bg-brand niuu:px-3 niuu:py-1.5 niuu:text-sm niuu:font-semibold niuu:text-bg-primary niuu:disabled:opacity-50';

function RemovedResult({
  host,
  result,
  onClose,
}: {
  host: ComputeHost;
  result: RemoveHostResult;
  onClose: () => void;
}) {
  return (
    <div className="niuu:space-y-3" data-testid="remove-result">
      <p className="niuu:text-sm niuu:text-text-primary">{host.name} was removed.</p>
      {result.hostCleanupCommand ? (
        <>
          <p className="niuu:text-sm niuu:text-text-secondary">
            Forge could not clean the host itself. Its access is cut off here already; run this on
            the host to finish:
          </p>
          <code className="niuu:block niuu:overflow-x-auto niuu:rounded-lg niuu:border niuu:border-border niuu:bg-bg-primary niuu:px-3 niuu:py-2 niuu:font-mono niuu:text-xs niuu:text-text-primary">
            {result.hostCleanupCommand}
          </code>
          <CopyButton text={result.hostCleanupCommand} />
        </>
      ) : (
        <p className="niuu:text-sm niuu:text-text-secondary">
          Forge removed its key from the host. To add it back, run the join command again.
        </p>
      )}
      <div className="niuu:flex niuu:justify-end">
        <button type="button" className={BUTTON} onClick={onClose}>
          Done
        </button>
      </div>
    </div>
  );
}

/**
 * The confirmation before a host leaves: drain, drain and remove, or remove an empty host.
 * Running sessions are never stopped. A removal shows the host's cleanup command when Forge
 * could not run it itself.
 */
export function HostRemovalDialog({
  host,
  mode,
  onDrain,
  onRemove,
  onClose,
}: HostRemovalDialogProps) {
  const [choice, setChoice] = useState<'drain' | 'drain_remove'>(
    mode === 'drain_remove' ? 'drain_remove' : 'drain',
  );
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [removed, setRemoved] = useState<RemoveHostResult | null>(null);
  const sessions = host.sessions.length;

  const confirmDrain = async () => {
    setPending(true);
    setError(null);
    try {
      await onDrain(choice === 'drain_remove');
      onClose();
    } catch (failure) {
      setError(errorText(failure));
      setPending(false);
    }
  };

  const confirmRemove = async () => {
    setPending(true);
    setError(null);
    try {
      setRemoved(await onRemove());
    } catch (failure) {
      setError(errorText(failure));
    }
    setPending(false);
  };

  const title = mode === 'remove' ? `Remove ${host.name}?` : `Take ${host.name} out?`;

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent title={title}>
        {removed ? (
          <RemovedResult host={host} result={removed} onClose={onClose} />
        ) : (
          <div className="niuu:space-y-3">
            {mode === 'remove' ? (
              <p className="niuu:text-sm niuu:text-text-secondary">
                Nothing runs on it. Forge removes its key from the host and forgets it. To add it
                back, run the join command again.
              </p>
            ) : (
              <>
                <p className="niuu:text-xs niuu:text-text-muted">
                  {sessions === 0
                    ? 'No sessions are running on it.'
                    : `${sessions} session${sessions === 1 ? ' is' : 's are'} running on it. They are never stopped.`}
                </p>
                <div className="niuu:grid niuu:gap-1.5" role="radiogroup" aria-label="Take out how">
                  <RadioCard
                    name="take-out"
                    value="drain"
                    title="Drain"
                    checked={choice === 'drain'}
                    onChange={() => setChoice('drain')}
                  >
                    No new sessions. When the last one ends, the host is checked and kept out of
                    service until you resume it.
                  </RadioCard>
                  <RadioCard
                    name="take-out"
                    value="drain_remove"
                    title="Drain and remove"
                    checked={choice === 'drain_remove'}
                    onChange={() => setChoice('drain_remove')}
                  >
                    The same, then Forge removes its key from the host and forgets it. To add it
                    back, run the join command again.
                  </RadioCard>
                </div>
              </>
            )}
            {error ? (
              <p role="alert" className="niuu:text-sm niuu:text-critical-fg">
                {error}
              </p>
            ) : null}
            <div className="niuu:flex niuu:justify-end niuu:gap-2">
              <button type="button" className={BUTTON} onClick={onClose}>
                Cancel
              </button>
              {mode === 'remove' ? (
                <button
                  type="button"
                  className={PRIMARY}
                  disabled={pending}
                  onClick={() => void confirmRemove()}
                >
                  Remove
                </button>
              ) : (
                <button
                  type="button"
                  className={PRIMARY}
                  disabled={pending}
                  onClick={() => void confirmDrain()}
                >
                  {choice === 'drain_remove' ? 'Drain and remove' : 'Drain'}
                </button>
              )}
            </div>
            <p className="niuu:text-xs niuu:text-text-muted">
              If Forge can no longer reach a host, removing it still cuts it off at once. To clean
              up the host itself, run{' '}
              <code className="niuu:font-mono niuu:text-text-secondary">{HOST_LEAVE_COMMAND}</code>{' '}
              there.
            </p>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
