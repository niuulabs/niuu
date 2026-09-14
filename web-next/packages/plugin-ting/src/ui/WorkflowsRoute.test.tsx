import { render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { WorkflowsRoute } from './WorkflowsRoute';

const mockMode = vi.hoisted(() => ({ current: 'simple' as 'simple' | 'advanced' }));

vi.mock('@niuulabs/shell', () => ({
  useUiMode: () => mockMode.current,
}));

vi.mock('./SimpleWorkflowsPage', () => ({
  SimpleWorkflowsPage: () => <div data-testid="simple-page" />,
}));

vi.mock('./WorkflowBuilderPage', () => ({
  WorkflowBuilderPage: () => <div data-testid="builder-page" />,
}));

describe('WorkflowsRoute', () => {
  beforeEach(() => {
    mockMode.current = 'simple';
  });

  it('renders the simple page in Simple mode', () => {
    render(<WorkflowsRoute />);
    expect(screen.getByTestId('simple-page')).toBeInTheDocument();
  });

  it('renders the builder in Advanced mode', () => {
    mockMode.current = 'advanced';
    render(<WorkflowsRoute />);
    expect(screen.getByTestId('builder-page')).toBeInTheDocument();
  });
});
