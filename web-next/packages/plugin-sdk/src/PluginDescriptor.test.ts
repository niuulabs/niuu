import { describe, it, expect } from 'vitest';
import { definePlugin } from './PluginDescriptor';

describe('definePlugin', () => {
  it('returns the descriptor unchanged', () => {
    const descriptor = definePlugin({
      id: 'x',
      rune: 'ᚷ',
      title: 'X',
      subtitle: 'testing',
    });
    expect(descriptor.id).toBe('x');
    expect(descriptor.rune).toBe('ᚷ');
  });
});
