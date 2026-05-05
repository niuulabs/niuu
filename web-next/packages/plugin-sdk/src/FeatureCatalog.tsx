import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';
import { useConfig } from './ConfigProvider';
import type { IFeatureCatalogService, FeatureModule } from './ports/feature-catalog.port';

export interface FeatureCatalog {
  isEnabled: (pluginId: string) => boolean;
  order: (pluginId: string) => number;
}

const FeatureCatalogContext = createContext<FeatureCatalog | null>(null);

interface FeatureCatalogProviderProps {
  /** Optional service for runtime feature data from the backend. Falls back to config-only when omitted. */
  service?: IFeatureCatalogService;
  overrides?: Partial<FeatureCatalog>;
  children: ReactNode;
}

export function FeatureCatalogProvider({
  service,
  overrides,
  children,
}: FeatureCatalogProviderProps) {
  const config = useConfig();
  const [modules, setModules] = useState<FeatureModule[]>([]);

  useEffect(() => {
    if (!service) return;
    service
      .getFeatureModules()
      .then(setModules)
      .catch(() => {
        // silently fall back to config-only on service error
      });
  }, [service]);

  const catalog = useMemo<FeatureCatalog>(() => {
    const moduleMap = new Map(modules.map((m) => [m.key, m]));
    return {
      isEnabled:
        overrides?.isEnabled ??
        ((key: string) => {
          const mod = moduleMap.get(key);
          if (mod !== undefined) return mod.enabled;
          return config.plugins[key]?.enabled !== false;
        }),
      order:
        overrides?.order ??
        ((key: string) => {
          const mod = moduleMap.get(key);
          if (mod !== undefined) return mod.order;
          return config.plugins[key]?.order ?? 100;
        }),
    };
  }, [config, modules, overrides]);

  return (
    <FeatureCatalogContext.Provider value={catalog}>{children}</FeatureCatalogContext.Provider>
  );
}

export function useFeatureCatalog(): FeatureCatalog {
  const ctx = useContext(FeatureCatalogContext);
  if (!ctx) throw new Error('useFeatureCatalog must be used within <FeatureCatalogProvider>');
  return ctx;
}
