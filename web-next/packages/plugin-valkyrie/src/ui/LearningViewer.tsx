import { Drawer, DrawerContent, Meter } from '@niuulabs/ui';
import type { ReactNode } from 'react';
import { useLearning } from '../application/useLearnings';
import { learningFeedbackVerdictLabel, type LearningRecord } from '../domain';

const SECTION_LABEL =
  'niuu:text-[11px] niuu:font-semibold niuu:uppercase niuu:tracking-[0.14em] niuu:text-text-muted';

const CHIP =
  'niuu:rounded-full niuu:bg-bg-tertiary niuu:px-2 niuu:py-0.5 niuu:text-[11px] niuu:text-text-secondary';

const LEARNING_VIEWER_WIDTH = 560;

function formatPercent(value: number): string {
  return `${Math.round(value * 100)}%`;
}

/** Human form of a learning status ("rolled_back" → "rolled back"). */
function statusLabel(status: string): string {
  return status.replace(/_/g, ' ');
}

/** The eyebrow above the title: the newest recorded lifecycle event type. */
export function learningEyebrow(learning: Pick<LearningRecord, 'history'>): string {
  const newest = [...(learning.history ?? [])].sort((a, b) =>
    b.observedAt.localeCompare(a.observedAt),
  )[0];
  return newest?.eventType || 'learning.recorded';
}

/** The correlation reference a learning carries, '' when it has none. */
export function learningCorrelation(
  learning: Pick<LearningRecord, 'sourceEvidence' | 'commandDelivery'>,
): string {
  const evidence = learning.sourceEvidence ?? {};
  for (const key of ['correlationId', 'correlation_id']) {
    const value = evidence[key];
    if (typeof value === 'string' && value) return value;
  }
  return learning.commandDelivery?.eventId ?? '';
}

function PayloadRow({
  label,
  value,
  testId,
}: {
  label: string;
  value: ReactNode;
  testId?: string;
}) {
  return (
    <div
      data-testid={testId}
      className="niuu:grid niuu:grid-cols-[140px_minmax(0,1fr)] niuu:gap-2 niuu:text-xs"
    >
      <span className="niuu:text-text-muted">{label}</span>
      <span className="niuu:min-w-0 niuu:break-words niuu:text-text-primary">{value}</span>
    </div>
  );
}

function ConfidenceMeter({ confidence }: { confidence: number }) {
  return (
    <span className="niuu:flex niuu:items-center niuu:gap-2">
      <Meter used={confidence} limit={1} className="niuu:w-24" />
      <span className="niuu:font-mono niuu:text-[11px] niuu:text-text-secondary">
        {formatPercent(confidence)}
      </span>
    </span>
  );
}

function StructuredPayload({ learning }: { learning: LearningRecord }) {
  return (
    <div>
      <div className={SECTION_LABEL}>structured payload</div>
      <div
        data-testid="learning-viewer-payload"
        className="niuu:mt-2 niuu:flex niuu:flex-col niuu:gap-1.5 niuu:rounded-md niuu:border niuu:border-solid niuu:border-border niuu:bg-bg-primary niuu:p-3"
      >
        <PayloadRow label="scope" value={learning.scope} />
        <PayloadRow label="status" value={statusLabel(learning.status)} />
        <PayloadRow label="source env" value={learning.sourceEnvironmentId} />
        <PayloadRow label="source valkyrie" value={learning.sourceValkyrieId} />
        <PayloadRow
          label="confidence"
          value={<ConfidenceMeter confidence={learning.confidence} />}
        />
        {learning.repetition != null ? (
          <PayloadRow
            label="repetition"
            value={`×${learning.repetition}`}
            testId="learning-viewer-repetition"
          />
        ) : null}
        <PayloadRow
          label="feedback"
          testId="learning-viewer-feedback"
          value={
            learning.feedback
              ? learningFeedbackVerdictLabel(learning.feedback.verdict).toLowerCase()
              : 'awaiting'
          }
        />
        <PayloadRow label="redaction" value={learning.redaction} />
        <PayloadRow label="evidence" value={learning.evaluation} />
      </div>
    </div>
  );
}

