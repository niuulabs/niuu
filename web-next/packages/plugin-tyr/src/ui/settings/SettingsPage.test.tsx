import { describe, it, expect, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';

vi.mock('@tanstack/react-router', () => ({
  Link: ({ children }: { to: string; className?: string; role?: string; children?: unknown }) =>
    children as unknown as JSX.Element | null,
}));
import { SettingsPage, SettingsIndexPage } from './SettingsPage';
import { createMockTyrSettingsService, createMockAuditLogService } from '../../adapters/mock';
import type { TyrPersonaSummary } from '../../ports';

const MOCK_PERSONA_STORE = {
  listPersonas: async () => [] as TyrPersonaSummary[],
  getPersonaYaml: async (_name: string) => '',
};

function wrap(services: Record<string, unknown>) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return function Wrapper({ children }: { children: React.ReactNode }) {
    return (
      <QueryClientProvider client={client}>
        <ServicesProvider services={services}>{children}</ServicesProvider>
      </QueryClientProvider>
    );
  };
}

const defaultServices = () => ({
  'tyr.settings': createMockTyrSettingsService(),
  'tyr.audit': createMockAuditLogService(),
  'ravn.personas': MOCK_PERSONA_STORE,
});

describe('SettingsIndexPage', () => {
  it('renders the index page heading', () => {
    const client = new QueryClient();
    render(
      <QueryClientProvider client={client}>
        <ServicesProvider services={defaultServices()}>
          <SettingsIndexPage />
        </ServicesProvider>
      </QueryClientProvider>,
    );
    expect(screen.getByText('Tyr Settings')).toBeInTheDocument();
  });

  it('shows all 9 setting section links', () => {
    const client = new QueryClient();
    render(
      <QueryClientProvider client={client}>
        <ServicesProvider services={defaultServices()}>
          <SettingsIndexPage />
        </ServicesProvider>
      </QueryClientProvider>,
    );
    expect(screen.getByText('General')).toBeInTheDocument();
    expect(screen.getByText('Dispatch rules')).toBeInTheDocument();
    expect(screen.getByText('Integrations')).toBeInTheDocument();
    expect(screen.getByText('Persona overrides')).toBeInTheDocument();
    expect(screen.getByText('Gates & reviewers')).toBeInTheDocument();
    expect(screen.getByText('Flock Config')).toBeInTheDocument();
    expect(screen.getByText('Notifications')).toBeInTheDocument();
    expect(screen.getByText('Advanced')).toBeInTheDocument();
    expect(screen.getByText('Audit Log')).toBeInTheDocument();
  });
});

describe('SettingsPage', () => {
  it('renders GeneralSection for "general" section', () => {
    render(<SettingsPage section="general" />, {
      wrapper: wrap(defaultServices()),
    });
    expect(screen.getByText('General')).toBeInTheDocument();
  });

  it('renders PersonasSection for "personas" section', async () => {
    render(<SettingsPage section="personas" />, {
      wrapper: wrap(defaultServices()),
    });
    await waitFor(() => expect(screen.getByText('Persona overrides')).toBeInTheDocument());
  });

  it('renders FlockConfigSection for "flock" section', async () => {
    render(<SettingsPage section="flock" />, {
      wrapper: wrap(defaultServices()),
    });
    await waitFor(() => expect(screen.getByText('Flock Config')).toBeInTheDocument());
  });

  it('renders DispatchDefaultsSection for "dispatch" section', async () => {
    render(<SettingsPage section="dispatch" />, {
      wrapper: wrap(defaultServices()),
    });
    await waitFor(() => expect(screen.getByText('Dispatch rules')).toBeInTheDocument());
  });

  it('renders NotificationsSection for "notifications" section', async () => {
    render(<SettingsPage section="notifications" />, {
      wrapper: wrap(defaultServices()),
    });
    await waitFor(() => expect(screen.getByText('Notifications')).toBeInTheDocument());
  });

  it('renders AuditLogSection for "audit" section', async () => {
    render(<SettingsPage section="audit" />, {
      wrapper: wrap(defaultServices()),
    });
    await waitFor(() => expect(screen.getByText('Audit Log')).toBeInTheDocument());
  });

  it('renders IntegrationsSection for "integrations" section', () => {
    render(<SettingsPage section="integrations" />, {
      wrapper: wrap(defaultServices()),
    });
    expect(screen.getByText('Integrations')).toBeInTheDocument();
  });

  it('renders GatesReviewersSection for "gates" section', () => {
    render(<SettingsPage section="gates" />, {
      wrapper: wrap(defaultServices()),
    });
    expect(screen.getByText('Gates & reviewers')).toBeInTheDocument();
  });

  it('renders AdvancedSection for "advanced" section', () => {
    render(<SettingsPage section="advanced" />, {
      wrapper: wrap(defaultServices()),
    });
    expect(screen.getByText('Advanced')).toBeInTheDocument();
  });
});
