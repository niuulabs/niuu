import { useId, useState, type FormEvent, type ReactNode } from 'react';
import { NOTIFICATION_KIND_LABELS, NOTIFICATION_SEVERITY_LABELS, cn } from '@niuulabs/ui';
import {
  FORGE_NOTIFICATION_KINDS,
  FORGE_NOTIFICATION_SEVERITIES,
  FORGE_NOTIFICATION_SOURCES,
  type ForgeNotificationSeverity,
} from '@niuulabs/domain';
import type {
  NotificationQuietHours,
  NotificationRuleDraft,
  NotificationSinkOption,
} from '../../domain/notifications';
import { SOURCE_LABELS } from './NotificationFilters';

export interface ConnectionOption {
  id: string;
  label: string;
}

const CONTROL =
  'niuu:h-8 niuu:w-full niuu:rounded-md niuu:border niuu:border-solid niuu:border-border niuu:bg-bg-primary niuu:px-2 niuu:text-sm niuu:text-text-primary';
const BUTTON =
  'niuu:rounded-md niuu:border niuu:border-solid niuu:px-3 niuu:py-1.5 niuu:text-sm niuu:cursor-pointer niuu:disabled:cursor-not-allowed niuu:disabled:opacity-50';
export const PRIMARY_BUTTON = cn(BUTTON, 'niuu:border-brand niuu:bg-brand niuu:text-bg-primary');
export const SECONDARY_BUTTON = cn(
  BUTTON,
  'niuu:border-border niuu:bg-transparent niuu:text-text-secondary niuu:hover:text-text-primary',
);

const TIME_PATTERN = /^([01]\d|2[0-3]):[0-5]\d$/;

function localTimezone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC';
  } catch {
    return 'UTC';
  }
}

function defaultQuietHours(): NotificationQuietHours {
  return { start: '22:00', end: '07:00', timezone: localTimezone(), allowMinSeverity: 'critical' };
}

function splitIds(value: string): string[] {
  return value
    .split(',')
    .map((item) => item.trim())
    .filter(Boolean);
}

function toggle<T>(values: readonly T[], value: T): T[] {
  return values.includes(value) ? values.filter((item) => item !== value) : [...values, value];
}

/** Human-readable problems with a draft, keyed by field. Empty when it can be saved. */
export function validateRuleDraft(
  draft: NotificationRuleDraft,
  sinks: readonly NotificationSinkOption[],
): Partial<Record<'name' | 'sink' | 'connection' | 'quietHours', string>> {
  const errors: Partial<Record<'name' | 'sink' | 'connection' | 'quietHours', string>> = {};
  if (!draft.name.trim()) errors.name = 'Give the rule a name.';
  const sink = sinks.find((option) => option.name === draft.sink);
  if (!sink) errors.sink = 'Choose where to deliver.';
  else if (sink.requiresIntegration && !draft.integrationConnectionId)
    errors.connection = `${sink.label} needs a messaging connection.`;
  const quiet = draft.quietHours;
  if (quiet && (!TIME_PATTERN.test(quiet.start) || !TIME_PATTERN.test(quiet.end)))
    errors.quietHours = 'Quiet hours use 24-hour HH:MM times.';
  else if (quiet && !quiet.timezone.trim()) errors.quietHours = 'Quiet hours need a timezone.';
  return errors;
}

function Labeled({
  label,
  error,
  children,
  htmlFor,
}: {
  label: string;
  error?: string;
  children: ReactNode;
  htmlFor: string;
}) {
  return (
    <div className="niuu:flex niuu:flex-col niuu:gap-1">
      <label htmlFor={htmlFor} className="niuu:text-xs niuu:font-medium niuu:text-text-secondary">
        {label}
      </label>
      {children}
      {error && (
        <p className="niuu:m-0 niuu:text-xs niuu:text-critical" id={`${htmlFor}-error`}>
          {error}
        </p>
      )}
    </div>
  );
}

function CheckboxGroup<T extends string>({
  legend,
  values,
  selected,
  labels,
  onToggle,
}: {
  legend: string;
  values: readonly T[];
  selected: readonly T[];
  labels: Record<T, string>;
  onToggle: (value: T) => void;
}) {
  return (
    <fieldset className="niuu:m-0 niuu:flex niuu:flex-col niuu:gap-1 niuu:border-0 niuu:p-0">
      <legend className="niuu:mb-1 niuu:p-0 niuu:text-xs niuu:font-medium niuu:text-text-secondary">
        {legend} <span className="niuu:font-normal niuu:text-text-muted">(none = any)</span>
      </legend>
      <div className="niuu:flex niuu:flex-wrap niuu:gap-x-3 niuu:gap-y-1">
        {values.map((value) => (
          <label
            key={value}
            className="niuu:flex niuu:items-center niuu:gap-1 niuu:text-sm niuu:text-text-primary"
          >
            <input
              type="checkbox"
              checked={selected.includes(value)}
              onChange={() => onToggle(value)}
            />
            {labels[value]}
          </label>
        ))}
      </div>
    </fieldset>
  );
}

