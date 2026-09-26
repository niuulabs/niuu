import { AlertTriangle, Check, Info, Pause, Play, RotateCw } from 'lucide-react';
import type { Ravn } from '../../domain/ravn';
import { canRestartResident, canResumeResident } from '../../domain/residentActions';
import {
  describeCondition,
  failingCondition,
  needsAttention,
  ravnLifeState,
  ravnTarget,
} from '../../application/ravnWorkbench';

export interface HealthBannerProps {
  ravn: Ravn;
  onLifecycle: (action: 'restart' | 'resume') => void;
  onShowActivity: () => void;
  lifecyclePending: boolean;
}

function Progress({ ravn }: { ravn: Ravn }) {
  const pending = ravn.observedState === 'pending';
  return (
    <div className="rw-steps" aria-label="Deployment progress">
      <span data-step={pending ? 'now' : 'done'}>
        {!pending && <Check size={12} aria-hidden="true" />} Accepted
      </span>
      <span className="rw-steps__rule" />
      <span data-step={pending ? 'next' : 'now'}>Deploying</span>
      <span className="rw-steps__rule" />
      <span data-step="next">Running</span>
    </div>
  );
}

/**
 * Why a ravn is not simply running, said once at the top of its page with the
 * backend's own condition and the fix next to it.
 */
export function HealthBanner({
  ravn,
  onLifecycle,
  onShowActivity,
  lifecyclePending,
}: HealthBannerProps) {
  const state = ravnLifeState(ravn);
  const target = ravnTarget(ravn);
  const showLogs = Boolean(ravn.managed && ravn.capabilities?.includes('logs'));

  if (state === 'failed' || needsAttention(ravn)) {
    const condition = failingCondition(ravn);
    const title =
      state === 'failed'
        ? `Not running — ${condition ? describeCondition(condition).toLowerCase() : 'no reason reported'}`
        : `Running with a failing check — ${condition ? describeCondition(condition).toLowerCase() : ''}`;
    return (
      <div className="rw-banner" role="alert" data-testid="ravn-health-banner" data-tone="critical">
        <span className="rw-banner__icon">
          <AlertTriangle size={18} aria-hidden="true" />
        </span>
        <div>
          <strong className="rw-banner__title">{title}</strong>
          <p className="rw-banner__text">
            {target} reports this {ravn.engine ?? ''} runtime as {ravn.observedState ?? ravn.status}
            {ravn.desiredState === 'running' && ' while it is meant to be running'}.
          </p>
          {condition?.message && (
            <code className="rw-banner__raw">
              {condition.type}: {condition.message}
            </code>
          )}
          {!condition && (
            <p className="rw-banner__text">
              The backend reported no failing condition{showLogs ? ' — the logs may say more' : ''}.
            </p>
          )}
        </div>
        <div className="rw-banner__acts">
          {showLogs && (
            <button type="button" className="rw-btn" onClick={onShowActivity}>
              View logs
            </button>
          )}
          {canRestartResident(ravn) && (
            <button
              type="button"
              className="rw-btn rw-btn--primary"
              onClick={() => onLifecycle('restart')}
              disabled={lifecyclePending}
              data-testid="ravn-banner-restart"
            >
              <RotateCw size={14} aria-hidden="true" />
              {lifecyclePending ? 'Restarting…' : 'Restart'}
            </button>
          )}
        </div>
      </div>
    );
  }

  if (state === 'starting' || state === 'removing') {
    return (
      <div
        className="rw-banner"
        role="status"
        data-testid="ravn-health-banner"
        data-tone="progress"
      >
        <span className="rw-banner__icon">
          <Info size={18} aria-hidden="true" />
        </span>
        <div>
          <strong className="rw-banner__title">
            {state === 'starting' ? `Starting on ${target}` : `Removing from ${target}`}
          </strong>
          {state === 'starting' ? (
            <Progress ravn={ravn} />
          ) : (
            <p className="rw-banner__text">The backend is tearing down its resources.</p>
          )}
        </div>
        <div className="rw-banner__acts">
          {showLogs && state === 'starting' && (
            <button type="button" className="rw-btn" onClick={onShowActivity}>
              Watch logs
            </button>
          )}
        </div>
      </div>
    );
  }

  if (state === 'suspended') {
    return (
      <div className="rw-banner" role="status" data-testid="ravn-health-banner" data-tone="quiet">
        <span className="rw-banner__icon">
          <Pause size={18} aria-hidden="true" />
        </span>
        <div>
          <strong className="rw-banner__title">Suspended</strong>
          <p className="rw-banner__text">Nothing runs while it is suspended.</p>
        </div>
        <div className="rw-banner__acts">
          {canResumeResident(ravn) && (
            <button
              type="button"
              className="rw-btn rw-btn--primary"
              onClick={() => onLifecycle('resume')}
              disabled={lifecyclePending}
              data-testid="ravn-banner-resume"
            >
              <Play size={14} aria-hidden="true" />
              {lifecyclePending ? 'Resuming…' : 'Resume'}
            </button>
          )}
        </div>
      </div>
    );
  }

  return null;
}
