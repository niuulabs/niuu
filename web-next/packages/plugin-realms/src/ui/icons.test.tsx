import { describe, expect, it } from 'vitest';
import { render } from '@testing-library/react';
import { TemplateIcon, templateLabel } from './icons';

describe('templateLabel', () => {
  it('names every template and falls back to Workspace', () => {
    expect(templateLabel('product-resident')).toBe('Product workspace');
    expect(templateLabel('qa-resident')).toBe('QA workspace');
    expect(templateLabel('docs-resident')).toBe('Docs workspace');
    expect(templateLabel('blank-resident')).toBe('Workspace');
    expect(templateLabel(undefined)).toBe('Workspace');
    expect(templateLabel('something-else')).toBe('Workspace');
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
