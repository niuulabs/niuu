import { Drawer, DrawerContent, Meter } from '@niuulabs/ui';
import { Pencil } from 'lucide-react';
import { useState } from 'react';
import type { ReactNode } from 'react';
import {
  useLearning,
  useReviseLearning,
  useSendLearningFeedback,
} from '../application/useLearnings';
import {
  adjacentLearningScopes,
  learningFeedbackVerdictLabel,
  LEARNING_FEEDBACK_VERDICTS,
  type LearningFeedbackVerdict,
  type LearningRecord,
  type LearningScope,
} from '../domain';
import type { LearningRevisionResult } from '../ports';
import { errorMessage } from './copy';

const OPERATOR_ID = 'human:operator';

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

const FEEDBACK_BUTTON =
  'niuu:rounded-md niuu:border niuu:border-solid niuu:border-border niuu:bg-bg-tertiary ' +
  'niuu:px-3 niuu:py-1.5 niuu:text-xs niuu:text-text-primary ' +
  'niuu:hover:border-brand/70 niuu:disabled:cursor-not-allowed niuu:disabled:opacity-50';

/**
 * The five operator verdicts as a button grid. Wrong tier expands an inline
 * picker limited to the scopes adjacent to the learning's current tier —
 * the backend refuses anything else.
 */
function FeedbackSection({ learning }: { learning: LearningRecord }) {
  const sendFeedback = useSendLearningFeedback();
  const [pickingScope, setPickingScope] = useState(false);
  const adjacent = adjacentLearningScopes(learning.scope);

  const submit = (verdict: LearningFeedbackVerdict, targetScope?: LearningScope) => {
    setPickingScope(false);
    sendFeedback.mutate({
      learningId: learning.id,
      verdict,
      operatorId: OPERATOR_ID,
      ...(targetScope ? { targetScope } : {}),
    });
  };

  return (
    <div data-testid="learning-feedback">
      <div className={SECTION_LABEL}>feedback</div>
      <div className="niuu:mt-2 niuu:flex niuu:flex-wrap niuu:gap-2">
        {LEARNING_FEEDBACK_VERDICTS.map(({ verdict, label }) => (
          <button
            key={verdict}
            type="button"
            data-testid={`learning-feedback-${verdict}`}
            disabled={sendFeedback.isPending}
            aria-pressed={
              verdict === 'wrong_tier' ? pickingScope : learning.feedback?.verdict === verdict
            }
            onClick={() => {
              if (verdict === 'wrong_tier') {
                setPickingScope((value) => !value);
                return;
              }
              submit(verdict);
            }}
            className={`${FEEDBACK_BUTTON} ${
              learning.feedback?.verdict === verdict
                ? 'niuu:border-brand niuu:bg-brand/10 niuu:text-brand'
                : ''
            }`}
          >
            {label}
          </button>
        ))}
      </div>
      {pickingScope ? (
        <div
          data-testid="learning-wrongtier-picker"
          className="niuu:mt-2 niuu:rounded-md niuu:border niuu:border-solid niuu:border-border niuu:bg-bg-primary niuu:p-3"
        >
          <p className="niuu:text-xs niuu:text-text-muted">
            Move this learning to an adjacent tier (currently {learning.scope}):
          </p>
          <div className="niuu:mt-2 niuu:flex niuu:flex-wrap niuu:gap-2">
            {adjacent.map((scope) => (
              <button
                key={scope}
                type="button"
                data-testid={`learning-wrongtier-scope-${scope}`}
                disabled={sendFeedback.isPending}
                onClick={() => submit('wrong_tier', scope)}
                className={FEEDBACK_BUTTON}
              >
                {scope}
              </button>
            ))}
          </div>
        </div>
      ) : null}
      {sendFeedback.isError ? (
        <p
          role="alert"
          data-testid="learning-feedback-error"
          className="niuu:mt-2 niuu:rounded-md niuu:border niuu:border-solid niuu:border-critical-bo niuu:bg-critical-bg niuu:p-2 niuu:text-xs niuu:text-critical"
        >
          {errorMessage(sendFeedback.error, 'Recording feedback failed')}
        </p>
      ) : null}
    </div>
  );
}

const EDIT_INPUT =
  'niuu:mt-1 niuu:w-full niuu:rounded-md niuu:border niuu:border-solid niuu:border-border ' +
  'niuu:bg-bg-secondary niuu:p-2 niuu:font-sans niuu:text-sm niuu:normal-case ' +
  'niuu:tracking-normal niuu:text-text-primary';

const EDIT_LABEL = `niuu:block niuu:text-[11px] niuu:font-semibold niuu:uppercase niuu:tracking-[0.14em] niuu:text-text-muted`;

/**
 * Operator edit flow. Saving calls reviseLearning: candidates update in
 * place; adopted/canary learnings spawn a NEW superseding candidate — the
 * original stays installed until the candidate passes review — and the
 * drawer switches to that new record via `onSaved`.
 */
