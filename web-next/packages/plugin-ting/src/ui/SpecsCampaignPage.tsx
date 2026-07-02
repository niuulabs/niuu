import { useEffect, useMemo, useState } from 'react';
import type { ReactNode } from 'react';
import { useNavigate, useParams } from '@tanstack/react-router';
import { useQueryClient } from '@tanstack/react-query';
import { usePluginCtx } from '@niuulabs/plugin-sdk';
import { openEventStream } from '@niuulabs/query';
import type { CampaignArtifactDetail, SpecCampaignDetail } from '../ports';
import {
  useDeleteSpecCampaign,
  useReviewSpecCampaign,
  useSpecArtifact,
  useSpecCampaign,
} from './useSpecs';
import './ResearchCampaignPage.css';
import './SpecsPage.css';

const DOC_TABS = [
  { kind: 'brief', label: 'Brief' },
  { kind: 'prd', label: 'PRD', gateNodeId: 'spec-prd-gate' },
  { kind: 'srd', label: 'SRD', gateNodeId: 'spec-srd-gate' },
  { kind: 'sdd', label: 'SDD', gateNodeId: 'spec-sdd-gate' },
  { kind: 'breakdown', label: 'Breakdown', gateNodeId: 'spec-breakdown-gate' },
  { kind: 'manifest', label: 'Manifest' },
] as const;

interface PendingSpecGate {
  id: string;
  nodeId: string;
  summary: string;
  instructions: string;
}

function pendingSpecGates(campaign: SpecCampaignDetail | null | undefined): PendingSpecGate[] {
  const raw = campaign?.metadata.pending_workflow_gates;
  if (!Array.isArray(raw)) return [];
  return raw
    .filter((gate): gate is Record<string, unknown> => typeof gate === 'object' && gate !== null)
    .map((gate) => ({
      id: String(gate.id ?? gate.gate_id ?? gate.gateId ?? ''),
      nodeId: String(gate.node_id ?? gate.nodeId ?? ''),
      summary: String(gate.summary ?? gate.label ?? 'Review required'),
      instructions: String(gate.instructions ?? ''),
    }))
    .filter((gate) => gate.id && gate.nodeId.startsWith('spec-') && gate.nodeId.endsWith('-gate'));
}

function docKindForGate(nodeId: string): string | null {
  if (nodeId === 'spec-prd-gate') return 'prd';
  if (nodeId === 'spec-srd-gate') return 'srd';
  if (nodeId === 'spec-sdd-gate') return 'sdd';
  if (nodeId === 'spec-breakdown-gate') return 'breakdown';
  return null;
}

function firstDocPath(campaign: SpecCampaignDetail, preferredKind?: string | null): string | null {
  const canonical = campaign.canonicalArtifacts;
  if (preferredKind && canonical[preferredKind]) return canonical[preferredKind];
  for (const tab of DOC_TABS) {
    const path = canonical[tab.kind];
    if (path) return path;
  }
  return campaign.artifacts[0]?.path ?? null;
}

function metadataRepos(campaign: SpecCampaignDetail): string[] {
  const raw = campaign.metadata.repos;
  if (Array.isArray(raw)) return raw.map(String).filter(Boolean);
  const repo = campaign.metadata.repo;
  return typeof repo === 'string' && repo.trim() ? [repo.trim()] : [];
}

