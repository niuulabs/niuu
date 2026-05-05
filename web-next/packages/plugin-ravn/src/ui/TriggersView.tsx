/**
 * TriggersView — fleet-wide trigger table grouped by kind.
 *
 * Groups: cron | event | webhook | manual
 * Columns: persona · spec · enabled · created
 */

import { StateDot } from '@niuulabs/ui';
import { useTriggers } from './hooks/useTriggers';
import type { Trigger, TriggerKind } from '../domain/trigger';

const KIND_ORDER: TriggerKind[] = ['cron', 'event', 'webhook', 'manual'];

const KIND_LABEL: Record<TriggerKind, string> = {
  cron: '⏰ cron',
  event: '⚡ event',
  webhook: '🔗 webhook',
  manual: '▶ manual',
};

function TriggerRow({ trigger }: { trigger: Trigger }) {
  return (
    <tr className="rv-trigger-row" data-enabled={trigger.enabled}>
      <td className="rv-trigger-row__persona">{trigger.personaName}</td>
      <td className="rv-trigger-row__spec">
        <code className="rv-trigger-row__spec-code">{trigger.spec}</code>
      </td>
      <td className="rv-trigger-row__enabled">
        <StateDot state={trigger.enabled ? 'healthy' : 'idle'} />
        <span className="rv-trigger-row__enabled-label">
          {trigger.enabled ? 'active' : 'disabled'}
        </span>
      </td>
      <td className="rv-trigger-row__created">{trigger.createdAt.slice(0, 10)}</td>
    </tr>
  );
}

function TriggerGroup({ kind, triggers }: { kind: TriggerKind; triggers: Trigger[] }) {
  if (!triggers.length) return null;
  return (
    <section className="rv-trigger-group" aria-label={`${kind} triggers`} data-kind={kind}>
      <h3 className="rv-trigger-group__title">{KIND_LABEL[kind]}</h3>
      <table className="rv-trigger-table" aria-label={`${kind} triggers table`}>
        <thead>
          <tr>
            <th>persona</th>
            <th>spec</th>
            <th>status</th>
            <th>created</th>
          </tr>
        </thead>
        <tbody>
          {triggers.map((t) => (
            <TriggerRow key={t.id} trigger={t} />
          ))}
        </tbody>
      </table>
    </section>
  );
}

export function TriggersView() {
  const { data: triggers, isLoading, isError } = useTriggers();

  if (isLoading) {
    return (
      <div className="rv-triggers-view">
        <div className="rv-triggers-view__loading">
          <StateDot state="processing" pulse />
          <span>loading triggers…</span>
        </div>
      </div>
    );
  }

  if (isError) {
    return (
      <div className="rv-triggers-view">
        <div className="rv-triggers-view__error">failed to load triggers</div>
      </div>
    );
  }

  const grouped = KIND_ORDER.reduce<Record<TriggerKind, Trigger[]>>(
    (acc, kind) => {
      acc[kind] = (triggers ?? []).filter((t) => t.kind === kind);
      return acc;
    },
    { cron: [], event: [], webhook: [], manual: [] },
  );

  const total = triggers?.length ?? 0;
  const active = triggers?.filter((t) => t.enabled).length ?? 0;

  return (
    <div className="rv-triggers-view">
      <div className="rv-triggers-view__header">
        <span className="rv-triggers-view__count">
          <strong>{active}</strong> active · {total} total
        </span>
      </div>
      {total === 0 ? (
        <div className="rv-triggers-view__empty">no triggers configured</div>
      ) : (
        KIND_ORDER.map((kind) => <TriggerGroup key={kind} kind={kind} triggers={grouped[kind]} />)
      )}
    </div>
  );
}
