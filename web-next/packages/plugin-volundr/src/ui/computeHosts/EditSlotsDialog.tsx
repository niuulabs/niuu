import { useState } from 'react';
import { Dialog, DialogContent } from '@niuulabs/ui';
import type { ComputeHost } from '../../domain/computeHosts';
import { errorText } from '../errorText';

export interface EditSlotsDialogProps {
  host: ComputeHost;
  /** PATCH the slot count; rejects with Forge's reason (the arithmetic) when it refuses. */
  onSave: (slots: number) => Promise<unknown>;
  onClose: () => void;
}

const BUTTON =
  'niuu:rounded-md niuu:border niuu:border-border niuu:bg-bg-tertiary niuu:px-3 niuu:py-1.5 niuu:text-sm niuu:text-text-primary';
const PRIMARY =
  'niuu:rounded-md niuu:bg-brand niuu:px-3 niuu:py-1.5 niuu:text-sm niuu:font-semibold niuu:text-bg-primary niuu:disabled:opacity-50';

function parseSlots(value: string): number | null {
  if (!/^\d+$/.test(value.trim())) return null;
  const slots = Number(value);
  return slots >= 1 ? slots : null;
}

/**
 * Change how many sessions a host runs at once. Forge accepts it only while no allocation
 * holds the host, and refuses a number the host cannot hold with the arithmetic; either
 * answer is shown as given.
 */
export function EditSlotsDialog({ host, onSave, onClose }: EditSlotsDialogProps) {
  const [value, setValue] = useState(host.slots === null ? '' : String(host.slots));
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const slots = parseSlots(value);
  const suggested = host.suggestedSlots;

  const save = async () => {
    if (slots === null) return;
    setPending(true);
    setError(null);
    try {
      await onSave(slots);
      onClose();
    } catch (failure) {
      setError(errorText(failure));
      setPending(false);
    }
  };

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent title={`Slots of ${host.name}`}>
        <div className="niuu:space-y-3">
          <p className="niuu:text-sm niuu:text-text-secondary">
            How many sessions the host runs at once. 1 is an exclusive host; a Mac runs one unless
            this Forge shares Mac slots.
          </p>
          {suggested ? (
            <div
              data-testid="slot-arithmetic"
              className="niuu:rounded-md niuu:border niuu:border-dashed niuu:border-border niuu:bg-bg-primary niuu:px-3 niuu:py-2 niuu:font-mono niuu:text-xs niuu:leading-6 niuu:text-text-secondary"
            >
              {suggested.lines.map((line) => (
                <div key={line}>{line}</div>
              ))}
              <div>suggested: {suggested.slots}</div>
            </div>
          ) : null}
          <input
            inputMode="numeric"
            aria-label="Sessions at once"
            value={value}
            onChange={(event) => setValue(event.target.value)}
            className="niuu:w-24 niuu:rounded-md niuu:border niuu:border-border niuu:bg-bg-primary niuu:px-2.5 niuu:py-1.5 niuu:text-sm niuu:text-text-primary"
          />
          <p className="niuu:text-xs niuu:text-text-muted">
            Only a host that is out of service (drained and empty) or not yet claimed can change its
            slots. Drain an in-service host first, then resume it afterwards.
          </p>
          {error ? (
            <p role="alert" className="niuu:text-sm niuu:text-critical-fg">
              {error}
            </p>
          ) : null}
          <div className="niuu:flex niuu:justify-end niuu:gap-2">
            <button type="button" className={BUTTON} onClick={onClose}>
              Cancel
            </button>
            <button
              type="button"
              className={PRIMARY}
              disabled={slots === null || slots === host.slots || pending}
              onClick={() => void save()}
            >
              Save
            </button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
