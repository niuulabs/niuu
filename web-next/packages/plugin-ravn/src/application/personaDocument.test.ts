import { describe, expect, it } from 'vitest';
import { makePersonaSummary } from '../testing/fixtures';
import {
  blankPersonaDocument,
  documentProblems,
  documentYaml,
  eventName,
  eventPrefix,
  knownEvents,
  outcomeName,
  outcomesOf,
  parseDocumentYaml,
  renameOutcome,
  startFrom,
  startsOn,
  withName,
  withOutcomes,
  withStartsOn,
} from './personaDocument';

describe('persona documents', () => {
  it('names events after the persona', () => {
    expect(eventPrefix('Migration Reviewer')).toBe('migration_reviewer');
    expect(eventPrefix('')).toBe('persona');
    expect(outcomeName(' Request Changes! ')).toBe('request_changes');
    expect(eventName('.Code Changed.')).toBe('code_changed');
  });

  it('renames the events that followed the old name and keeps the ones chosen', () => {
    const named = withName(blankPersonaDocument(), 'night-watch');
    const chosen = withOutcomes(named, [
      ...outcomesOf(named),
      { verdict: 'alarm', event: 'security.alarm', when: 'Something is wrong.' },
    ]);
    const renamed = withName(chosen, 'day-watch');
    expect(outcomesOf(renamed)).toEqual([
      { verdict: 'done', event: 'day_watch.done', when: '' },
      { verdict: 'alarm', event: 'security.alarm', when: 'Something is wrong.' },
    ]);
    expect((renamed.produces as Record<string, unknown>).event_type).toBe('day_watch.completed');
    expect(Object.keys(renamed)[0]).toBe('name');
  });

  it('renames a persona without outcomes without inventing any', () => {
    expect(withName({ name: 'a' }, 'b')).toEqual({ name: 'b' });
  });

  it('keeps the verdict field of the outcome schema in step with the outcomes', () => {
    const doc = withOutcomes(blankPersonaDocument(), [
      { verdict: 'pass', event: 'x.pass', when: 'It passes.' },
      { verdict: 'fail', event: 'x.fail', when: '' },
    ]);
    const produces = doc.produces as Record<string, Record<string, unknown>>;
    expect(produces.event_type_map).toEqual({ pass: 'x.pass', fail: 'x.fail' });
    expect(produces.when).toEqual({ pass: 'It passes.' });
    expect((produces.schema!.verdict as Record<string, unknown>).values).toEqual(['pass', 'fail']);
    const none = withOutcomes(doc, [{ verdict: 'pass', event: 'x.pass', when: '' }]);
    expect('when' in (none.produces as Record<string, unknown>)).toBe(false);
  });

  it('leaves a schema without an enum verdict as it is', () => {
    const doc = withOutcomes({ name: 'x', produces: { schema: { verdict: { type: 'string' } } } }, [
      { verdict: 'ok', event: 'x.ok', when: 'Fine.' },
    ]);
    expect((doc.produces as Record<string, unknown>).schema).toEqual({
      verdict: { type: 'string' },
    });
  });

  it('renames an outcome, following its event only when it followed the persona', () => {
    const doc = withOutcomes(withName(blankPersonaDocument(), 'checker'), [
      { verdict: 'done', event: 'checker.done', when: 'Done.' },
      { verdict: 'stuck', event: 'help.needed', when: 'Stuck.' },
    ]);
    const renamed = renameOutcome(renameOutcome(doc, 0, 'finished'), 1, 'blocked');
    expect(outcomesOf(renamed)).toEqual([
      { verdict: 'finished', event: 'checker.finished', when: 'Done.' },
      { verdict: 'blocked', event: 'help.needed', when: 'Stuck.' },
    ]);
  });

  it('starts from another persona, keeping everything but its name', () => {
    const copy = startFrom(
      { name: 'reviewer', voice: { voices: { a: 'b' } }, consumes: { event_types: ['x.y'] } },
      { name: 'mine' },
    );
    expect(copy).toEqual({
      name: 'mine',
      consumes: { event_types: ['x.y'] },
      voice: { voices: { a: 'b' } },
    });
    expect(startsOn(withStartsOn(copy, ['a.b']))).toEqual(['a.b']);
    expect(startsOn({ name: 'x', consumes: 'nonsense' })).toEqual([]);
  });

  it('names every problem with the step that fixes it', () => {
    const doc = {
      name: 'Bad Name!',
      consumes: { event_types: ['not an event'] },
      produces: {
        event_type_map: { 'Bad Verdict': 'nope' },
        when: { ghost: 'Never.' },
      },
    };
    expect(documentProblems(doc)).toEqual([
      expect.objectContaining({ step: 'name', message: expect.stringContaining('Use letters') }),
      expect.objectContaining({ step: 'starts', message: expect.stringContaining('not an event') }),
      expect.objectContaining({
        step: 'finishes',
        message: expect.stringContaining('outcome name'),
      }),
      expect.objectContaining({ step: 'finishes', message: expect.stringContaining('sends nope') }),
      expect.objectContaining({ message: 'Say when to report Bad Verdict.' }),
      expect.objectContaining({ message: expect.stringContaining('names ghost') }),
      { step: 'instructions', message: 'Tell it what to do.' },
    ]);
    expect(documentProblems({ name: 'x', system_prompt_template: 'Go.' })).toEqual([
      { step: 'finishes', message: 'Add at least one way it can finish.' },
    ]);
    expect(documentProblems({ name: 'x' }, { taken: ['x'] })[0]!.message).toMatch(/exists/);
    expect(documentProblems({})[0]!.message).toBe('Give it a name.');
  });

  it('writes YAML in the order the wizard asks and reads it back', () => {
    const yaml = documentYaml({ produces: {}, extra: 1, name: 'x', description: 'd' });
    expect(yaml.split('\n').slice(0, 3)).toEqual(['name: x', 'description: d', 'produces: {}']);
    expect(parseDocumentYaml(yaml)).toEqual({
      ok: true,
      doc: { name: 'x', description: 'd', produces: {}, extra: 1 },
    });
    expect(parseDocumentYaml('name: [')).toMatchObject({ ok: false, error: /^Line \d+:/ });
    expect(parseDocumentYaml('- a')).toMatchObject({ ok: false, error: /mapping/ });
    expect(parseDocumentYaml('')).toMatchObject({ ok: false });
  });

  it('lists the events existing personas send, and who sends them', () => {
    expect(
      knownEvents([
        makePersonaSummary({ name: 'coder', outcomeEvents: { pass: 'code.changed' } }),
        makePersonaSummary({
          name: 'fixer',
          outcomeEvents: { pass: 'code.changed', fail: 'code.failed' },
        }),
        makePersonaSummary({ name: 'quiet' }),
      ]),
    ).toEqual([
      { event: 'code.changed', senders: ['coder on pass', 'fixer on pass'] },
      { event: 'code.failed', senders: ['fixer on fail'] },
    ]);
  });
});

describe('the order a persona is written in', () => {
  it('puts each rule under the outcomes, whatever order the document had', () => {
    const doc = withOutcomes(
      { name: 'x', produces: { schema: { summary: { type: 'string' } }, extra: true } },
      [{ verdict: 'ok', event: 'x.ok', when: 'Fine.' }],
    );
    expect(Object.keys(doc.produces as object)).toEqual([
      'event_type_map',
      'when',
      'schema',
      'extra',
    ]);
  });
});
