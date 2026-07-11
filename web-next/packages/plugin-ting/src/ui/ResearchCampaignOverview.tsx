import {
  cx,
  formatClock,
  formatElapsed,
  formatKiloWords,
  normalizeStageLabel,
  sentenceCase,
  statusDotClass,
} from './researchCampaignModel';
import {
  CitationPopover,
  ConfidenceBadge,
  InlineCitation,
  ResearchMarkdown,
} from './ResearchCampaignPresentation';
import type { ResearchCampaignPageController } from './useResearchCampaignPage';

export function ResearchCampaignOverview({
  controller,
}: {
  controller: ResearchCampaignPageController;
}) {
  const {
    navigate,
    campaign,
    deleteCampaign,
    viewMode,
    setViewMode,
    popoverCitation,
    setPopoverCitation,
    artifactByKind,
    parsedArtifacts,
    openMimirPage,
    sourceList,
    critiques,
    finalArtifact,
    manifestArtifact,
    workingThesis,
    derivedState,
    activeStage,
    confidence,
    selectedFilePath,
    visibleCitationSourceLabels,
    popoverSource,
    popoverCritique,
    openDrawer,
    handleDeleteCampaign,
  } = controller;
  return (
    <>
      <div className="ting-research-detail__crumbs-row">
        <div className="ting-research-detail__crumbs">
          <button
            type="button"
            className="ting-research-detail__crumb-link"
            onClick={() => void navigate({ to: '/ting/research' })}
          >
            Research
          </button>
          <span className="ting-research-detail__crumb-separator">›</span>
          <span className="ting-research-detail__crumb-slug">{campaign.slug}</span>
          <span className={cx('ting-research-detail__state-dot', statusDotClass(derivedState))} />
          <span className="ting-research-detail__crumb-state">
            {derivedState === 'review' ? 'review-ready' : sentenceCase(derivedState)}
          </span>
          <span className="ting-research-detail__mode-pill">
            {String(campaign.metadata.mode ?? 'exploratory')}
          </span>
        </div>
        <div className="ting-research-detail__page-actions">
          <button
            type="button"
            className={cx('ting-research-detail__files-chip', 'is-danger')}
            onClick={() => void handleDeleteCampaign()}
            disabled={deleteCampaign.isPending}
          >
            {deleteCampaign.isPending ? 'Deleting…' : 'Delete'}
          </button>
          <button
            type="button"
            className="ting-research-detail__files-chip"
            onClick={() => openDrawer({ tab: 'files', file: selectedFilePath })}
          >
            ☰ Files · {parsedArtifacts.length}
          </button>
        </div>
      </div>

      <h1 className="ting-research-detail__title">{campaign.name}</h1>
      <p className="ting-research-detail__question">{String(campaign.metadata.question ?? '')}</p>

      <div className="ting-research-detail__meta-line">
        <span>audience · {String(campaign.metadata.audience ?? 'unspecified')}</span>
        <span>deliverable · {String(campaign.metadata.deliverable ?? 'open synthesis')}</span>
        <span>updated {formatClock(campaign.updatedAt)}</span>
        <span>@{campaign.ownerId}</span>
      </div>

      {(derivedState === 'review' || derivedState === 'published') && finalArtifact ? (
        <div className="ting-research-detail__view-toggle">
          <span className="ting-research-detail__view-label">view:</span>
          <div className="ting-research-detail__segmented">
            <button
              type="button"
              className={cx(viewMode === 'clean' && 'is-active')}
              onClick={() => setViewMode('clean')}
            >
              Clean
            </button>
            <button
              type="button"
              className={cx(viewMode === 'annotated' && 'is-active')}
              onClick={() => setViewMode('annotated')}
            >
              Annotated
            </button>
          </div>
        </div>
      ) : null}

      <section className={cx('ting-research-detail__hero-card', `is-${derivedState}`)}>
        <div className="ting-research-detail__hero-main">
          <div className="ting-research-detail__hero-eyebrow">
            {derivedState === 'running'
              ? `Working thesis · stage ${Math.max(1, campaign.stageState.findIndex((stage) => stage.stageId === activeStage?.stageId) + 1)}/${campaign.stageState.length}`
              : derivedState === 'review'
                ? 'Review-ready · awaiting publish to Mímir'
                : derivedState === 'published'
                  ? 'Final synthesis · published'
                  : derivedState === 'blocked'
                    ? `Blocked at ${normalizeStageLabel(activeStage)}`
                    : derivedState === 'failed'
                      ? `Failed at ${normalizeStageLabel(activeStage)}`
                      : 'Draft · not yet dispatched'}
          </div>

          {derivedState === 'running' ||
          derivedState === 'blocked' ||
          derivedState === 'failed' ||
          derivedState === 'draft' ? (
            <>
              <div className="ting-research-detail__hero-heading">
                {derivedState === 'draft'
                  ? "This campaign hasn't been dispatched yet."
                  : derivedState === 'blocked'
                    ? 'Why we paused'
                    : derivedState === 'failed'
                      ? 'Failure'
                      : 'Tentative answer in one line.'}
              </div>
              <p className="ting-research-detail__hero-paragraph">
                {derivedState === 'blocked'
                  ? `${activeStage?.reason ?? 'The active persona cannot proceed until an operator resolves the current issue.'}`
                  : derivedState === 'failed'
                    ? `${activeStage?.reason ?? 'The most recent run ended before the final synthesis could be completed.'}`
                    : derivedState === 'draft'
                      ? 'Frame the question, pick a mode, and Ting will run the seven-stage research workflow.'
                      : workingThesis}
              </p>

              {derivedState === 'running' && critiques.length > 0 ? (
                <div className="ting-research-detail__challenge-callout">
                  <div className="ting-research-detail__challenge-title">
                    What&apos;s being challenged right now
                  </div>
                  <ul>
                    {critiques.slice(0, 3).map((critique) => (
                      <li key={critique.id}>
                        <InlineCitation
                          label={critique.citation}
                          kind="critique"
                          isActive={
                            popoverCitation?.kind === 'critique' &&
                            popoverCitation.key === critique.citation
                          }
                          onClick={(event) => {
                            const rect = event.currentTarget.getBoundingClientRect();
                            setPopoverCitation({
                              kind: 'critique',
                              key: critique.citation,
                              anchor: `critique-${critique.citation}`,
                              x: rect.left,
                              y: rect.bottom,
                            });
                          }}
                        />
                        <span>{critique.claim}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              ) : null}

              {(derivedState === 'running' ||
                derivedState === 'blocked' ||
                derivedState === 'failed') && (
                <p className="ting-research-detail__hero-note">
                  {derivedState === 'running'
                    ? "The synthesist will write the final answer after the challenge stage completes. The working thesis above is the explorer's best read so far."
                    : 'The notebook below captures what the campaign has learned so far.'}
                </p>
              )}
            </>
          ) : finalArtifact ? (
            <div
              className={cx(
                'ting-research-detail__rendered-answer',
                viewMode === 'annotated' && 'is-annotated',
              )}
            >
              <div className="ting-research-detail__rendered-answer-main">
                <div className="ting-research-detail__hero-heading">
                  Final synthesis — {campaign.name}
                </div>
                <ResearchMarkdown
                  content={finalArtifact.body}
                  activeCitation={popoverCitation}
                  onCitationClick={setPopoverCitation}
                  fallbackSourceLabels={visibleCitationSourceLabels}
                />
              </div>
              {viewMode === 'annotated' ? (
                <aside className="ting-research-detail__annotations">
                  {sourceList.slice(0, 4).map((source) => (
                    <div
                      key={source.id}
                      className="ting-research-detail__annotation-card is-source"
                    >
                      <div className="ting-research-detail__annotation-tag">
                        [{source.citation}]
                      </div>
                      <div className="ting-research-detail__annotation-title">{source.title}</div>
                      <div className="ting-research-detail__annotation-body">{source.excerpt}</div>
                      <button
                        type="button"
                        className="ting-research-detail__annotation-action"
                        onClick={() => openDrawer({ tab: 'sources', sourceId: source.id })}
                      >
                        open
                      </button>
                    </div>
                  ))}
                  {critiques.slice(0, 3).map((critique) => (
                    <div
                      key={critique.id}
                      className="ting-research-detail__annotation-card is-critique"
                    >
                      <div className="ting-research-detail__annotation-tag">
                        [{critique.citation}]
                      </div>
                      <div className="ting-research-detail__annotation-title">{critique.claim}</div>
                      <div className="ting-research-detail__annotation-body">{critique.note}</div>
                      <button
                        type="button"
                        className="ting-research-detail__annotation-action"
                        onClick={() => openDrawer({ tab: 'critiques', critiqueId: critique.id })}
                      >
                        open
                      </button>
                    </div>
                  ))}
                </aside>
              ) : null}
            </div>
          ) : (
            <p className="ting-research-detail__hero-paragraph">
              A synthesized answer will appear here once the campaign reaches the synthesis stage.
            </p>
          )}
        </div>

        <aside className="ting-research-detail__hero-side">
          <div className="ting-research-detail__hero-meta-row">
            <span className="ting-research-detail__hero-meta-label">
              {derivedState === 'running'
                ? 'Current stage'
                : derivedState === 'review'
                  ? 'Confidence'
                  : derivedState === 'published'
                    ? 'Final confidence'
                    : 'Status'}
            </span>
            {derivedState === 'running' ? (
              <span className="ting-research-detail__hero-meta-value is-stage">
                {normalizeStageLabel(activeStage)}
              </span>
            ) : derivedState === 'review' || derivedState === 'published' ? (
              <ConfidenceBadge percent={confidence.percent} label={confidence.label} />
            ) : (
              <span className="ting-research-detail__hero-meta-value">
                {sentenceCase(derivedState)}
              </span>
            )}
          </div>

          {derivedState === 'running' ? (
            <>
              <div className="ting-research-detail__hero-meta-row">
                <span className="ting-research-detail__hero-meta-label">Persona</span>
                <span className="ting-research-detail__hero-meta-value is-mono">
                  {normalizeStageLabel(activeStage).toLowerCase() === 'challenge'
                    ? 'research-skeptic'
                    : 'research-campaign'}
                </span>
              </div>
              <div className="ting-research-detail__hero-meta-row">
                <span className="ting-research-detail__hero-meta-label">Confidence so far</span>
                <ConfidenceBadge percent={confidence.percent} label={confidence.label} />
              </div>
              <button
                type="button"
                className="ting-research-detail__ghost-button"
                onClick={() =>
                  openDrawer({
                    tab: 'files',
                    file: artifactByKind.get('analysis')?.path ?? selectedFilePath,
                  })
                }
              >
                Open notebook
              </button>
            </>
          ) : null}

          {(derivedState === 'review' || derivedState === 'published') && finalArtifact ? (
            <>
              <div className="ting-research-detail__hero-meta-row">
                <span className="ting-research-detail__hero-meta-label">Synthesis size</span>
                <span className="ting-research-detail__hero-meta-value">
                  {formatKiloWords(finalArtifact.body)}
                </span>
              </div>
              <div className="ting-research-detail__hero-meta-row">
                <span className="ting-research-detail__hero-meta-label">Sources cited</span>
                <span className="ting-research-detail__hero-meta-value">{sourceList.length}</span>
              </div>
              <div className="ting-research-detail__hero-meta-row">
                <span className="ting-research-detail__hero-meta-label">Critiques addressed</span>
                <span className="ting-research-detail__hero-meta-value">{critiques.length}</span>
              </div>
              {derivedState === 'review' ? (
                <>
                  <button
                    type="button"
                    className="ting-research-detail__primary-button"
                    onClick={() =>
                      openDrawer({
                        tab: 'files',
                        file: manifestArtifact?.path ?? finalArtifact.path,
                      })
                    }
                  >
                    Publish to Mímir →
                  </button>
                  <button
                    type="button"
                    className="ting-research-detail__ghost-button"
                    onClick={() => openDrawer({ tab: 'operator', operatorSub: 'actions' })}
                  >
                    Send back for revision
                  </button>
                </>
              ) : (
                <>
                  <div className="ting-research-detail__hero-meta-row">
                    <span className="ting-research-detail__hero-meta-label">Published</span>
                    <span className="ting-research-detail__hero-meta-value">
                      {formatClock(campaign.completedAt)}
                    </span>
                  </div>
                  <div className="ting-research-detail__hero-meta-row">
                    <span className="ting-research-detail__hero-meta-label">Elapsed</span>
                    <span className="ting-research-detail__hero-meta-value">
                      {formatElapsed(campaign.createdAt, campaign.completedAt)}
                    </span>
                  </div>
                  <button
                    type="button"
                    className="ting-research-detail__ghost-button"
                    onClick={() => openMimirPage(finalArtifact.path)}
                  >
                    ᛗ open in Mímir
                  </button>
                </>
              )}
            </>
          ) : null}

          {derivedState === 'blocked' ? (
            <>
              <div className="ting-research-detail__hero-meta-row">
                <span className="ting-research-detail__hero-meta-label">At stage</span>
                <span className="ting-research-detail__hero-meta-value is-stage">
                  {normalizeStageLabel(activeStage)}
                </span>
              </div>
              <div className="ting-research-detail__hero-meta-row">
                <span className="ting-research-detail__hero-meta-label">Elapsed since block</span>
                <span className="ting-research-detail__hero-meta-value">
                  {formatElapsed(activeStage?.startedAt ?? campaign.updatedAt)}
                </span>
              </div>
              <button
                type="button"
                className="ting-research-detail__primary-button"
                onClick={() => openDrawer({ tab: 'operator', operatorSub: 'actions' })}
              >
                Resume after fix
              </button>
              <button
                type="button"
                className="ting-research-detail__ghost-button"
                onClick={() => openDrawer({ tab: 'operator', operatorSub: 'activity' })}
              >
                View blocking error
              </button>
            </>
          ) : null}

          {derivedState === 'failed' ? (
            <>
              <div className="ting-research-detail__hero-meta-row">
                <span className="ting-research-detail__hero-meta-label">At stage</span>
                <span className="ting-research-detail__hero-meta-value is-stage">
                  {normalizeStageLabel(activeStage)}
                </span>
              </div>
              <div className="ting-research-detail__hero-meta-row">
                <span className="ting-research-detail__hero-meta-label">Persona</span>
                <span className="ting-research-detail__hero-meta-value is-mono">
                  research-campaign
                </span>
              </div>
              <button
                type="button"
                className="ting-research-detail__primary-button"
                onClick={() => openDrawer({ tab: 'operator', operatorSub: 'actions' })}
              >
                Retry from stage
              </button>
              <button
                type="button"
                className="ting-research-detail__ghost-button"
                onClick={() => openDrawer({ tab: 'operator', operatorSub: 'activity' })}
              >
                View run logs
              </button>
            </>
          ) : null}

          {derivedState === 'draft' ? (
            <>
              <button
                type="button"
                className="ting-research-detail__primary-button"
                onClick={() => void navigate({ to: '/ting/research/new' })}
              >
                Complete brief → dispatch
              </button>
              <button type="button" className="ting-research-detail__ghost-button">
                Discard draft
              </button>
            </>
          ) : null}
        </aside>

        {popoverCitation ? (
          <div
            className="ting-research-detail__floating-popover"
            style={{
              top: `${popoverCitation.y + 14}px`,
              left: `${Math.max(24, popoverCitation.x - 12)}px`,
            }}
          >
            <CitationPopover
              citation={popoverCitation}
              source={popoverSource}
              critique={popoverCritique}
              onOpenDrawer={() => {
                if (popoverCitation.kind === 'source' && popoverSource) {
                  openDrawer({ tab: 'sources', sourceId: popoverSource.id });
                } else if (popoverCitation.kind === 'critique' && popoverCritique) {
                  openDrawer({ tab: 'critiques', critiqueId: popoverCritique.id });
                }
                setPopoverCitation(null);
              }}
              onClose={() => setPopoverCitation(null)}
            />
          </div>
        ) : null}
      </section>
    </>
  );
}
