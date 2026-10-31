import { useCallback, useLayoutEffect, useRef, type ComponentType } from 'react';
import { ExternalLink } from 'lucide-react';
import {
  ExternalLinkPreview,
  MarkdownContent,
  SandboxedPage,
  SessionDiffs,
  SessionFilePreview,
  SessionFilesWorkspace,
  SessionPage,
  type IFileSystemPort,
  type ReviewControls,
} from '@niuulabs/ui';
import { diffLines, moduleWidgetRef, type ReviewRevision } from '../../domain/review';
import type { ReviewPanel } from '../../domain/work';
import { useReviewWidget } from '../useWork';
import { markQuotes } from './markQuotes';

const NO_MARKS: readonly string[] = [];
const NO_FILES: readonly string[] = [];
const NO_CONTEXT: Readonly<Record<string, string | number>> = {};
const WORKSPACE_ROOT = '/workspace';

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
  /** Plain facts a page may read about this review. */
  pageContext?: Readonly<Record<string, string | number>>;
  /** Puts text from a page in front of the person; absent once there is nothing to add to. */
  onQuote?: (text: string) => void;
  onAsk?: (text: string) => void;
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
  const field = panel.source?.value;
  const text = field ? revision.fields[field] : undefined;
  if (!text) {
    return (
      <p role="alert" className="ting-review-panel__missing">
        The log of this run holds no {panel.title.toLowerCase()} for this revision.
      </p>
    );
  }
  const before = field ? compareWith?.fields[field] : undefined;
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

function FilePanel({ panel, filesystem, sessionId }: ReviewPanelProps) {
  if (!filesystem || !sessionId || !panel.source) {
    return (
      <p role="alert" className="ting-review-panel__missing">
        This app has no file browser connected.
      </p>
    );
  }
  return (
    <div className="ting-review-panel__file">
      <SessionFilePreview
        sessionId={sessionId}
        path={`${WORKSPACE_ROOT}/${panel.source.value}`}
        filesystem={filesystem}
      />
    </div>
  );
}

function LinkPanel({ panel }: ReviewPanelProps) {
  if (!panel.source) return null;
  return (
    <div className="ting-review-panel__file">
      <ExternalLinkPreview href={panel.source.value} name={panel.title} />
    </div>
  );
}

function PagePanel({
  panel,
  filesystem,
  sessionId,
  pageContext,
  onQuote,
  onAsk,
}: ReviewPanelProps) {
  if (!filesystem || !sessionId || !panel.source) {
    return (
      <p role="alert" className="ting-review-panel__missing">
        This app has no file browser connected.
      </p>
    );
  }
  return (
    <SessionPage
      sessionId={sessionId}
      path={panel.source.value}
      title={panel.title}
      context={pageContext ?? NO_CONTEXT}
      files={panel.files ?? NO_FILES}
      filesystem={filesystem}
      onQuote={onQuote}
      onAsk={onAsk}
    />
  );
}

function ModuleWidgetPanel({
  module,
  name,
  panel,
  revision,
  filesystem,
  sessionId,
  pageContext,
  onQuote,
  onAsk,
}: ReviewPanelProps & { module: string; name: string }) {
  const widget = useReviewWidget(module, name);
  const source = panel.source;
  const download = useCallback(
    async (path: string) => {
      if (!filesystem?.downloadFile || !sessionId) {
        throw new Error('This app has no file browser connected.');
      }
      const blob = await filesystem.downloadFile(sessionId, `${WORKSPACE_ROOT}/${path}`);
      return blob.arrayBuffer();
    },
    [filesystem, sessionId],
  );
  const readSource = useCallback(async () => {
    if (!source) return null;
    if (source.kind === 'file') return download(source.value);
    if (source.kind === 'outcome') return revision.fields[source.value] ?? null;
    return source.value;
  }, [download, revision.fields, source]);

  if (widget.isLoading) return <p role="status">Loading widget…</p>;
  if (!widget.data) {
    return (
      <p role="alert" className="ting-review-panel__missing">
        {widget.error instanceof Error
          ? widget.error.message
          : `Review widget ${module}/${name} could not be read.`}
      </p>
    );
  }
  return (
    <SandboxedPage
      key={revision.number}
      title={panel.title}
      html={widget.data.html}
      context={pageContext ?? NO_CONTEXT}
      files={panel.files ?? NO_FILES}
      readFile={download}
      readSource={readSource}
      onQuote={onQuote}
      onAsk={onAsk}
    />
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

/** The stock panels. Anything else a workflow names comes from a module. */
export const REVIEW_WIDGETS: Readonly<Record<string, ComponentType<ReviewPanelProps>>> = {
  document: DocumentPanel,
  diff: DiffPanel,
  files: FilesPanel,
  file: FilePanel,
  link: LinkPanel,
  page: PagePanel,
  pullRequest: PullRequestPanel,
  summary: SummaryPanel,
};

export function ReviewPanelSlot(props: ReviewPanelProps) {
  const Widget = REVIEW_WIDGETS[props.panel.widget];
  const moduleRef = moduleWidgetRef(props.panel.widget);
  return (
    <section className="ting-review-panel" aria-label={props.panel.title}>
      <h2>{props.panel.title}</h2>
      {Widget ? (
        <Widget {...props} />
      ) : moduleRef ? (
        <ModuleWidgetPanel {...props} {...moduleRef} />
      ) : (
        <p role="alert" className="ting-review-panel__missing">
          This workflow needs a {props.panel.widget} panel, which this app does not have.
        </p>
      )}
    </section>
  );
}
