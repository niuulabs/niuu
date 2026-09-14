import { describe, expect, it } from 'vitest';
import { render } from '@testing-library/react';
import { TemplateIcon, agoLabel, templateLabel } from './icons';

describe('templateLabel', () => {
  it('names every template and falls back to Resident', () => {
    expect(templateLabel('product-resident')).toBe('Product resident');
    expect(templateLabel('qa-resident')).toBe('QA resident');
    expect(templateLabel('docs-resident')).toBe('Docs resident');
    expect(templateLabel('blank-resident')).toBe('Resident');
    expect(templateLabel(undefined)).toBe('Resident');
    expect(templateLabel('something-else')).toBe('Resident');
  });
});

describe('TemplateIcon', () => {
  it('renders a different picture per template and a castle for the rest', () => {
    const ids = ['product-resident', 'qa-resident', 'docs-resident', 'blank-resident', null];
    const markup = ids.map((id) => render(<TemplateIcon templateId={id} />).container.innerHTML);
    expect(new Set(markup).size).toBe(ids.length);
    expect(markup.every((html) => html.includes('<svg'))).toBe(true);
  });
});

describe('agoLabel', () => {
  const now = Date.parse('2026-09-14T12:00:00Z');

  it('says nothing for a missing or unreadable time', () => {
    expect(agoLabel(undefined, now)).toBeNull();
    expect(agoLabel('not a date', now)).toBeNull();
  });

  it('rounds to the nearest sensible unit', () => {
    expect(agoLabel('2026-09-14T11:59:50Z', now)).toBe('just now');
    expect(agoLabel('2026-09-14T11:41:00Z', now)).toBe('19 min ago');
    expect(agoLabel('2026-09-14T09:00:00Z', now)).toBe('3 h ago');
    expect(agoLabel('2026-09-10T12:00:00Z', now)).toBe('4 d ago');
  });
});
