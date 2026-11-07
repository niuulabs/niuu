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

  it('describes every template as a workspace', () => {
    for (const template of REALM_TEMPLATES) {
      expect(template.name).toMatch(/workspace$/);
      expect(template.covers.length).toBeGreaterThan(0);
    }
  });

  it('finds a template by id', () => {
    expect(templateById('qa-resident').needsBugBoard).toBe(true);
  });

  it('fails loudly on an unknown template', () => {
    expect(() => templateById('nope')).toThrow(/Unknown workspace template/);
  });
});