export interface RuleEditorProps {
  initial: NotificationRuleDraft;
  sinks: NotificationSinkOption[];
  connections: ConnectionOption[];
  saving: boolean;
  error: string | null;
  isNew: boolean;
  onSave: (draft: NotificationRuleDraft) => void;
  onCancel: () => void;
}

export function RuleEditor({
  initial,
  sinks,
  connections,
  saving,
  error,
  isNew,
  onSave,
  onCancel,
}: RuleEditorProps) {
  const id = useId();
  const [draft, setDraft] = useState<NotificationRuleDraft>(initial);
  const [projects, setProjects] = useState(initial.match.projectIds.join(', '));
  const [sessions, setSessions] = useState(initial.match.sessionIds.join(', '));
  const [submitted, setSubmitted] = useState(false);
  const errors = validateRuleDraft(draft, sinks);
  const shown = submitted ? errors : {};
  const sink = sinks.find((option) => option.name === draft.sink);

  function update(patch: Partial<NotificationRuleDraft>) {
    setDraft((current) => ({ ...current, ...patch }));
  }

  function updateMatch(patch: Partial<NotificationRuleDraft['match']>) {
    setDraft((current) => ({ ...current, match: { ...current.match, ...patch } }));
  }

  function updateQuiet(patch: Partial<NotificationQuietHours>) {
    setDraft((current) => ({
      ...current,
      quietHours: { ...(current.quietHours ?? defaultQuietHours()), ...patch },
    }));
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    setSubmitted(true);
    if (Object.keys(errors).length > 0) return;
    onSave({
      ...draft,
      name: draft.name.trim(),
      integrationConnectionId: sink?.requiresIntegration ? draft.integrationConnectionId : null,
      match: { ...draft.match, projectIds: splitIds(projects), sessionIds: splitIds(sessions) },
    });
  }

  return (
    <form
      aria-label={isNew ? 'New delivery rule' : `Edit ${initial.name}`}
      className="niuu:flex niuu:flex-col niuu:gap-3 niuu:rounded-md niuu:border niuu:border-solid niuu:border-border niuu:bg-bg-tertiary niuu:p-3"
      onSubmit={submit}
      noValidate
    >
      <Labeled label="Name" htmlFor={`${id}-name`} error={shown.name}>
        <input
          id={`${id}-name`}
          className={CONTROL}
          value={draft.name}
          aria-invalid={Boolean(shown.name) || undefined}
          onChange={(event) => update({ name: event.target.value })}
        />
      </Labeled>
      <label className="niuu:flex niuu:items-center niuu:gap-2 niuu:text-sm niuu:text-text-primary">
        <input
          type="checkbox"
          checked={draft.enabled}
          onChange={(event) => update({ enabled: event.target.checked })}
        />
        Enabled
      </label>
      <CheckboxGroup
        legend="Kinds"
        values={FORGE_NOTIFICATION_KINDS}
        selected={draft.match.kinds}
        labels={NOTIFICATION_KIND_LABELS}
        onToggle={(kind) => updateMatch({ kinds: toggle(draft.match.kinds, kind) })}
      />
      <Labeled label="Minimum severity" htmlFor={`${id}-severity`}>
        <select
          id={`${id}-severity`}
          className={CONTROL}
          value={draft.match.minSeverity}
          onChange={(event) =>
            updateMatch({ minSeverity: event.target.value as ForgeNotificationSeverity })
          }
        >
          {FORGE_NOTIFICATION_SEVERITIES.map((severity) => (
            <option key={severity} value={severity}>
              {NOTIFICATION_SEVERITY_LABELS[severity]}
            </option>
          ))}
        </select>
      </Labeled>
      <CheckboxGroup
        legend="Sources"
        values={FORGE_NOTIFICATION_SOURCES}
        selected={draft.match.sources}
        labels={SOURCE_LABELS}
        onToggle={(source) => updateMatch({ sources: toggle(draft.match.sources, source) })}
      />
      <Labeled label="Project ids (comma separated, empty = any)" htmlFor={`${id}-projects`}>
        <input
          id={`${id}-projects`}
          className={CONTROL}
          value={projects}
          onChange={(event) => setProjects(event.target.value)}
        />
      </Labeled>
      <Labeled label="Session ids (comma separated, empty = any)" htmlFor={`${id}-sessions`}>
        <input
          id={`${id}-sessions`}
          className={CONTROL}
          value={sessions}
          onChange={(event) => setSessions(event.target.value)}
        />
      </Labeled>
      <Labeled label="Deliver to" htmlFor={`${id}-sink`} error={shown.sink}>
        <select
          id={`${id}-sink`}
          className={CONTROL}
          value={draft.sink}
          aria-invalid={Boolean(shown.sink) || undefined}
          onChange={(event) => update({ sink: event.target.value })}
        >
          <option value="">Choose a sink…</option>
          {sinks.map((option) => (
            <option key={option.name} value={option.name}>
              {option.label}
            </option>
          ))}
        </select>
      </Labeled>
      {sink?.requiresIntegration && (
        <Labeled label="Messaging connection" htmlFor={`${id}-connection`} error={shown.connection}>
          <select
            id={`${id}-connection`}
            className={CONTROL}
            value={draft.integrationConnectionId ?? ''}
            aria-invalid={Boolean(shown.connection) || undefined}
            onChange={(event) => update({ integrationConnectionId: event.target.value || null })}
          >
            <option value="">Choose a connection…</option>
            {connections.map((connection) => (
              <option key={connection.id} value={connection.id}>
                {connection.label}
              </option>
            ))}
          </select>
          {connections.length === 0 && (
            <p className="niuu:m-0 niuu:text-xs niuu:text-text-muted">
              No messaging connections yet — add one under Settings → Integrations.
            </p>
          )}
        </Labeled>
      )}
      <fieldset className="niuu:m-0 niuu:flex niuu:flex-col niuu:gap-2 niuu:border-0 niuu:p-0">
        <legend className="niuu:mb-1 niuu:p-0 niuu:text-xs niuu:font-medium niuu:text-text-secondary">
          Quiet hours
        </legend>
        <label className="niuu:flex niuu:items-center niuu:gap-2 niuu:text-sm niuu:text-text-primary">
          <input
            type="checkbox"
            checked={draft.quietHours !== null}
            onChange={(event) =>
              update({ quietHours: event.target.checked ? defaultQuietHours() : null })
            }
          />
          Hold messages during quiet hours
        </label>
        {draft.quietHours && (
          <div className="niuu:grid niuu:grid-cols-2 niuu:gap-2">
            <Labeled label="From" htmlFor={`${id}-quiet-start`}>
              <input
                id={`${id}-quiet-start`}
                type="time"
                className={CONTROL}
                value={draft.quietHours.start}
                onChange={(event) => updateQuiet({ start: event.target.value })}
              />
            </Labeled>
            <Labeled label="Until" htmlFor={`${id}-quiet-end`}>
              <input
                id={`${id}-quiet-end`}
                type="time"
                className={CONTROL}
                value={draft.quietHours.end}
                onChange={(event) => updateQuiet({ end: event.target.value })}
              />
            </Labeled>
            <Labeled label="Timezone" htmlFor={`${id}-quiet-tz`}>
              <input
                id={`${id}-quiet-tz`}
                className={CONTROL}
                value={draft.quietHours.timezone}
                onChange={(event) => updateQuiet({ timezone: event.target.value })}
              />
            </Labeled>
            <Labeled label="Still send at or above" htmlFor={`${id}-quiet-allow`}>
              <select
                id={`${id}-quiet-allow`}
                className={CONTROL}
                value={draft.quietHours.allowMinSeverity}
                onChange={(event) =>
                  updateQuiet({
                    allowMinSeverity: event.target.value as ForgeNotificationSeverity,
                  })
                }
              >
                {FORGE_NOTIFICATION_SEVERITIES.map((severity) => (
                  <option key={severity} value={severity}>
                    {NOTIFICATION_SEVERITY_LABELS[severity]}
                  </option>
                ))}
              </select>
            </Labeled>
          </div>
        )}
        {shown.quietHours && (
          <p className="niuu:m-0 niuu:text-xs niuu:text-critical">{shown.quietHours}</p>
        )}
      </fieldset>
      {error && (
        <p role="alert" className="niuu:m-0 niuu:text-sm niuu:text-critical">
          {error}
        </p>
      )}
      <div className="niuu:flex niuu:justify-end niuu:gap-2">
        <button type="button" className={SECONDARY_BUTTON} onClick={onCancel}>
          Cancel
        </button>
        <button type="submit" className={PRIMARY_BUTTON} disabled={saving}>
          {saving ? 'Saving…' : isNew ? 'Create rule' : 'Save rule'}
        </button>
      </div>
    </form>
  );
}
