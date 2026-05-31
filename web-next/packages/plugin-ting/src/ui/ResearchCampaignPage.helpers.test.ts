import { describe, expect, it, vi } from 'vitest';
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
  splitTableRow,
  stageTickClass,
  statusDotClass,
  stripHeading,
  takeExcerpt,
  writeDrawerState,
} from './ResearchCampaignPage';

const now = '2026-05-25T12:00:00.000Z';

describe('ResearchCampaignPage helpers', () => {
  it('reads and writes drawer state from the location search params', () => {
    window.history.replaceState({}, '', '/research?drawer=sources&n=src-1&sub=run');
    expect(readDrawerState()).toEqual({
      open: true,
      tab: 'sources',
      file: undefined,
      sourceId: 'src-1',
      critiqueId: undefined,
      operatorSub: 'run',
    });

    writeDrawerState({ open: true, tab: 'files', file: 'final.md' });
    expect(window.location.search).toContain('drawer=files');
    expect(window.location.search).toContain('file=final.md');

    writeDrawerState({ open: false, tab: 'files' });
    expect(window.location.search).toBe('');

    window.history.replaceState({}, '', '/research?drawer=unknown&c=crit-2');
    expect(readDrawerState()).toEqual({
      open: true,
      tab: 'files',
      file: undefined,
      sourceId: undefined,
      critiqueId: 'crit-2',
      operatorSub: 'activity',
    });
  });

  it('formats clocks, elapsed time, word counts, and simple YAML scalars', () => {
    expect(formatClock(null)).toBe('unknown');
    expect(formatClock('not-a-date')).toBe('not-a-date');
    expect(formatElapsed(undefined, now)).toBe('—');
    expect(formatElapsed(now, '2026-05-25T11:59:00.000Z')).toBe('—');
    expect(formatElapsed(now, '2026-05-25T12:45:00.000Z')).toBe('45m');
    expect(formatElapsed('2026-05-25T10:00:00.000Z', '2026-05-25T12:45:00.000Z')).toBe('2h 45m');
    expect(formatElapsed('2026-05-23T10:00:00.000Z', '2026-05-25T12:45:00.000Z')).toBe('2d 2h');
    expect(countWords(' one  two\nthree ')).toBe(3);
    expect(formatKiloWords('word '.repeat(1001))).toBe('1.0k');
    expect(parseYamlScalar('')).toBe('');
    expect(parseYamlScalar('false')).toBe(false);
    expect(parseYamlScalar('null')).toBeNull();
    expect(parseYamlScalar('true')).toBe(true);
    expect(parseYamlScalar('12.5')).toBe(12.5);
    expect(parseYamlScalar('[a, "b"]')).toEqual(['a', 'b']);
    expect(parseYamlScalar('"quoted"')).toBe('quoted');
  });

  it('parses frontmatter and basic text helpers', () => {
    const parsed = parseFrontmatter('---\nfoo: bar\ncount: 2\n---\n# Title\n\nFirst.\n\nSecond.');
    expect(parsed.frontmatter).toEqual({ foo: 'bar', count: 2 });
    expect(parseFrontmatter('plain body')).toEqual({ frontmatter: {}, body: 'plain body' });
    expect(stripHeading('# Title\n\nFirst paragraph')).toBe('First paragraph');
    expect(firstParagraph('# Title\n\nFirst paragraph.\n\nSecond paragraph.')).toBe('First paragraph.');
    expect(sentenceCase('published')).toBe('Published');
    expect(sentenceCase('')).toBe('');
    expect(cx('a', false, 'b', null, undefined, 'c')).toBe('a b c');
  });

  it('normalizes stage labels and derives campaign states', () => {
    expect(normalizeStageLabel(undefined)).toBe('Research');
    expect(normalizeStageLabel({ label: 'Frame the inquiry' } as never)).toBe('Frame');
    expect(normalizeStageLabel({ label: 'Explore the evidence' } as never)).toBe('Explore');
    expect(normalizeStageLabel({ label: 'Challenge the thesis' } as never)).toBe('Challenge');
    expect(normalizeStageLabel({ label: 'Synthesize conclusion' } as never)).toBe('Synthesize');
    expect(normalizeStageLabel({ label: 'Curate the memory' } as never)).toBe('Curate');
    expect(normalizeStageLabel({ label: 'Publish to Mimir' } as never)).toBe('Publish');
    expect(normalizeStageLabel({ label: 'Complete follow-up' } as never)).toBe('Complete');
    expect(normalizeStageLabel({ label: 'Custom stage' } as never)).toBe('Custom stage');

    const baseCampaign = {
      status: 'running',
      stageState: [{ status: 'active', label: 'Explore', stageId: 'explore' }],
    };
    const finalArtifact = [{ kind: 'final', publishState: 'published' }];
    expect(deriveCampaignState({ ...baseCampaign, status: 'failed' } as never, [] as never)).toBe(
      'failed',
    );
    expect(
      deriveCampaignState(
        { ...baseCampaign, status: 'running', stageState: [{ status: 'blocked', label: 'Explore' }] } as never,
        [] as never,
      ),
    ).toBe('blocked');
    expect(deriveCampaignState({ ...baseCampaign, status: 'pending', stageState: [] } as never, [] as never)).toBe(
      'draft',
    );
    expect(
      deriveCampaignState({ ...baseCampaign, status: 'completed', stageState: [] } as never, finalArtifact as never),
    ).toBe('published');
    expect(
      deriveCampaignState(
        { ...baseCampaign, status: 'completed', stageState: [] } as never,
        [{ kind: 'manifest', publishState: 'published' }] as never,
      ),
    ).toBe('published');
    expect(
      deriveCampaignState(
        { ...baseCampaign, status: 'completed', stageState: [] } as never,
        [{ kind: 'final', publishState: 'review-ready' }] as never,
      ),
    ).toBe('review');
    expect(
      deriveCampaignState(
        { ...baseCampaign, status: 'running', stageState: [{ status: 'pending', label: 'Explore' }] } as never,
        [] as never,
      ),
    ).toBe('running');
    expect(
      deriveCampaignState({ ...baseCampaign, status: 'completed', stageState: [] } as never, [] as never),
    ).toBe('running');
    expect(findActiveStage([{ status: 'complete' }, { status: 'failed' }] as never)).toMatchObject({
      status: 'failed',
    });
    expect(findActiveStage([{ status: 'complete' }, { status: 'pending' }] as never)).toMatchObject({
      status: 'pending',
    });
  });

  it('computes confidence and thesis defaults from artifact sets', () => {
    expect(confidenceFromArtifacts('published', null, 1, 1)).toMatchObject({
      percent: 84,
      label: 'high',
    });
    expect(confidenceFromArtifacts('review', null, 0, 0)).toMatchObject({ label: 'high' });
    expect(confidenceFromArtifacts('blocked', null, 0, 0)).toMatchObject({ label: 'med' });
    expect(
      confidenceFromArtifacts(
        'running',
        { frontmatter: { confidence: 'medium' } } as never,
        2,
        1,
      ),
    ).toMatchObject({ label: 'med' });
    expect(
      confidenceFromArtifacts('running', { frontmatter: { confidence: 'high' } } as never, 0, 0),
    ).toMatchObject({ label: 'high' });
    expect(
      confidenceFromArtifacts('running', { frontmatter: { confidence: 'low' } } as never, 0, 0),
    ).toMatchObject({ label: 'low' });
    expect(confidenceFromArtifacts('failed', null, 0, 0)).toMatchObject({ label: 'low' });
    expect(confidenceFromArtifacts('running', null, 10, 8)).toMatchObject({ label: 'high' });
    expect(confidenceFromArtifacts('running', null, 4, 0)).toMatchObject({ label: 'med' });
    expect(confidenceFromArtifacts('running', null, 0, 0)).toMatchObject({ label: 'low' });

    expect(
      deriveWorkingThesis(
        [
          {
            kind: 'analysis',
            path: 'research/campaigns/demo/analysis.md',
            body: '# Title\n\nFirst useful paragraph.',
          },
          {
            kind: 'final',
            path: 'research/campaigns/demo/final.md',
            body: '# Final\n\nIgnored because first candidate already wins.',
          },
        ] as never,
      ),
    ).toBe('First useful paragraph.');
    expect(
      deriveWorkingThesis(
        [
          {
            kind: 'final',
            path: 'research/campaigns/demo/final.md',
            body: '# Final\n\nFinal answer paragraph.',
          },
        ] as never,
      ),
    ).toBe('Final answer paragraph.');
    expect(
      deriveWorkingThesis(
        [
          {
            kind: 'note',
            path: 'research/campaigns/demo/notes/exploration.md',
            body: '# Notes\n\nExploration paragraph.',
          },
        ] as never,
      ),
    ).toBe('Exploration paragraph.');
    expect(
      deriveWorkingThesis(
        [
          {
            kind: 'brief',
            path: 'research/campaigns/demo/brief.md',
            body: '# Brief\n\nBrief paragraph.',
          },
        ] as never,
      ),
    ).toBe('Brief paragraph.');
    expect(deriveWorkingThesis([] as never)).toContain('still building');
    expect(
      artifactDisplayTitle({ title: '', path: 'research/campaigns/demo/final.md' } as never),
    ).toBe('final.md');
    expect(artifactDisplayTitle({ title: 'Named', path: 'research/campaigns/demo/final.md' } as never)).toBe(
      'Named',
    );
  });

  it('infers source domains, kinds, quality, and excerpts', () => {
    expect(
      inferDomain({ originUrl: 'https://www.example.com/path', originType: 'web' } as never),
    ).toBe('example.com');
    expect(inferDomain({ originUrl: 'not a url', originType: 'web' } as never)).toBe('not a url');
    expect(inferDomain({ originPath: '/tmp/a.txt', originType: 'file' } as never)).toBe('/tmp/a.txt');
    expect(inferDomain({ originType: 'chat' } as never)).toBe('chat');

    expect(inferSourceKind({ originType: 'arxiv' } as never)).toBe('paper');
    expect(inferSourceKind({ originType: 'file' } as never)).toBe('file');
    expect(inferSourceKind({ originType: 'chat' } as never)).toBe('chat');
    expect(inferSourceKind({ originType: 'rss' } as never)).toBe('feed');
    expect(inferSourceKind({ originType: 'web' } as never)).toBe('web');

    expect(qualityForSource({ originType: 'arxiv' } as never)).toBe(5);
    expect(qualityForSource({ originType: 'file' } as never)).toBe(5);
    expect(qualityForSource({ originType: 'web' } as never)).toBe(4);
    expect(qualityForSource({ originType: 'rss' } as never)).toBe(4);
    expect(qualityForSource({ originType: 'chat' } as never)).toBe(3);
    expect(takeExcerpt('')).toContain('No excerpt');
    expect(takeExcerpt('short excerpt')).toBe('short excerpt');
    expect(takeExcerpt('word '.repeat(80))).toContain('…');
  });

  it('parses critique markdown, list cards, table rows, and UI class helpers', () => {
    expect(
      parseCritiques(
        '# Skeptic\n\n## Claim one\nagainst: thesis\nseverity: high\n\nNeeds more proof.',
        ['final.md'],
      )[0],
    ).toMatchObject({
      citation: 'c1',
      against: 'thesis',
        severity: 'high',
        linkedArtifacts: ['final.md'],
      });
    expect(
      parseCritiques('# Skeptic\n\n## First objection\n\nBody\n\n## Second objection\n\nBody', [
        'final.md',
      ]).map((item) => item.severity),
    ).toEqual(['high', 'med']);
    expect(
      parseCritiques('# Skeptic\n\n- First objection\n- Second objection\n- Third objection', [
        'final.md',
      ])[0],
    ).toMatchObject({
      claim: '- First objection',
      severity: 'high',
      against: 'Current working thesis',
    });
    expect(parseCritiques('# Skeptic\n\nNo critiques surfaced.', ['final.md'])).toEqual([
      expect.objectContaining({
        claim: 'No critiques surfaced.',
        severity: 'high',
      }),
    ]);

    expect(
      parseListCards('# Learnings\n\n## Card one\nBody line\n\n- Card two: follow-up body'),
    ).toEqual([
      { title: 'Card one', body: 'Body line' },
      { title: 'Card two', body: 'follow-up body' },
    ]);
    expect(parseListCards('# Learnings\n\n- Card three')).toEqual([
      { title: 'Card three', body: 'Card three' },
    ]);

    expect(citationToken('s1')).toBe('[s1]');
    expect(statusDotClass('published' as never)).toBe('is-brand');
    expect(statusDotClass('review' as never)).toBe('is-brand');
    expect(statusDotClass('running' as never)).toBe('is-brand');
    expect(statusDotClass('blocked' as never)).toBe('is-amber');
    expect(statusDotClass('failed' as never)).toBe('is-rose');
    expect(statusDotClass('draft' as never)).toBe('is-muted');
    expect(stageTickClass('complete')).toBe('is-complete');
    expect(stageTickClass('active')).toBe('is-active');
    expect(stageTickClass('blocked')).toBe('is-blocked');
    expect(stageTickClass('failed')).toBe('is-failed');
    expect(stageTickClass('pending')).toBe('is-pending');
    expect(drawerSectionCountLabel(1, 'file', 'files')).toBe('1 file');
    expect(drawerSectionCountLabel(2, 'file', 'files')).toBe('2 files');
    expect(splitTableRow('| a | b |')).toEqual(['a', 'b']);
  });
});
