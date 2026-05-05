import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ConfigProvider } from '@niuulabs/plugin-sdk';
import type { ReactNode } from 'react';
import { describe, expect, it, vi } from 'vitest';
import { SettingsPage } from './SettingsPage';

const routerMocks = vi.hoisted(() => ({
  navigate: vi.fn(),
  params: { providerId: 'tyr', sectionId: 'general' },
}));

vi.mock('@tanstack/react-router', () => ({
  Link: ({ children, to, ...props }: any) => (
    <a href={String(to)} {...props}>
      {children}
    </a>
  ),
  useParams: () => routerMocks.params,
  useRouter: () => ({ navigate: routerMocks.navigate }),
}));

vi.mock('@niuulabs/plugin-tyr', () => ({
  tyrMountedSettingsProvider: {
    id: 'tyr',
    pluginId: 'tyr',
    title: 'Tyr',
    subtitle: 'saga coordinator settings',
    scope: 'service',
    defaultSectionId: 'general',
    sections: [
      {
        id: 'general',
        label: 'General',
        description: 'Core service bindings for the saga coordinator',
        render: () => <div>Tyr General Mounted</div>,
      },
    ],
  },
}));

function wrap(children: ReactNode) {
  const queryClient = new QueryClient();
  return render(
    <ConfigProvider
      value={{
        theme: 'ice',
        plugins: {
          tyr: { enabled: true, order: 2 },
        },
        services: {},
      }}
    >
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    </ConfigProvider>,
  );
}

describe('SettingsPage', () => {
  it('renders the mounted local provider section for Tyr', () => {
    wrap(<SettingsPage />);

    expect(screen.getByRole('navigation', { name: 'Settings providers' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Tyr' })).toBeTruthy();
    expect(screen.getByRole('heading', { name: 'Tyr Settings' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'General' })).toBeTruthy();
    expect(screen.getByText('Tyr General Mounted')).toBeTruthy();
  });
});
