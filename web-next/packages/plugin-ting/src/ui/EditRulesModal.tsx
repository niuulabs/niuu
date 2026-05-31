import { useState } from 'react';
import { Modal, cn } from '@niuulabs/ui';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface RulesFormState {
  threshold: number;
  maxConcurrentRuns: number;
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
  const rulesKey = `${rules.threshold}:${rules.maxConcurrentRuns}:${rules.autoContinue}:${rules.retryCount}`;
  const [draft, setDraft] = useState<{ key: string; value: RulesFormState } | null>(null);
  const current = draft?.key === rulesKey ? draft.value : rules;

  function updateDraft(patch: Partial<RulesFormState>) {
    setDraft((prev) => ({
      key: rulesKey,
      value: { ...(prev?.key === rulesKey ? prev.value : rules), ...patch },
    }));
  }

  function handleOpenChange(nextOpen: boolean) {
    if (!nextOpen) {
      setDraft(null);
    }
    onOpenChange(nextOpen);
  }

  function handleSave() {
    onSave(current);
    setDraft(null);
    onOpenChange(false);
  }

  const inputClass =
    'niuu-w-24 niuu-rounded-md niuu-border niuu-border-border niuu-bg-bg-tertiary niuu-px-2 niuu-py-1 niuu-text-right niuu-font-mono niuu-text-sm niuu-text-text-primary';

  return (
    <Modal
      open={open}
      onOpenChange={handleOpenChange}
      title="Edit dispatch rules"
      actions={[
        { label: 'Cancel', variant: 'secondary' },
        { label: 'Save', variant: 'primary', onClick: handleSave, closes: false },
      ]}
    >
      <div className="niuu-mt-2 niuu-flex niuu-flex-col niuu-gap-3">
        <div className="niuu-flex niuu-items-center niuu-justify-between">
          <label className="niuu-text-sm niuu-text-text-secondary">Confidence threshold</label>
          <input
            type="number"
            min="0"
            max="100"
            value={current.threshold}
            onChange={(e) => updateDraft({ threshold: parseFloat(e.target.value) || 0 })}
            className={inputClass}
            aria-label="Confidence threshold"
          />
        </div>

        <div className="niuu-flex niuu-items-center niuu-justify-between">
          <label className="niuu-text-sm niuu-text-text-secondary">Max concurrent runs</label>
          <input
            type="number"
            min="1"
            value={current.maxConcurrentRuns}
            onChange={(e) => updateDraft({ maxConcurrentRuns: parseInt(e.target.value) || 1 })}
            className={inputClass}
            aria-label="Max concurrent runs"
          />
        </div>

        <div className="niuu-flex niuu-items-center niuu-justify-between">
          <label className="niuu-text-sm niuu-text-text-secondary">Auto-continue</label>
          <button
            type="button"
            onClick={() => updateDraft({ autoContinue: !current.autoContinue })}
            aria-pressed={current.autoContinue}
            aria-label="Toggle auto-continue"
            className={cn(
              'niuu-rounded-md niuu-px-3 niuu-py-1 niuu-text-sm niuu-font-medium niuu-transition-colors',
              current.autoContinue
                ? 'niuu-bg-brand niuu-text-bg-primary'
                : 'niuu-border niuu-border-border niuu-bg-transparent niuu-text-text-muted',
            )}
          >
            {current.autoContinue ? 'on' : 'off'}
          </button>
        </div>

        <div className="niuu-flex niuu-items-center niuu-justify-between">
          <label className="niuu-text-sm niuu-text-text-secondary">Retry count</label>
          <input
            type="number"
            min="0"
            value={current.retryCount}
            onChange={(e) => updateDraft({ retryCount: parseInt(e.target.value) || 0 })}
            className={inputClass}
            aria-label="Retry count"
          />
        </div>
      </div>
    </Modal>
  );
}
