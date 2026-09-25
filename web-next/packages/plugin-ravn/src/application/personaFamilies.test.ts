import { describe, expect, it } from 'vitest';
import {
  GENERAL_FAMILY,
  groupPersonaFamilies,
  humanizePersonaName,
  isCustomPersona,
  matchesPersonaQuery,
  matchesPersonaSource,
  personaTagline,
} from './personaFamilies';
import { buildDraftPersona, personaNameProblem } from './personaDraft';
import { makePersonaSummary } from '../testing/fixtures';

const persona = (name: string, extra = {}) => makePersonaSummary({ name, ...extra });

describe('groupPersonaFamilies', () => {
  it('makes a family of every shared prefix and puts singletons in General first', () => {
    const families = groupPersonaFamilies([
      persona('research-framer'),
      persona('reviewer'),
      persona('developer-coder'),
      persona('research-analyst'),
      persona('developer-analyst'),
      persona('coder'),
    ]);
    expect(families.map((family) => [family.key, family.label])).toEqual([
      [GENERAL_FAMILY, 'General'],
      ['developer', 'Developer'],
      ['research', 'Research'],
    ]);
    expect(families[0]!.personas.map((entry) => entry.name)).toEqual(['coder', 'reviewer']);
    expect(families[2]!.personas.map((entry) => entry.name)).toEqual([
      'research-analyst',
      'research-framer',
    ]);
  });

  it('returns nothing for no personas', () => {
    expect(groupPersonaFamilies([])).toEqual([]);
  });
});

describe('personaTagline', () => {
  it('keeps a summary that says something', () => {
    expect(personaTagline(persona('reviewer'))).toBe('Reviews code changes and provides feedback.');
  });

  it('shows what it emits when the summary only repeats the name', () => {
    expect(
      personaTagline(
        persona('research-framer', { summary: 'Research framer', producesEvent: 'x.y' }),
      ),
    ).toBe('emits x.y');
  });

  it('falls to the tool count when it emits nothing', () => {
    expect(
      personaTagline(
        persona('closer', { summary: 'Closer', producesEvent: '', allowedTools: ['git'] }),
      ),
    ).toBe('1 tools');
  });

  it('humanizes names', () => {
    expect(humanizePersonaName('developer-code_reviewer')).toBe('Developer code reviewer');
  });
});

describe('filters', () => {
  it('knows custom and overridden personas', () => {
    expect(isCustomPersona(persona('a'))).toBe(false);
    expect(isCustomPersona(persona('a', { hasOverride: true }))).toBe(true);
    expect(isCustomPersona(persona('a', { isBuiltin: false }))).toBe(true);
  });

  it('searches names, events and tools', () => {
    expect(matchesPersonaQuery(persona('reviewer'), 'code.changed')).toBe(true);
    expect(matchesPersonaQuery(persona('reviewer'), 'GIT')).toBe(true);
    expect(matchesPersonaQuery(persona('reviewer'), '')).toBe(true);
    expect(matchesPersonaQuery(persona('reviewer'), 'kubernetes')).toBe(false);
  });

  it('filters by source', () => {
    const usage = (name: string) => (name === 'used' ? 2 : 0);
    expect(matchesPersonaSource(persona('x'), 'all', usage)).toBe(true);
    expect(matchesPersonaSource(persona('used'), 'in-use', usage)).toBe(true);
    expect(matchesPersonaSource(persona('idle'), 'in-use', usage)).toBe(false);
    expect(matchesPersonaSource(persona('x', { isBuiltin: false }), 'custom', usage)).toBe(true);
  });
});

describe('persona drafts', () => {
  it('validates names', () => {
    expect(personaNameProblem('', [])).toBe('Give it a name.');
    expect(personaNameProblem('Bad Name', [])).toMatch(/lowercase/);
    expect(personaNameProblem('reviewer', ['reviewer'])).toMatch(/already exists/);
    expect(personaNameProblem('night-watch', ['reviewer'])).toBeNull();
  });

  it('builds the smallest valid persona', () => {
    expect(buildDraftPersona('night-watch')).toMatchObject({
      name: 'night-watch',
      summary: 'Night watch',
      allowedTools: [],
      consumesEvents: [],
    });
  });
});
