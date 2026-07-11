import type {
  CampaignArtifactDetail,
  CampaignStageState,
  ResearchCampaignDetail,
} from '../domain/research';

export type DerivedCampaignState =
  'draft' | 'running' | 'blocked' | 'failed' | 'review' | 'published';

export type DrawerTab = 'files' | 'sources' | 'critiques' | 'operator';
export type OperatorSubTab = 'activity' | 'run' | 'actions';
export type ViewMode = 'clean' | 'annotated';

export interface MimirSource {
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

export interface MimirServiceLike {
  pages: {
    listSources(options?: { originType?: string; mountName?: string }): Promise<MimirSource[]>;
  };
}

export interface ParsedArtifact extends CampaignArtifactDetail {
  body: string;
  frontmatter: Record<string, unknown>;
  displayTitle: string;
}

export interface SourceVm {
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

export interface CritiqueVm {
  id: string;
  citation: string;
  claim: string;
  against: string;
  note: string;
  severity: 'high' | 'med' | 'low';
  linkedArtifacts: string[];
}

export interface DrawerState {
  open: boolean;
  tab: DrawerTab;
  file?: string;
  sourceId?: string;
  critiqueId?: string;
  operatorSub?: OperatorSubTab;
}

export interface CitationTarget {
  kind: 'source' | 'critique';
  key: string;
}

export interface CitationPopoverState extends CitationTarget {
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
  if (label.includes('fram')) return 'Frame';
  if (label.includes('explor')) return 'Explore';
  if (label.includes('challenge')) return 'Challenge';
  if (label.includes('synth')) return 'Synthesize';
  if (label.includes('curat')) return 'Curate';
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
  if (campaign.status === 'completed') return 'running';
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
    percent: Math.min(74, base),
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
    const rawHeading = lines[0] ?? '';
    if (!rawHeading.startsWith('## ')) continue;
    const heading = rawHeading.replace(/^##\s+/, '');
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
