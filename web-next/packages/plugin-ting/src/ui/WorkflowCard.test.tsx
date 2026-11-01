import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { WorkflowCard } from './WorkflowCard';

describe('WorkflowCard', () => {
  it('renders the workflow heading', () => {
    render(<WorkflowCard />);
    expect(screen.getByText('Workflow')).toBeInTheDocument();
  });

  it('renders unassigned state when no workflow prop supplied', () => {
    render(<WorkflowCard />);
    expect(screen.getByText('No workflow assigned')).toBeInTheDocument();
  });

  it('renders the provided workflow name', () => {
    render(<WorkflowCard workflow="custom-flow" />);
    expect(screen.getByText('custom-flow')).toBeInTheDocument();
  });

  it('omits the version chip when no workflowVersion prop supplied', () => {
    render(<WorkflowCard />);
    expect(screen.queryByText(/^v/)).not.toBeInTheDocument();
  });

  it('renders the provided version chip', () => {
    render(<WorkflowCard workflowVersion="2.3.4" />);
    expect(screen.getByText('v2.3.4')).toBeInTheDocument();
  });

  it('renders all five flock persona labels', () => {
    render(<WorkflowCard />);
    expect(screen.getByText('Decomposer')).toBeInTheDocument();
    expect(screen.getByText('Coding Agent')).toBeInTheDocument();
    expect(screen.getByText('QA Agent')).toBeInTheDocument();
    expect(screen.getByText('Reviewer')).toBeInTheDocument();
    expect(screen.getByText('Ship Agent')).toBeInTheDocument();
  });

  it('renders the workflow description text', () => {
    render(<WorkflowCard workflow="ship" />);
    expect(screen.getByText(/saved as the saga default/i)).toBeInTheDocument();
  });

  it('renders the dispatch override info row', () => {
    render(<WorkflowCard />);
    expect(screen.getByText(/per-dispatch overrides from dispatch/i)).toBeInTheDocument();
  });

  it('renders the flock section with accessible container', () => {
    render(<WorkflowCard />);
    expect(screen.getByLabelText(/workflow participants/i)).toBeInTheDocument();
  });

  it('renders APPLIED · PER-SAGA label', () => {
    render(<WorkflowCard />);
    expect(screen.getByText(/APPLIED · PER-SAGA/i)).toBeInTheDocument();
  });

  it('renders assign and clear actions when callbacks are supplied', () => {
    render(
      <WorkflowCard
        workflow="ship"
        workflowVersion="1.4.2"
        onAssign={() => {}}
        onClear={() => {}}
      />,
    );
    expect(screen.getByRole('button', { name: 'Change' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Clear' })).toBeInTheDocument();
  });

  it('renders FLOCK section label', () => {
    render(<WorkflowCard />);
    expect(screen.getByText(/^FLOCK$/i)).toBeInTheDocument();
  });

  it('renders workflow section with accessible region label', () => {
    render(<WorkflowCard />);
    expect(screen.getByRole('region', { name: /workflow/i })).toBeInTheDocument();
  });
});
