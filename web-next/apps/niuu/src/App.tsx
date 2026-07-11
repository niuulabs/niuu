import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { QueryClientProvider } from '@tanstack/react-query';
import { ReactQueryDevtools } from '@tanstack/react-query-devtools';
import { ThemeProvider } from '@niuulabs/design-tokens';
import {
  ConfigProvider,
  FeatureCatalogProvider,
  ServicesProvider,
  type IFeatureCatalogService,
  niuuConfigSchema,
  type NiuuConfig,
  useConfig,
} from '@niuulabs/plugin-sdk';
import { createQueryClient } from '@niuulabs/query';
import { AuthProvider, useAuth } from '@niuulabs/auth';
import { Shell } from '@niuulabs/shell';
import { LogoKnot } from '@niuulabs/plugin-login';
import { plugins } from './plugins';
import { buildServiceBackendStatus, buildServices, isUnavailableService } from './services';

const DEFAULT_CONFIG_ENDPOINT = '/config.json';
const LIVE_CONFIG_ENDPOINT = '/config.live.json';
const PRODUCTION_CONFIG_ENDPOINT = import.meta.env.PROD
  ? LIVE_CONFIG_ENDPOINT
  : DEFAULT_CONFIG_ENDPOINT;
const CONFIG_ENDPOINT_QUERY_KEY = 'config';
const CONFIG_ENDPOINT_STORAGE_KEY = 'niuu.config.endpoint';
type ConfigMode = 'default' | 'live';

export function shouldBypassAuthGate(pathname: string): boolean {
  return pathname === '/login' || pathname === '/login/callback' || pathname.startsWith('/login/');
}

function normalizeConfigMode(value: string | null): ConfigMode | null {
  if (!value) return null;
  if (value === 'default' || value === DEFAULT_CONFIG_ENDPOINT) return 'default';
  if (value === 'live' || value === LIVE_CONFIG_ENDPOINT) return 'live';
  return null;
}

export function listUnavailableServiceNames(backends: Record<string, { mode?: string }>): string[] {
  return Object.entries(backends)
    .filter(([, status]) => status.mode === 'unavailable')
    .map(([serviceName]) => serviceName);
}

export function publishServiceBackends(
  backends: Record<string, unknown>,
  target: Record<string, unknown> = globalThis as Record<string, unknown>,
): void {
  target.__NIUU_SERVICE_BACKENDS__ = backends;
}

function AppInner() {
  const config = useConfig();
  const serviceState = useMemo(() => {
    try {
      return {
        services: buildServices(config),
        backendStatus: buildServiceBackendStatus(config),
        error: null,
      };
    } catch (error: unknown) {
      return {
        services: null,
        backendStatus: {},
        error: error instanceof Error ? error : new Error(String(error)),
      };
    }
  }, [config]);

  useEffect(() => {
    publishServiceBackends(serviceState.backendStatus);
  }, [serviceState.backendStatus]);

  if (serviceState.error) {
    return <BootScreen label={`service bootstrap error: ${serviceState.error.message}`} />;
  }

  const services = serviceState.services;
  const featureCatalogService = isUnavailableService(services.features)
    ? undefined
    : (services.features as IFeatureCatalogService | undefined);
  const unavailableServices = listUnavailableServiceNames(serviceState.backendStatus);

  return (
    <AuthProvider>
      <AuthGate>
        <ServicesProvider services={services}>
          <FeatureCatalogProvider service={featureCatalogService}>
            <div className="niuu:flex niuu:h-full niuu:min-h-0 niuu:flex-col">
              {unavailableServices.length > 0 ? (
                <div
                  role="status"
                  title={unavailableServices.join(', ')}
                  className="niuu:border-b niuu:border-amber-400/30 niuu:bg-amber-400/10 niuu:px-3 niuu:py-1.5 niuu:text-xs niuu:text-amber-200"
                >
                  {unavailableServices.length} optional service backend
                  {unavailableServices.length === 1 ? ' is' : 's are'} unavailable. Features without
                  a live backend will report unavailable instead of showing demo data.
                </div>
              ) : null}
              <Shell
                plugins={plugins}
                brand={
                  <span className="niuu:inline-flex niuu:items-center niuu:justify-center niuu:text-sky-300">
                    <LogoKnot size={22} stroke={1.8} />
                  </span>
                }
              />
            </div>
          </FeatureCatalogProvider>
        </ServicesProvider>
      </AuthGate>
    </AuthProvider>
  );
}