function EditSection({
  learning,
  onSaved,
}: {
  learning: LearningRecord;
  onSaved: (result: LearningRevisionResult) => void;
}) {
  const revise = useReviseLearning();
  const [editing, setEditing] = useState(false);
  const [title, setTitle] = useState(learning.title);
  const [summary, setSummary] = useState(learning.summary);
  const [content, setContent] = useState(learning.artifactContent ?? '');
  const [reason, setReason] = useState('');

  const closeEditor = () => {
    setEditing(false);
    setTitle(learning.title);
    setSummary(learning.summary);
    setContent(learning.artifactContent ?? '');
    setReason('');
  };

  if (!editing) {
    return (
      <button
        type="button"
        data-testid="learning-edit"
        onClick={() => setEditing(true)}
        className={`${FEEDBACK_BUTTON} niuu:flex niuu:items-center niuu:gap-1.5 niuu:self-start`}
      >
        <Pencil size={12} aria-hidden="true" />
        Edit learning
      </button>
    );
  }

  return (
    <form
      data-testid="learning-edit-form"
      className="niuu:flex niuu:flex-col niuu:gap-3 niuu:rounded-md niuu:border niuu:border-solid niuu:border-border niuu:bg-bg-primary niuu:p-3"
      onSubmit={(event) => {
        event.preventDefault();
        const trimmedReason = reason.trim();
        if (!trimmedReason) return;
        revise.mutate(
          {
            learningId: learning.id,
            title,
            summary,
            ...(content ? { content } : {}),
            reason: trimmedReason,
            operatorId: OPERATOR_ID,
          },
          {
            onSuccess: (result) => {
              setEditing(false);
              setReason('');
              onSaved(result);
            },
          },
        );
      }}
    >
      <label className={EDIT_LABEL}>
        title
        <input
          aria-label="Learning title"
          value={title}
          onChange={(event) => setTitle(event.target.value)}
          className={EDIT_INPUT}
        />
      </label>
      <label className={EDIT_LABEL}>
        summary
        <textarea
          aria-label="Learning summary"
          value={summary}
          rows={3}
          onChange={(event) => setSummary(event.target.value)}
          className={EDIT_INPUT}
        />
      </label>
      <label className={EDIT_LABEL}>
        content
        <textarea
          aria-label="Learning content"
          value={content}
          rows={5}
          onChange={(event) => setContent(event.target.value)}
          className={`${EDIT_INPUT} niuu:font-mono niuu:text-xs`}
        />
      </label>
      <label className={EDIT_LABEL}>
        reason (required)
        <input
          aria-label="Reason for this revision"
          data-testid="learning-edit-reason"
          value={reason}
          placeholder="Why this record needed correcting"
          onChange={(event) => setReason(event.target.value)}
          className={EDIT_INPUT}
        />
      </label>
      <div className="niuu:flex niuu:items-center niuu:gap-2">
        <button
          type="submit"
          data-testid="learning-edit-save"
          disabled={revise.isPending || !reason.trim()}
          className={FEEDBACK_BUTTON}
        >
          {revise.isPending ? 'Saving…' : 'Save'}
        </button>
        <button
          type="button"
          data-testid="learning-edit-cancel"
          onClick={closeEditor}
          className="niuu:text-xs niuu:text-text-muted"
        >
          Cancel
        </button>
      </div>
      {revise.isError ? (
        <p
          role="alert"
          data-testid="learning-edit-error"
          className="niuu:rounded-md niuu:border niuu:border-solid niuu:border-critical-bo niuu:bg-critical-bg niuu:p-2 niuu:text-xs niuu:text-critical"
        >
          {errorMessage(revise.error, 'Saving the revision failed')}
        </p>
      ) : null}
    </form>
  );
}

/**
 * Right-hand drawer showing everything an operator needs to judge a learning:
 * lifecycle, structured payload, raw record, correlation, and evidence.
 */
export function LearningViewer({
  learningId,
  onClose,
  onNavigate,
}: {
  learningId: string | null;
  onClose: () => void;
  /** Switch the drawer to another record (e.g. a superseding candidate). */
  onNavigate?: (learningId: string) => void;
}) {
  const { data: learning, isLoading, error } = useLearning(learningId);
  const correlation = learning ? learningCorrelation(learning) : '';
  // The supersede notice is pinned to the record it announces so it never
  // lingers when the operator opens an unrelated learning.
  const [notice, setNotice] = useState<{ learningId: string; message: string } | null>(null);

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
          {notice && notice.learningId === learningId ? (
            <p
              data-testid="learning-supersede-notice"
              className="niuu:rounded-md niuu:border niuu:border-solid niuu:border-brand/60 niuu:bg-brand/10 niuu:p-2 niuu:text-xs niuu:text-brand"
            >
              {notice.message}
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
              <FeedbackSection learning={learning} />
              <EditSection
                key={learning.id}
                learning={learning}
                onSaved={(result) => {
                  if (!result.supersededId) return;
                  setNotice({
                    learningId: result.learning.id,
                    message:
                      `Created superseding candidate ${result.learning.id} — the original ` +
                      'stays installed until it passes review',
                  });
                  onNavigate?.(result.learning.id);
                }}
              />
            </>
          ) : null}
        </div>
      </DrawerContent>
    </Drawer>
  );
}
