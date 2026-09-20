import { cn } from '@niuulabs/ui';

export type WorkflowEvidencePolicy = Record<string, unknown>;

export interface EvidencePolicyEditorProps {
  policy: WorkflowEvidencePolicy | undefined;
  onChange: (policy: WorkflowEvidencePolicy) => void;
}

const SECTION_LABEL =
  'niuu:text-[9px] niuu:font-semibold niuu:uppercase niuu:tracking-[0.22em] niuu:text-text-faint niuu:font-mono';
const INPUT =
  'niuu:w-full niuu:py-2.5 niuu:px-3.5 niuu:bg-bg-tertiary niuu:border niuu:border-border-subtle niuu:rounded-lg niuu:text-text-primary niuu:font-sans niuu:text-[12px]';
const ROW =
  'niuu:rounded-lg niuu:border niuu:border-border-subtle niuu:bg-bg-elevated niuu:p-3 niuu:flex niuu:flex-col niuu:gap-2';
const ACTION =
  'niuu:rounded-md niuu:border niuu:border-border-subtle niuu:bg-bg-tertiary niuu:px-2.5 niuu:py-1.5 niuu:text-[11px] niuu:font-mono niuu:text-text-secondary';

function stringList(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === 'string')
    : [];
}

function producerMap(value: unknown): Record<string, string[]> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return {};
  return Object.fromEntries(
    Object.entries(value).map(([key, producers]) => [key, stringList(producers)]),
  );
}

function csv(value: string): string[] {
  return [
    ...new Set(
      value
        .split(',')
        .map((item) => item.trim())
        .filter(Boolean),
    ),
  ];
}

export function emptyEvidencePolicy(): WorkflowEvidencePolicy {
  return {
    required_review_roles: [],
    required_result_contract_ids: [],
    review_producers: {},
    result_producers: {},
    required_check_names: [],
    require_checks: false,
    check_producers: [],
  };
}

function RequirementRows({
  title,
  itemLabel,
  addLabel,
  values,
  producers,
  onChange,
}: {
  title: string;
  itemLabel: string;
  addLabel: string;
  values: string[];
  producers: Record<string, string[]>;
  onChange: (values: string[], producers: Record<string, string[]>) => void;
}) {
  const rename = (index: number, nextName: string) => {
    const previousName = values[index] ?? '';
    const nextValues = values.map((value, itemIndex) => (itemIndex === index ? nextName : value));
    const nextProducers = { ...producers };
    const retained = nextProducers[previousName] ?? [];
    if (previousName !== nextName) delete nextProducers[previousName];
    nextProducers[nextName] = retained;
    onChange(nextValues, nextProducers);
  };

  const remove = (index: number) => {
    const removedName = values[index] ?? '';
    const nextProducers = { ...producers };
    delete nextProducers[removedName];
    onChange(
      values.filter((_, itemIndex) => itemIndex !== index),
      nextProducers,
    );
  };

  return (
    <section className="niuu:flex niuu:flex-col niuu:gap-2">
      <div className="niuu:flex niuu:items-center niuu:justify-between">
        <span className={SECTION_LABEL}>{title}</span>
        <button
          type="button"
          className={ACTION}
          disabled={values.includes('')}
          onClick={() => onChange([...values, ''], { ...producers, '': [] })}
        >
          + {addLabel}
        </button>
      </div>
      {values.length === 0 ? (
        <p className="niuu:m-0 niuu:text-[11px] niuu:text-text-faint">
          None required. Add one to make this evidence type mandatory.
        </p>
      ) : null}
      {values.map((value, index) => (
        <div className={ROW} key={`${index}-${value}`}>
          <div>
            <label className={SECTION_LABEL}>
              {itemLabel} {index + 1}
            </label>
            <input
              className={INPUT}
              aria-label={`${itemLabel} ${index + 1}`}
              value={value}
              onChange={(event) => rename(index, event.target.value.trimStart())}
            />
          </div>
          <div>
            <label className={SECTION_LABEL}>Trusted producer IDs</label>
            <input
              className={INPUT}
              aria-label={`Trusted producers for ${itemLabel.toLowerCase()} ${index + 1}`}
              value={(producers[value] ?? []).join(', ')}
              placeholder="producer-a, producer-b"
              onChange={(event) =>
                onChange(values, { ...producers, [value]: csv(event.target.value) })
              }
            />
          </div>
          <button type="button" className={ACTION} onClick={() => remove(index)}>
            Remove {itemLabel.toLowerCase()}
          </button>
        </div>
      ))}
    </section>
  );
}

export function EvidencePolicyEditor({ policy, onChange }: EvidencePolicyEditorProps) {
  const value = policy ?? emptyEvidencePolicy();
  const resultContracts = stringList(value.required_result_contract_ids);
  const resultProducers = producerMap(value.result_producers);
  const reviewRoles = stringList(value.required_review_roles);
  const reviewProducers = producerMap(value.review_producers);
  const requiredChecks = stringList(value.required_check_names);
  const checkProducers = stringList(value.check_producers);

  return (
    <div className="niuu:flex niuu:flex-col niuu:gap-4 niuu:rounded-lg niuu:border niuu:border-border-subtle niuu:bg-bg-secondary niuu:p-3">
      <div>
        <div className={SECTION_LABEL}>Evidence requirements</div>
        <p className="niuu:mt-1 niuu:mb-0 niuu:text-[11px] niuu:leading-relaxed niuu:text-text-secondary">
          Verify signed receipts for the exact artifact above. Every required result contract and
          reviewer role must pin the producer IDs trusted to sign it.
        </p>
      </div>

      <RequirementRows
        title="Result contracts"
        itemLabel="Result contract"
        addLabel="result"
        values={resultContracts}
        producers={resultProducers}
        onChange={(required_result_contract_ids, result_producers) =>
          onChange({ ...value, required_result_contract_ids, result_producers })
        }
      />

      <RequirementRows
        title="Reviewer roles"
        itemLabel="Reviewer role"
        addLabel="role"
        values={reviewRoles}
        producers={reviewProducers}
        onChange={(required_review_roles, review_producers) =>
          onChange({ ...value, required_review_roles, review_producers })
        }
      />

      <section className="niuu:flex niuu:flex-col niuu:gap-2">
        <span className={SECTION_LABEL}>Checks</span>
        <label className="niuu:flex niuu:items-center niuu:gap-2 niuu:text-[12px] niuu:text-text-secondary">
          <input
            type="checkbox"
            aria-label="Require checks receipt"
            checked={value.require_checks === true}
            onChange={(event) => onChange({ ...value, require_checks: event.target.checked })}
          />
          Require a signed checks receipt
        </label>
        <div>
          <label className={SECTION_LABEL}>Named checks</label>
          <input
            className={INPUT}
            aria-label="Named checks"
            value={requiredChecks.join(', ')}
            placeholder="unit-tests, policy-scan"
            onChange={(event) =>
              onChange({ ...value, required_check_names: csv(event.target.value) })
            }
          />
        </div>
        <div>
          <label className={SECTION_LABEL}>Trusted check producer IDs</label>
          <input
            className={cn(INPUT, 'niuu:font-mono')}
            aria-label="Trusted check producers"
            value={checkProducers.join(', ')}
            placeholder="ci-primary, scanner-prod"
            onChange={(event) => onChange({ ...value, check_producers: csv(event.target.value) })}
          />
        </div>
      </section>
    </div>
  );
}
