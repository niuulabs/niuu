import type { ComponentType } from 'react';
import { ExternalLink } from 'lucide-react';
import { MarkdownContent } from '@niuulabs/ui';
import { diffLines, type ReviewRevision } from '../../domain/review';
import type { ReviewPanel } from '../../domain/work';

export interface ReviewPanelProps {
  panel: ReviewPanel;
  revision: ReviewRevision;
  /** The revision before this one, when the person wants to see what changed. */
  compareWith: ReviewRevision | null;
  pullRequestUrl: string | null;
}

function DocumentPanel({ panel, revision, compareWith }: ReviewPanelProps) {
  const text = panel.field ? revision.fields[panel.field] : undefined;
  if (!text) {
    return (
      <p role="alert" className="ting-review-panel__missing">
        The log of this run holds no {panel.title.toLowerCase()} for this revision.
      </p>
    );
  }
  const before = panel.field ? compareWith?.fields[panel.field] : undefined;
  if (before === undefined) return <MarkdownContent content={text} />;
  return (
    <pre className="ting-review-diff" aria-label={`Changes to ${panel.title}`}>
      {diffLines(before, text).map((line, index) => (
        <span key={`${index}:${line.text}`} className={`ting-review-diff__${line.kind}`}>
          {line.text || ' '}
        </span>
      ))}
    </pre>
  );
}

function PullRequestPanel({ pullRequestUrl, revision }: ReviewPanelProps) {
  return (
    <>
      {revision.summary ? <p>{revision.summary}</p> : null}
      {pullRequestUrl ? (
        <a href={pullRequestUrl} target="_blank" rel="noreferrer">
          Open pull request <ExternalLink aria-hidden="true" />
        </a>
      ) : (
        <p>This run has no pull request yet.</p>
      )}
    </>
  );
}

function SummaryPanel({ revision }: ReviewPanelProps) {
  return <p>{revision.summary || 'This step left no summary.'}</p>;
}

/** Panels a workflow can declare; a plugin adds a kind of review by adding one here. */
export const REVIEW_WIDGETS: Readonly<Record<string, ComponentType<ReviewPanelProps>>> = {
  document: DocumentPanel,
  pullRequest: PullRequestPanel,
  summary: SummaryPanel,
};

export function ReviewPanelSlot(props: ReviewPanelProps) {
  const Widget = REVIEW_WIDGETS[props.panel.widget];
  return (
    <section className="ting-review-panel" aria-label={props.panel.title}>
      <h2>{props.panel.title}</h2>
      {Widget ? (
        <Widget {...props} />
      ) : (
        <p role="alert" className="ting-review-panel__missing">
          This workflow needs a {props.panel.widget} panel, which this app does not have.
        </p>
      )}
    </section>
  );
}
