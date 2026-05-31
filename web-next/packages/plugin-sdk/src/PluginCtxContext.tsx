import { createContext, useContext } from 'react';
import type { PluginCtx } from './PluginDescriptor';

const DEFAULT_PLUGIN_CTX: PluginCtx = {
  tweaks: {},
  setTweak: () => {},
};

export const PluginCtxContext = createContext<PluginCtx>(DEFAULT_PLUGIN_CTX);

export function PluginCtxProvider({
  value,
  children,
}: {
  value: PluginCtx;
  children: React.ReactNode;
}) {
  return <PluginCtxContext.Provider value={value}>{children}</PluginCtxContext.Provider>;
}

export function usePluginCtx(): PluginCtx {
  return useContext(PluginCtxContext);
}