const queryClient = createQueryClient();

export function resolveConfigEndpoint(
  location: Pick<Location, 'search'> = window.location,
  storage: Pick<Storage, 'getItem' | 'setItem' | 'removeItem'> = window.localStorage,
): string {
  const params = new URLSearchParams(location.search);
  const requested = normalizeConfigMode(params.get(CONFIG_ENDPOINT_QUERY_KEY));

  if (requested === 'default') {
    storage.removeItem(CONFIG_ENDPOINT_STORAGE_KEY);
    return DEFAULT_CONFIG_ENDPOINT;
  }

  if (requested === 'live') {
    storage.setItem(CONFIG_ENDPOINT_STORAGE_KEY, LIVE_CONFIG_ENDPOINT);
    return LIVE_CONFIG_ENDPOINT;
  }

  const stored = normalizeConfigMode(storage.getItem(CONFIG_ENDPOINT_STORAGE_KEY));
  if (stored === 'live') return LIVE_CONFIG_ENDPOINT;
  return PRODUCTION_CONFIG_ENDPOINT;
}

export function App() {
  const configEndpoint = resolveConfigEndpoint();
  const [state, setState] = useState<
    | { status: 'loading' }
    | { status: 'ready'; config: NiuuConfig }
    | { status: 'error'; error: Error }
  >({ status: 'loading' });

  useEffect(() => {
    const requestUrl =
      configEndpoint === LIVE_CONFIG_ENDPOINT ? LIVE_CONFIG_ENDPOINT : DEFAULT_CONFIG_ENDPOINT;
    let cancelled = false;

    fetch(requestUrl, { cache: 'no-store' })
      .then(async (response) => {
        if (!response.ok) {
          throw new Error(`GET ${requestUrl} returned ${response.status}`);
        }
        return niuuConfigSchema.parse(await response.json());
      })
      .then((config) => {
        if (!cancelled) {
          setState({ status: 'ready', config });
        }
      })
      .catch((error: unknown) => {
        if (!cancelled) {
          setState({
            status: 'error',
            error: error instanceof Error ? error : new Error(String(error)),
          });
        }
      });

    return () => {
      cancelled = true;
    };
  }, [configEndpoint]);

  if (state.status === 'loading') {
    return <BootScreen label="loading config…" />;
  }
  if (state.status === 'error') {
    return <BootScreen label={`config error: ${state.error.message}`} />;
  }

  return (
    <ConfigProvider value={state.config}>
      <ThemeProvider theme="ice">
        <QueryClientProvider client={queryClient}>
          <AppInner />
          <ReactQueryDevtools initialIsOpen={false} />
        </QueryClientProvider>
      </ThemeProvider>
    </ConfigProvider>
  );
}

function BootScreen({ label }: { label: string }) {
  return (
    <div className="niuu:h-screen niuu:flex niuu:items-center niuu:justify-center niuu:bg-bg-primary niuu:text-text-secondary niuu:font-mono niuu:text-xs">
      {label}
    </div>
  );
}

function AuthGate({ children }: { children: ReactNode }) {
  const { enabled, authenticated, loading } = useAuth();
  const pathname = window.location.pathname;
  const bypass = shouldBypassAuthGate(pathname);

  useEffect(() => {
    if (loading || !enabled) return;
    if (!authenticated && !bypass) {
      window.location.replace('/login');
      return;
    }
    if (authenticated && pathname === '/login') {
      window.location.replace('/');
    }
  }, [authenticated, bypass, enabled, loading, pathname]);

  if (loading) {
    return <BootScreen label="checking session…" />;
  }

  if (enabled && !authenticated && !bypass) {
    return null;
  }

  if (enabled && authenticated && pathname === '/login') {
    return null;
  }

  return <>{children}</>;
}
