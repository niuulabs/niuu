import { beforeEach, describe, expect, it } from 'vitest';
import {
  artifactDisplayTitle,
  citationToken,
  confidenceFromArtifacts,
  countWords,
  cx,
  deriveCampaignState,
  deriveWorkingThesis,
  drawerSectionCountLabel,
  findActiveStage,
  firstParagraph,
  formatClock,
  formatElapsed,
  formatKiloWords,
  inferDomain,
  inferSourceKind,
  normalizeStageLabel,
  parseCritiques,
  parseFrontmatter,
  parseListCards,
  parseYamlScalar,
  qualityForSource,
  readDrawerState,
  sentenceCase,
  stageTickClass,
  statusDotClass,
  stripHeading,
  takeExcerpt,
  writeDrawerState,
  type MimirSource,
  type ParsedArtifact,
} from './researchCampaignModel';

const source = (overrides: Partial<MimirSource> = {}): MimirSource => ({
  id: 'source-1',
  title: 'Source',
  originType: 'web',
  ingestedAt: '2026-01-01T00:00:00Z',
  ingestAgent: 'researcher',
  compiledInto: [],
  content: 'content',
  ...overrides,
});

const artifact = (overrides: Partial<ParsedArtifact> = {}): ParsedArtifact =>
  ({
    id: 'artifact-1',
    path: 'campaign/final.md',
    title: 'Final',
    kind: 'final',
    publishState: 'draft',
    body: 'Useful thesis.',
    frontmatter: {},
    displayTitle: 'Final',
    ...overrides,
  }) as ParsedArtifact;

const campaign = (status: string, stageState: Array<{ label: string; status: string }> = []) =>
  ({ status, stageState }) as any;

