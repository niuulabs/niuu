/**
 * General section — read-only key-value display of core Tyr service bindings.
 * Matches web2's `tyr.general` section.
 */

interface KVRowProps {
  label: string;
  value: string;
}

function KVRow({ label, value }: KVRowProps) {
  return (
    <div className="niuu-flex niuu-items-center niuu-justify-between niuu-py-2 niuu-border-b niuu-border-border-subtle">
      <span className="niuu-text-sm niuu-text-text-secondary">{label}</span>
      <span className="niuu-text-sm niuu-font-mono niuu-text-text-primary">{value}</span>
    </div>
  );
}

const SERVICE_BINDINGS: KVRowProps[] = [
  { label: 'Service URL', value: 'https://tyr.niuu.internal' },
  { label: 'Event backbone', value: 'sleipnir · nats' },
  { label: 'Knowledge store', value: 'mímir · qdrant:/niuu' },
  { label: 'Default workflow', value: 'tpl-ship v1.4.2' },
];

export function GeneralSection() {
  return (
    <section aria-label="General settings">
      <h3 className="niuu-text-base niuu-font-semibold niuu-text-text-primary niuu-mb-1">
        General
      </h3>
      <p className="niuu-text-sm niuu-text-text-secondary niuu-mb-4">
        Core service bindings for the saga coordinator.
      </p>

      <div className="niuu-max-w-lg" role="list" aria-label="Service bindings">
        {SERVICE_BINDINGS.map((binding) => (
          <div key={binding.label} role="listitem">
            <KVRow label={binding.label} value={binding.value} />
          </div>
        ))}
      </div>
    </section>
  );
}
