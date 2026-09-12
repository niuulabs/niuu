import { useState } from 'react';
import { Field, Input } from '@niuulabs/ui';
import { memoryPlan, type ModelOption, type StackChanges, type StackView } from '../domain/setup';
import { AlertIcon, CheckIcon } from './icons';

export interface ModelStepProps {
  stack: StackView | undefined;
  loading: boolean;
  /** Set when the install has no stack controller (not started with `niuu up`). */
  unavailable: Error | null;
  staging: boolean;
  stageError: Error | null;
  onStage: (changes: StackChanges) => void;
}

export type ModelChoice = { kind: 'curated'; id: string } | { kind: 'custom' } | { kind: 'skip' };

/** Which choice the staged (or current) settings correspond to. */
export function choiceFor(stack: StackView | undefined): ModelChoice {
  if (!stack) return { kind: 'skip' };
  const vllm = stack.effective.vllm;
  if (!vllm.enabled || !vllm.model) return { kind: 'skip' };
  const curated = stack.models.find((option) => option.model === vllm.model);
  return curated ? { kind: 'curated', id: curated.id } : { kind: 'custom' };
}

function fitChip(option: ModelOption) {
  if (option.fits === null) {
    return <span className="setup-chip">Needs ~{option.memoryNeededGib} GB</span>;
  }
  if (option.fits) {
    return (
      <span className="setup-chip setup-chip--ok">
        <CheckIcon size={12} /> Fits · ~{option.weightGib} GB
      </span>
    );
  }
  return (
    <span className="setup-chip setup-chip--warn">
      <AlertIcon size={12} /> Does not fit · needs ~{option.memoryNeededGib} GB
    </span>
  );
}

/**
 * Local model: pick a curated model (with a fit verdict for this host), a
 * custom Hugging Face id, or cloud-only. The choice is staged; the finish
 * step applies it together with everything else.
 */
export function ModelStep({
  stack,
  loading,
  unavailable,
  staging,
  stageError,
  onStage,
}: ModelStepProps) {
  const initial = choiceFor(stack);
  const [custom, setCustom] = useState(
    initial.kind === 'custom' ? (stack?.effective.vllm.model ?? '') : '',
  );
  const [customTouched, setCustomTouched] = useState(false);
  const choice = initial;
  const totalGib = stack?.acceleratorMemoryGib ?? 0;
  const selectedWeight =
    choice.kind === 'curated'
      ? (stack?.models.find((option) => option.id === choice.id)?.weightGib ?? 0)
      : 0;
  const plan = memoryPlan(totalGib, selectedWeight);

  if (unavailable) {
    return (
      <div className="setup-col" data-testid="setup-model-unavailable">
        <div className="setup-note setup-note--warn">
          <AlertIcon size={13} /> {unavailable.message}
        </div>
      </div>
    );
  }

  const pick = (option: ModelOption) => onStage({ vllm_enabled: true, vllm_model: option.model });
  const pickSkip = () => onStage({ vllm_enabled: false });
  const submitCustom = () => {
    setCustomTouched(true);
    if (!custom.trim()) return;
    onStage({ vllm_enabled: true, vllm_model: custom.trim() });
  };

  return (
    <div className="setup-two" data-testid="setup-model">
      <div className="setup-card">
        <div className="setup-card__head">
          <div>
            <h3 className="setup-card__title">
              {totalGib > 0
                ? `Recommended for ${totalGib} GB accelerator memory`
                : 'No accelerator memory reported'}
            </h3>
            <p className="setup-card__desc">
              {totalGib > 0
                ? 'Sizes include the KV cache at 64k context. You can add more models later.'
                : 'Without a GPU the platform cannot judge what fits; cloud models still work.'}
            </p>
          </div>
        </div>
        {loading ? <div className="setup-note">Reading the host…</div> : null}
        {stack?.models.map((option) => {
          const selected = choice.kind === 'curated' && choice.id === option.id;
          return (
            <button
              key={option.id}
              type="button"
              className={`setup-option ${selected ? 'setup-option--selected' : ''}`}
              onClick={() => pick(option)}
              disabled={staging || option.fits === false}
              aria-pressed={selected}
              data-testid={`setup-model-${option.id}`}
            >
              <span className="setup-option__radio" />
              <span className="setup-option__body">
                <span className="setup-option__title">{option.name}</span>
                <span className="setup-option__desc">{option.description}</span>
              </span>
              <span className="setup-option__aside setup-chips">
                {option.recommended ? (
                  <span className="setup-chip setup-chip--brand">Recommended</span>
                ) : null}
                {fitChip(option)}
              </span>
            </button>
          );
        })}
        <div
          className={`setup-option ${choice.kind === 'custom' ? 'setup-option--selected' : ''}`}
          data-testid="setup-model-custom"
        >
          <span className="setup-option__radio" />
          <span className="setup-option__body">
            <span className="setup-option__title">Custom Hugging Face model</span>
            <span className="setup-option__desc">
              Any vLLM-compatible repo. The download starts when you finish setup; sizes are not
              checked for custom ids.
            </span>
            <Field
              label="Model id"
              error={customTouched && !custom.trim() ? 'Enter a Hugging Face model id' : undefined}
            >
              <Input
                value={custom}
                placeholder="org/model-name"
                onChange={(event) => setCustom(event.target.value)}
                data-testid="setup-model-custom-input"
              />
            </Field>
            <div className="setup-form__actions">
              <button
                type="button"
                className="setup-btn"
                onClick={submitCustom}
                disabled={staging}
                data-testid="setup-model-custom-use"
              >
                Use this model
              </button>
            </div>
          </span>
        </div>
        <button
          type="button"
          className={`setup-option ${choice.kind === 'skip' ? 'setup-option--selected' : ''}`}
          onClick={pickSkip}
          disabled={staging}
          aria-pressed={choice.kind === 'skip'}
          data-testid="setup-model-skip"
        >
          <span className="setup-option__radio" />
          <span className="setup-option__body">
            <span className="setup-option__title">Skip — cloud models only</span>
            <span className="setup-option__desc">
              You can add a local model later from Settings → Models.
            </span>
          </span>
        </button>
        {stageError ? (
          <div className="setup-error" role="alert">
            {stageError.message}
          </div>
        ) : null}
      </div>

      <div className="setup-col">
        <div className="setup-card" data-testid="setup-model-memory">
          <div className="setup-card__head">
            <div>
              <h3 className="setup-card__title">Memory after this choice</h3>
            </div>
          </div>
          <div className="setup-meter" aria-hidden="true">
            <span className="setup-meter__model" style={{ width: `${plan.modelPct}%` }} />
            <span className="setup-meter__sandbox" style={{ width: `${plan.sandboxPct}%` }} />
          </div>
          <div className="setup-legend">
            <div className="setup-legend__row">
              <span>Local model</span>
              <span className="setup-legend__value">{plan.modelGib} GB</span>
            </div>
            <div className="setup-legend__row">
              <span>Session sandboxes</span>
              <span className="setup-legend__value">~{plan.sandboxGib} GB</span>
            </div>
            <div className="setup-legend__row">
              <span>Free</span>
              <span className="setup-legend__value">{plan.freeGib} GB</span>
            </div>
          </div>
        </div>
        {stack?.current.vllm.enabled ? (
          <div className="setup-note" data-testid="setup-model-current">
            <CheckIcon size={13} /> Currently serving {stack.current.vllm.model}
          </div>
        ) : null}
        <div className="setup-note">
          vLLM serves the model on this host and Bifrost routes to it. Sessions, workflows and
          residents can then work without code leaving the box.
        </div>
      </div>
    </div>
  );
}
