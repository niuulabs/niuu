import { useState, type FormEvent } from 'react';
import { Field, Input, ValidationSummary, StateDot, type ValidationError } from '@niuulabs/ui';
import type { FlockConfig } from '../../ports';
import { useFlockConfig, useUpdateFlockConfig } from './useSettings';

function validate(values: Partial<FlockConfig>): ValidationError[] {
  const errors: ValidationError[] = [];

  if (!values.flockName?.trim()) {
    errors.push({ id: 'flock-name', label: 'Flock name', message: 'Required' });
  }
  if (!values.defaultBaseBranch?.trim()) {
    errors.push({ id: 'flock-branch', label: 'Default base branch', message: 'Required' });
  }
  if (!values.defaultTrackerType?.trim()) {
    errors.push({ id: 'flock-tracker', label: 'Default tracker type', message: 'Required' });
  }
  if (values.maxActiveSagas !== undefined && values.maxActiveSagas < 1) {
    errors.push({
      id: 'flock-max-sagas',
      label: 'Max active sagas',
      message: 'Must be at least 1',
    });
  }

  return errors;
}

export function FlockConfigSection() {
  const { data: config, isLoading, isError, error } = useFlockConfig();
  const { mutateAsync: update, isPending: isSaving } = useUpdateFlockConfig();
  const [errors, setErrors] = useState<ValidationError[]>([]);
  const [saved, setSaved] = useState(false);

  async function handleSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const form = e.currentTarget;
    const data = new FormData(form);

    const patch: Partial<Omit<FlockConfig, 'updatedAt'>> = {
      flockName: String(data.get('flockName') ?? ''),
      defaultBaseBranch: String(data.get('defaultBaseBranch') ?? ''),
      defaultTrackerType: String(data.get('defaultTrackerType') ?? ''),
      defaultRepos: String(data.get('defaultRepos') ?? '')
        .split(',')
        .map((r) => r.trim())
        .filter(Boolean),
      maxActiveSagas: Number(data.get('maxActiveSagas')),
      autoCreateMilestones: data.get('autoCreateMilestones') === 'true',
    };

    const validationErrors = validate(patch);
    setErrors(validationErrors);
    if (validationErrors.length > 0) return;

    await update(patch);
    setSaved(true);
    setTimeout(() => setSaved(false), 2000);
  }

  if (isLoading) {
    return (
      <div className="niuu-flex niuu-items-center niuu-gap-2" role="status">
        <StateDot state="processing" pulse />
        <span className="niuu-text-sm niuu-text-text-secondary">loading flock config…</span>
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
    <section aria-label="Flock configuration">
      <h3 className="niuu-text-base niuu-font-semibold niuu-text-text-primary niuu-mb-1">
        Flock Config
      </h3>
      <p className="niuu-text-sm niuu-text-text-secondary niuu-mb-4">
        Global defaults applied to new Sagas and Raids in this Tyr deployment.
      </p>

      <form
        onSubmit={(e) => void handleSubmit(e)}
        noValidate
        className="niuu-flex niuu-flex-col niuu-gap-4 niuu-max-w-lg"
        aria-label="Flock configuration form"
      >
        {errors.length > 0 && <ValidationSummary errors={errors} />}

        <Field id="flock-name" label="Flock name" required>
          <Input
            name="flockName"
            defaultValue={config?.flockName}
            placeholder="My Tyr deployment"
          />
        </Field>

        <Field id="flock-branch" label="Default base branch" required>
          <Input
            name="defaultBaseBranch"
            defaultValue={config?.defaultBaseBranch}
            placeholder="main"
          />
        </Field>

        <Field id="flock-tracker" label="Default tracker type" required>
          <Input
            name="defaultTrackerType"
            defaultValue={config?.defaultTrackerType}
            placeholder="linear"
          />
        </Field>

        <Field
          id="flock-repos"
          label="Default repos"
          hint="Comma-separated list of org/repo identifiers"
        >
          <Input
            name="defaultRepos"
            defaultValue={config?.defaultRepos.join(', ')}
            placeholder="niuulabs/volundr, niuulabs/backend"
          />
        </Field>

        <Field id="flock-max-sagas" label="Max active sagas" required>
          <Input
            name="maxActiveSagas"
            type="number"
            defaultValue={String(config?.maxActiveSagas ?? 5)}
            min="1"
          />
        </Field>

        <div className="niuu-flex niuu-items-center niuu-gap-2">
          <input
            type="checkbox"
            id="flock-auto-milestones"
            name="autoCreateMilestones"
            value="true"
            defaultChecked={config?.autoCreateMilestones}
            className="niuu-accent-brand"
          />
          <label
            htmlFor="flock-auto-milestones"
            className="niuu-text-sm niuu-text-text-primary niuu-select-none"
          >
            Auto-create tracker milestones for new phases
          </label>
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
