import { describe, expect, it } from 'vitest';
import { REALM_TEMPLATES, templateById } from './templates';

describe('realm templates', () => {
  it('ships the four templates with a recommended default', () => {
    expect(REALM_TEMPLATES.map((template) => template.id)).toEqual([
      'product-resident',
      'qa-resident',
      'docs-resident',
      'blank-resident',
    ]);
    expect(REALM_TEMPLATES.filter((template) => template.recommended)).toHaveLength(1);
  });

  it('writes the charter, repository and board into the persona prompt', () => {
    const prompt = templateById('product-resident').prompt(
      'Keep it shippable.',
      'niuulabs/lexi-api',
      'LXA',
    );
    expect(prompt).toContain('# Product resident');
    expect(prompt).toContain('Keep it shippable.');
    expect(prompt).toContain('Repository: niuulabs/lexi-api');
    expect(prompt).toContain('Tracker board: LXA');
  });

  it('keeps every persona seed writing to realm memory', () => {
    for (const template of REALM_TEMPLATES) {
      expect(template.persona.mimirWriteRouting).toBe('domain');
      expect(template.persona.role).toBe('build');
    }
  });

  it('fails loudly on an unknown template', () => {
    expect(() => templateById('nope')).toThrow(/Unknown realm template/);
  });
});