describe('research campaign model', () => {
  beforeEach(() => {
    window.history.replaceState({}, '', '/ting/research');
  });

  it('joins conditional classes and manages drawer query state', () => {
    expect(cx('a', false, null, undefined, 'b')).toBe('a b');
    expect(readDrawerState()).toEqual({ open: false, tab: 'files' });

    window.history.replaceState(
      {},
      '',
      '/ting/research?drawer=sources&n=source-1&file=ignored&c=ignored&sub=run',
    );
    expect(readDrawerState()).toMatchObject({
      open: true,
      tab: 'sources',
      sourceId: 'source-1',
      file: 'ignored',
      critiqueId: 'ignored',
      operatorSub: 'run',
    });

    window.history.replaceState({}, '', '/ting/research?drawer=unknown');
    expect(readDrawerState()).toMatchObject({ open: true, tab: 'files', operatorSub: 'activity' });

    writeDrawerState({ open: true, tab: 'files', file: 'brief.md' });
    expect(window.location.search).toBe('?drawer=files&file=brief.md');
    writeDrawerState({ open: true, tab: 'sources', sourceId: 'source-2' });
    expect(window.location.search).toBe('?drawer=sources&n=source-2');
    writeDrawerState({ open: true, tab: 'critiques', critiqueId: 'c2' });
    expect(window.location.search).toBe('?drawer=critiques&c=c2');
    writeDrawerState({ open: true, tab: 'operator', operatorSub: 'actions' });
    expect(window.location.search).toBe('?drawer=operator&sub=actions');
    writeDrawerState({ open: false, tab: 'files' });
    expect(window.location.search).toBe('');
  });

  it('formats clocks, elapsed time, and word counts defensively', () => {
    expect(formatClock()).toBe('unknown');
    expect(formatClock('not-a-date')).toBe('not-a-date');
    expect(formatClock('2026-01-02T03:04:00Z')).not.toBe('unknown');
    expect(formatElapsed()).toBe('—');
    expect(formatElapsed('bad', 'also-bad')).toBe('—');
    expect(formatElapsed('2026-01-02T03:00:00Z', '2026-01-02T02:00:00Z')).toBe('—');
    expect(formatElapsed('2026-01-02T03:00:00Z', '2026-01-02T03:00:20Z')).toBe('1m');
    expect(formatElapsed('2026-01-02T03:00:00Z', '2026-01-02T05:05:00Z')).toBe('2h 5m');
    expect(formatElapsed('2026-01-01T03:00:00Z', '2026-01-03T05:00:00Z')).toBe('2d 2h');
    expect(countWords('  one   two ')).toBe(2);
    expect(formatKiloWords('one two')).toBe('2');
    expect(formatKiloWords(Array.from({ length: 1200 }, () => 'word').join(' '))).toBe('1.2k');
  });

  it('parses scalar values and frontmatter', () => {
    expect(parseYamlScalar('')).toBe('');
    expect(parseYamlScalar('true')).toBe(true);
    expect(parseYamlScalar('false')).toBe(false);
    expect(parseYamlScalar('null')).toBeNull();
    expect(parseYamlScalar('12.5')).toBe(12.5);
    expect(parseYamlScalar("['one', two, '']")).toEqual(['one', 'two']);
    expect(parseYamlScalar('"quoted"')).toBe('quoted');
    expect(parseFrontmatter('plain body')).toEqual({ frontmatter: {}, body: 'plain body' });
    expect(
      parseFrontmatter('---\n# comment\ntitle: "Report"\ninvalid\nenabled: true\n---\n# Body'),
    ).toEqual({ frontmatter: { title: 'Report', enabled: true }, body: '# Body' });
  });

  it('normalizes headings, paragraphs, labels, and sentence case', () => {
    expect(stripHeading('# Title\nBody')).toBe('Body');
    expect(firstParagraph('# Title\nFirst.\n\nSecond.')).toBe('First.');
    expect(firstParagraph('')).toBe('');
    expect(sentenceCase('')).toBe('');
    expect(sentenceCase('research')).toBe('Research');
    expect(normalizeStageLabel(undefined)).toBe('Research');
    for (const [label, expected] of [
      ['framing', 'Frame'],
      ['exploration', 'Explore'],
      ['challenge assumptions', 'Challenge'],
      ['synthesis', 'Synthesize'],
      ['curation', 'Curate'],
      ['publishing', 'Publish'],
      ['completed', 'Complete'],
      ['Custom', 'Custom'],
    ]) {
      expect(normalizeStageLabel({ label, status: 'pending' } as any)).toBe(expected);
    }
  });

  it('derives every campaign state and active-stage fallback', () => {
    expect(deriveCampaignState(campaign('failed'), [])).toBe('failed');
    expect(deriveCampaignState(campaign('running', [{ label: 'x', status: 'failed' }]), [])).toBe(
      'failed',
    );
    expect(deriveCampaignState(campaign('blocked'), [])).toBe('blocked');
    expect(deriveCampaignState(campaign('running', [{ label: 'x', status: 'blocked' }]), [])).toBe(
      'blocked',
    );
    expect(deriveCampaignState(campaign('pending'), [])).toBe('draft');
    expect(
      deriveCampaignState(campaign('completed'), [artifact({ publishState: 'published' })]),
    ).toBe('published');
    expect(
      deriveCampaignState(campaign('completed'), [
        artifact({ kind: 'manifest', publishState: 'published' }),
      ]),
    ).toBe('published');
    expect(deriveCampaignState(campaign('completed'), [artifact()])).toBe('review');
    expect(deriveCampaignState(campaign('running', [{ label: 'x', status: 'active' }]), [])).toBe(
      'running',
    );
    expect(deriveCampaignState(campaign('running', [{ label: 'x', status: 'pending' }]), [])).toBe(
      'running',
    );
    expect(deriveCampaignState(campaign('completed'), [])).toBe('running');
    expect(deriveCampaignState(campaign('running'), [])).toBe('running');

    const stages = [
      { label: 'one', status: 'complete' },
      { label: 'two', status: 'active' },
    ] as any;
    expect(findActiveStage(stages)?.label).toBe('two');
    expect(findActiveStage([{ label: 'one', status: 'complete' }] as any)?.label).toBe('one');
    expect(findActiveStage([])).toBeUndefined();
  });

  it('derives confidence from explicit metadata, state, and evidence volume', () => {
    expect(
      confidenceFromArtifacts('running', artifact({ frontmatter: { confidence: 'HIGH' } }), 0, 0),
    ).toEqual({ percent: 78, label: 'high', raw: 'high' });
    expect(
      confidenceFromArtifacts('running', artifact({ frontmatter: { confidence: 'medium' } }), 0, 0)
        .label,
    ).toBe('med');
    expect(
      confidenceFromArtifacts('running', artifact({ frontmatter: { confidence: 'low' } }), 0, 0)
        .label,
    ).toBe('low');
    expect(confidenceFromArtifacts('published', null, 0, 0).percent).toBe(84);
    expect(confidenceFromArtifacts('review', null, 0, 0).percent).toBe(78);
    expect(confidenceFromArtifacts('blocked', null, 0, 0).percent).toBe(55);
    expect(confidenceFromArtifacts('failed', null, 0, 0).percent).toBe(34);
    expect(confidenceFromArtifacts('running', null, 0, 0).label).toBe('low');
    expect(confidenceFromArtifacts('running', null, 4, 0).label).toBe('med');
    expect(confidenceFromArtifacts('running', null, 12, 8).label).toBe('high');
  });

  it('derives artifact titles and a working thesis', () => {
    expect(artifactDisplayTitle(artifact())).toBe('Final');
    expect(artifactDisplayTitle(artifact({ title: '', path: 'notes/exploration.md' }))).toBe(
      'exploration.md',
    );
    expect(artifactDisplayTitle(artifact({ title: '', path: '' }))).toBe('');
    expect(deriveWorkingThesis([artifact({ kind: 'analysis', body: '# Analysis\nThesis.' })])).toBe(
      'Thesis.',
    );
    expect(deriveWorkingThesis([artifact({ kind: 'brief', body: 'Brief thesis.' })])).toBe(
      'Brief thesis.',
    );
    expect(deriveWorkingThesis([artifact({ kind: 'analysis', body: '' })])).toContain(
      'still building',
    );
  });

  it('maps source metadata and excerpts', () => {
    expect(inferDomain(source({ originUrl: 'https://www.example.com/path' }))).toBe('example.com');
    expect(inferDomain(source({ originUrl: 'not a url' }))).toBe('not a url');
    expect(inferDomain(source({ originPath: '/docs/file.md' }))).toBe('/docs/file.md');
    expect(inferDomain(source({ originType: 'mail' }))).toBe('mail');
    expect(inferSourceKind(source({ originType: 'arxiv' }))).toBe('paper');
    expect(inferSourceKind(source({ originType: 'file' }))).toBe('file');
    expect(inferSourceKind(source({ originType: 'chat' }))).toBe('chat');
    expect(inferSourceKind(source({ originType: 'rss' }))).toBe('feed');
    expect(inferSourceKind(source({ originType: 'web' }))).toBe('web');
    expect(qualityForSource(source({ originType: 'arxiv' }))).toBe(5);
    expect(qualityForSource(source({ originType: 'file' }))).toBe(5);
    expect(qualityForSource(source({ originType: 'rss' }))).toBe(4);
    expect(qualityForSource(source({ originType: 'web' }))).toBe(4);
    expect(qualityForSource(source({ originType: 'mail' }))).toBe(3);
    expect(takeExcerpt('   ')).toContain('No excerpt');
    expect(takeExcerpt('a'.repeat(221))).toHaveLength(221);
    expect(takeExcerpt('short')).toBe('short');
  });

  it('parses structured and bullet critiques', () => {
    const structured = parseCritiques(
      '# Critiques\n## First\nAgainst: Thesis\nSeverity: high\nImportant note.\n## Second\nSeverity: low\n## Third\nA concern.\n## Fourth\nAnother concern.',
      ['final.md'],
    );
    expect(structured).toHaveLength(4);
    expect(structured[0]).toMatchObject({
      against: 'Thesis',
      severity: 'high',
      note: 'Important note.',
    });
    expect(structured[1]).toMatchObject({ against: 'Current working thesis', severity: 'low' });
    expect(structured[2]?.severity).toBe('med');
    expect(structured[3]?.severity).toBe('low');
    expect(parseCritiques('- First objection\n* Second objection', [])).toMatchObject([
      { claim: 'First objection', severity: 'high' },
      { claim: 'Second objection', severity: 'med' },
    ]);
    expect(parseCritiques('No objections', [])).toEqual([]);
  });

  it('parses list cards and display helper classes', () => {
    expect(parseListCards('# List\n## Heading\nBody text\n- Item: Detail\n* Plain')).toEqual([
      { title: 'Heading', body: 'Body text' },
      { title: 'Item', body: 'Detail' },
      { title: 'Plain', body: 'Plain' },
    ]);
    expect(citationToken('n1')).toBe('[n1]');
    expect(statusDotClass('published')).toBe('is-brand');
    expect(statusDotClass('review')).toBe('is-brand');
    expect(statusDotClass('running')).toBe('is-brand');
    expect(statusDotClass('blocked')).toBe('is-amber');
    expect(statusDotClass('failed')).toBe('is-rose');
    expect(statusDotClass('draft')).toBe('is-muted');
    expect(stageTickClass('complete')).toBe('is-complete');
    expect(stageTickClass('active')).toBe('is-active');
    expect(stageTickClass('blocked')).toBe('is-blocked');
    expect(stageTickClass('failed')).toBe('is-failed');
    expect(stageTickClass('pending')).toBe('is-pending');
    expect(drawerSectionCountLabel(1, 'source', 'sources')).toBe('1 source');
    expect(drawerSectionCountLabel(2, 'source', 'sources')).toBe('2 sources');
  });
});
