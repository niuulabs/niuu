import {
  cx,
  drawerSectionCountLabel,
  formatClock,
  formatElapsed,
  sentenceCase,
  statusDotClass,
  type DrawerTab,
} from './researchCampaignModel';
import { QualityDots, ResearchMarkdown } from './ResearchCampaignPresentation';
import type { ResearchCampaignPageController } from './useResearchCampaignPage';

export function ResearchCampaignDrawer({
  controller,
}: {
  controller: ResearchCampaignPageController;
}) {
  const {
    campaign,
    deleteCampaign,
    drawer,
    setDrawer,
    popoverCitation,
    setPopoverCitation,
    parsedArtifacts,
    openMimirPage,
    sourceList,
    visibleCitationSourceLabels,
    critiques,
    finalArtifact,
    manifestArtifact,
    selectedFilePath,
    selectedFileArtifact,
    selectedSource,
    selectedCritique,
    filteredActivity,
    sessionsCount,
    handleDeleteCampaign,
  } = controller;

  return drawer.open ? (
    <aside className="ting-research-detail__drawer" aria-label="Research drawer">
      <div className="ting-research-detail__drawer-header">
        <div>
          <div className="ting-research-detail__drawer-title">{campaign.name}</div>
          <div className="ting-research-detail__drawer-slug">{campaign.slug}</div>
        </div>
        <button
          type="button"
          className="ting-research-detail__drawer-close"
          onClick={() => setDrawer({ open: false, tab: 'files' })}
        >
          ×
        </button>
      </div>

      <div className="ting-research-detail__drawer-tabs">
        {[
          ['files', `Files ${parsedArtifacts.length}`],
          ['sources', `Sources ${sourceList.length}`],
          ['critiques', `Critiques ${critiques.length}`],
          ['operator', 'Operator'],
        ].map(([tab, label]) => (
          <button
            type="button"
            key={tab}
            className={cx(drawer.tab === tab && 'is-active')}
            onClick={() =>
              setDrawer((current) => ({ ...current, open: true, tab: tab as DrawerTab }))
            }
          >
            {label}
          </button>
        ))}
      </div>

      <div className="ting-research-detail__drawer-body">
        {drawer.tab === 'files' ? (
          <div className="ting-research-detail__drawer-files">
            <div className="ting-research-detail__chip-strip">
              {parsedArtifacts.map((artifact) => (
                <button
                  type="button"
                  key={artifact.path}
                  className={cx(
                    'ting-research-detail__file-chip',
                    selectedFileArtifact?.path === artifact.path && 'is-active',
                    artifact.publishState === 'published' && 'is-published',
                  )}
                  onClick={() =>
                    setDrawer((current) => ({ ...current, tab: 'files', file: artifact.path }))
                  }
                >
                  {artifact.path.split('/').slice(-1)[0]}
                </button>
              ))}
            </div>

            {selectedFileArtifact ? (
              <div className="ting-research-detail__drawer-file">
                <div className="ting-research-detail__drawer-file-head">
                  <div className="ting-research-detail__drawer-file-title">
                    {selectedFileArtifact.displayTitle}
                  </div>
                  <div className="ting-research-detail__drawer-file-path">
                    {selectedFileArtifact.path}
                  </div>
                </div>
                <ResearchMarkdown
                  content={selectedFileArtifact.body}
                  activeCitation={popoverCitation}
                  onCitationClick={setPopoverCitation}
                  fallbackSourceLabels={visibleCitationSourceLabels}
                />
              </div>
            ) : null}
          </div>
        ) : null}

        {drawer.tab === 'sources' ? (
          <div className="ting-research-detail__drawer-split">
            <div className="ting-research-detail__drawer-master">
              {sourceList.map((source) => (
                <button
                  type="button"
                  key={source.id}
                  className={cx(
                    'ting-research-detail__master-row',
                    selectedSource?.id === source.id && 'is-active',
                  )}
                  onClick={() => setDrawer((current) => ({ ...current, sourceId: source.id }))}
                >
                  <span className="ting-research-detail__master-citation">
                    [{source.citation}]
                  </span>
                  <span className="ting-research-detail__master-title">{source.title}</span>
                  <span className="ting-research-detail__master-quality">
                    <QualityDots score={source.quality} />
                    <span>x{source.citedCount}</span>
                  </span>
                </button>
              ))}
            </div>
            {selectedSource ? (
              <div className="ting-research-detail__drawer-detail">
                <div className="ting-research-detail__drawer-detail-eyebrow">
                  Source · [{selectedSource.citation}]
                </div>
                <div className="ting-research-detail__drawer-detail-title">
                  {selectedSource.title}
                </div>
                <div className="ting-research-detail__drawer-detail-domain">
                  {selectedSource.domain}
                </div>
                <div className="ting-research-detail__drawer-meta-card">
                  <div>
                    <span>Quality</span>
                    <strong>
                      <QualityDots score={selectedSource.quality} /> {selectedSource.quality}/5
                    </strong>
                  </div>
                  <div>
                    <span>Cited</span>
                    <strong>x{selectedSource.citedCount}</strong>
                  </div>
                  <div>
                    <span>Kind</span>
                    <strong>{selectedSource.kind}</strong>
                  </div>
                </div>
                <div className="ting-research-detail__drawer-subheading">Excerpt</div>
                <div className="ting-research-detail__drawer-quote">
                  {selectedSource.excerpt}
                </div>
                <div className="ting-research-detail__drawer-subheading">Cited in</div>
                <div className="ting-research-detail__drawer-linked-list">
                  {selectedSource.compiledInto.map((path) => (
                    <button
                      type="button"
                      key={path}
                      className="ting-research-detail__drawer-linked-item"
                      onClick={() =>
                        setDrawer((current) => ({ ...current, tab: 'files', file: path }))
                      }
                    >
                      {path}
                    </button>
                  ))}
                </div>
                <div className="ting-research-detail__drawer-actions">
                  <button
                    type="button"
                    onClick={() =>
                      openMimirPage(
                        selectedSource.compiledInto[0] ??
                          finalArtifact?.path ??
                          selectedFilePath ??
                          '',
                      )
                    }
                  >
                    ᛗ open in Mímir
                  </button>
                  {selectedSource.originUrl ? (
                    <button
                      type="button"
                      onClick={() =>
                        window.open(selectedSource.originUrl, '_blank', 'noopener,noreferrer')
                      }
                    >
                      ↗ open external
                    </button>
                  ) : null}
                </div>
              </div>
            ) : null}
          </div>
        ) : null}

        {drawer.tab === 'critiques' ? (
          <div className="ting-research-detail__drawer-split">
            <div className="ting-research-detail__drawer-master">
              {critiques.map((critique) => (
                <button
                  type="button"
                  key={critique.id}
                  className={cx(
                    'ting-research-detail__master-row',
                    selectedCritique?.id === critique.id && 'is-active',
                  )}
                  onClick={() =>
                    setDrawer((current) => ({ ...current, critiqueId: critique.id }))
                  }
                >
                  <span className="ting-research-detail__master-citation">
                    [{critique.citation}]
                  </span>
                  <span className="ting-research-detail__master-title">{critique.claim}</span>
                  <span
                    className={cx(
                      'ting-research-detail__severity-pill',
                      `is-${critique.severity}`,
                    )}
                  >
                    {critique.severity}
                  </span>
                </button>
              ))}
            </div>
            {selectedCritique ? (
              <div className="ting-research-detail__drawer-detail">
                <div className="ting-research-detail__drawer-detail-eyebrow">
                  Critique · [{selectedCritique.citation}]
                </div>
                <div className="ting-research-detail__drawer-detail-title">
                  {selectedCritique.claim}
                </div>
                <div className="ting-research-detail__drawer-detail-domain">
                  against {selectedCritique.against}
                </div>
                <div className="ting-research-detail__drawer-quote">
                  {selectedCritique.note}
                </div>
                <div className="ting-research-detail__drawer-subheading">Linked artifacts</div>
                <div className="ting-research-detail__drawer-linked-list">
                  {selectedCritique.linkedArtifacts.map((path) => (
                    <button
                      type="button"
                      key={path}
                      className="ting-research-detail__drawer-linked-item"
                      onClick={() =>
                        setDrawer((current) => ({ ...current, tab: 'files', file: path }))
                      }
                    >
                      {path}
                    </button>
                  ))}
                </div>
              </div>
            ) : null}
          </div>
        ) : null}

        {drawer.tab === 'operator' ? (
          <div className="ting-research-detail__operator">
            <div className="ting-research-detail__operator-tabs">
              {(['activity', 'run', 'actions'] as const).map((tab) => (
                <button
                  type="button"
                  key={tab}
                  className={cx(drawer.operatorSub === tab && 'is-active')}
                  onClick={() => setDrawer((current) => ({ ...current, operatorSub: tab }))}
                >
                  {sentenceCase(tab)}
                </button>
              ))}
            </div>

            {(drawer.operatorSub ?? 'activity') === 'activity' ? (
              <div className="ting-research-detail__activity-list">
                {filteredActivity.map((event) => (
                  <div key={event.id} className="ting-research-detail__activity-item">
                    <div className="ting-research-detail__activity-top">
                      <span>{event.event}</span>
                      <span>{formatClock(event.timestamp)}</span>
                    </div>
                    <div className="ting-research-detail__activity-body">
                      {String(
                        event.data.summary ?? event.data.message ?? JSON.stringify(event.data),
                      )}
                    </div>
                  </div>
                ))}
              </div>
            ) : null}

            {(drawer.operatorSub ?? 'activity') === 'run' ? (
              <div className="ting-research-detail__operator-grid">
                <div>
                  <span>Run id</span>
                  <strong>{campaign.sessionId}</strong>
                </div>
                <div>
                  <span>Cluster</span>
                  <strong>valhalla</strong>
                </div>
                <div>
                  <span>Sessions</span>
                  <strong>{sessionsCount}</strong>
                </div>
                <div>
                  <span>Elapsed</span>
                  <strong>
                    {formatElapsed(campaign.createdAt, campaign.completedAt ?? undefined)}
                  </strong>
                </div>
              </div>
            ) : null}

            {(drawer.operatorSub ?? 'activity') === 'actions' ? (
              <div className="ting-research-detail__operator-actions">
                <button
                  type="button"
                  onClick={() =>
                    window.location.assign(
                      `/volundr/sessions/${encodeURIComponent(campaign.sessionId)}`,
                    )
                  }
                >
                  Open in Völundr
                </button>
                {manifestArtifact ? (
                  <button
                    type="button"
                    onClick={() =>
                      setDrawer((current) => ({
                        ...current,
                        tab: 'files',
                        file: manifestArtifact.path,
                      }))
                    }
                  >
                    Open manifest
                  </button>
                ) : null}
                <button
                  type="button"
                  onClick={() =>
                    setDrawer((current) => ({
                      ...current,
                      tab: 'files',
                      file: finalArtifact?.path ?? selectedFilePath,
                    }))
                  }
                >
                  Open final synthesis
                </button>
                <button
                  type="button"
                  className="is-danger"
                  onClick={() => void handleDeleteCampaign()}
                  disabled={deleteCampaign.isPending}
                >
                  {deleteCampaign.isPending ? 'Deleting campaign…' : 'Delete campaign'}
                </button>
              </div>
            ) : null}
          </div>
        ) : null}
      </div>
    </aside>
  ) : null;
}
