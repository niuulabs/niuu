import { cx, normalizeStageLabel, sentenceCase, stageTickClass } from './researchCampaignModel';
import { ConfidenceBadge } from './ResearchCampaignPresentation';
import type { ResearchCampaignPageController } from './useResearchCampaignPage';

export function ResearchCampaignHeader({ controller }: { controller: ResearchCampaignPageController }) {
  const {
    campaign,
    derivedState,
    activeStage,
    confidence,
    latestActivity,
    sessionsCount,
    openDrawer,
  } = controller;
  return (
    <header className="ting-research-detail__state-strip">
      <div className="ting-research-detail__state-cluster">
        <div className="ting-research-detail__ticks" aria-label="Stage progress">
          {campaign.stageState.map((stage) => (
            <span
              key={stage.stageId}
              className={cx('ting-research-detail__tick', stageTickClass(stage.status))}
              title={`${stage.label} · ${stage.status}`}
            />
          ))}
        </div>
        <div className="ting-research-detail__strip-copy">
          stage{' '}
          {Math.max(
            1,
            campaign.stageState.findIndex((stage) => stage.stageId === activeStage?.stageId) + 1,
          )}
          /{campaign.stageState.length} · {normalizeStageLabel(activeStage)}
        </div>
      </div>

      <div className="ting-research-detail__ticker">
        {derivedState === 'running' && latestActivity ? (
          <>
            <span className="ting-research-detail__live-dot" />
            <span className="ting-research-detail__ticker-persona">
              {String(
                latestActivity.data.persona ?? latestActivity.data.raven ?? 'research-campaign',
              )}
            </span>
            <span className="ting-research-detail__ticker-message">
              {String(latestActivity.data.summary ?? latestActivity.event)}
            </span>
          </>
        ) : (
          <>
            <span className="ting-research-detail__live-dot is-idle" />
            <span className="ting-research-detail__ticker-persona">
              {derivedState === 'review' ? 'review-ready' : sentenceCase(derivedState)}
            </span>
            <span className="ting-research-detail__ticker-message">
              {derivedState === 'published'
                ? 'publisher committed the durable memory set'
                : derivedState === 'review'
                  ? 'publisher idle'
                  : derivedState === 'blocked'
                    ? 'campaign is waiting on an operator decision'
                    : derivedState === 'failed'
                      ? 'latest run ended in failure'
                      : 'working through the research graph'}
            </span>
          </>
        )}
      </div>

      <div className="ting-research-detail__state-cluster is-right">
        <ConfidenceBadge percent={confidence.percent} label={confidence.label} />
        <button
          type="button"
          className="ting-research-detail__run-chip"
          onClick={() =>
            window.location.assign(`/volundr/sessions/${encodeURIComponent(campaign.sessionId)}`)
          }
        >
          ‹ run/{campaign.sessionName || campaign.slug} · {sessionsCount}{' '}
          {sessionsCount === 1 ? 'session' : 'sessions'}
        </button>
        <button
          type="button"
          className="ting-research-detail__operator-button"
          onClick={() => openDrawer({ tab: 'operator', operatorSub: 'activity' })}
        >
          Operator ▸
        </button>
      </div>
    </header>
  );
}
