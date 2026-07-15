import { citationToken, cx, drawerSectionCountLabel } from './researchCampaignModel';
import { QualityDots, ResearchSection } from './ResearchCampaignPresentation';
import type { ResearchCampaignPageController } from './useResearchCampaignPage';

export function ResearchCampaignSections({
  controller,
}: {
  controller: ResearchCampaignPageController;
}) {
  const {
    sourceList,
    critiques,
    learningsArtifact,
    followupsArtifact,
    selectedSource,
    selectedCritique,
    learnings,
    followups,
    memoryGroups,
    evidenceOpen,
    setEvidenceOpenOverride,
    skepticOpen,
    setSkepticOpenOverride,
    memoryOpen,
    setMemoryOpenOverride,
    learningsOpen,
    setLearningsOpenOverride,
    openDrawer,
  } = controller;
  return (
    <>
      <ResearchSection
        title="Evidence"
        meta={drawerSectionCountLabel(sourceList.length, 'source cited', 'sources cited')}
        open={evidenceOpen}
        onToggle={() => setEvidenceOpenOverride((value) => !(value ?? evidenceOpen))}
        actionLabel="Open all in side panel →"
        onAction={() => openDrawer({ tab: 'sources', sourceId: selectedSource?.id })}
      >
        <div className="ting-research-detail__table">
          <div className="ting-research-detail__table-head">
            <span />
            <span>Title</span>
            <span>Domain</span>
            <span>Quality</span>
            <span>Cited</span>
            <span />
          </div>
          {sourceList.map((source) => (
            <button
              type="button"
              key={source.id}
              className="ting-research-detail__table-row"
              onClick={() => openDrawer({ tab: 'sources', sourceId: source.id })}
            >
              <span className="ting-research-detail__mono-pill">
                {citationToken(source.citation)}
              </span>
              <span className="ting-research-detail__row-title">{source.title}</span>
              <span className="ting-research-detail__row-muted">{source.domain}</span>
              <QualityDots score={source.quality} />
              <span className="ting-research-detail__row-muted">×{source.citedCount}</span>
              <span className="ting-research-detail__row-open">ᛗ open</span>
            </button>
          ))}
        </div>
      </ResearchSection>

      <ResearchSection
        title="Skeptic's pass"
        meta={`${critiques.length} ${critiques.length === 1 ? 'challenge' : 'challenges'}`}
        open={skepticOpen}
        onToggle={() => setSkepticOpenOverride((value) => !(value ?? skepticOpen))}
        actionLabel="Open all in side panel →"
        onAction={() => openDrawer({ tab: 'critiques', critiqueId: selectedCritique?.id })}
      >
        <div className="ting-research-detail__critique-list">
          {critiques.map((critique) => (
            <button
              type="button"
              key={critique.id}
              className={cx('ting-research-detail__critique-card', `is-${critique.severity}`)}
              onClick={() => openDrawer({ tab: 'critiques', critiqueId: critique.id })}
            >
              <div className="ting-research-detail__critique-top">
                <span className="ting-research-detail__mono-pill">
                  {citationToken(critique.citation)}
                </span>
                <span className="ting-research-detail__critique-claim">{critique.claim}</span>
                <span
                  className={cx('ting-research-detail__severity-pill', `is-${critique.severity}`)}
                >
                  {critique.severity}
                </span>
              </div>
              <div className="ting-research-detail__critique-against">
                against: {critique.against}
              </div>
              <div className="ting-research-detail__critique-note">{critique.note}</div>
            </button>
          ))}
        </div>
      </ResearchSection>

      {(learnings.length > 0 || followups.length > 0) && (
        <ResearchSection
          title="Learnings & follow-ups"
          meta={`${learnings.length} durable · ${followups.length} open`}
          open={learningsOpen}
          onToggle={() => setLearningsOpenOverride((value) => !(value ?? learningsOpen))}
        >
          <div className="ting-research-detail__lf-grid">
            <div className="ting-research-detail__lf-column">
              <div className="ting-research-detail__lf-heading">Durable learnings</div>
              {learnings.map((item, index) => (
                <div key={`${item.title}-${index}`} className="ting-research-detail__lf-card">
                  <div className="ting-research-detail__lf-title">{item.title}</div>
                  <div className="ting-research-detail__lf-body">{item.body}</div>
                  {learningsArtifact ? (
                    <button
                      type="button"
                      className="ting-research-detail__lf-action"
                      onClick={() => openDrawer({ tab: 'files', file: learningsArtifact.path })}
                    >
                      ᛗ open
                    </button>
                  ) : null}
                </div>
              ))}
            </div>
            <div className="ting-research-detail__lf-column">
              <div className="ting-research-detail__lf-heading">Open follow-ups</div>
              {followups.map((item, index) => (
                <div key={`${item.title}-${index}`} className="ting-research-detail__lf-card">
                  <div className="ting-research-detail__lf-title">{item.title}</div>
                  <div className="ting-research-detail__lf-body">{item.body}</div>
                  {followupsArtifact ? (
                    <button
                      type="button"
                      className="ting-research-detail__lf-action"
                      onClick={() => openDrawer({ tab: 'files', file: followupsArtifact.path })}
                    >
                      ᛗ open
                    </button>
                  ) : null}
                </div>
              ))}
            </div>
          </div>
        </ResearchSection>
      )}

      <ResearchSection
        title="Durable memory"
        meta={`${memoryGroups.published.length} published · ${memoryGroups.reviewReady.length} review-ready · ${memoryGroups.notebook.length} notebook`}
        open={memoryOpen}
        onToggle={() => setMemoryOpenOverride((value) => !(value ?? memoryOpen))}
      >
        <div className="ting-research-detail__memory-group">
          <div className="ting-research-detail__memory-heading">Published</div>
          {memoryGroups.published.map((artifact) => (
            <button
              type="button"
              key={artifact.path}
              className="ting-research-detail__memory-card is-published"
              onClick={() => openDrawer({ tab: 'files', file: artifact.path })}
            >
              <span className="ting-research-detail__memory-title">{artifact.displayTitle}</span>
              <span className="ting-research-detail__memory-path">{artifact.path}</span>
            </button>
          ))}
        </div>

        {memoryGroups.reviewReady.length > 0 ? (
          <div className="ting-research-detail__memory-group">
            <div className="ting-research-detail__memory-heading">Review-ready</div>
            {memoryGroups.reviewReady.map((artifact) => (
              <button
                type="button"
                key={artifact.path}
                className="ting-research-detail__memory-card is-review"
                onClick={() => openDrawer({ tab: 'files', file: artifact.path })}
              >
                <span className="ting-research-detail__memory-title">{artifact.displayTitle}</span>
                <span className="ting-research-detail__memory-path">{artifact.path}</span>
              </button>
            ))}
          </div>
        ) : null}

        {memoryGroups.notebook.length > 0 ? (
          <details className="ting-research-detail__memory-details">
            <summary>Local notebook only</summary>
            <div className="ting-research-detail__memory-group">
              {memoryGroups.notebook.map((artifact) => (
                <button
                  type="button"
                  key={artifact.path}
                  className="ting-research-detail__memory-card is-notebook"
                  onClick={() => openDrawer({ tab: 'files', file: artifact.path })}
                >
                  <span className="ting-research-detail__memory-title">
                    {artifact.displayTitle}
                  </span>
                  <span className="ting-research-detail__memory-path">{artifact.path}</span>
                </button>
              ))}
            </div>
          </details>
        ) : null}
      </ResearchSection>
    </>
  );
}
