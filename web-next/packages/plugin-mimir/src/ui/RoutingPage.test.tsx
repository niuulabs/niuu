import { describe, it, expect, vi } from 'vitest';
import { screen, fireEvent, waitFor } from '@testing-library/react';
import { RoutingPage } from './RoutingPage';
import { createMimirMockAdapter } from '../adapters/mock';
import type { IMimirService } from '../ports';
import { renderWithMimir } from '../testing/renderWithMimir';

vi.mock('@tanstack/react-router', () => ({
  Link: ({
    to,
    className,
    children,
  }: {
    to: string;
    className?: string;
    children: React.ReactNode;
  }) => (
    <a href={to} className={className}>
      {children}
    </a>
  ),
}));

const wrap = renderWithMimir;

describe('RoutingPage', () => {
  it('renders the page title', () => {
    wrap(<RoutingPage />);
    expect(screen.getByRole('heading', { name: /write routing/i })).toBeInTheDocument();
  });

  it('renders a link to the Sources page for ingest', () => {
    wrap(<RoutingPage />);
    const link = screen.getByRole('link', { name: /sources/i });
    expect(link).toBeInTheDocument();
    expect(link).toHaveAttribute('href', '/mimir');
  });

  it('shows loading state initially', () => {
    wrap(<RoutingPage />);
    expect(screen.getByText(/loading rules/)).toBeInTheDocument();
  });

  it('renders routing rule rows after load', async () => {
    wrap(<RoutingPage />);
    await waitFor(() =>
      expect(screen.getAllByTestId('routing-rule-row').length).toBeGreaterThan(0),
    );
  });

  it('shows rule prefixes in the table', async () => {
    wrap(<RoutingPage />);
    await waitFor(() => expect(screen.getByText('/infra')).toBeInTheDocument());
    expect(screen.getByText('/api')).toBeInTheDocument();
  });

  it('shows "Add rule" button', async () => {
    wrap(<RoutingPage />);
    await waitFor(() =>
      expect(screen.getByRole('button', { name: /add rule/i })).toBeInTheDocument(),
    );
  });

  it('clicking "Add rule" shows the rule editor form', async () => {
    wrap(<RoutingPage />);
    await waitFor(() => screen.getByRole('button', { name: /add rule/i }));
    fireEvent.click(screen.getByRole('button', { name: /add rule/i }));
    expect(screen.getByTestId('rule-editor')).toBeInTheDocument();
    expect(screen.getByRole('form', { name: /routing rule editor/i })).toBeInTheDocument();
  });

  it('clicking "Edit" on a rule opens the inline editor', async () => {
    wrap(<RoutingPage />);
    await waitFor(() =>
      expect(screen.getAllByRole('button', { name: /edit rule/i }).length).toBeGreaterThan(0),
    );
    fireEvent.click(screen.getAllByRole('button', { name: /edit rule/i })[0]!);
    expect(screen.getByTestId('rule-editor')).toBeInTheDocument();
  });

  it('cancelling the editor hides it', async () => {
    wrap(<RoutingPage />);
    await waitFor(() => screen.getByRole('button', { name: /add rule/i }));
    fireEvent.click(screen.getByRole('button', { name: /add rule/i }));
    expect(screen.getByTestId('rule-editor')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /cancel/i }));
    expect(screen.queryByTestId('rule-editor')).not.toBeInTheDocument();
  });

  it('renders the test pane section', async () => {
    wrap(<RoutingPage />);
    await waitFor(() =>
      expect(screen.getByRole('region', { name: /route test pane/i })).toBeInTheDocument(),
    );
  });

  it('test pane shows no result before typing', async () => {
    wrap(<RoutingPage />);
    await waitFor(() => screen.getByTestId('test-path-input'));
    expect(screen.queryByTestId('test-result')).not.toBeInTheDocument();
  });

  it('test pane shows match result after typing a matching path', async () => {
    wrap(<RoutingPage />);
    await waitFor(() => screen.getByTestId('test-path-input'));
    fireEvent.change(screen.getByTestId('test-path-input'), {
      target: { value: '/infra/k8s' },
    });
    await waitFor(() => expect(screen.getByTestId('test-result')).toBeInTheDocument());
    expect(screen.getByTestId('test-result')).toHaveTextContent(/platform/);
  });

  it('test pane shows no-match result when no rules exist', async () => {
    const noRules: IMimirService = {
      ...createMimirMockAdapter(),
      mounts: {
        ...createMimirMockAdapter().mounts,
        listRoutingRules: async () => [],
      },
    };
    wrap(<RoutingPage />, noRules);
    await waitFor(() => screen.getByTestId('test-path-input'));
    fireEvent.change(screen.getByTestId('test-path-input'), {
      target: { value: '/some/path' },
    });
    await waitFor(() => expect(screen.getByTestId('test-result')).toBeInTheDocument());
    expect(screen.getByTestId('test-result')).toHaveTextContent(/no active rule/i);
  });

  it('shows error state when service throws', async () => {
    const failing: IMimirService = {
      ...createMimirMockAdapter(),
      mounts: {
        ...createMimirMockAdapter().mounts,
        listRoutingRules: async () => {
          throw new Error('routing service down');
        },
      },
    };
    wrap(<RoutingPage />, failing);
    await waitFor(() => expect(screen.getByText('routing service down')).toBeInTheDocument());
  });

  it('saving a rule via the form calls upsertRule', async () => {
    let savedRule = null;
    const spy: IMimirService = {
      ...createMimirMockAdapter(),
      mounts: {
        ...createMimirMockAdapter().mounts,
        upsertRoutingRule: async (rule) => {
          savedRule = rule;
          return rule;
        },
      },
    };
    wrap(<RoutingPage />, spy);
    await waitFor(() => screen.getByRole('button', { name: /add rule/i }));
    fireEvent.click(screen.getByRole('button', { name: /add rule/i }));
    fireEvent.change(screen.getByLabelText(/path prefix/i), {
      target: { value: '/test' },
    });
    fireEvent.change(screen.getByLabelText(/target mount/i), {
      target: { value: 'local' },
    });
    fireEvent.click(screen.getByRole('button', { name: /save rule/i }));
    await waitFor(() => expect(savedRule).not.toBeNull());
  });
});
