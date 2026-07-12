import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import { describe, expect, it, vi } from 'vitest';
import { ResidentModelSelect } from './ResidentModelSelect';

function wrap(models: Array<Record<string, unknown>>) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return function Wrapper({ children }: { children: React.ReactNode }) {
    return (
      <QueryClientProvider client={client}>
        <ServicesProvider services={{ bifrost: { listModels: vi.fn().mockResolvedValue(models) } }}>
          {children}
        </ServicesProvider>
      </QueryClientProvider>
    );
  };
}

describe('ResidentModelSelect', () => {
  it('uses Bifrost metadata while preserving resident IDs and order', async () => {
    const onChange = vi.fn();
    render(
      <ResidentModelSelect
        allowedModels={['niuu/Qwen/Qwen3.6', 'niuu/gpt-5.6-sol']}
        modelPrefix="niuu/"
        value="niuu/gpt-5.6-sol"
        onChange={onChange}
        testId="resident-model"
      />,
      {
        wrapper: wrap([
          { id: 'gpt-5.6-sol', name: 'Sol', provider: 'cloud', tier: 'frontier' },
          { id: 'Qwen/Qwen3.6', name: 'Qwen 3.6', provider: 'local', tier: 'execution' },
        ]),
      },
    );

    await waitFor(() => expect(screen.getByText('Qwen 3.6 · local · execution')).toBeVisible());
    const options = screen.getAllByRole('option');
    expect(options.map((option) => option.getAttribute('value'))).toEqual([
      'niuu/Qwen/Qwen3.6',
      'niuu/gpt-5.6-sol',
    ]);
    fireEvent.change(screen.getByTestId('resident-model'), {
      target: { value: 'niuu/Qwen/Qwen3.6' },
    });
    expect(onChange).toHaveBeenCalledWith('niuu/Qwen/Qwen3.6');
  });

  it('falls back to the resident model ID when metadata is unavailable', () => {
    render(
      <ResidentModelSelect
        allowedModels={['custom-model']}
        modelPrefix=""
        value="custom-model"
        onChange={vi.fn()}
        testId="resident-model"
      />,
      { wrapper: wrap([]) },
    );

    expect(screen.getByText('custom-model')).toBeVisible();
  });
});
