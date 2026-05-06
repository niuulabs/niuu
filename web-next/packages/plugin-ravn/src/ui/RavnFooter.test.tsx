import { describe, it, expect, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { RavnFooter } from './RavnFooter';
import {
  createMockRavenStream,
  createMockSessionStream,
  createMockPersonaStore,
  createMockTriggerStore,
  createMockBudgetStream,
} from '../adapters/mock';
import { wrapWithServices } from '../testing/wrapWithRavn';

function services() {
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

describe('RavnFooter', () => {
  it('renders the footer container', () => {
    render(<RavnFooter />, { wrapper: wrapWithServices(services()) });
    expect(screen.getByTestId('ravn-footer')).toBeInTheDocument();
  });

  it('shows ravens count', async () => {
    render(<RavnFooter />, { wrapper: wrapWithServices(services()) });
    await waitFor(() => {
      const chip = screen.getByTestId('footer-chip-ravens');
      expect(chip.textContent).toContain('ravens');
      expect(chip.textContent).toMatch(/\d+\/\d+/);
    });
  });

  it('shows sessions count', async () => {
    render(<RavnFooter />, { wrapper: wrapWithServices(services()) });
    await waitFor(() => {
      const chip = screen.getByTestId('footer-chip-sessions');
      expect(chip.textContent).toContain('active');
    });
  });
});
