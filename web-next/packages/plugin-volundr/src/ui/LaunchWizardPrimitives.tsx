import type { ReactNode } from 'react';
import type { WizardStep } from './launchWizardModel';

export const STEPS: WizardStep[] = ['source', 'runtime', 'confirm'];
const STEP_LABELS: Record<string, string> = {
  source: 'Source',
  runtime: 'Runtime',
  confirm: 'Confirm',
};
export const BOOT_STEPS = [
  { id: 'schedule', label: 'schedule pod' },
  { id: 'pull', label: 'pull image' },
  { id: 'creds', label: 'check credentials' },
  { id: 'clone', label: 'clone workspace' },
  { id: 'mount', label: 'attach PVCs' },
  { id: 'mcp', label: 'bring MCP servers up' },
  { id: 'cli', label: 'boot CLI tool' },
  { id: 'ready', label: 'ready' },
];
export const NEW_WORKSPACE_VALUE = '__new__';
export const NO_PRESET_VALUE = '__custom__';
export const SECONDARY_BUTTON_CLASS =
  'niuu:rounded-md niuu:border niuu:border-border-subtle niuu:bg-bg-primary niuu:px-3 niuu:py-2 niuu:text-xs niuu:text-text-primary niuu:hover:border-brand niuu:hover:bg-bg-tertiary';
export const MUTED_BUTTON_CLASS =
  'niuu:rounded-md niuu:border niuu:border-border-subtle niuu:bg-bg-secondary niuu:px-3 niuu:py-2 niuu:text-xs niuu:text-text-primary niuu:hover:border-brand niuu:hover:bg-bg-tertiary';

export function WizardSelect({
  options,
  value,
  onChange,
  placeholder,
  testId,
}: {
  options: Array<{ value: string; label: string }>;
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  testId?: string;
}) {
  return (
    <select
      value={value}
      onChange={(event) => onChange(event.target.value)}
      data-testid={testId}
      aria-label={placeholder}
      className="niuu-form-control niuu:w-full niuu:rounded-md niuu:border niuu:border-border-subtle niuu:bg-bg-primary niuu:px-3 niuu:py-2 niuu:text-sm niuu:text-text-primary niuu:outline-none niuu:focus:border-brand"
    >
      {options.map((option) => (
        <option key={option.value} value={option.value}>
          {option.label}
        </option>
      ))}
    </select>
  );
}

export interface LaunchWizardProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  initialLaunchSpecRef?: string;
}

export function StepIndicator({ current, steps }: { current: WizardStep; steps: WizardStep[] }) {
  const idx = steps.indexOf(current);
  return (
    <div className="niuu:flex niuu:items-center niuu:gap-2 niuu:py-4" data-testid="step-indicator">
      {steps.map((step, i) => (
        <div key={step} className="niuu:flex niuu:items-center niuu:gap-2">
          <div
            className={`niuu:flex niuu:h-6 niuu:w-6 niuu:items-center niuu:justify-center niuu:rounded-full niuu:font-mono niuu:text-xs ${
              i < idx
                ? 'niuu:bg-brand niuu:text-bg-primary'
                : i === idx
                  ? 'niuu:border-2 niuu:border-brand niuu:text-brand'
                  : 'niuu:border niuu:border-border-subtle niuu:text-text-faint'
            }`}
            data-testid={`step-${step}`}
          >
            {i < idx ? '\u2713' : i + 1}
          </div>
          <span
            className={`niuu:text-xs ${
              i === idx ? 'niuu:text-text-primary' : 'niuu:text-text-faint'
            }`}
          >
            {STEP_LABELS[step]}
          </span>
          {i < steps.length - 1 && (
            <div
              className={`niuu:h-px niuu:w-8 ${
                i < idx ? 'niuu:bg-brand' : 'niuu:bg-border-subtle'
              }`}
            />
          )}
        </div>
      ))}
    </div>
  );
}

export function SectionCard({
  title,
  description,
  children,
}: {
  title: string;
  description?: string;
  children: ReactNode;
}) {
  return (
    <section className="niuu:rounded-xl niuu:border niuu:border-border-subtle niuu:bg-bg-secondary niuu:p-4">
      <div className="niuu:mb-4 niuu:border-b niuu:border-border-subtle niuu:pb-3">
        <h3 className="niuu:text-sm niuu:font-medium niuu:text-text-primary">{title}</h3>
        {description ? (
          <p className="niuu:mt-1 niuu:text-xs niuu:text-text-faint">{description}</p>
        ) : null}
      </div>
      <div className="niuu:flex niuu:flex-col niuu:gap-4">{children}</div>
    </section>
  );
}

export function RuntimePanel({
  title,
  description,
  children,
}: {
  title: string;
  description?: string;
  children: ReactNode;
}) {
  return (
    <div className="niuu:rounded-lg niuu:border niuu:border-border-subtle niuu:bg-bg-primary niuu:p-4">
      <div className="niuu:mb-3">
        <div className="niuu:text-sm niuu:font-medium niuu:text-text-primary">{title}</div>
        {description ? (
          <div className="niuu:mt-1 niuu:text-xs niuu:text-text-faint">{description}</div>
        ) : null}
      </div>
      <div className="niuu:flex niuu:flex-col niuu:gap-4">{children}</div>
    </div>
  );
}
