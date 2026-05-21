import { usePluginCtx } from '@niuulabs/plugin-sdk';

export interface ActiveMountState {
  activeMount: string;
  mountName?: string;
}

export function useActiveMount(): ActiveMountState {
  const ctx = usePluginCtx();
  const activeMount = (ctx.tweaks.activeMount as string | undefined) ?? 'all';
  return {
    activeMount,
    mountName: activeMount === 'all' ? undefined : activeMount,
  };
}
