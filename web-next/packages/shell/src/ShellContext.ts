import { createContext, useContext, type ReactNode } from 'react';
import type { PluginDescriptor, PluginCtx } from '@niuulabs/plugin-sdk';

export interface ShellContextValue {
  enabled: PluginDescriptor[];
  brand: ReactNode;
  version: string;
  ctx: PluginCtx;
}

export const ShellContext = createContext<ShellContextValue | null>(null);

export function useShellContext(): ShellContextValue {
  const value = useContext(ShellContext);
  if (!value) throw new Error('useShellContext must be used inside <Shell>');
  return value;
}
