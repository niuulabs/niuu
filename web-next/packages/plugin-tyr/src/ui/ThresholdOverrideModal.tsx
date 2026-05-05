import { useState, useEffect } from 'react';
import { Modal } from '@niuulabs/ui';

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const THRESHOLD_MIN = 0;
const THRESHOLD_MAX = 1;
const THRESHOLD_STEP = 0.05;

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface ThresholdOverrideModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  currentThreshold: number;
  onApply: (threshold: number) => void;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function ThresholdOverrideModal({
  open,
  onOpenChange,
  currentThreshold,
  onApply,
}: ThresholdOverrideModalProps) {
  const [value, setValue] = useState(currentThreshold);

  useEffect(() => {
    if (open) setValue(currentThreshold);
  }, [open, currentThreshold]);

  function handleApply() {
    onApply(value);
    onOpenChange(false);
  }

  return (
    <Modal
      open={open}
      onOpenChange={onOpenChange}
      title="Override dispatch threshold"
      description="Raids with confidence below this threshold stay queued. Lower it to dispatch more aggressively; raise it to be conservative."
      actions={[
        { label: 'Cancel', variant: 'secondary' },
        { label: 'Apply', variant: 'primary', onClick: handleApply, closes: false },
      ]}
    >
      <div className="niuu-mt-4 niuu-flex niuu-items-center niuu-gap-4">
        <input
          type="range"
          min={THRESHOLD_MIN}
          max={THRESHOLD_MAX}
          step={THRESHOLD_STEP}
          value={value}
          onChange={(e) => setValue(parseFloat(e.target.value))}
          className="niuu-flex-1"
          aria-label="Threshold value"
        />
        <span className="niuu-min-w-[3.5rem] niuu-text-right niuu-font-mono niuu-text-xl niuu-text-brand">
          {value.toFixed(2)}
        </span>
      </div>
    </Modal>
  );
}
