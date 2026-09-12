import {
  WIZARD_STEPS,
  isStepDone,
  stepIndex,
  type SetupState,
  type WizardStepId,
} from '../domain/setup';
import { CheckIcon, NiuuMark } from './icons';

export interface SetupRailProps {
  current: WizardStepId;
  state: SetupState | undefined;
  hostname: string | null;
  onSelect: (step: WizardStepId) => void;
}

/** Vertical progress rail. Done steps are clickable; future steps are not. */
export function SetupRail({ current, state, hostname, onSelect }: SetupRailProps) {
  const currentIndex = stepIndex(current);
  return (
    <aside className="setup-rail" data-testid="setup-rail">
      <div className="setup-rail__brand">
        <NiuuMark size={28} />
        <span>Niuu</span>
      </div>
      <div className="setup-rail__kicker">first launch</div>
      <ol className="setup-rail__steps">
        {WIZARD_STEPS.map((step, index) => {
          const done = isStepDone(state, step.id) || index < currentIndex;
          const isCurrent = step.id === current;
          const className = [
            'setup-rail__step',
            done ? 'setup-rail__step--done' : '',
            isCurrent ? 'setup-rail__step--current' : '',
          ]
            .filter(Boolean)
            .join(' ');
          return (
            <li key={step.id}>
              <button
                type="button"
                className={className}
                disabled={!done && !isCurrent}
                aria-current={isCurrent ? 'step' : undefined}
                onClick={() => onSelect(step.id)}
                data-testid={`setup-rail-${step.id}`}
              >
                <span className="setup-rail__index">
                  {done && !isCurrent ? <CheckIcon size={13} /> : index + 1}
                </span>
                <span>{step.label}</span>
              </button>
              {index < WIZARD_STEPS.length - 1 ? (
                <div
                  className={`setup-rail__line ${done && !isCurrent ? 'setup-rail__line--done' : ''}`}
                />
              ) : null}
            </li>
          );
        })}
      </ol>
      <div className="setup-rail__foot">
        <span>{hostname ?? 'niuu'}</span>
        <span>{state?.mode ? `${state.mode} mode` : ''}</span>
      </div>
    </aside>
  );
}
