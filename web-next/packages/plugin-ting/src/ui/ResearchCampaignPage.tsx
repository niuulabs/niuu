import { useEffect, useMemo, useState } from 'react';
import { useNavigate, useParams } from '@tanstack/react-router';
import { useQueries, useQuery, useQueryClient } from '@tanstack/react-query';
import { usePluginCtx, useService } from '@niuulabs/plugin-sdk';
import type { IDispatcherService, IResearchService } from '../ports';
import type {
  CampaignArtifactDetail,
  ResearchCampaignDetail,
  CampaignStageState,
} from '../domain/research';
import { useDeleteResearchCampaign, useResearchCampaign } from './useResearch';
import './ResearchCampaignPage.css';

type DerivedCampaignState = 'draft' | 'running' | 'blocked' | 'failed' | 'review' | 'published';

type DrawerTab = 'files' | 'sources' | 'critiques' | 'operator';
type OperatorSubTab = 'activity' | 'run' | 'actions';
type ViewMode = 'clean' | 'annotated';

interface MimirSource {
  id: string;
  title: string;
  originType: 'web' | 'rss' | 'arxiv' | 'file' | 'mail' | 'chat';
  originUrl?: string;
  originPath?: string;
  ingestedAt: string;
  ingestAgent: string;
  compiledInto: string[];
  content: string;
}

interface MimirServiceLike {
  pages: {
    listSources(options?: { originType?: string; mountName?: string }): Promise<MimirSource[]>;
  };
}

interface ParsedArtifact extends CampaignArtifactDetail {
  body: string;
  frontmatter: Record<string, unknown>;
  displayTitle: string;
}

interface SourceVm {
  id: string;
  citation: string;
  title: string;
  domain: string;
  quality: number;
  citedCount: number;
  compiledInto: string[];
  excerpt: string;
  kind: string;
  content: string;
  originUrl?: string;
}

interface CritiqueVm {
  id: string;
  citation: string;
  claim: string;
  against: string;
  note: string;
  severity: 'high' | 'med' | 'low';
  linkedArtifacts: string[];
}

interface DrawerState {
  open: boolean;
  tab: DrawerTab;
  file?: string;
  sourceId?: string;
  critiqueId?: string;
  operatorSub?: OperatorSubTab;
}

interface CitationTarget {
  kind: 'source' | 'critique';
  key: string;
}

interface CitationPopoverState extends CitationTarget {
  anchor: string;
  x: number;
  y: number;
}

export function cx(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(' ');
}

export function readDrawerState(): DrawerState {
  if (typeof window === 'undefined') {
    return { open: false, tab: 'files' };
  }
  const params = new URLSearchParams(window.location.search);
  const drawer = params.get('drawer');
  if (!drawer) {
    return { open: false, tab: 'files' };
  }
  const tab = (['files', 'sources', 'critiques', 'operator'] as const).includes(drawer as DrawerTab)
    ? (drawer as DrawerTab)
    : 'files';
  return {
    open: true,
    tab,
    file: params.get('file') ?? undefined,
    sourceId: params.get('n') ?? undefined,
    critiqueId: params.get('c') ?? undefined,
    operatorSub: ((params.get('sub') as OperatorSubTab | null) ?? 'activity') as OperatorSubTab,
  };
}

export function writeDrawerState(next: DrawerState) {
  if (typeof window === 'undefined') return;
  const url = new URL(window.location.href);
  url.searchParams.delete('drawer');
  url.searchParams.delete('file');
  url.searchParams.delete('n');
  url.searchParams.delete('c');
  url.searchParams.delete('sub');
  if (next.open) {
    url.searchParams.set('drawer', next.tab);
    if (next.tab === 'files' && next.file) url.searchParams.set('file', next.file);
    if (next.tab === 'sources' && next.sourceId) url.searchParams.set('n', next.sourceId);
    if (next.tab === 'critiques' && next.critiqueId) url.searchParams.set('c', next.critiqueId);
    if (next.tab === 'operator' && next.operatorSub) url.searchParams.set('sub', next.operatorSub);
  }
  window.history.replaceState({}, '', url.toString());
}

export function formatClock(iso?: string | null): string {
  if (!iso) return 'unknown';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleString([], {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  });
}

export function formatElapsed(from?: string | null, to?: string | null): string {
  if (!from) return '—';
  const start = new Date(from).getTime();
  const end = new Date(to ?? Date.now()).getTime();
  if (Number.isNaN(start) || Number.isNaN(end) || end < start) return '—';
  const minutes = Math.max(1, Math.round((end - start) / 60000));
  const days = Math.floor(minutes / 1440);
  const hours = Math.floor((minutes % 1440) / 60);
  const mins = minutes % 60;
  if (days > 0) return `${days}d ${hours}h`;
  if (hours > 0) return `${hours}h ${mins}m`;
  return `${mins}m`;
}

export function countWords(text: string): number {
  return text.trim().split(/\s+/).filter(Boolean).length;
}

export function formatKiloWords(text: string): string {
  const words = countWords(text);
  return words >= 1000 ? `${(words / 1000).toFixed(1)}k` : `${words}`;
}

