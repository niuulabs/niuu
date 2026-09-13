import { Field } from '@niuulabs/ui';
import {
  describeEngineProviders,
  PROVIDER_SETTINGS_PATH,
  type EngineOption,
} from './launchEngines';

const SELECT_CLASS =
  'niuu-form-control niuu:w-full niuu:rounded-md niuu:border niuu:border-border-subtle niuu:bg-bg-primary niuu:px-3 niuu:py-2 niuu:text-sm niuu:text-text-primary niuu:outline-none niuu:focus:border-brand';
const LINK_CLASS = 'niuu:text-text-secondary niuu:underline niuu:hover:text-text-primary';

export interface EngineSelectProps {
  /** The engines the person can launch, with the providers that power each. */
  engines: EngineOption[];
  /** The selected session definition key. */
  value: string;
  onChange: (definitionKey: string) => void;
  /** Providers or engines are still being fetched. */
  loading?: boolean;
  /** Providers or engines could not be fetched; shown instead of the picker. */
  error?: Error | null;
  /** The engine the value names when it is not launchable (no provider). */
  unavailableName?: string;
  testId?: string;
}

/**
 * The engine picker shared by the quick and the advanced launch: a dropdown of
 * the engines a connected provider powers, what the selected one is for, and a
 * way to the provider settings when the one you want is missing.
 */
export function EngineSelect({
  engines,
  value,
  onChange,
  loading = false,
  error = null,
  unavailableName,
  testId = 'engine-select',
}: EngineSelectProps) {
  const selected = engines.find((engine) => engine.definition.key === value);
  const orphaned = !selected && Boolean(value) && !loading && !error;

  if (error) {
    return (
      <Field label="Engine">
        <p role="alert" className="niuu:text-xs niuu:text-danger" data-testid={`${testId}-error`}>
          Could not load your providers: {error.message}
        </p>
        <ManageProvidersLink label="Open provider settings" />
      </Field>
    );
  }

  if (!loading && engines.length === 0) {
    return (
      <Field label="Engine">
        <p
          role="status"
          className="niuu:text-xs niuu:text-text-muted"
          data-testid={`${testId}-empty`}
        >
          No engine has a connected AI provider yet.
        </p>
        <ManageProvidersLink label="Connect a provider" />
      </Field>
    );
  }

  return (
    <Field label="Engine">
      <select
        className={SELECT_CLASS}
        aria-label="Engine"
        value={value}
        onChange={(event) => onChange(event.target.value)}
        disabled={loading}
        data-testid={testId}
      >
        {orphaned ? (
          <option value={value} disabled>
            {unavailableName ?? value} (no provider connected)
          </option>
        ) : null}
        {engines.map((engine) => (
          <option key={engine.definition.key} value={engine.definition.key}>
            {engine.definition.displayName}
          </option>
        ))}
      </select>
      {selected ? (
        <div
          className="niuu:mt-1 niuu:flex niuu:flex-col niuu:gap-0.5 niuu:text-xs niuu:text-text-muted"
          data-testid={`${testId}-hint`}
        >
          {selected.definition.description ? <p>{selected.definition.description}</p> : null}
          <p>
            Uses {describeEngineProviders(selected)} ·{' '}
            <a href={PROVIDER_SETTINGS_PATH} className={LINK_CLASS}>
              Manage providers
            </a>
          </p>
        </div>
      ) : orphaned ? (
        <p
          role="alert"
          className="niuu:mt-1 niuu:text-xs niuu:text-danger"
          data-testid={`${testId}-orphaned`}
        >
          None of your connected providers powers {unavailableName ?? value}.{' '}
          <a href={PROVIDER_SETTINGS_PATH} className={LINK_CLASS}>
            Connect one
          </a>{' '}
          or pick another engine.
        </p>
      ) : null}
    </Field>
  );
}

function ManageProvidersLink({ label }: { label: string }) {
  return (
    <a
      href={PROVIDER_SETTINGS_PATH}
      className={`niuu:mt-1 niuu:self-start niuu:text-xs ${LINK_CLASS}`}
      data-testid="engine-manage-providers"
    >
      {label} →
    </a>
  );
}
