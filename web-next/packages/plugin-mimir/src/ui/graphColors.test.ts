import { expect, it } from 'vitest';
import { categoryColor } from './graphColors';

it('keeps familiar categories distinct and unknown categories stable', () => {
  const categories = ['research', 'concepts', 'notes', 'projects', 'skills', 'decisions'];
  expect(new Set(categories.map(categoryColor)).size).toBe(categories.length);
  expect(categoryColor(' Research ')).toBe(categoryColor('research'));
  expect(categoryColor('experiments')).toBe(categoryColor('EXPERIMENTS'));
  expect(categoryColor('constructor')).toMatch(/^var\(--knowledge-[a-z]+\)$/);
});