export function parseYamlScalar(raw: string): unknown {
  const value = raw.trim();
  if (!value) return '';
  if (value === 'true') return true;
  if (value === 'false') return false;
  if (value === 'null') return null;
  if (/^\d+(\.\d+)?$/.test(value)) return Number(value);
  if (value.startsWith('[') && value.endsWith(']')) {
    return value
      .slice(1, -1)
      .split(',')
      .map((part) => part.trim().replace(/^['"]|['"]$/g, ''))
      .filter(Boolean);
  }
  return value.replace(/^['"]|['"]$/g, '');
}

export function parseFrontmatter(content: string): {
  frontmatter: Record<string, unknown>;
  body: string;
} {
  const match = content.match(/^---\n([\s\S]*?)\n---\n?/);
  if (!match) return { frontmatter: {}, body: content };
  const yaml = match[1] ?? '';
  const body = content.slice(match[0].length);
  const frontmatter: Record<string, unknown> = {};
  for (const line of yaml.split('\n')) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith('#')) continue;
    const index = trimmed.indexOf(':');
    if (index === -1) continue;
    const key = trimmed.slice(0, index).trim();
    const value = trimmed.slice(index + 1);
    frontmatter[key] = parseYamlScalar(value);
  }
  return { frontmatter, body };
}

export function stripHeading(text: string): string {
  return text.replace(/^# .+\n?/m, '').trim();
}

export function firstParagraph(text: string): string {
  const paragraphs = stripHeading(text)
    .split(/\n\s*\n/)
    .map((part) => part.trim())
    .filter(Boolean);
  return paragraphs[0] ?? '';
}

export function sentenceCase(value: string): string {
  if (!value) return '';
  return value.charAt(0).toUpperCase() + value.slice(1);
}

export function normalizeStageLabel(stage: CampaignStageState | undefined): string {
  if (!stage) return 'Research';
  const label = stage.label.toLowerCase();
  if (label.includes('frame')) return 'Frame';
  if (label.includes('explore')) return 'Explore';
  if (label.includes('challenge')) return 'Challenge';
  if (label.includes('synth')) return 'Synthesize';
  if (label.includes('curate')) return 'Curate';
  if (label.includes('publish')) return 'Publish';
  if (label.includes('complete')) return 'Complete';
  return stage.label;
}

export function deriveCampaignState(
  campaign: ResearchCampaignDetail,
  artifacts: ParsedArtifact[],
): DerivedCampaignState {
  const activeLike = campaign.stageState.find(
    (stage) => stage.status === 'active' || stage.status === 'blocked' || stage.status === 'failed',
  );
  if (campaign.status === 'failed' || activeLike?.status === 'failed') return 'failed';
  if (campaign.status === 'blocked' || activeLike?.status === 'blocked') return 'blocked';
  if (campaign.status === 'pending') return 'draft';
  const hasPublishedFinal = artifacts.some(
    (artifact) => artifact.kind === 'final' && artifact.publishState === 'published',
  );
  const hasManifest = artifacts.some(
    (artifact) => artifact.kind === 'manifest' && artifact.publishState === 'published',
  );
  if (hasPublishedFinal || hasManifest) return 'published';
  const hasFinal = artifacts.some((artifact) => artifact.kind === 'final');
  if (hasFinal && campaign.status === 'completed') return 'review';
  if (activeLike || campaign.stageState.some((stage) => stage.status === 'pending'))
    return 'running';
  if (campaign.status === 'completed') return hasFinal ? 'review' : 'running';
  return 'running';
}

export function findActiveStage(stageState: CampaignStageState[]): CampaignStageState | undefined {
  return (
    stageState.find(
      (stage) =>
        stage.status === 'active' || stage.status === 'blocked' || stage.status === 'failed',
    ) ?? stageState[stageState.length - 1]
  );
}

export function confidenceFromArtifacts(
  state: DerivedCampaignState,
  finalArtifact: ParsedArtifact | null,
  sourceCount: number,
  critiqueCount: number,
): { percent: number; label: 'low' | 'med' | 'high'; raw: string } {
  const frontmatterConfidence = String(finalArtifact?.frontmatter.confidence ?? '').toLowerCase();
  if (frontmatterConfidence === 'high') return { percent: 78, label: 'high', raw: 'high' };
  if (frontmatterConfidence === 'medium') return { percent: 62, label: 'med', raw: 'medium' };
  if (frontmatterConfidence === 'low') return { percent: 41, label: 'low', raw: 'low' };
  if (state === 'published') return { percent: 84, label: 'high', raw: 'high' };
  if (state === 'review') return { percent: 78, label: 'high', raw: 'high' };
  if (state === 'blocked') return { percent: 55, label: 'med', raw: 'medium' };
  if (state === 'failed') return { percent: 34, label: 'low', raw: 'low' };
  const base = 46 + Math.min(24, sourceCount * 2) + Math.min(8, critiqueCount);
  return {
    percent: Math.max(38, Math.min(74, base)),
    label: base >= 68 ? 'high' : base >= 54 ? 'med' : 'low',
    raw: base >= 68 ? 'high' : base >= 54 ? 'medium' : 'low',
  };
}

export function artifactDisplayTitle(artifact: CampaignArtifactDetail): string {
  return artifact.title || artifact.path.split('/').slice(-1)[0] || artifact.path;
}

export function deriveWorkingThesis(artifacts: ParsedArtifact[]): string {
  const candidates = [
    artifacts.find((artifact) => artifact.kind === 'analysis'),
    artifacts.find((artifact) => artifact.kind === 'final'),
    artifacts.find((artifact) => artifact.path.endsWith('/notes/exploration.md')),
    artifacts.find((artifact) => artifact.kind === 'brief'),
  ].filter(Boolean) as ParsedArtifact[];
  for (const artifact of candidates) {
    const paragraph = firstParagraph(artifact.body);
    if (paragraph) return paragraph;
  }
  return 'Ting is still building the first useful answer for this campaign.';
}

export function inferDomain(source: MimirSource): string {
  if (source.originUrl) {
    try {
      return new URL(source.originUrl).hostname.replace(/^www\./, '');
    } catch {
      return source.originUrl;
    }
  }
  if (source.originPath) return source.originPath;
  return source.originType;
}

export function inferSourceKind(source: MimirSource): string {
  if (source.originType === 'arxiv') return 'paper';
  if (source.originType === 'file') return 'file';
  if (source.originType === 'chat') return 'chat';
  if (source.originType === 'rss') return 'feed';
  return 'web';
}

export function qualityForSource(source: MimirSource): number {
  if (source.originType === 'arxiv') return 5;
  if (source.originType === 'file') return 5;
  if (source.originType === 'rss') return 4;
  if (source.originType === 'web') return 4;
  return 3;
}

export function takeExcerpt(content: string): string {
  const cleaned = content.replace(/\s+/g, ' ').trim();
  if (!cleaned) return 'No excerpt is available for this source yet.';
  return cleaned.slice(0, 220) + (cleaned.length > 220 ? '…' : '');
}

export function parseCritiques(content: string, linkedArtifacts: string[]): CritiqueVm[] {
  const body = stripHeading(content);
  const sections = body
    .split(/\n(?=## )/)
    .map((section) => section.trim())
    .filter(Boolean);
  const critiques: CritiqueVm[] = [];

  for (const [index, section] of sections.entries()) {
    const lines = section.split('\n').map((line) => line.trim());
    const heading = lines[0]?.replace(/^##\s+/, '') ?? '';
    if (!heading) continue;
    const againstLine = lines.find((line) => /^against:/i.test(line)) ?? '';
    const severityLine = lines.find((line) => /^severity:/i.test(line)) ?? '';
    const paragraphs = lines
      .slice(1)
      .filter((line) => line && !/^against:/i.test(line) && !/^severity:/i.test(line));
    const note = paragraphs.join(' ');
    const normalizedSeverity = severityLine.toLowerCase();
    const severity: CritiqueVm['severity'] = normalizedSeverity.includes('high')
      ? 'high'
      : normalizedSeverity.includes('low')
        ? 'low'
        : index === 0
          ? 'high'
          : index < 3
            ? 'med'
            : 'low';
    critiques.push({
      id: `critique-${index + 1}`,
      citation: `c${index + 1}`,
      claim: heading,
      against: againstLine.replace(/^against:\s*/i, '') || 'Current working thesis',
      note: note || 'The skeptic surfaced this as a material risk for the final synthesis.',
      severity,
      linkedArtifacts,
    });
  }

  if (critiques.length > 0) return critiques;

  const bulletCritiques = body
    .split('\n')
    .map((line) => line.trim())
    .filter((line) => line.startsWith('- ') || line.startsWith('* '));
  return bulletCritiques.slice(0, 5).map((line, index) => ({
    id: `critique-${index + 1}`,
    citation: `c${index + 1}`,
    claim: line.slice(2),
    against: 'Current thesis',
    note: 'The skeptic flagged this objection as unresolved.',
    severity: index === 0 ? 'high' : 'med',
    linkedArtifacts,
  }));
}

export function parseListCards(content: string): Array<{ title: string; body: string }> {
  const body = stripHeading(content);
  const items = body
    .split(/\n(?=- |\* |## )/)
    .map((part) => part.trim())
    .filter(Boolean);
  return items.slice(0, 6).map((item) => {
    if (item.startsWith('## ')) {
      const [title, ...rest] = item.split('\n');
      return { title: (title ?? '').replace(/^##\s+/, ''), body: rest.join(' ').trim() };
    }
    const cleaned = item.replace(/^[-*]\s+/, '');
    const [title, ...rest] = cleaned.split(':');
    return {
      title: (title ?? '').trim(),
      body: rest.join(':').trim() || cleaned,
    };
  });
}

export function citationToken(label: string): string {
  return `[${label}]`;
}

export function statusDotClass(state: DerivedCampaignState): string {
  if (state === 'published' || state === 'review' || state === 'running') return 'is-brand';
  if (state === 'blocked') return 'is-amber';
  if (state === 'failed') return 'is-rose';
  return 'is-muted';
}

export function stageTickClass(status: string): string {
  if (status === 'complete') return 'is-complete';
  if (status === 'active') return 'is-active';
  if (status === 'blocked') return 'is-blocked';
  if (status === 'failed') return 'is-failed';
  return 'is-pending';
}

export function drawerSectionCountLabel(count: number, singular: string, plural: string): string {
  return `${count} ${count === 1 ? singular : plural}`;
}

function ResearchSection({
  title,
  meta,
  open,
  onToggle,
  actionLabel,
  onAction,
  children,
}: {
  title: string;
  meta?: string;
  open: boolean;
  onToggle: () => void;
  actionLabel?: string;
  onAction?: () => void;
  children: React.ReactNode;
}) {
  return (
    <section className="ting-research-detail__section">
      <div className="ting-research-detail__section-header">
        <button type="button" className="ting-research-detail__section-toggle" onClick={onToggle}>
          <span className="ting-research-detail__section-caret">{open ? '▾' : '▸'}</span>
          <span className="ting-research-detail__section-title">{title}</span>
          {meta ? <span className="ting-research-detail__section-meta">· {meta}</span> : null}
        </button>
        {actionLabel && onAction ? (
          <button type="button" className="ting-research-detail__section-action" onClick={onAction}>
            {actionLabel}
          </button>
        ) : null}
      </div>
      {open ? <div className="ting-research-detail__section-body">{children}</div> : null}
    </section>
  );
}

function QualityDots({ score }: { score: number }) {
  return (
    <span className="ting-research-detail__quality-dots" aria-label={`quality ${score} of 5`}>
      {[0, 1, 2, 3, 4].map((index) => (
        <span
          key={index}
          className={cx(
            'ting-research-detail__quality-dot',
            index < score && 'is-filled',
            index >= score && 'is-empty',
          )}
        />
      ))}
    </span>
  );
}

function ConfidenceBadge({ percent, label }: { percent: number; label: 'low' | 'med' | 'high' }) {
  return (
    <div className="ting-research-detail__confidence">
      <div className="ting-research-detail__confidence-bar">
        <span className="ting-research-detail__confidence-fill" style={{ width: `${percent}%` }} />
      </div>
      <span className="ting-research-detail__confidence-text">
        {percent}% · {label.toUpperCase()}
      </span>
    </div>
  );
}

function InlineCitation({
  label,
  kind,
  isActive,
  onClick,
}: {
  label: string;
  kind: 'source' | 'critique';
  isActive: boolean;
  onClick: (event: React.MouseEvent<HTMLButtonElement>) => void;
}) {
  return (
    <button
      type="button"
      className={cx(
        'ting-research-detail__citation',
        kind === 'source' ? 'is-source' : 'is-critique',
        isActive && 'is-active',
      )}
      onClick={onClick}
    >
      [{label}]
    </button>
  );
}

function ResearchMarkdown({
  content,
  activeCitation,
  onCitationClick,
  fallbackSourceLabels,
}: {
  content: string;
  activeCitation: CitationPopoverState | null;
  onCitationClick: (citation: CitationPopoverState | null) => void;
  fallbackSourceLabels?: string[];
}) {
  const clean = stripHeading(content);
  const lines = clean.split('\n');
  const elements: React.ReactNode[] = [];
  let paragraphIndex = 0;

  for (let index = 0; index < lines.length; ) {
    const line = lines[index] ?? '';
    if (!line.trim()) {
      index += 1;
      continue;
    }
    if (/^##\s+/.test(line)) {
      elements.push(
        <h2 key={`h2-${index}`} className="ting-research-detail__markdown-h2">
          {line.replace(/^##\s+/, '')}
        </h2>,
      );
      index += 1;
      continue;
    }
    if (/^###\s+/.test(line)) {
      elements.push(
        <h3 key={`h3-${index}`} className="ting-research-detail__markdown-h3">
          {line.replace(/^###\s+/, '')}
        </h3>,
      );
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
        <ul key={`ul-${index}`} className="ting-research-detail__markdown-list">
          {items.map((item, itemIndex) => (
            <li key={`${item}-${itemIndex}`}>
              {renderInlineRich(item, activeCitation, onCitationClick)}
            </li>
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
        <ol key={`ol-${index}`} className="ting-research-detail__markdown-ordered-list">
          {items.map((item, itemIndex) => (
            <li key={`${item}-${itemIndex}`}>
              {renderInlineRich(item, activeCitation, onCitationClick)}
            </li>
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
                    <td key={`${rowIndex}-${cellIndex}`}>
                      {renderInlineRich(cell, activeCitation, onCitationClick)}
                    </td>
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
    const paragraph = paragraphLines.join(' ');
    const shouldAppendFallback = paragraphIndex === 0 && !/\[[sc]\d+\]/i.test(paragraph);
    elements.push(
      <p key={`p-${paragraphIndex}`} className="ting-research-detail__markdown-p">
        {renderInlineRich(paragraph, activeCitation, onCitationClick)}
        {shouldAppendFallback && fallbackSourceLabels?.length
          ? fallbackSourceLabels.slice(0, 3).map((label) => (
              <span key={label} className="ting-research-detail__markdown-fallback-citation">
                <InlineCitation
                  label={label}
                  kind="source"
                  isActive={activeCitation?.kind === 'source' && activeCitation.key === label}
                  onClick={(event) => {
                    const rect = event.currentTarget.getBoundingClientRect();
                    onCitationClick({
                      kind: 'source',
                      key: label,
                      anchor: `source-${label}`,
                      x: rect.left,
                      y: rect.bottom,
                    });
                  }}
                />
              </span>
            ))
          : null}
      </p>,
    );
    paragraphIndex += 1;
  }

  return <div className="ting-research-detail__markdown">{elements}</div>;
}

export function splitTableRow(row: string): string[] {
  const trimmed = row.trim().replace(/^\|/, '').replace(/\|$/, '');
  return trimmed.split('|').map((cell) => cell.trim());
}

function renderInlineRich(
  text: string,
  activeCitation: CitationPopoverState | null,
  onCitationClick: (citation: CitationPopoverState | null) => void,
): React.ReactNode {
  const parts: React.ReactNode[] = [];
  let cursor = 0;
  let key = 0;
  const tokenMatcher = /(\*\*[^*]+\*\*|`[^`]+`|\[[^\]]+\]\([^)]+\)|\[(s|c)\d+\])/g;
  let match: RegExpExecArray | null;
  while ((match = tokenMatcher.exec(text)) !== null) {
    if (match.index > cursor) {
      parts.push(text.slice(cursor, match.index));
    }
    const token = match[0];
    if (token.startsWith('**') && token.endsWith('**')) {
      parts.push(<strong key={`strong-${key++}`}>{token.slice(2, -2)}</strong>);
    } else if (token.startsWith('`') && token.endsWith('`')) {
      parts.push(
        <code key={`code-${key++}`} className="ting-research-detail__inline-code">
          {token.slice(1, -1)}
        </code>,
      );
    } else if (/^\[[^\]]+\]\([^)]+\)$/.test(token)) {
      const labelEnd = token.indexOf('](');
      const label = token.slice(1, labelEnd);
      const href = token.slice(labelEnd + 2, -1);
      parts.push(
        <a
          key={`link-${key++}`}
          href={href}
          target="_blank"
          rel="noreferrer"
          className="ting-research-detail__markdown-link"
        >
          {label}
        </a>,
      );
    } else {
      const label = token.slice(1, -1);
      const kind = label.startsWith('c') ? 'critique' : 'source';
      const anchor = `${kind}-${label}`;
      parts.push(
        <span key={`citation-${key++}`} className="ting-research-detail__citation-anchor">
          <InlineCitation
            label={label}
            kind={kind}
            isActive={activeCitation?.anchor === anchor}
            onClick={(event) => {
              if (activeCitation?.anchor === anchor) {
                onCitationClick(null);
                return;
              }
              const rect = event.currentTarget.getBoundingClientRect();
              onCitationClick({
                kind,
                key: label,
                anchor,
                x: rect.left,
                y: rect.bottom,
              });
            }}
          />
        </span>,
      );
    }
    cursor = tokenMatcher.lastIndex;
  }
  if (cursor < text.length) {
    parts.push(text.slice(cursor));
  }
  return parts.length === 1 ? parts[0] : parts;
}

function CitationPopover({
  citation,
  source,
  critique,
  onOpenDrawer,
  onClose,
}: {
  citation: CitationPopoverState;
  source: SourceVm | null;
  critique: CritiqueVm | null;
  onOpenDrawer: () => void;
  onClose: () => void;
}) {
  const isSource = citation.kind === 'source';
  return (
    <div className={cx('ting-research-detail__popover', !isSource && 'is-critique')}>
      <div className="ting-research-detail__popover-eyebrow">
        {isSource ? 'Source' : 'Critique'} · [{citation.key}]
      </div>
      <div className="ting-research-detail__popover-title">
        {isSource ? (source?.title ?? 'Source') : (critique?.claim ?? 'Critique')}
      </div>
      <div className="ting-research-detail__popover-meta">
        {isSource
          ? `${source?.domain ?? 'unknown'} · cited x${source?.citedCount ?? 0} · q${source?.quality ?? 0}/5`
          : `${critique?.severity ?? 'med'} severity · against ${critique?.against ?? 'current thesis'}`}
      </div>
      <div className="ting-research-detail__popover-quote">
        {isSource
          ? (source?.excerpt ?? 'Excerpt from this source supporting the claim would surface here.')
          : (critique?.note ?? 'This critique challenges the current thesis.')}
      </div>
      <div className="ting-research-detail__popover-actions">
        <button type="button" onClick={onOpenDrawer}>
          open
        </button>
        <button type="button" onClick={onClose}>
          close
        </button>
      </div>
    </div>
  );
}

export function ResearchCampaignPage() {
  const { slug } = useParams({ from: '/ting/research/$slug' });
  const navigate = useNavigate();
  const ctx = usePluginCtx();
  const queryClient = useQueryClient();
  const research = useService<IResearchService>('ting.research');
  const dispatcher = useService<IDispatcherService>('ting.dispatcher');
  const mimir = useService<MimirServiceLike>('mimir');
  const { data: campaign, isLoading, isError, error } = useResearchCampaign(slug);
  const deleteCampaign = useDeleteResearchCampaign();
  const [drawer, setDrawer] = useState<DrawerState>(() => readDrawerState());
  const [viewMode, setViewMode] = useState<ViewMode>('clean');
  const [popoverCitation, setPopoverCitation] = useState<CitationPopoverState | null>(null);
  const [evidenceOpenOverride, setEvidenceOpenOverride] = useState<boolean | null>(null);
  const [skepticOpenOverride, setSkepticOpenOverride] = useState<boolean | null>(null);
  const [memoryOpenOverride, setMemoryOpenOverride] = useState<boolean | null>(null);
  const [learningsOpenOverride, setLearningsOpenOverride] = useState<boolean | null>(null);

  const activityLog = useQuery({
    queryKey: ['ting', 'research', 'campaign', slug, 'activity'],
    queryFn: () => dispatcher.getActivityLog(150),
    refetchInterval: 15000,
  });

  useEffect(() => {
    const sync = () => setDrawer(readDrawerState());
    window.addEventListener('popstate', sync);
    return () => window.removeEventListener('popstate', sync);
  }, []);

  useEffect(() => {
    writeDrawerState(drawer);
  }, [drawer]);

  useEffect(() => {
    if (typeof window === 'undefined') return;
    const source = new EventSource('/api/v1/ting/events');
    const invalidate = (event: MessageEvent) => {
      try {
        const data = JSON.parse(event.data) as { slug?: string };
        if (data.slug && data.slug !== slug) return;
      } catch {
        return;
      }
      void queryClient.invalidateQueries({ queryKey: ['ting', 'research', 'campaign', slug] });
      void queryClient.invalidateQueries({ queryKey: ['ting', 'research', 'campaigns'] });
      void queryClient.invalidateQueries({
        queryKey: ['ting', 'research', 'campaign', slug, 'activity'],
      });
    };
    source.addEventListener('workflow.campaign.updated', invalidate);
    source.addEventListener('workflow.campaign.completed', invalidate);
    source.addEventListener('workflow.campaign.failed', invalidate);
    source.addEventListener('workflow.campaign.deleted', invalidate);
    return () => source.close();
  }, [queryClient, slug]);

  const artifactQueries = useQueries({
    queries: (campaign?.artifacts ?? []).map((artifact) => ({
      queryKey: ['ting', 'research', 'campaign', slug, 'artifact', artifact.path, 'detail'],
      queryFn: () => research.getArtifact(slug, artifact.path),
      enabled: !!campaign,
      staleTime: 15_000,
    })),
  });

  const artifactDetails = useMemo(
    () => artifactQueries.map((query) => query.data).filter(Boolean) as CampaignArtifactDetail[],
    [artifactQueries],
  );

  const parsedArtifacts = useMemo<ParsedArtifact[]>(() => {
    return artifactDetails.map((artifact) => {
      const { frontmatter, body } = parseFrontmatter(artifact.content);
      return {
        ...artifact,
        frontmatter,
        body,
        displayTitle: artifactDisplayTitle(artifact),
      };
    });
  }, [artifactDetails]);

  const openMimirPage = (path: string | null | undefined) => {
    if (!path) return;
    ctx.setTweak('mimir.selectedPagePath', path);
    void navigate({ to: '/mimir/pages' });
  };

  const artifactByPath = useMemo(() => {
    const map = new Map<string, ParsedArtifact>();
    for (const artifact of parsedArtifacts) map.set(artifact.path, artifact);
    return map;
  }, [parsedArtifacts]);

  const artifactByKind = useMemo(() => {
    const map = new Map<string, ParsedArtifact>();
    for (const artifact of parsedArtifacts) {
      if (artifact.kind && !map.has(artifact.kind)) {
        map.set(artifact.kind, artifact);
      }
    }
    return map;
  }, [parsedArtifacts]);

  const sourceIds = useMemo(() => {
    const ids = new Set<string>();
    for (const artifact of parsedArtifacts) {
      for (const id of artifact.sourceIds ?? []) ids.add(id);
      const extraIds = Array.isArray(artifact.frontmatter.source_ids)
        ? (artifact.frontmatter.source_ids as string[])
        : [];
      for (const id of extraIds) ids.add(id);
      const bodyMatches = artifact.content.match(/src_[a-z0-9]+/gi) ?? [];
      for (const id of bodyMatches) ids.add(id);
    }
    return Array.from(ids);
  }, [parsedArtifacts]);

  const sourcesQuery = useQuery({
    queryKey: ['ting', 'research', 'campaign', slug, 'sources', sourceIds.join(',')],
    queryFn: async () => {
      const all = await mimir.pages.listSources();
      return all.filter((source) => sourceIds.includes(source.id));
    },
    enabled: sourceIds.length > 0,
    staleTime: 15_000,
  });

  const sourceList = useMemo<SourceVm[]>(() => {
    const orderedSources = [...(sourcesQuery.data ?? [])].sort((left, right) => {
      const leftCount = left.compiledInto.filter(
        (path) => path.includes(`/research/campaigns/${slug}/`) || path.endsWith(`${slug}.md`),
      ).length;
      const rightCount = right.compiledInto.filter(
        (path) => path.includes(`/research/campaigns/${slug}/`) || path.endsWith(`${slug}.md`),
      ).length;
      return rightCount - leftCount;
    });
    return orderedSources.map((source, index) => ({
      id: source.id,
      citation: `s${index + 1}`,
      title: source.title,
      domain: inferDomain(source),
      quality: qualityForSource(source),
      citedCount:
        parsedArtifacts.filter((artifact) => artifact.sourceIds.includes(source.id)).length ||
        source.compiledInto.filter((path) => artifactByPath.has(path)).length ||
        1,
      compiledInto:
        source.compiledInto.filter((path) => artifactByPath.has(path)).length > 0
          ? source.compiledInto.filter((path) => artifactByPath.has(path))
          : parsedArtifacts
              .filter((artifact) => artifact.sourceIds.includes(source.id))
              .map((artifact) => artifact.path),
      excerpt: takeExcerpt(source.content),
      kind: inferSourceKind(source),
      content: source.content,
      originUrl: source.originUrl,
    }));
  }, [artifactByPath, parsedArtifacts, slug, sourcesQuery.data]);

  const sourceByCitation = useMemo(() => {
    const map = new Map<string, SourceVm>();
    for (const source of sourceList) map.set(source.citation, source);
    return map;
  }, [sourceList]);

  const critiques = useMemo(() => {
    const critiqueArtifact = artifactByKind.get('critique');
    if (!critiqueArtifact) return [] as CritiqueVm[];
    const linkedArtifacts = ['research/campaigns/' + slug + '/critique.md'];
    const finalArtifact = artifactByKind.get('final');
    if (finalArtifact) linkedArtifacts.push(finalArtifact.path);
    return parseCritiques(critiqueArtifact.body, linkedArtifacts);
  }, [artifactByKind, slug]);

  const critiqueByCitation = useMemo(() => {
    const map = new Map<string, CritiqueVm>();
    for (const critique of critiques) map.set(critique.citation, critique);
    return map;
  }, [critiques]);

  const finalArtifact = artifactByKind.get('final') ?? null;
  const manifestArtifact = artifactByKind.get('manifest') ?? null;
  const learningsArtifact = artifactByKind.get('learnings') ?? null;
  const followupsArtifact = artifactByKind.get('followups') ?? null;
  const workingThesis = useMemo(() => deriveWorkingThesis(parsedArtifacts), [parsedArtifacts]);
  const derivedState = useMemo(
    () => (campaign ? deriveCampaignState(campaign, parsedArtifacts) : 'draft'),
    [campaign, parsedArtifacts],
  );
  const activeStage = useMemo(
    () => (campaign ? findActiveStage(campaign.stageState) : undefined),
    [campaign],
  );
  const confidence = useMemo(
    () => confidenceFromArtifacts(derivedState, finalArtifact, sourceList.length, critiques.length),
    [critiques.length, derivedState, finalArtifact, sourceList.length],
  );
  const selectedFilePath =
    drawer.file ??
    finalArtifact?.path ??
    artifactByKind.get('analysis')?.path ??
    artifactByKind.get('brief')?.path ??
    parsedArtifacts[0]?.path;
  const selectedFileArtifact = selectedFilePath
    ? (artifactByPath.get(selectedFilePath) ?? null)
    : null;
  const selectedSource = useMemo(() => {
    const sourceFromDrawer = drawer.sourceId
      ? (sourceList.find((source) => source.id === drawer.sourceId) ?? null)
      : null;
    if (sourceFromDrawer) return sourceFromDrawer;

    const sourceFromCitation =
      popoverCitation?.kind === 'source'
        ? (sourceByCitation.get(popoverCitation.key) ?? null)
        : null;
    return sourceFromCitation ?? sourceList[0] ?? null;
  }, [drawer.sourceId, popoverCitation, sourceByCitation, sourceList]);
  const selectedCritique = useMemo(() => {
    const critiqueFromDrawer = drawer.critiqueId
      ? (critiques.find((critique) => critique.id === drawer.critiqueId) ?? null)
      : null;
    if (critiqueFromDrawer) return critiqueFromDrawer;

    const critiqueFromCitation =
      popoverCitation?.kind === 'critique'
        ? (critiqueByCitation.get(popoverCitation.key) ?? null)
        : null;
    return critiqueFromCitation ?? critiques[0] ?? null;
  }, [critiqueByCitation, critiques, drawer.critiqueId, popoverCitation]);

  const filteredActivity = (activityLog.data ?? []).filter((event) => {
    const eventSlug = typeof event.data.slug === 'string' ? event.data.slug : undefined;
    const sessionId = typeof event.data.session_id === 'string' ? event.data.session_id : undefined;
    return eventSlug === slug || (campaign?.sessionId && sessionId === campaign.sessionId);
  });

  const latestActivity = filteredActivity[0];
  const sessionsCount = Math.max(
    1,
    new Set(
      filteredActivity
        .map((event) => String(event.data.persona ?? event.data.raven ?? ''))
        .filter(Boolean),
    ).size,
  );

  const learnings = useMemo(
    () => (learningsArtifact ? parseListCards(learningsArtifact.body) : []),
    [learningsArtifact],
  );
  const followups = useMemo(
    () => (followupsArtifact ? parseListCards(followupsArtifact.body) : []),
    [followupsArtifact],
  );

  const memoryGroups = useMemo(() => {
    const published = parsedArtifacts.filter((artifact) => artifact.publishState === 'published');
    const reviewReady = parsedArtifacts.filter(
      (artifact) => artifact.publishState === 'unpublished',
    );
    const notebook = parsedArtifacts.filter((artifact) => artifact.publishState === 'unknown');
    return { published, reviewReady, notebook };
  }, [parsedArtifacts]);
  const campaignPanelState = campaign ? deriveCampaignState(campaign, []) : 'draft';
  const evidenceOpen = evidenceOpenOverride ?? campaignPanelState === 'published';
  const skepticOpen =
    skepticOpenOverride ??
    (campaignPanelState === 'running' ||
      campaignPanelState === 'review' ||
      campaignPanelState === 'blocked');
  const memoryOpen = memoryOpenOverride ?? campaignPanelState === 'published';
  const learningsOpen =
    learningsOpenOverride ??
    (campaignPanelState === 'published' || campaignPanelState === 'review');

  const visibleCitationSourceLabels = sourceList.slice(0, 3).map((source) => source.citation);
  const popoverSource =
    popoverCitation?.kind === 'source' ? (sourceByCitation.get(popoverCitation.key) ?? null) : null;
  const popoverCritique =
    popoverCitation?.kind === 'critique'
      ? (critiqueByCitation.get(popoverCitation.key) ?? null)
      : null;

  function openDrawer(next: Partial<DrawerState>) {
    setDrawer((current) => ({
      open: true,
      tab: next.tab ?? current.tab,
      file: next.file ?? current.file,
      sourceId: next.sourceId ?? current.sourceId,
      critiqueId: next.critiqueId ?? current.critiqueId,
      operatorSub: next.operatorSub ?? current.operatorSub ?? 'activity',
    }));
  }

  async function handleDeleteCampaign() {
    if (!campaign) return;
    if (typeof window !== 'undefined') {
      const confirmed = window.confirm(
        `Delete research campaign "${campaign.name}"? This removes the campaign record from Ting but does not delete any existing Mimir pages.`,
      );
      if (!confirmed) return;
    }
    await deleteCampaign.mutateAsync(campaign.slug);
    await queryClient.invalidateQueries({ queryKey: ['ting', 'research', 'campaigns'] });
    void navigate({ to: '/ting/research' });
  }

  if (isLoading) {
    return <div className="ting-research-detail__loading">Loading campaign…</div>;
  }

  if (isError || !campaign) {
    return (
      <div className="ting-research-detail__error">
        {error instanceof Error ? error.message : 'Campaign not found.'}
      </div>
    );
  }

  return (
    <div className="ting-research-detail">
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

      <div className="ting-research-detail__canvas">
        <div className="ting-research-detail__content">
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
              <span
                className={cx('ting-research-detail__state-dot', statusDotClass(derivedState))}
              />
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
          <p className="ting-research-detail__question">
            {String(campaign.metadata.question ?? '')}
          </p>

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
                          <div className="ting-research-detail__annotation-title">
                            {source.title}
                          </div>
                          <div className="ting-research-detail__annotation-body">
                            {source.excerpt}
                          </div>
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
                          <div className="ting-research-detail__annotation-title">
                            {critique.claim}
                          </div>
                          <div className="ting-research-detail__annotation-body">
                            {critique.note}
                          </div>
                          <button
                            type="button"
                            className="ting-research-detail__annotation-action"
                            onClick={() =>
                              openDrawer({ tab: 'critiques', critiqueId: critique.id })
                            }
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
                  A synthesized answer will appear here once the campaign reaches the synthesis
                  stage.
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
                    <span className="ting-research-detail__hero-meta-value">
                      {sourceList.length}
                    </span>
                  </div>
                  <div className="ting-research-detail__hero-meta-row">
                    <span className="ting-research-detail__hero-meta-label">
                      Critiques addressed
                    </span>
                    <span className="ting-research-detail__hero-meta-value">
                      {critiques.length}
                    </span>
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
                    <span className="ting-research-detail__hero-meta-label">
                      Elapsed since block
                    </span>
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
                      className={cx(
                        'ting-research-detail__severity-pill',
                        `is-${critique.severity}`,
                      )}
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
                  <span className="ting-research-detail__memory-title">
                    {artifact.displayTitle}
                  </span>
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
                    <span className="ting-research-detail__memory-title">
                      {artifact.displayTitle}
                    </span>
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
        </div>
      </div>

      {drawer.open ? (
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
      ) : null}
    </div>
  );
}
