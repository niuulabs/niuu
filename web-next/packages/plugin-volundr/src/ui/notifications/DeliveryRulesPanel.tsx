import { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { X } from 'lucide-react';
import { useService } from '@niuulabs/plugin-sdk';
import { NOTIFICATION_KIND_LABELS, NOTIFICATION_SEVERITY_LABELS, cn } from '@niuulabs/ui';
import {
  emptyRuleDraft,
  type NotificationRule,
  type NotificationSinkOption,
} from '../../domain/notifications';
import type { IVolundrService } from '../../ports/IVolundrService';
import {
  useDeleteNotificationRule,
  useNotificationRules,
  useNotificationSinks,
  useSaveNotificationRule,
} from '../hooks/useNotifications';
import { SOURCE_LABELS, type FilterOption } from './NotificationFilters';
import { describeError } from './errors';
import { PRIMARY_BUTTON, RuleEditor, SECONDARY_BUTTON, type ConnectionOption } from './RuleEditor';

const MESSAGING = 'messaging';

function errorMessage(error: unknown): string {
  return describeError(error, 'Something went wrong.');
}

/** Messaging integration connections a sink such as Telegram can deliver through. */
function useMessagingConnections(): ConnectionOption[] {
  const volundr = useService<IVolundrService>('volundr');
  const { data } = useQuery({
    queryKey: ['volundr', 'integrations'],
    queryFn: () => volundr.getIntegrations(),
  });
  return useMemo(
    () =>
      (data ?? [])
        .map((connection) => connection as typeof connection & Record<string, unknown>)
        .filter((connection) => {
          const type = connection.integrationType ?? connection.integration_type;
          return typeof type === 'string' && type.toLowerCase() === MESSAGING;
        })
        .map((connection) => ({
          id: connection.id,
          label:
            [connection.slug, connection.credentialName ?? connection.credential_name]
              .filter((part): part is string => typeof part === 'string' && part !== '')
              .join(' · ') || connection.id,
        })),
    [data],
  );
}

export function describeRuleMatch(rule: NotificationRule): string {
  const parts: string[] = [];
  parts.push(
    rule.match.kinds.length > 0
      ? rule.match.kinds.map((kind) => NOTIFICATION_KIND_LABELS[kind]).join(', ')
      : 'Any kind',
  );
  if (rule.match.minSeverity !== 'info')
    parts.push(`${NOTIFICATION_SEVERITY_LABELS[rule.match.minSeverity]}+`);
  if (rule.match.sources.length > 0)
    parts.push(rule.match.sources.map((source) => SOURCE_LABELS[source]).join(', '));
  if (rule.match.projectIds.length > 0) parts.push(`${rule.match.projectIds.length} project(s)`);
  if (rule.match.sessionIds.length > 0) parts.push(`${rule.match.sessionIds.length} session(s)`);
  return parts.join(' · ');
}

function RuleItem({
  rule,
  sinks,
  onEdit,
  onDelete,
  deleting,
}: {
  rule: NotificationRule;
  sinks: NotificationSinkOption[];
  onEdit: () => void;
  onDelete: () => void;
  deleting: boolean;
}) {
  const [confirming, setConfirming] = useState(false);
  const sinkLabel = sinks.find((sink) => sink.name === rule.sink)?.label ?? rule.sink;
  return (
    <li
      data-testid="notification-rule"
      className="niuu:flex niuu:flex-col niuu:gap-1 niuu:rounded-md niuu:border niuu:border-solid niuu:border-border-subtle niuu:bg-bg-primary niuu:p-3"
    >
      <div className="niuu:flex niuu:items-center niuu:gap-2">
        <span className="niuu:flex-1 niuu:text-sm niuu:font-medium niuu:text-text-primary">
          {rule.name}
        </span>
        <span
          className={cn(
            'niuu:rounded-full niuu:px-2 niuu:py-0.5 niuu:text-xs',
            rule.enabled
              ? 'niuu:bg-state-ok-bg niuu:text-state-ok'
              : 'niuu:bg-bg-tertiary niuu:text-text-muted',
          )}
        >
          {rule.enabled ? 'On' : 'Off'}
        </span>
      </div>
      <p className="niuu:m-0 niuu:text-xs niuu:text-text-secondary">
        {describeRuleMatch(rule)} → {sinkLabel}
      </p>
      {rule.quietHours && (
        <p className="niuu:m-0 niuu:text-xs niuu:text-text-muted">
          Quiet {rule.quietHours.start}–{rule.quietHours.end} {rule.quietHours.timezone}, still
          sends {NOTIFICATION_SEVERITY_LABELS[rule.quietHours.allowMinSeverity]}+
        </p>
      )}
      <div className="niuu:flex niuu:justify-end niuu:gap-2">
        {confirming ? (
          <>
            <span className="niuu:self-center niuu:text-xs niuu:text-text-secondary">
              Delete this rule?
            </span>
            <button type="button" className={SECONDARY_BUTTON} onClick={() => setConfirming(false)}>
              Keep
            </button>
            <button
              type="button"
              className={cn(SECONDARY_BUTTON, 'niuu:text-critical')}
              disabled={deleting}
              onClick={onDelete}
            >
              Delete
            </button>
          </>
        ) : (
          <>
            <button
              type="button"
              className={SECONDARY_BUTTON}
              onClick={onEdit}
              aria-label={`Edit ${rule.name}`}
            >
              Edit
            </button>
            <button
              type="button"
              className={SECONDARY_BUTTON}
              onClick={() => setConfirming(true)}
              aria-label={`Delete ${rule.name}`}
            >
              Delete
            </button>
          </>
        )}
      </div>
    </li>
  );
}

export interface DeliveryRulesPanelProps {
  hosts: FilterOption[];
  onClose: () => void;
}

export function DeliveryRulesPanel({ hosts, onClose }: DeliveryRulesPanelProps) {
  const [instanceId, setInstanceId] = useState<string | null>(null);
  const rules = useNotificationRules(instanceId);
  const sinks = useNotificationSinks(instanceId);
  const connections = useMessagingConnections();
  const save = useSaveNotificationRule(instanceId);
  const remove = useDeleteNotificationRule(instanceId);
  const [editing, setEditing] = useState<NotificationRule | 'new' | null>(null);
  const sinkOptions = sinks.data ?? [];

  function closeEditor() {
    setEditing(null);
    save.reset();
  }

  let list;
  if (rules.isLoading) list = <p className="niuu:m-0 niuu:text-sm">Loading rules…</p>;
  else if (rules.isError)
    list = (
      <div role="alert" className="niuu:flex niuu:flex-col niuu:gap-2 niuu:text-sm">
        <p className="niuu:m-0 niuu:text-critical">
          Could not load delivery rules: {errorMessage(rules.error)}
        </p>
        <button type="button" className={SECONDARY_BUTTON} onClick={() => void rules.refetch()}>
          Retry
        </button>
      </div>
    );
  else if ((rules.data ?? []).length === 0)
    list = (
      <p className="niuu:m-0 niuu:text-sm niuu:text-text-muted">
        No delivery rules. Notifications stay in Forge until you add one.
      </p>
    );
  else
    list = (
      <ul className="niuu:m-0 niuu:flex niuu:list-none niuu:flex-col niuu:gap-2 niuu:p-0">
        {rules.data!.map((rule) => (
          <RuleItem
            key={rule.id}
            rule={rule}
            sinks={sinkOptions}
            onEdit={() => setEditing(rule)}
            onDelete={() => remove.mutate(rule.id)}
            deleting={remove.isPending}
          />
        ))}
      </ul>
    );

  return (
    <aside
      id="notification-rules"
      aria-label="Delivery rules"
      className="niuu:flex niuu:w-full niuu:shrink-0 niuu:flex-col niuu:gap-3 niuu:self-start niuu:rounded-lg niuu:border niuu:border-solid niuu:border-border niuu:bg-bg-secondary niuu:p-4 niuu:lg:w-96"
      onKeyDown={(event) => {
        if (event.key === 'Escape' && !editing) onClose();
      }}
    >
      <div className="niuu:flex niuu:items-center niuu:justify-between">
        <h3 className="niuu:m-0 niuu:text-base niuu:text-text-primary">Delivery rules</h3>
        <button
          type="button"
          className={cn(SECONDARY_BUTTON, 'niuu:px-2')}
          onClick={onClose}
          aria-label="Close delivery rules"
        >
          <X className="niuu:h-3 niuu:w-3" aria-hidden="true" />
        </button>
      </div>
      <p className="niuu:m-0 niuu:text-xs niuu:text-text-muted">
        Rules send matching notifications to Telegram, push or a webhook. Nothing leaves Forge
        without one.
      </p>
      {hosts.length > 1 && (
        <label className="niuu:flex niuu:items-center niuu:gap-2 niuu:text-xs niuu:text-text-muted">
          Host
          <select
            className="niuu:h-8 niuu:flex-1 niuu:rounded-md niuu:border niuu:border-solid niuu:border-border niuu:bg-bg-primary niuu:px-2 niuu:text-sm niuu:text-text-primary"
            value={instanceId ?? ''}
            onChange={(event) => {
              setInstanceId(event.target.value || null);
              closeEditor();
            }}
          >
            <option value="">This Forge</option>
            {hosts.map((host) => (
              <option key={host.id} value={host.id}>
                {host.label}
              </option>
            ))}
          </select>
        </label>
      )}
      {remove.isError && (
        <p role="alert" className="niuu:m-0 niuu:text-sm niuu:text-critical">
          Could not delete the rule: {errorMessage(remove.error)}
        </p>
      )}
      {sinks.isError && (
        <p role="alert" className="niuu:m-0 niuu:text-sm niuu:text-critical">
          Could not load delivery sinks: {errorMessage(sinks.error)}
        </p>
      )}
      {editing ? (
        <RuleEditor
          key={editing === 'new' ? 'new' : editing.id}
          initial={editing === 'new' ? emptyRuleDraft(sinkOptions[0]?.name ?? '') : editing}
          isNew={editing === 'new'}
          sinks={sinkOptions}
          connections={connections}
          saving={save.isPending}
          error={save.isError ? `Could not save the rule: ${errorMessage(save.error)}` : null}
          onCancel={closeEditor}
          onSave={(draft) =>
            save.mutate(
              { id: editing === 'new' ? null : editing.id, draft },
              { onSuccess: closeEditor },
            )
          }
        />
      ) : (
        <>
          {list}
          <button
            type="button"
            className={PRIMARY_BUTTON}
            onClick={() => setEditing('new')}
            disabled={sinks.isLoading || sinkOptions.length === 0}
          >
            New rule
          </button>
        </>
      )}
    </aside>
  );
}