function EvidenceAndLinks({ learning }: { learning: LearningRecord }) {
  return (
    <div>
      <div className={SECTION_LABEL}>evidence &amp; links</div>
      <div
        data-testid="learning-viewer-evidence"
        className="niuu:mt-2 niuu:flex niuu:flex-col niuu:gap-1.5"
      >
        {learning.artifactPath ? (
          <PayloadRow
            label="artifact"
            value={<span className="niuu:font-mono">{learning.artifactPath}</span>}
          />
        ) : null}
        {learning.sourceSignalIds?.length ? (
          <PayloadRow
            label="source signals"
            value={
              <span className="niuu:flex niuu:flex-wrap niuu:gap-1.5">
                {learning.sourceSignalIds.map((signalId) => (
                  <span key={signalId} className={`${CHIP} niuu:font-mono`}>
                    {signalId}
                  </span>
                ))}
              </span>
            }
          />
        ) : null}
        <PayloadRow
          label="source valkyrie"
          value={<span className="niuu:font-mono">{learning.sourceValkyrieId}</span>}
        />
      </div>
    </div>
  );
}

/**
 * Right-hand drawer showing everything an operator needs to judge a learning:
 * lifecycle, structured payload, raw record, correlation, and evidence.
 */
export function LearningViewer({
  learningId,
  onClose,
}: {
  learningId: string | null;
  onClose: () => void;
}) {
  const { data: learning, isLoading, error } = useLearning(learningId);
  const correlation = learning ? learningCorrelation(learning) : '';

  return (
    <Drawer
      open={Boolean(learningId)}
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      <DrawerContent title={learning?.title ?? 'Learning record'} width={LEARNING_VIEWER_WIDTH}>
        <div
          data-testid="learning-viewer"
          className="niuu:flex niuu:flex-col niuu:gap-4 niuu:text-sm"
        >
          {isLoading ? (
            <p data-testid="learning-viewer-loading" className="niuu:text-text-muted">
              Loading learning…
            </p>
          ) : null}
          {error ? (
            <p
              role="alert"
              className="niuu:rounded-md niuu:border niuu:border-solid niuu:border-critical-bo niuu:bg-critical-bg niuu:p-2 niuu:text-xs niuu:text-critical"
            >
              {error instanceof Error ? error.message : 'Unable to load this learning'}
            </p>
          ) : null}
          {!isLoading && !error && learningId && !learning ? (
            <p data-testid="learning-viewer-missing" className="niuu:text-text-muted">
              Learning {learningId} is no longer available. It was referenced here but has since
              been retracted or superseded, so its record cannot be shown.
            </p>
          ) : null}
          {learning ? (
            <>
              <div data-testid="learning-viewer-header">
                <div className="niuu:font-mono niuu:text-[11px] niuu:uppercase niuu:tracking-[0.14em] niuu:text-brand">
                  {learningEyebrow(learning)}
                </div>
                <p className="niuu:mt-1 niuu:font-mono niuu:text-xs niuu:text-text-muted">
                  {learning.id}
                </p>
              </div>
              <div className="niuu:flex niuu:flex-wrap niuu:items-center niuu:gap-2">
                <span data-testid="learning-viewer-status" className={CHIP}>
                  {statusLabel(learning.status)}
                </span>
                <span data-testid="learning-viewer-scope" className={CHIP}>
                  {learning.scope}
                </span>
                <ConfidenceMeter confidence={learning.confidence} />
              </div>
              <p className="niuu:leading-6 niuu:text-text-primary">{learning.summary}</p>
              <StructuredPayload learning={learning} />
              <div>
                <div className={SECTION_LABEL}>raw json</div>
                <pre
                  data-testid="learning-viewer-json"
                  className="niuu:mt-2 niuu:max-h-64 niuu:overflow-auto niuu:rounded-md niuu:border niuu:border-solid niuu:border-border niuu:bg-bg-primary niuu:p-3 niuu:font-mono niuu:text-[11px] niuu:leading-5 niuu:text-text-secondary"
                >
                  {JSON.stringify(learning, null, 2)}
                </pre>
              </div>
              <div>
                <div className={SECTION_LABEL}>correlation</div>
                <p
                  data-testid="learning-viewer-correlation"
                  className="niuu:mt-2 niuu:font-mono niuu:text-xs niuu:text-text-secondary"
                >
                  {correlation || '—'}
                </p>
              </div>
              <EvidenceAndLinks learning={learning} />
            </>
          ) : null}
        </div>
      </DrawerContent>
    </Drawer>
  );
}
