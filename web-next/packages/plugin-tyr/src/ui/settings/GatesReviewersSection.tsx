/**
 * Gates & Reviewers section — shows reviewer routing configuration.
 * Matches web2's `tyr.gates` section.
 */

interface Reviewer {
  email: string;
  routing: string;
}

const REVIEWERS: Reviewer[] = [
  { email: 'jonas@niuulabs.io', routing: 'all gates · auto-forward after 30m' },
  { email: 'oskar@niuulabs.io', routing: 'all gates · auto-forward after 30m' },
  { email: 'yngve@niuulabs.io', routing: 'all gates · auto-forward after 30m' },
];

interface ReviewerRowProps {
  reviewer: Reviewer;
}

function ReviewerRow({ reviewer }: ReviewerRowProps) {
  return (
    <div className="niuu-flex niuu-items-center niuu-justify-between niuu-py-2 niuu-border-b niuu-border-border-subtle">
      <span className="niuu-text-sm niuu-text-text-primary">{reviewer.email}</span>
      <span className="niuu-text-sm niuu-font-mono niuu-text-text-secondary">
        {reviewer.routing}
      </span>
    </div>
  );
}

export function GatesReviewersSection() {
  return (
    <section aria-label="Gates and reviewers">
      <h3 className="niuu-text-base niuu-font-semibold niuu-text-text-primary niuu-mb-1">
        Gates &amp; reviewers
      </h3>
      <p className="niuu-text-sm niuu-text-text-secondary niuu-mb-4">
        Who can approve gates in workflows. Routing rules.
      </p>

      <div className="niuu-max-w-lg" role="list" aria-label="Reviewer list">
        {REVIEWERS.map((reviewer) => (
          <div key={reviewer.email} role="listitem">
            <ReviewerRow reviewer={reviewer} />
          </div>
        ))}
      </div>
    </section>
  );
}
