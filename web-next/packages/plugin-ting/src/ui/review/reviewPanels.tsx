import { useLayoutEffect, useRef, type ComponentType } from 'react';
import { ExternalLink } from 'lucide-react';
import {
  MarkdownContent,
  SessionDiffs,
  SessionFilesWorkspace,
  type IFileSystemPort,
  type ReviewControls,
} from '@niuulabs/ui';
import { diffLines, type ReviewRevision } from '../../domain/review';
import type { ReviewPanel } from '../../domain/work';
import { markQuotes } from './markQuotes';

const NO_MARKS: readonly string[] = [];

export interface ReviewPanelProps {
  panel: ReviewPanel;
  revision: ReviewRevision;
  /** The revision before this one, when the person wants to see what changed. */
  compareWith: ReviewRevision | null;
  pullRequestUrl: string | null;
  /** The run's session, which diffs are read from; null once it has stopped. */
  chatEndpoint?: string | null;
  sessionId?: string;
  filesystem?: IFileSystemPort | null;
  /** Adds line comments where a panel can take them. */
  review?: ReviewControls;
  /** Passages to mark, from the note and the chat. */
  marks?: readonly string[];
  /** Opens the conversation a mark came from. */
  onMarkClick?: (quote: string) => void;
}

function MarkedDocument({
  text,
  marks,
  onMarkClick,
}: {
  text: string;
  marks: readonly string[];
  onMarkClick?: (quote: string) => void;
}) {
  const root = useRef<HTMLDivElement>(null);
  useLayoutEffect(
    () => (root.current ? markQuotes(root.current, marks, onMarkClick) : undefined),
    [marks, onMarkClick, text],
  );
  return (
    <div ref={root}>
      {/* Marks split rendered text, so new text renders from scratch. */}
      <MarkdownContent key={text} content={text} />
    </div>
  );
}

function DocumentPanel({ panel, revision, compareWith, marks, onMarkClick }: ReviewPanelProps) {
  const text = panel.field ? revision.fields[panel.field] : undefined;
  if (!text) {
    return (
      <p role="alert" className="ting-review-panel__missing">
        The log of this run holds no {panel.title.toLowerCase()} for this revision.
      </p>
    );
  }
  const before = panel.field ? compareWith?.fields[panel.field] : undefined;
  if (before === undefined) {
    return <MarkedDocument text={text} marks={marks ?? NO_MARKS} onMarkClick={onMarkClick} />;
  }
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

function DiffPanel({ chatEndpoint, review }: ReviewPanelProps) {
  if (!chatEndpoint) {
    return (
      <p role="alert" className="ting-review-panel__missing">
        The session of this run is stopped, so its changes cannot be read.
      </p>
    );
  }
  return (
    <div className="ting-review-panel__workspace">
      <SessionDiffs chatEndpoint={chatEndpoint} review={review} />
    </div>
  );
}

function FilesPanel({ filesystem, sessionId, review }: ReviewPanelProps) {
  if (!filesystem || !sessionId) {
    return (
      <p role="alert" className="ting-review-panel__missing">
        This app has no file browser connected.
      </p>
    );
  }
  return (
    <div className="ting-review-panel__workspace">
      <SessionFilesWorkspace sessionId={sessionId} filesystem={filesystem} review={review} />
    </div>
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
  diff: DiffPanel,
  files: FilesPanel,
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
