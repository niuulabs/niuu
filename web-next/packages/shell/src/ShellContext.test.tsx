import { describe, expect, it } from 'vitest';
import { renderHook } from '@testing-library/react';
import type { ReactNode } from 'react';
import { ShellContext, useShellContext, type ShellContextValue } from './ShellContext';

const value: ShellContextValue = {
  enabled: [],
  brand: 'Niuu',
  version: '1.0.0',
  ctx: { tweaks: {}, setTweak: () => {} },
};

describe('useShellContext', () => {
  it('returns the surrounding shell context value', () => {
    const wrapper = ({ children }: { children: ReactNode }) => (
      <ShellContext.Provider value={value}>{children}</ShellContext.Provider>
    );

    const { result } = renderHook(() => useShellContext(), { wrapper });
    expect(result.current).toBe(value);
  });

  it('throws when used outside the shell provider', () => {
    expect(() => renderHook(() => useShellContext())).toThrow(
      'useShellContext must be used inside <Shell>',
    );
  });
});
