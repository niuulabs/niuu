import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { WorkflowsRoute } from './WorkflowsRoute';
import type { ReactNode } from 'react';

vi.mock('@tanstack/react-router', () => ({
  Link: ({ to, children }: { to: string; children: ReactNode }) => <a href={to}>{children}</a>,
  useSearch: () => mockSearch.current,
}));

const mockSearch = vi.hoisted(() => ({ current: {} as { id?: string } }));

vi.mock('./SimpleWorkflowsPage', () => ({
  SimpleWorkflowsPage: () => <div data-testid="simple-page" />,
}));

vi.mock('./WorkflowBuilderPage', () => ({
  WorkflowBuilderPage: () => <div data-testid="builder-page" />,
}));

describe('WorkflowsRoute', () => {
  it('renders the workflow catalog in every interface mode', () => {
    mockSearch.current = {};
    render(<WorkflowsRoute />);
    expect(screen.getByTestId('simple-page')).toBeInTheDocument();
  });

  it('preserves historical editor deep links carrying a workflow id', () => {
    mockSearch.current = { id: 'workflow-1' };
    render(<WorkflowsRoute />);
    expect(screen.getByTestId('builder-page')).toBeInTheDocument();
  });
});
