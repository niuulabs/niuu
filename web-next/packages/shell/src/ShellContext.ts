import { createContext, useContext, type ReactNode } from 'react';
import type { PluginDescriptor, PluginCtx } from '@niuulabs/plugin-sdk';

export interface ShellContextValue {
  enabled: PluginDescriptor[];
  brand: ReactNode;
  topbarContent?: ReactNode;
  version: string;
  ctx: PluginCtx;
}

export const ShellContext = createContext<ShellContextValue | null>(null);

export function useShellContext(): ShellContextValue {
  const value = useContext(ShellContext);
  if (!value) throw new Error('useShellContext must be used inside <Shell>');
  return value;
}

/**
 * Whether another plugin's pages exist here, so a link to them leads somewhere.
 * A page mounted without the Shell has its routes decided by its host.
 */
export function usePluginEnabled(id: string): boolean {
  const shell = useContext(ShellContext);
  return shell === null || shell.enabled.some((plugin) => plugin.id === id);
}
