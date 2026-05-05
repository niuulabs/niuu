import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { IntegrationsSection } from './IntegrationsSection';

describe('IntegrationsSection', () => {
  it('renders the section heading', () => {
    render(<IntegrationsSection />);
    expect(screen.getByText('Integrations')).toBeInTheDocument();
  });

  it('renders the description', () => {
    render(<IntegrationsSection />);
    expect(
      screen.getByText('Trackers, repos, notifiers reachable by the saga coordinator.'),
    ).toBeInTheDocument();
  });

  it('renders all 5 integration cards', () => {
    render(<IntegrationsSection />);
    expect(screen.getByText('Linear')).toBeInTheDocument();
    expect(screen.getByText('GitHub')).toBeInTheDocument();
    expect(screen.getByText('Jira')).toBeInTheDocument();
    expect(screen.getByText('Slack')).toBeInTheDocument();
    expect(screen.getByText('PagerDuty')).toBeInTheDocument();
  });

  it('shows Connect button for disconnected integrations', () => {
    render(<IntegrationsSection />);
    const connectButtons = screen.getAllByText('Connect');
    expect(connectButtons).toHaveLength(2); // Jira, PagerDuty
  });

  it('shows Disconnect button for connected integrations', () => {
    render(<IntegrationsSection />);
    const disconnectButtons = screen.getAllByText('Disconnect');
    expect(disconnectButtons).toHaveLength(3); // Linear, GitHub, Slack
  });

  it('shows "not connected" for disconnected integrations', () => {
    render(<IntegrationsSection />);
    const notConnected = screen.getAllByText('not connected');
    expect(notConnected).toHaveLength(2);
  });

  it('shows api key detail for connected integrations', () => {
    render(<IntegrationsSection />);
    const apiDetails = screen.getAllByText('api key · ends ···g84');
    expect(apiDetails).toHaveLength(3);
  });

  it('has an accessible section label', () => {
    render(<IntegrationsSection />);
    expect(screen.getByRole('region', { name: /integrations/i })).toBeInTheDocument();
  });

  it('renders an accessible list', () => {
    render(<IntegrationsSection />);
    expect(screen.getByRole('list', { name: /integration list/i })).toBeInTheDocument();
    expect(screen.getAllByRole('listitem')).toHaveLength(5);
  });
});
