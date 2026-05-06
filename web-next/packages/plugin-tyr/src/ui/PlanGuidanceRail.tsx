/**
 * PlanGuidanceRail — right-side guidance panel for the Plan wizard.
 * Shown on prompt, questions, and draft steps; hidden on raiding and approved.
 */

interface GuidanceCard {
  title: string;
  items: string[];
  numbered?: boolean;
}

const HOW_PLAN_WORKS: GuidanceCard = {
  title: 'How Plan works',
  numbered: true,
  items: [
    'Describe your goal in plain language — the more detail, the better the plan.',
    'Answer clarifying questions to help Tyr understand scope and constraints.',
    'Ravens raid the plan: they decompose it into phases and work items.',
    'Review the draft plan and approve or edit phases before committing.',
  ],
};

const WHAT_A_PLANNING_RAID_PRODUCES: GuidanceCard = {
  title: 'What a planning raid produces',
  items: [
    'Phased plan with clear phase names and goals',
    'Acceptance criteria for each work item',
    'File-level effort estimates',
    'Dependency graph across phases',
    'Risk and confidence assessment',
  ],
};

function GuidanceCard({ card }: { card: GuidanceCard }) {
  return (
    <div className="niuu-rounded-lg niuu-border niuu-border-border niuu-bg-bg-secondary niuu-p-4">
      <h3 className="niuu-m-0 niuu-mb-3 niuu-text-sm niuu-font-semibold niuu-text-text-primary">
        {card.title}
      </h3>
      <ul className="niuu-list-none niuu-p-0 niuu-m-0 niuu-flex niuu-flex-col niuu-gap-2">
        {card.items.map((item, i) => (
          <li key={i} className="niuu-flex niuu-gap-2 niuu-text-xs niuu-text-text-secondary">
            {card.numbered ? (
              <span
                className="niuu-inline-flex niuu-items-center niuu-justify-center niuu-w-4 niuu-h-4 niuu-rounded-full niuu-bg-brand niuu-text-bg-primary niuu-font-bold niuu-shrink-0"
                style={{ fontSize: 9 }}
              >
                {i + 1}
              </span>
            ) : (
              <span className="niuu-text-text-faint niuu-shrink-0">·</span>
            )}
            <span>{item}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

export function PlanGuidanceRail() {
  return (
    <aside
      className="niuu-flex niuu-flex-col niuu-gap-4 niuu-p-5"
      style={{ width: 280, flexShrink: 0 }}
      aria-label="Plan guidance"
    >
      <GuidanceCard card={HOW_PLAN_WORKS} />
      <GuidanceCard card={WHAT_A_PLANNING_RAID_PRODUCES} />
    </aside>
  );
}