function stripHeading(text: string): string {
  return text.replace(/^# .+\n?/m, '').trim();
}

function splitTableRow(row: string): string[] {
  return row
    .trim()
    .replace(/^\|/, '')
    .replace(/\|$/, '')
    .split('|')
    .map((cell) => cell.trim());
}

function renderInline(text: string): ReactNode {
  const parts: ReactNode[] = [];
  let cursor = 0;
  let key = 0;
  const matcher = /(\*\*[^*]+\*\*|`[^`]+`|\[[^\]]+\]\([^)]+\))/g;
  let match: RegExpExecArray | null;
  while ((match = matcher.exec(text)) !== null) {
    if (match.index > cursor) parts.push(text.slice(cursor, match.index));
    const token = match[0];
    if (token.startsWith('**') && token.endsWith('**')) {
      parts.push(<strong key={`strong-${key++}`}>{token.slice(2, -2)}</strong>);
    } else if (token.startsWith('`') && token.endsWith('`')) {
      parts.push(<code key={`code-${key++}`}>{token.slice(1, -1)}</code>);
    } else {
      const labelEnd = token.indexOf('](');
      const label = token.slice(1, labelEnd);
      const href = token.slice(labelEnd + 2, -1);
      parts.push(
        <a key={`link-${key++}`} href={href} target="_blank" rel="noreferrer">
          {label}
        </a>,
      );
    }
    cursor = matcher.lastIndex;
  }
  if (cursor < text.length) parts.push(text.slice(cursor));
  return parts.length === 1 ? parts[0] : parts;
}

function SpecMarkdown({ content }: { content: string }) {
  const clean = stripHeading(content);
  const lines = clean.split('\n');
  const elements: ReactNode[] = [];
  let paragraphIndex = 0;

  for (let index = 0; index < lines.length; ) {
    const line = lines[index] ?? '';
    if (!line.trim()) {
      index += 1;
      continue;
    }
    if (/^#\s+/.test(line)) {
      elements.push(<h1 key={`h1-${index}`}>{line.replace(/^#\s+/, '')}</h1>);
      index += 1;
      continue;
    }
    if (/^##\s+/.test(line)) {
      elements.push(<h2 key={`h2-${index}`}>{line.replace(/^##\s+/, '')}</h2>);
      index += 1;
      continue;
    }
    if (/^###\s+/.test(line)) {
      elements.push(<h3 key={`h3-${index}`}>{line.replace(/^###\s+/, '')}</h3>);
      index += 1;
      continue;
    }
    if (/^[-*]\s+/.test(line)) {
      const items: string[] = [];
      while (index < lines.length && /^[-*]\s+/.test(lines[index] ?? '')) {
        items.push((lines[index] ?? '').replace(/^[-*]\s+/, ''));
        index += 1;
      }
      elements.push(
        <ul key={`ul-${index}`}>
          {items.map((item, itemIndex) => (
            <li key={`${item}-${itemIndex}`}>{renderInline(item)}</li>
          ))}
        </ul>,
      );
      continue;
    }
    if (/^\d+\.\s+/.test(line)) {
      const items: string[] = [];
      while (index < lines.length && /^\d+\.\s+/.test(lines[index] ?? '')) {
        items.push((lines[index] ?? '').replace(/^\d+\.\s+/, ''));
        index += 1;
      }
      elements.push(
        <ol key={`ol-${index}`}>
          {items.map((item, itemIndex) => (
            <li key={`${item}-${itemIndex}`}>{renderInline(item)}</li>
          ))}
        </ol>,
      );
      continue;
    }
    if (/^\|.+\|$/.test(line.trim()) && /^\|?[-:\s|]+\|?$/.test((lines[index + 1] ?? '').trim())) {
      const headers = splitTableRow(line);
      index += 2;
      const rows: string[][] = [];
      while (index < lines.length && /^\|.+\|$/.test((lines[index] ?? '').trim())) {
        rows.push(splitTableRow(lines[index] ?? ''));
        index += 1;
      }
      elements.push(
        <div key={`table-${index}`} className="ting-research-detail__markdown-table-wrap">
          <table className="ting-research-detail__markdown-table">
            <thead>
              <tr>
                {headers.map((header) => (
                  <th key={header}>{header}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row, rowIndex) => (
                <tr key={`row-${rowIndex}`}>
                  {row.map((cell, cellIndex) => (
                    <td key={`${rowIndex}-${cellIndex}`}>{renderInline(cell)}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>,
      );
      continue;
    }

    const paragraphLines: string[] = [];
    while (index < lines.length && lines[index]?.trim()) {
      paragraphLines.push(lines[index] ?? '');
      index += 1;
    }
    elements.push(<p key={`p-${paragraphIndex++}`}>{renderInline(paragraphLines.join(' '))}</p>);
  }

  return <div className="specs-markdown">{elements}</div>;
}

function artifactTitle(artifact: CampaignArtifactDetail | null | undefined, path: string | null) {
  if (artifact?.title) return artifact.title;
  return (
    path?.split('/').pop()?.replace(/^\d+-/, '').replace(/-/g, ' ').replace(/\.md$/, '') ??
    'Document'
  );
}

export function SpecsCampaignPage() {
  const { slug } = useParams({ from: '/ting/specs/$slug' });
  const navigate = useNavigate();
  const ctx = usePluginCtx();
  const queryClient = useQueryClient();
  const { data: campaign, isLoading, isError, error } = useSpecCampaign(slug);
  const deleteCampaign = useDeleteSpecCampaign();
  const reviewCampaign = useReviewSpecCampaign(slug);
  const gates = pendingSpecGates(campaign);
  const primaryGate = gates[0] ?? null;
  const preferredKind = primaryGate ? docKindForGate(primaryGate.nodeId) : null;
  const [selectedPath, setSelectedPath] = useState<string | null>(null);
  const [notes, setNotes] = useState('');
  const effectivePath = campaign ? (selectedPath ?? firstDocPath(campaign, preferredKind)) : null;
  const artifactQuery = useSpecArtifact(slug, effectivePath);
  const reviewKind = primaryGate ? docKindForGate(primaryGate.nodeId) : null;
  const reviewNotesPath =
    campaign && reviewKind ? (campaign.canonicalArtifacts[`${reviewKind}_review`] ?? null) : null;
  const reviewNotesQuery = useSpecArtifact(slug, reviewNotesPath);

  useEffect(() => {
    if (typeof window === 'undefined') return;
    const stream = openEventStream('/api/v1/ting/events', {
      onMessage: () => {},
      onEvent: ({ event, data }) => {
        if (!event?.startsWith('workflow.campaign.')) return;
        try {
          const parsed = JSON.parse(data) as { slug?: string };
          if (parsed.slug && parsed.slug !== slug) return;
        } catch {
          return;
        }
        void queryClient.invalidateQueries({ queryKey: ['ting', 'specs', 'campaign', slug] });
        void queryClient.invalidateQueries({ queryKey: ['ting', 'specs', 'campaigns'] });
      },
    });
    return () => stream.close();
  }, [queryClient, slug]);

  const repos = useMemo(() => (campaign ? metadataRepos(campaign) : []), [campaign]);

  async function submitReview(decision: 'approve' | 'changes_requested') {
    if (!primaryGate) return;
    await reviewCampaign.mutateAsync({
      decision,
      notes: decision === 'approve' ? notes.trim() : notes.trim(),
      gateId: primaryGate.id,
      nodeId: primaryGate.nodeId,
    });
    setNotes('');
  }

  function openMimirPage() {
    if (!effectivePath) return;
    ctx.setTweak('mimir.selectedPagePath', effectivePath);
    void navigate({ to: '/mimir/pages' });
  }

  if (isLoading) return <div className="research-empty-state">Loading spec…</div>;
  if (isError)
    return (
      <div className="research-empty-state is-error">
        {error instanceof Error ? error.message : 'Failed to load spec.'}
      </div>
    );
  if (!campaign) return <div className="research-empty-state">Spec not found.</div>;

  const artifact = artifactQuery.data ?? null;

  return (
    <div className="niuu:h-full niuu:overflow-y-auto niuu:bg-bg-primary">
      <div className="niuu:px-6 niuu:py-6">
        <button
          type="button"
          onClick={() => navigate({ to: '/ting/specs' })}
          className="niuu:mb-4 niuu:text-sm niuu:text-text-secondary niuu:hover:text-text-primary"
        >
          ← Specs
        </button>

        <div className="ting-research-detail__header">
          <div>
            <div className="ting-research-detail__eyebrow">
              Specification Stack · {campaign.status}
            </div>
            <h1 className="ting-research-detail__title">{campaign.name}</h1>
            {primaryGate ? <div className="specs-review-pill">{primaryGate.summary}</div> : null}
          </div>
          <div className="ting-research-detail__header-actions">
            <button type="button" onClick={openMimirPage} className="specs-action-button">
              Open in Mimir
            </button>
            <button
              type="button"
              onClick={() =>
                deleteCampaign.mutate(campaign.slug, {
                  onSuccess: () => void navigate({ to: '/ting/specs' }),
                })
              }
              className="specs-action-button is-danger"
            >
              Delete
            </button>
          </div>
        </div>

        <div className="specs-detail-layout">
          <main className="niuu:min-w-0">
            <div className="specs-doc-tabs">
              {DOC_TABS.map((tab) => {
                const path = campaign.canonicalArtifacts[tab.kind] ?? null;
                const gateNodeId = 'gateNodeId' in tab ? tab.gateNodeId : null;
                const isReviewTab = gateNodeId && primaryGate?.nodeId === gateNodeId;
                return (
                  <button
                    key={tab.kind}
                    type="button"
                    disabled={!path}
                    onClick={() => setSelectedPath(path)}
                    className={[
                      'specs-doc-tab',
                      path && effectivePath === path ? 'is-active' : '',
                    ].join(' ')}
                  >
                    {tab.label}
                    {isReviewTab ? ' · review' : ''}
                  </button>
                );
              })}
            </div>

            <section className="specs-document-shell">
              {artifactQuery.isLoading ? (
                <div className="research-empty-state">Loading document…</div>
              ) : artifact ? (
                <>
                  <h1 className="niuu:m-0 niuu:mb-5 niuu:text-3xl niuu:font-semibold niuu:text-text-primary">
                    {artifactTitle(artifact, effectivePath)}
                  </h1>
                  <SpecMarkdown content={artifact.content} />
                </>
              ) : (
                <div className="research-empty-state">No document has been written yet.</div>
              )}
            </section>
          </main>

          <aside className="specs-side-stack">
            <section className="specs-review-panel">
              <h2 className="specs-panel-title">Review</h2>
              {primaryGate ? (
                <>
                  <p className="niuu:m-0 niuu:mb-3 niuu:text-sm niuu:text-text-secondary">
                    {primaryGate.instructions || 'Approve or request changes for this document.'}
                  </p>
                  {reviewNotesPath ? (
                    <div className="specs-review-notes">
                      <h3>Critic notes</h3>
                      {reviewNotesQuery.isLoading ? (
                        <p>Loading critic notes…</p>
                      ) : reviewNotesQuery.data ? (
                        <SpecMarkdown content={reviewNotesQuery.data.content} />
                      ) : (
                        <p>No critic notes are available yet.</p>
                      )}
                    </div>
                  ) : null}
                  <textarea
                    value={notes}
                    onChange={(event) => setNotes(event.target.value)}
                    placeholder="Feedback for revisions"
                  />
                  <div className="specs-review-panel__actions">
                    <button
                      type="button"
                      onClick={() => submitReview('changes_requested')}
                      disabled={reviewCampaign.isPending || notes.trim().length === 0}
                      className="specs-action-button"
                    >
                      Request changes
                    </button>
                    <button
                      type="button"
                      onClick={() => submitReview('approve')}
                      disabled={reviewCampaign.isPending}
                      className="specs-action-button is-primary"
                    >
                      Approve
                    </button>
                  </div>
                </>
              ) : (
                <p className="niuu:m-0 niuu:text-sm niuu:text-text-secondary">
                  No document review is waiting right now.
                </p>
              )}
            </section>

            <section className="specs-side-panel">
              <h2 className="specs-panel-title">Details</h2>
              <dl className="specs-meta-list">
                <div>
                  <dt>Session</dt>
                  <dd>
                    <button
                      type="button"
                      className="specs-inline-link"
                      onClick={() =>
                        window.location.assign(
                          `/volundr/sessions/${encodeURIComponent(campaign.sessionId)}`,
                        )
                      }
                    >
                      {campaign.sessionName || campaign.sessionId}
                    </button>
                  </dd>
                </div>
                <div>
                  <dt>Workflow</dt>
                  <dd>
                    {campaign.workflowName} v{campaign.workflowVersion}
                  </dd>
                </div>
                <div>
                  <dt>Repos</dt>
                  <dd>{repos.length > 0 ? repos.join(', ') : 'none'}</dd>
                </div>
                <div>
                  <dt>Branch</dt>
                  <dd>{String(campaign.metadata.branch ?? '') || 'default'}</dd>
                </div>
                <div>
                  <dt>Gate</dt>
                  <dd>{reviewKind ? reviewKind.toUpperCase() : 'none'}</dd>
                </div>
              </dl>
            </section>
          </aside>
        </div>
      </div>
    </div>
  );
}
