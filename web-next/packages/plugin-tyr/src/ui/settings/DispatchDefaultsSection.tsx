import { useState, type FormEvent } from 'react';
import { Field, Input, ValidationSummary, StateDot, type ValidationError } from '@niuulabs/ui';
import type { DispatchDefaults } from '../../ports';
import { useDispatchDefaults, useUpdateDispatchDefaults } from './useSettings';

function validate(values: {
  confidenceThreshold: number;
  maxConcurrentRaids: number;
  batchSize: number;
  maxRetries: number;
  retryDelaySeconds: number;
}): ValidationError[] {
  const errors: ValidationError[] = [];

  if (values.confidenceThreshold < 0 || values.confidenceThreshold > 100) {
    errors.push({
      id: 'dispatch-threshold',
      label: 'Confidence threshold',
      message: 'Must be between 0 and 100',
    });
  }
  if (values.maxConcurrentRaids < 1) {
    errors.push({
      id: 'dispatch-concurrent',
      label: 'Max concurrent raids',
      message: 'Must be at least 1',
    });
  }
  if (values.batchSize < 1) {
    errors.push({
      id: 'dispatch-batch',
      label: 'Batch size',
      message: 'Must be at least 1',
    });
  }
  if (values.maxRetries < 0) {
    errors.push({
      id: 'dispatch-retries',
      label: 'Max retries',
      message: 'Cannot be negative',
    });
  }
  if (values.retryDelaySeconds < 0) {
    errors.push({
      id: 'dispatch-delay',
      label: 'Retry delay',
      message: 'Cannot be negative',
    });
  }

  return errors;
}

