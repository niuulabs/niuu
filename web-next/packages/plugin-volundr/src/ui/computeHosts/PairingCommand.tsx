import { useEffect, useState } from 'react';
import {
  countdownLabel,
  secondsUntil,
  truncateCode,
  type NewPairingCode,
} from '../../domain/computeHosts';
import { CopyButton } from './CopyButton';
import { Timeline } from './Timeline';

const CLOCK_TICK_MS = 1_000;
/** How much of the long code the command shows; Copy still copies all of it. */
const VISIBLE_CODE_CHARS = 20;

/** Wall-clock time, advanced every second while mounted. */
function useSecondClock(): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), CLOCK_TICK_MS);
    return () => clearInterval(timer);
  }, []);
  return now;
}

const GHOST =
  'niuu:rounded-md niuu:px-2.5 niuu:py-1.5 niuu:text-sm niuu:text-text-secondary niuu:hover:bg-bg-tertiary';

export interface PairingCommandProps {
  code: NewPairingCode;
  revoking: boolean;
  error: string | null;
  onRevoke: () => void;
  /** After the code expired: back to the form for a new one. */
  onRestart: () => void;
}

/**
 * The one-line command that joins a host, with a countdown to its expiry and Revoke. The
 * code is single use and short lived; the page waits for the host to run it.
 */
export function PairingCommand({
  code,
  revoking,
  error,
  onRevoke,
  onRestart,
}: PairingCommandProps) {
  const now = useSecondClock();
  const [useWget, setUseWget] = useState(false);
  const remaining = secondsUntil(code.expiresAt, now);
  const expired = remaining === 0;
  const command = useWget && code.commandWget !== null ? code.commandWget : code.command;

  return (
    <div className="niuu:space-y-3">
      <div>
        <p className="niuu:text-base niuu:font-semibold niuu:text-text-primary">
          Run this on the host
        </p>
        <p className="niuu:text-sm niuu:text-text-secondary">
          Any Linux with systemd. Nothing to install first; it needs only{' '}
          <code className="niuu:font-mono">curl</code> and OpenSSH, which a stock server has.
        </p>
      </div>
      <pre
        aria-label="Join command"
        className="niuu:m-0 niuu:overflow-x-auto niuu:whitespace-nowrap niuu:rounded-lg niuu:border niuu:border-brand/35 niuu:bg-bg-primary niuu:p-3 niuu:font-mono niuu:text-xs niuu:text-text-primary"
      >
        {truncateCode(command, code.code, VISIBLE_CODE_CHARS)}
      </pre>
      {code.pin !== null ? (
        <p className="niuu:text-xs niuu:text-text-muted">
          The join is pinned to this Forge&apos;s certificate at{' '}
          <code className="niuu:font-mono">{code.joinUrl}</code> (
          <code className="niuu:break-all niuu:font-mono">{code.pin}</code>), so it needs curl: wget
          cannot check a pin.
        </p>
      ) : null}
      <div className="niuu:flex niuu:flex-wrap niuu:items-center niuu:justify-between niuu:gap-2">
        {expired ? (
          <span role="status" className="niuu:text-xs niuu:text-critical-fg">
            Expired
          </span>
        ) : (
          <span role="timer" className="niuu:text-xs niuu:text-text-muted">
            One host · expires in {countdownLabel(remaining)}
          </span>
        )}
        <span className="niuu:flex niuu:items-center niuu:gap-1">
          {code.commandWget !== null ? (
            <button type="button" className={GHOST} onClick={() => setUseWget((value) => !value)}>
              {useWget ? 'Use curl' : 'Use wget'}
            </button>
          ) : null}
          <CopyButton text={command} primary />
        </span>
      </div>
      <p className="niuu:text-xs niuu:text-text-muted">
        The host shows up here as soon as it runs. Forge then prepares it with this Forge&apos;s
        recipe; you don&apos;t need to stay on the host.
      </p>
      {error ? (
        <p role="alert" className="niuu:text-sm niuu:text-critical-fg">
          {error}
        </p>
      ) : null}
      <div>
        <button
          type="button"
          disabled={revoking}
          className={`${GHOST} niuu:text-critical-fg niuu:disabled:opacity-50`}
          onClick={onRevoke}
        >
          Revoke
        </button>
      </div>
      {expired ? (
        <div className="niuu:space-y-2">
          <h3 className="niuu:text-sm niuu:font-semibold niuu:text-text-primary">
            This command expired
          </h3>
          <p className="niuu:text-sm niuu:text-text-secondary">
            No host ran it in time. A command is single use and short lived.
          </p>
          <button
            type="button"
            className="niuu:rounded-md niuu:border niuu:border-border niuu:bg-bg-tertiary niuu:px-3 niuu:py-1.5 niuu:text-sm niuu:text-text-primary"
            onClick={onRestart}
          >
            Create a new command
          </button>
        </div>
      ) : (
        <div>
          <h3 className="niuu:text-sm niuu:font-semibold niuu:text-text-primary">
            Waiting for a host…
          </h3>
          <Timeline
            steps={[
              {
                id: 'joined',
                title: 'Waiting for the command to run',
                detail: null,
                status: 'now',
              },
            ]}
          />
        </div>
      )}
    </div>
  );
}
