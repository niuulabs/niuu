import { describe, expect, it } from 'vitest';
import { renderHook } from '@testing-library/react';
import type { ReactNode } from 'react';
import type { PluginDescriptor } from '@niuulabs/plugin-sdk';
import {
  ShellContext,
  usePluginEnabled,
  useShellContext,
  type ShellContextValue,
} from './ShellContext';

const value: ShellContextValue = {
  enabled: [],
  brand: 'Niuu',
  version: '1.0.0',
  ctx: { tweaks: {}, setTweak: () => {} },
};

describe('usePluginEnabled', () => {
  const inShell = (enabled: PluginDescriptor[]) =>
    function Wrapper({ children }: { children: ReactNode }) {
      return (
        <ShellContext.Provider value={{ ...value, enabled }}>{children}</ShellContext.Provider>
      );
    };

  it('answers from the plugins the Shell enabled', () => {
    const ting = { id: 'ting' } as PluginDescriptor;
    const wrapper = inShell([ting]);
    expect(renderHook(() => usePluginEnabled('ting'), { wrapper }).result.current).toBe(true);
    expect(renderHook(() => usePluginEnabled('realms'), { wrapper }).result.current).toBe(false);
  });

  it('leaves the routes to the host outside a Shell', () => {
    expect(renderHook(() => usePluginEnabled('realms')).result.current).toBe(true);
  });
});

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