export function DispatchDefaultsSection() {
  const { data: defaults, isLoading, isError, error } = useDispatchDefaults();
  const { mutateAsync: update, isPending: isSaving } = useUpdateDispatchDefaults();
  const [errors, setErrors] = useState<ValidationError[]>([]);
  const [saved, setSaved] = useState(false);

  async function handleSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const form = e.currentTarget;
    const data = new FormData(form);

    const confidenceThreshold = Number(data.get('confidenceThreshold'));
    const maxConcurrentRaids = Number(data.get('maxConcurrentRaids'));
    const batchSize = Number(data.get('batchSize'));
    const maxRetries = Number(data.get('maxRetries'));
    const retryDelaySeconds = Number(data.get('retryDelaySeconds'));
    const autoContinue = data.get('autoContinue') === 'true';
    const escalateOnExhaustion = data.get('escalateOnExhaustion') === 'true';
    const quietHours = String(data.get('quietHours') ?? '');
    const escalateAfter = String(data.get('escalateAfter') ?? '');

    const validationErrors = validate({
      confidenceThreshold,
      maxConcurrentRaids,
      batchSize,
      maxRetries,
      retryDelaySeconds,
    });
    setErrors(validationErrors);
    if (validationErrors.length > 0) return;

    const patch: Partial<Omit<DispatchDefaults, 'updatedAt'>> = {
      confidenceThreshold,
      maxConcurrentRaids,
      batchSize,
      autoContinue,
      quietHours,
      escalateAfter,
      retryPolicy: {
        maxRetries,
        retryDelaySeconds,
        escalateOnExhaustion,
      },
    };

    await update(patch);
    setSaved(true);
    setTimeout(() => setSaved(false), 2000);
  }

  if (isLoading) {
    return (
      <div className="niuu-flex niuu-items-center niuu-gap-2" role="status">
        <StateDot state="processing" pulse />
        <span className="niuu-text-sm niuu-text-text-secondary">loading dispatch defaults…</span>
      </div>
    );
  }

  if (isError) {
    return (
      <div className="niuu-flex niuu-items-center niuu-gap-2" role="alert">
        <StateDot state="failed" />
        <span className="niuu-text-sm niuu-text-critical">
          {error instanceof Error ? error.message : 'failed to load'}
        </span>
      </div>
    );
  }

  return (
    <section aria-label="Dispatch rules">
      <h3 className="niuu-text-base niuu-font-semibold niuu-text-text-primary niuu-mb-1">
        Dispatch rules
      </h3>
      <p className="niuu-text-sm niuu-text-text-secondary niuu-mb-4">
        How the dispatcher promotes queued raids into running ones.
      </p>

      <form
        onSubmit={(e) => void handleSubmit(e)}
        noValidate
        className="niuu-flex niuu-flex-col niuu-gap-4 niuu-max-w-lg"
        aria-label="Dispatch rules form"
      >
        {errors.length > 0 && <ValidationSummary errors={errors} />}

        <Field
          id="dispatch-threshold"
          label="Confidence threshold (0–100)"
          hint="Raids below this confidence score will not be dispatched"
          required
        >
          <Input
            name="confidenceThreshold"
            type="number"
            defaultValue={String(defaults?.confidenceThreshold ?? 70)}
            min="0"
            max="100"
            data-testid="confidence-threshold"
          />
        </Field>

        <Field
          id="dispatch-concurrent"
          label="Max concurrent raids"
          hint="Maximum number of raids allowed to run simultaneously"
          required
        >
          <Input
            name="maxConcurrentRaids"
            type="number"
            defaultValue={String(defaults?.maxConcurrentRaids ?? 3)}
            min="1"
          />
        </Field>

        <Field
          id="dispatch-batch"
          label="Batch size"
          hint="Number of queued raids to evaluate per dispatch cycle"
          required
        >
          <Input
            name="batchSize"
            type="number"
            defaultValue={String(defaults?.batchSize ?? 10)}
            min="1"
          />
        </Field>

        <div className="niuu-flex niuu-items-center niuu-gap-2">
          <input
            type="checkbox"
            id="dispatch-auto-continue"
            name="autoContinue"
            value="true"
            defaultChecked={defaults?.autoContinue}
            className="niuu-accent-brand"
          />
          <label
            htmlFor="dispatch-auto-continue"
            className="niuu-text-sm niuu-text-text-primary niuu-select-none"
          >
            Auto-continue after each raid completes
          </label>
        </div>

        <Field
          id="dispatch-quiet-hours"
          label="Quiet hours"
          hint="Time window where the dispatcher will not start new raids (UTC)"
        >
          <Input
            name="quietHours"
            type="text"
            defaultValue={defaults?.quietHours ?? '22:00–07:00 UTC'}
            data-testid="quiet-hours"
          />
        </Field>

        <Field
          id="dispatch-escalate-after"
          label="Escalate after (review)"
          hint="Auto-escalate pending reviews after this duration"
        >
          <Input
            name="escalateAfter"
            type="text"
            defaultValue={defaults?.escalateAfter ?? '30m'}
            data-testid="escalate-after"
          />
        </Field>

        <div className="niuu-border-t niuu-border-border niuu-pt-4">
          <h4 className="niuu-text-sm niuu-font-semibold niuu-text-text-primary niuu-mb-3">
            Retry Policy
          </h4>

          <div className="niuu-flex niuu-flex-col niuu-gap-3">
            <Field id="dispatch-retries" label="Max retries per raid" required>
              <Input
                name="maxRetries"
                type="number"
                defaultValue={String(defaults?.retryPolicy.maxRetries ?? 2)}
                min="0"
              />
            </Field>

            <Field id="dispatch-delay" label="Retry delay (seconds)" required>
              <Input
                name="retryDelaySeconds"
                type="number"
                defaultValue={String(defaults?.retryPolicy.retryDelaySeconds ?? 30)}
                min="0"
              />
            </Field>

            <div className="niuu-flex niuu-items-center niuu-gap-2">
              <input
                type="checkbox"
                id="dispatch-escalate"
                name="escalateOnExhaustion"
                value="true"
                defaultChecked={defaults?.retryPolicy.escalateOnExhaustion}
                className="niuu-accent-brand"
              />
              <label
                htmlFor="dispatch-escalate"
                className="niuu-text-sm niuu-text-text-primary niuu-select-none"
              >
                Escalate to human review after retries exhausted
              </label>
            </div>
          </div>
        </div>

        <div className="niuu-flex niuu-items-center niuu-gap-3">
          <button
            type="submit"
            disabled={isSaving}
            className="niuu-px-4 niuu-py-2 niuu-bg-brand niuu-text-white niuu-rounded-md niuu-text-sm niuu-font-medium niuu-transition-opacity disabled:niuu-opacity-50"
          >
            {isSaving ? 'Saving…' : 'Save'}
          </button>
          {saved && (
            <span className="niuu-text-sm niuu-text-accent-emerald" aria-live="polite">
              Saved
            </span>
          )}
        </div>
      </form>
    </section>
  );
}
