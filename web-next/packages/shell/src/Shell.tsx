import { useMemo, useState, useCallback, type ReactNode } from 'react';
import { RouterProvider } from '@tanstack/react-router';
import type { RouterHistory } from '@tanstack/react-router';
import {
  PluginCtxProvider,
  useFeatureCatalog,
  type PluginDescriptor,
  type PluginCtx,
} from '@niuulabs/plugin-sdk';
import { CommandPaletteProvider } from '@niuulabs/ui';
import { ShellContext } from './ShellContext';
import { composeRouter } from './composeRouter';

interface ShellProps {
  plugins: PluginDescriptor[];
  brand?: ReactNode;
  version?: string;
  /** @internal Override the router history — for tests and Storybook only. */
  _testHistory?: RouterHistory;
}

export function Shell({ plugins, brand = 'ᚾ', version = '0.0.1', _testHistory }: ShellProps) {
  const features = useFeatureCatalog();

  const enabled = useMemo(
    () =>
      plugins
        .filter((p) => features.isEnabled(p.id))
        .sort((a, b) => features.order(a.id) - features.order(b.id)),
    [plugins, features],
  );

  const [tweaks, setTweaks] = useState<Record<string, unknown>>({});
  const setTweak = useCallback((key: string, value: unknown) => {
    setTweaks((t) => ({ ...t, [key]: value }));
  }, []);

  const ctx: PluginCtx = useMemo(() => ({ tweaks, setTweak }), [tweaks, setTweak]);

  // Catalog refreshes often leave the route set unchanged. Keep that router
  // alive; replacing it also requires remounting its transition state.
  const routeKey = JSON.stringify(enabled.map((plugin) => plugin.id));
  const router = useMemo(
    () =>
      composeRouter(
        (JSON.parse(routeKey) as string[]).map((id) => plugins.find((plugin) => plugin.id === id)!),
        { history: _testHistory },
      ),
    [plugins, routeKey, _testHistory],
  );

  return (
    <ShellContext.Provider value={{ enabled, brand, version, ctx }}>
      <PluginCtxProvider value={ctx}>
        <CommandPaletteProvider>
          <RouterProvider key={routeKey} router={router} />
        </CommandPaletteProvider>
      </PluginCtxProvider>
    </ShellContext.Provider>
  );
}
