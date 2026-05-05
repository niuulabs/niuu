import { useState, useEffect } from 'react';
import { Modal, cn } from '@niuulabs/ui';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface RulesFormState {
  threshold: number;
  maxConcurrentRaids: number;
  autoContinue: boolean;
  retryCount: number;
}

export interface EditRulesModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  rules: RulesFormState;
  onSave: (rules: RulesFormState) => void;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function EditRulesModal({ open, onOpenChange, rules, onSave }: EditRulesModalProps) {
  const [threshold, setThreshold] = useState(rules.threshold);
  const [maxConcurrentRaids, setMaxConcurrentRaids] = useState(rules.maxConcurrentRaids);
  const [autoContinue, setAutoContinue] = useState(rules.autoContinue);
  const [retryCount, setRetryCount] = useState(rules.retryCount);

  useEffect(() => {
    if (!open) return;
    setThreshold(rules.threshold);
    setMaxConcurrentRaids(rules.maxConcurrentRaids);
    setAutoContinue(rules.autoContinue);
    setRetryCount(rules.retryCount);
  }, [open, rules.threshold, rules.maxConcurrentRaids, rules.autoContinue, rules.retryCount]);

  function handleSave() {
    onSave({ threshold, maxConcurrentRaids, autoContinue, retryCount });
    onOpenChange(false);
  }

  const inputClass =
    'niuu-w-24 niuu-rounded-md niuu-border niuu-border-border niuu-bg-bg-tertiary niuu-px-2 niuu-py-1 niuu-text-right niuu-font-mono niuu-text-sm niuu-text-text-primary';

  return (
    <Modal
      open={open}
      onOpenChange={onOpenChange}
      title="Edit dispatch rules"
      actions={[
        { label: 'Cancel', variant: 'secondary' },
        { label: 'Save', variant: 'primary', onClick: handleSave, closes: false },
      ]}
    >
      <div className="niuu-mt-2 niuu-flex niuu-flex-col niuu-gap-3">
        {/* Confidence threshold */}
        <div className="niuu-flex niuu-items-center niuu-justify-between">
          <label className="niuu-text-sm niuu-text-text-secondary">Confidence threshold</label>
          <input
            type="number"
            min="0"
            max="100"
            value={threshold}
            onChange={(e) => setThreshold(parseFloat(e.target.value) || 0)}
            className={inputClass}
            aria-label="Confidence threshold"
          />
        </div>

        {/* Max concurrent raids */}
        <div className="niuu-flex niuu-items-center niuu-justify-between">
          <label className="niuu-text-sm niuu-text-text-secondary">Max concurrent raids</label>
          <input
            type="number"
            min="1"
            value={maxConcurrentRaids}
            onChange={(e) => setMaxConcurrentRaids(parseInt(e.target.value) || 1)}
            className={inputClass}
            aria-label="Max concurrent raids"
          />
        </div>

        {/* Auto-continue */}
        <div className="niuu-flex niuu-items-center niuu-justify-between">
          <label className="niuu-text-sm niuu-text-text-secondary">Auto-continue</label>
          <button
            type="button"
            onClick={() => setAutoContinue(!autoContinue)}
            aria-pressed={autoContinue}
            aria-label="Toggle auto-continue"
            className={cn(
              'niuu-rounded-md niuu-px-3 niuu-py-1 niuu-text-sm niuu-font-medium niuu-transition-colors',
              autoContinue
                ? 'niuu-bg-brand niuu-text-bg-primary'
                : 'niuu-border niuu-border-border niuu-bg-transparent niuu-text-text-muted',
            )}
          >
            {autoContinue ? 'on' : 'off'}
          </button>
        </div>

        {/* Retry count */}
        <div className="niuu-flex niuu-items-center niuu-justify-between">
          <label className="niuu-text-sm niuu-text-text-secondary">Retry count</label>
          <input
            type="number"
            min="0"
            value={retryCount}
            onChange={(e) => setRetryCount(parseInt(e.target.value) || 0)}
            className={inputClass}
            aria-label="Retry count"
          />
        </div>
      </div>
    </Modal>
  );
}
