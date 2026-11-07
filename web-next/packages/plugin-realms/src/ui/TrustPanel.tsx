import { SectionCard } from '@niuulabs/plugin-volundr';
import { SegmentedFilter } from '@niuulabs/ui';
import { useCreateTrustGrant } from '../application/useRealmGovernance';
import {
  ACTION_CLASSES,
  ACTION_CLASS_COPY,
  TRUST_LEVEL_FOR,
  trustSettingForLevel,
  type ActionClass,
  type RealmView,
  type TrustSetting,
} from '../domain/realm';

const TRUST_OPTIONS: Array<{ value: TrustSetting; label: string }> = [
  { value: 'auto', label: 'on its own' },
  { value: 'ask', label: 'asks first' },
  { value: 'never', label: 'never' },
];

function TrustRung({
  slug,
  actionClass,
  level,
}: {
  slug: string;
  actionClass: ActionClass;
  level: number | null;
}) {
  const create = useCreateTrustGrant(slug);
  const current = trustSettingForLevel(level);
  return (
    <div className="niuu:flex niuu:items-center niuu:gap-3 niuu:border-b niuu:border-border-subtle niuu:py-2">
      <span className="niuu:w-16 niuu:font-mono niuu:text-xs niuu:text-text-primary">
        {actionClass}
      </span>
      <span className="niuu:flex-1 niuu:text-xs niuu:text-text-muted">
        {ACTION_CLASS_COPY[actionClass]}
      </span>
      <SegmentedFilter<TrustSetting>
        options={TRUST_OPTIONS.filter((option) => option.value !== 'never' || current === 'never')}
        value={current}
        aria-label={`${actionClass} trust`}
        onChange={(value) => {
          const nextLevel = TRUST_LEVEL_FOR[value];
          if (nextLevel === null || nextLevel === level) return;
          create.mutate({ action_class: actionClass, target: '*', level: nextLevel, limits: {} });
        }}
      />
      {create.error ? (
        <span className="niuu:text-xs niuu:text-critical-fg">{String(create.error)}</span>
      ) : null}
    </div>
  );
}

/** What the workspace may do on its own, one rung per action class. */
export function TrustPanel({ slug, view }: { slug: string; view: RealmView }) {
  const levelFor = (actionClass: ActionClass) =>
    view.grants.find((grant) => grant.actionClass === actionClass)?.level ?? null;
  return (
    <SectionCard
      title="What it may do on its own"
      description="A change adds a new grant; the latest one per action counts."
    >
      <div className="niuu:flex niuu:flex-col" data-testid="trust-panel">
        {ACTION_CLASSES.map((actionClass) => (
          <TrustRung
            key={actionClass}
            slug={slug}
            actionClass={actionClass}
            level={levelFor(actionClass)}
          />
        ))}
      </div>
    </SectionCard>
  );
}
