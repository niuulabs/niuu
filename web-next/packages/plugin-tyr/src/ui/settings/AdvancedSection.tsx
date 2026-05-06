/**
 * Advanced section — danger-zone actions for the dispatcher.
 * Matches web2's `tyr.advanced` section.
 */

import { useState } from 'react';
import { cn } from '@niuulabs/ui';

interface DangerAction {
  label: string;
  buttonText: string;
  danger: boolean;
  confirmMessage: string;
}

const ACTIONS: DangerAction[] = [
  {
    label: 'Flush queue',
    buttonText: 'Flush',
    danger: true,
    confirmMessage: 'Are you sure you want to flush the dispatch queue? This cannot be undone.',
  },
  {
    label: 'Reset dispatcher',
    buttonText: 'Reset',
    danger: true,
    confirmMessage:
      'Are you sure you want to reset the dispatcher? All running raids will be interrupted.',
  },
  {
    label: 'Rebuild confidence scores',
    buttonText: 'Rebuild',
    danger: false,
    confirmMessage: 'Rebuild all confidence scores from scratch? This may take a few minutes.',
  },
];

export interface AdvancedSectionProps {
  onAction?: (label: string) => void;
}

export function AdvancedSection({ onAction }: AdvancedSectionProps = {}) {
  const [confirming, setConfirming] = useState<string | null>(null);

  function handleClick(action: DangerAction) {
    if (confirming === action.label) {
      onAction?.(action.label);
      setConfirming(null);
      return;
    }
    setConfirming(action.label);
  }

  return (
    <section aria-label="Advanced settings">
      <h3 className="niuu-text-base niuu-font-semibold niuu-text-text-primary niuu-mb-1">
        Advanced
      </h3>
      <p className="niuu-text-sm niuu-text-text-secondary niuu-mb-4">
        Danger zone. These actions can disrupt running sagas and raids.
      </p>

      <div className="niuu-flex niuu-flex-col niuu-gap-3 niuu-max-w-lg">
        {ACTIONS.map((action) => (
          <div
            key={action.label}
            className="niuu-flex niuu-items-center niuu-justify-between niuu-py-2 niuu-border-b niuu-border-border-subtle"
          >
            <span className="niuu-text-sm niuu-text-text-primary">{action.label}</span>
            <div className="niuu-flex niuu-items-center niuu-gap-2">
              {confirming === action.label && (
                <span
                  className="niuu-text-xs niuu-text-text-muted niuu-max-w-[220px]"
                  aria-live="polite"
                  data-testid={`confirm-msg-${action.label.toLowerCase().replace(/\s+/g, '-')}`}
                >
                  {action.confirmMessage}
                </span>
              )}
              <button
                type="button"
                onClick={() => handleClick(action)}
                className={cn(
                  'niuu-px-3 niuu-py-1.5 niuu-rounded-md niuu-text-xs niuu-font-medium niuu-transition-colors',
                  action.danger
                    ? confirming === action.label
                      ? 'niuu-bg-critical niuu-text-white'
                      : 'niuu-border niuu-border-critical niuu-text-critical hover:niuu-bg-critical hover:niuu-text-white'
                    : 'niuu-border niuu-border-border niuu-text-text-secondary hover:niuu-bg-bg-secondary',
                )}
                data-testid={`action-${action.label.toLowerCase().replace(/\s+/g, '-')}`}
              >
                {action.buttonText}
              </button>
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}
