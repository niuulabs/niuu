import { useEffect, useRef } from 'react';
import {
  errorMessage,
  progressPercent,
  type ApplyStatus,
  type ModelTestResult,
  type Progress,
} from '../domain/setup';
import { AlertIcon, CheckIcon } from './icons';
import { useTestModel } from './hooks';

const GB = 1000 ** 3;

export function formatGb(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return '0 GB';
  return `${(bytes / GB).toFixed(bytes >= 10 * GB ? 0 : 1)} GB`;
}

/** The apply's own progress while it runs, else the local model's. */
export function currentProgress(status: ApplyStatus | undefined): Progress | null {
  if (!status) return null;
  if (status.state === 'applying' && status.progress) return status.progress;
  return status.vllm?.progress ?? null;
}

const PHASE_TITLES: Record<string, string> = {
  pulling: 'Pulling the vLLM image',
  starting: 'Starting',
  downloading: 'Downloading the model',
  loading: 'Loading the model',
  warming: 'Warming up the GPU',
  serving: 'Almost there',
};

export interface LocalModelCardProps {
  status: ApplyStatus | undefined;
  /** True while the platform restarts and the poll gets no answer. */
  reconnecting?: boolean;
  /** Run the quick test on its own the first time the model reports ready. */
  autoTest?: boolean;
}

/**
 * What the local model is doing right now, with a bar when the size is known,
 * and a one-line test once it serves. Used on the Finish step during the
 * apply and on the Ready page while the model keeps downloading afterwards.
 */
export function LocalModelCard({
  status,
  reconnecting = false,
  autoTest = true,
}: LocalModelCardProps) {
  const test = useTestModel();
  const testedRef = useRef(false);
  const vllm = status?.vllm ?? null;
  const ready = vllm?.state === 'ready';

  useEffect(() => {
    if (!autoTest || !ready || testedRef.current) return;
    testedRef.current = true;
    test.mutate();
    // The mutation object is stable for the life of the card.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoTest, ready]);

  if (!status) return null;
  const progress = currentProgress(status);
  const percent = progressPercent(progress);
  const failed = status.state === 'failed' || vllm?.state === 'failed';
  const title = failed
    ? 'Local model failed'
    : ready
      ? 'Local model is serving'
      : reconnecting
        ? 'Restarting the platform'
        : (progress && PHASE_TITLES[progress.phase]) || 'Local model starting';
  const detail = failed
    ? status.state === 'failed'
      ? status.detail
      : (vllm?.detail ?? '')
    : reconnecting
      ? 'Waiting for it to answer again…'
      : (progress?.detail ?? vllm?.detail ?? status.detail);
  const result: ModelTestResult | undefined = test.data;

  return (
    <div className="setup-card" data-testid="setup-local-model">
      <div className="setup-card__head">
        <div>
          <h3 className="setup-card__title" data-testid="setup-local-model-title">
            {title}
          </h3>
          <p className="setup-card__desc" data-testid="setup-local-model-detail">
            {detail}
          </p>
        </div>
        {ready ? (
          <span className="setup-chip setup-chip--ok">
            <CheckIcon size={12} /> Ready
          </span>
        ) : failed ? (
          <span className="setup-chip setup-chip--fail">Failed</span>
        ) : (
          <span className="setup-chip setup-chip--brand">
            {percent === null ? 'Working…' : `${percent}%`}
          </span>
        )}
      </div>
      {!ready && !failed ? (
        <div
          className={`setup-meter ${percent === null ? 'setup-meter--busy' : ''}`}
          role="progressbar"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={percent ?? undefined}
          data-testid="setup-local-model-bar"
        >
          <span className="setup-meter__fill" style={{ width: `${percent ?? 30}%` }} />
        </div>
      ) : null}
      {!ready && !failed && progress && progress.totalBytes > 0 && progress.phase !== 'loading' ? (
        <div className="setup-legend">
          <div className="setup-legend__row">
            <span>{PHASE_TITLES[progress.phase] ?? progress.phase}</span>
            <span className="setup-legend__value">
              {formatGb(progress.completedBytes)} of {formatGb(progress.totalBytes)}
            </span>
          </div>
        </div>
      ) : null}
      {ready ? (
        <div className="setup-form__actions" data-testid="setup-local-model-test">
          {test.isPending ? (
            <span className="setup-note">Asking the model for one word…</span>
          ) : result ? (
            <span
              className={`setup-note ${result.ok ? '' : 'setup-note--warn'}`}
              data-testid="setup-local-model-result"
            >
              {result.ok ? <CheckIcon size={13} /> : <AlertIcon size={13} />}{' '}
              {result.ok
                ? `Asked for one word, it answered "${result.reply.slice(0, 40)}" in ${(result.latencyMs / 1000).toFixed(1)} s`
                : `No answer: ${result.detail}`}
            </span>
          ) : test.error ? (
            <span className="setup-note setup-note--warn" role="alert">
              <AlertIcon size={13} /> {errorMessage(test.error)}
            </span>
          ) : null}
          <button
            type="button"
            className="setup-btn"
            onClick={() => test.mutate()}
            disabled={test.isPending}
            data-testid="setup-local-model-test-btn"
          >
            {result || test.error ? 'Test again' : 'Test the model'}
          </button>
        </div>
      ) : null}
    </div>
  );
}
