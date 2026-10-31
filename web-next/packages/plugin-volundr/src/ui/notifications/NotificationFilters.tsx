import type { ReactNode } from 'react';
import {
  FilterBar,
  FilterToggle,
  NOTIFICATION_KIND_LABELS,
  NOTIFICATION_SEVERITY_LABELS,
  type ActiveFilter,
  type FilterState,
} from '@niuulabs/ui';
import {
  FORGE_NOTIFICATION_KINDS,
  FORGE_NOTIFICATION_SEVERITIES,
  FORGE_NOTIFICATION_SOURCES,
  isForgeNotificationSeverity,
  type ForgeNotificationKind,
  type ForgeNotificationSource,
} from '@niuulabs/domain';
import { DEFAULT_NOTIFICATION_FILTER, type NotificationFilter } from '../../domain/notifications';

export interface FilterOption {
  id: string;
  label: string;
}

export const SOURCE_LABELS: Record<ForgeNotificationSource, string> = {
  agent: 'Agent',
  system: 'System',
  operator: 'Operator',
};

const SELECT_CLASS =
  'niuu:h-8 niuu:rounded-md niuu:border niuu:border-solid niuu:border-border niuu:bg-bg-secondary niuu:px-2 niuu:text-sm niuu:text-text-primary';

type ChipKey =
  'kinds' | 'minSeverity' | 'sources' | 'instanceId' | 'projectId' | 'sessionId' | 'unreadOnly';

function labelOf(options: readonly FilterOption[], id: string): string {
  return options.find((option) => option.id === id)?.label ?? id;
}

function toggle<T>(values: readonly T[], value: T): T[] {
  return values.includes(value) ? values.filter((item) => item !== value) : [...values, value];
}

function clearChip(filter: NotificationFilter, key: ChipKey): NotificationFilter {
  return { ...filter, [key]: DEFAULT_NOTIFICATION_FILTER[key] };
}

function LabeledSelect({
  label,
  value,
  onChange,
  children,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  children: ReactNode;
}) {
  return (
    <label className="niuu:flex niuu:items-center niuu:gap-2 niuu:text-xs niuu:text-text-muted">
      {label}
      <select
        className={SELECT_CLASS}
        value={value}
        onChange={(event) => onChange(event.target.value)}
      >
        {children}
      </select>
    </label>
  );
}

export interface NotificationFiltersProps {
  filter: NotificationFilter;
  onChange: (next: NotificationFilter) => void;
  hosts: FilterOption[];
  projects: FilterOption[];
  sessions: FilterOption[];
}

export function NotificationFilters({
  filter,
  onChange,
  hosts,
  projects,
  sessions,
}: NotificationFiltersProps) {
  const chips: Array<ActiveFilter & { key: ChipKey }> = [];
  if (filter.kinds.length > 0)
    chips.push({
      key: 'kinds',
      label: 'Kind',
      value: filter.kinds.map((kind) => NOTIFICATION_KIND_LABELS[kind]).join(', '),
    });
  if (filter.minSeverity)
    chips.push({
      key: 'minSeverity',
      label: 'Severity',
      value: `${NOTIFICATION_SEVERITY_LABELS[filter.minSeverity]}+`,
    });
  if (filter.sources.length > 0)
    chips.push({
      key: 'sources',
      label: 'Source',
      value: filter.sources.map((source) => SOURCE_LABELS[source]).join(', '),
    });
  if (filter.instanceId !== null)
    chips.push({ key: 'instanceId', label: 'Host', value: labelOf(hosts, filter.instanceId) });
  if (filter.projectId)
    chips.push({ key: 'projectId', label: 'Project', value: labelOf(projects, filter.projectId) });
  if (filter.sessionId)
    chips.push({ key: 'sessionId', label: 'Session', value: labelOf(sessions, filter.sessionId) });
  if (filter.unreadOnly) chips.push({ key: 'unreadOnly', label: 'Show', value: 'Unread only' });

  const barValue: FilterState = { q: filter.query };
  for (const chip of chips) barValue[chip.key] = chip.value;

  function handleBarChange(next: FilterState) {
    if (Object.keys(next).length === 0) {
      onChange(DEFAULT_NOTIFICATION_FILTER);
      return;
    }
    let updated: NotificationFilter = { ...filter, query: next.q ?? '' };
    for (const chip of chips) if (!(chip.key in next)) updated = clearChip(updated, chip.key);
    onChange(updated);
  }

  return (
    <div className="niuu:flex niuu:flex-col niuu:gap-3">
      <FilterBar
        value={barValue}
        onChange={handleBarChange}
        placeholder="Search loaded notifications…"
        activeFilters={chips}
      />
      <div
        role="group"
        aria-label="Notification filters"
        className="niuu:flex niuu:flex-wrap niuu:items-center niuu:gap-x-4 niuu:gap-y-2"
      >
        <div role="group" aria-label="Kind" className="niuu:flex niuu:flex-wrap niuu:gap-1">
          {FORGE_NOTIFICATION_KINDS.map((kind: ForgeNotificationKind) => (
            <FilterToggle
              key={kind}
              label={NOTIFICATION_KIND_LABELS[kind]}
              active={filter.kinds.includes(kind)}
              onChange={() => onChange({ ...filter, kinds: toggle(filter.kinds, kind) })}
            />
          ))}
        </div>
        <LabeledSelect
          label="Min severity"
          value={filter.minSeverity ?? ''}
          onChange={(value) =>
            onChange({
              ...filter,
              minSeverity: isForgeNotificationSeverity(value) ? value : null,
            })
          }
        >
          <option value="">Any</option>
          {FORGE_NOTIFICATION_SEVERITIES.slice(1).map((severity) => (
            <option key={severity} value={severity}>
              {NOTIFICATION_SEVERITY_LABELS[severity]}+
            </option>
          ))}
        </LabeledSelect>
        <div role="group" aria-label="Source" className="niuu:flex niuu:gap-1">
          {FORGE_NOTIFICATION_SOURCES.map((source) => (
            <FilterToggle
              key={source}
              label={SOURCE_LABELS[source]}
              active={filter.sources.includes(source)}
              onChange={() => onChange({ ...filter, sources: toggle(filter.sources, source) })}
            />
          ))}
        </div>
        {hosts.length > 1 && (
          <LabeledSelect
            label="Host"
            value={filter.instanceId ?? '*'}
            onChange={(value) => onChange({ ...filter, instanceId: value === '*' ? null : value })}
          >
            <option value="*">All hosts</option>
            {hosts.map((host) => (
              <option key={host.id} value={host.id}>
                {host.label}
              </option>
            ))}
          </LabeledSelect>
        )}
        {projects.length > 0 && (
          <LabeledSelect
            label="Project"
            value={filter.projectId ?? ''}
            onChange={(value) => onChange({ ...filter, projectId: value || null })}
          >
            <option value="">All projects</option>
            {projects.map((project) => (
              <option key={project.id} value={project.id}>
                {project.label}
              </option>
            ))}
          </LabeledSelect>
        )}
        {sessions.length > 0 && (
          <LabeledSelect
            label="Session"
            value={filter.sessionId ?? ''}
            onChange={(value) => onChange({ ...filter, sessionId: value || null })}
          >
            <option value="">All sessions</option>
            {sessions.map((session) => (
              <option key={session.id} value={session.id}>
                {session.label}
              </option>
            ))}
          </LabeledSelect>
        )}
        <FilterToggle
          label="Unread only"
          active={filter.unreadOnly}
          onChange={(unreadOnly) => onChange({ ...filter, unreadOnly })}
        />
      </div>
    </div>
  );
}
