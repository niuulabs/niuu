/**
 * Standalone consumer app — demonstrates that @niuulabs/plugin-tyr
 * can be installed from GitHub Packages and rendered in a third-party app
 * without pulling in the full Niuu monorepo.
 *
 * This is the composability proof from web-next/CLAUDE.md §1.
 */
import { QueryClientProvider } from '@tanstack/react-query';
import { ThemeProvider } from '@niuulabs/design-tokens';
import { ConfigProvider, FeatureCatalogProvider, ServicesProvider } from '@niuulabs/plugin-sdk';
import { createQueryClient } from '@niuulabs/query';
import { Shell } from '@niuulabs/shell';
import {
  tyrPlugin,
  createMockTyrService,
  createMockDispatcherService,
  createMockTyrSessionService,
  createMockTrackerService,
  createMockWorkflowService,
  createMockDispatchBus,
  createMockTyrSettingsService,
  createMockAuditLogService,
  createMockTyrIntegrationService,
} from '@niuulabs/plugin-tyr';

const queryClient = createQueryClient();

// All Tyr sub-services wired with mock adapters — no backend required
const services = {
  tyr: createMockTyrService(),
  'tyr.dispatcher': createMockDispatcherService(),
  'tyr.sessions': createMockTyrSessionService(),
  'tyr.tracker': createMockTrackerService(),
  'tyr.workflows': createMockWorkflowService(),
  'tyr.dispatch': createMockDispatchBus(),
  'tyr.settings': createMockTyrSettingsService(),
  'tyr.audit': createMockAuditLogService(),
  'tyr.integrations': createMockTyrIntegrationService(),
};

export function App() {
  return (
    <ConfigProvider
      endpoint="/config.json"
      fallback={<BootScreen label="loading config…" />}
      errorFallback={(err: Error) => <BootScreen label={`config error: ${err.message}`} />}
    >
      <ThemeProvider theme="ice">
        <QueryClientProvider client={queryClient}>
          <ServicesProvider services={services}>
            <FeatureCatalogProvider>
              <Shell plugins={[tyrPlugin]} />
            </FeatureCatalogProvider>
          </ServicesProvider>
        </QueryClientProvider>
      </ThemeProvider>
    </ConfigProvider>
  );
}

function BootScreen({ label }: { label: string }) {
  return (
    <div
      style={{
        height: '100vh',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        fontFamily: 'monospace',
        fontSize: '0.75rem',
        color: '#a1a1aa',
        background: '#09090b',
      }}
    >
      {label}
    </div>
  );
}
