import { describe, it, expect } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import { PersonaYaml } from './PersonaYaml';
import { createMockPersonaStore } from '../adapters/mock';

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

describe('PersonaYaml', () => {
  it('shows loading state while fetching', () => {
    const slowService = {
      getPersonaYaml: () => new Promise(() => {}),
      listPersonas: () => new Promise(() => {}),
    };
    render(<PersonaYaml name="reviewer" />, {
      wrapper: wrap({ 'ravn.personas': slowService }),
    });
    expect(screen.getByTestId('persona-yaml-loading')).toBeInTheDocument();
  });

  it('shows error state when service throws', async () => {
    const failing = {
      getPersonaYaml: async () => {
        throw new Error('yaml fetch failed');
      },
      listPersonas: async () => [],
    };
    render(<PersonaYaml name="reviewer" />, {
      wrapper: wrap({ 'ravn.personas': failing }),
    });
    await waitFor(() => expect(screen.getByTestId('persona-yaml-error')).toBeInTheDocument());
    expect(screen.getByText('yaml fetch failed')).toBeInTheDocument();
  });

  it('renders YAML content', async () => {
    render(<PersonaYaml name="reviewer" />, {
      wrapper: wrap({ 'ravn.personas': createMockPersonaStore() }),
    });
    await waitFor(() => expect(screen.getByTestId('persona-yaml')).toBeInTheDocument());
    expect(screen.getByTestId('persona-yaml').textContent).toContain('reviewer');
  });

  it('renders YAML in a pre element', async () => {
    render(<PersonaYaml name="reviewer" />, {
      wrapper: wrap({ 'ravn.personas': createMockPersonaStore() }),
    });
    await waitFor(() => expect(screen.getByTestId('persona-yaml')).toBeInTheDocument());
    const container = screen.getByTestId('persona-yaml');
    expect(container.querySelector('pre')).toBeInTheDocument();
  });

  it('renders line numbers in the gutter', async () => {
    render(<PersonaYaml name="reviewer" />, {
      wrapper: wrap({ 'ravn.personas': createMockPersonaStore() }),
    });
    await waitFor(() => expect(screen.getByTestId('persona-yaml')).toBeInTheDocument());
    const lineNumbers = screen.getAllByTestId('yaml-line-number');
    expect(lineNumbers.length).toBeGreaterThan(0);
    // First line number should be "1"
    expect(lineNumbers[0]!.textContent).toBe('1');
  });

  it('renders line numbers sequentially', async () => {
    render(<PersonaYaml name="reviewer" />, {
      wrapper: wrap({ 'ravn.personas': createMockPersonaStore() }),
    });
    await waitFor(() => expect(screen.getByTestId('persona-yaml')).toBeInTheDocument());
    const lineNumbers = screen.getAllByTestId('yaml-line-number');
    lineNumbers.forEach((el, i) => {
      expect(el.textContent).toBe(String(i + 1));
    });
  });

  it('renders token spans with syntax highlighting classes', async () => {
    const mockService = {
      getPersonaYaml: async () => 'name: reviewer\nrole: review',
      listPersonas: async () => [],
    };
    render(<PersonaYaml name="reviewer" />, {
      wrapper: wrap({ 'ravn.personas': mockService }),
    });
    await waitFor(() => expect(screen.getByTestId('persona-yaml')).toBeInTheDocument());
    const container = screen.getByTestId('persona-yaml');
    // Keys should have cyan token class
    const spans = container.querySelectorAll('span.niuu-text-status-cyan');
    expect(spans.length).toBeGreaterThan(0);
  });

  it('highlights YAML comment lines', async () => {
    const mockService = {
      getPersonaYaml: async () => '# this is a comment\nname: test',
      listPersonas: async () => [],
    };
    render(<PersonaYaml name="reviewer" />, {
      wrapper: wrap({ 'ravn.personas': mockService }),
    });
    await waitFor(() => expect(screen.getByTestId('persona-yaml')).toBeInTheDocument());
    const container = screen.getByTestId('persona-yaml');
    const comments = container.querySelectorAll('span.niuu-text-text-muted.niuu-italic');
    expect(comments.length).toBeGreaterThan(0);
  });

  it('highlights boolean values with boolean token class', async () => {
    const mockService = {
      getPersonaYaml: async () => 'thinking: true\nactive: false',
      listPersonas: async () => [],
    };
    render(<PersonaYaml name="reviewer" />, {
      wrapper: wrap({ 'ravn.personas': mockService }),
    });
    await waitFor(() => expect(screen.getByTestId('persona-yaml')).toBeInTheDocument());
    const container = screen.getByTestId('persona-yaml');
    const booleans = container.querySelectorAll('span.niuu-text-status-purple');
    expect(booleans.length).toBeGreaterThan(0);
  });

  it('highlights numeric values with number token class', async () => {
    const mockService = {
      getPersonaYaml: async () => 'max_tokens: 8192\nbudget: 25',
      listPersonas: async () => [],
    };
    render(<PersonaYaml name="reviewer" />, {
      wrapper: wrap({ 'ravn.personas': mockService }),
    });
    await waitFor(() => expect(screen.getByTestId('persona-yaml')).toBeInTheDocument());
    const container = screen.getByTestId('persona-yaml');
    const numbers = container.querySelectorAll('span.niuu-text-status-amber');
    expect(numbers.length).toBeGreaterThan(0);
  });
});
