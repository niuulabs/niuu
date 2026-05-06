/**
 * RavnPage tests.
 *
 * The component is a thin wrapper around OverviewPage at /ravn.
 * Tab navigation is handled by the shell via PluginDescriptor.tabs — tested in
 * integration/e2e, not here.
 */

import { describe, it, expect, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { RavnPage } from './RavnPage';
import {
  createMockPersonaStore,
  createMockRavenStream,
  createMockTriggerStore,
  createMockSessionStream,
  createMockBudgetStream,
} from '../adapters/mock';
import { wrapWithServices } from '../testing/wrapWithRavn';

function allServices() {
  return {
    'ravn.personas': createMockPersonaStore(),
    'ravn.ravens': createMockRavenStream(),
    'ravn.sessions': createMockSessionStream(),
    'ravn.triggers': createMockTriggerStore(),
    'ravn.budget': createMockBudgetStream(),
  };
}

beforeEach(() => {
  localStorage.clear();
});

describe('RavnPage', () => {
  it('renders the ravn-page container', () => {
    render(<RavnPage />, { wrapper: wrapWithServices(allServices()) });
    expect(screen.getByTestId('ravn-page')).toBeInTheDocument();
  });

  it('renders the overview page by default', async () => {
    render(<RavnPage />, { wrapper: wrapWithServices(allServices()) });
    await waitFor(() => expect(screen.getByTestId('overview-page')).toBeInTheDocument());
  });

  it('shows KPI strip on load', async () => {
    render(<RavnPage />, { wrapper: wrapWithServices(allServices()) });
    await waitFor(() => {
      expect(screen.getByTestId('kpi-ravens')).toBeInTheDocument();
    });
  });

  it('shows active ravens section', async () => {
    render(<RavnPage />, { wrapper: wrapWithServices(allServices()) });
    await waitFor(() => {
      expect(screen.getByTestId('active-ravens-list')).toBeInTheDocument();
    });
  });
});
