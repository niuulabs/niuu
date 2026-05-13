import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import { createMockBifrostService } from '../adapters/mock';
import { BifrostPage } from './BifrostPage';

function renderPage(defaultTab: 'overview' | 'models' | 'providers' | 'usage' = 'overview') {
  const queryClient = new QueryClient();
  return render(
    <QueryClientProvider client={queryClient}>
      <ServicesProvider services={{ bifrost: createMockBifrostService() }}>
        <BifrostPage defaultTab={defaultTab} />
      </ServicesProvider>
    </QueryClientProvider>,
  );
}

describe('BifrostPage', () => {
  it('renders the control-plane heading and overview stats', async () => {
    renderPage();

    expect(await screen.findByText('LLM control plane')).toBeInTheDocument();
    expect(await screen.findByText('Enabled models')).toBeInTheDocument();
    expect(screen.getByText('Usage snapshot')).toBeInTheDocument();
  });

  it('renders the models tab as a real table without an in-page tab row', async () => {
    renderPage('models');

    expect(await screen.findByRole('table', { name: 'Model inventory' })).toBeInTheDocument();
    expect(screen.getByRole('columnheader', { name: 'Model' })).toBeInTheDocument();
    expect(screen.getByRole('columnheader', { name: 'Runtime' })).toBeInTheDocument();
    expect(
      screen.queryAllByRole('button', { name: /overview|models|providers|usage/i }),
    ).toHaveLength(0);
  });
});
