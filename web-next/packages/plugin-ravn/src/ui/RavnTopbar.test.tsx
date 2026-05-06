import { describe, it, expect, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { RavnTopbar } from './RavnTopbar';
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

describe('RavnTopbar', () => {
  it('renders the topbar container', () => {
    render(<RavnTopbar />, { wrapper: wrapWithServices(services()) });
    expect(screen.getByTestId('ravn-topbar')).toBeInTheDocument();
  });

  it('renders active ravens chip', async () => {
    render(<RavnTopbar />, { wrapper: wrapWithServices(services()) });
    await waitFor(() => {
      const chip = screen.getByTestId('topbar-chip-ok');
      expect(chip).toBeInTheDocument();
      expect(chip.textContent).toMatch(/active/);
    });
  });

  it('renders sessions chip', async () => {
    render(<RavnTopbar />, { wrapper: wrapWithServices(services()) });
    await waitFor(() => {
      const chip = screen.getByTestId('topbar-chip-dim');
      expect(chip).toBeInTheDocument();
      expect(chip.textContent).toMatch(/sessions/);
    });
  });

  it('does not render failed chip when no failed ravens', async () => {
    render(<RavnTopbar />, { wrapper: wrapWithServices(services()) });
    await waitFor(() => expect(screen.getByTestId('ravn-topbar')).toBeInTheDocument());
    // Mock data has no failed ravens
    expect(screen.queryByTestId('topbar-chip-err')).not.toBeInTheDocument();
  });

  it('shows 10 active ravens from mock data', async () => {
    render(<RavnTopbar />, { wrapper: wrapWithServices(services()) });
    // Mock has 10 active ravens (sindri, víðar, muninn, gefjon, fjölnir, saga, eir, bragi, delling, höðr)
    await waitFor(() => {
      expect(screen.getByTestId('topbar-chip-ok').textContent).toContain('10');
    });
  });

  it('shows 10 running sessions from mock data', async () => {
    render(<RavnTopbar />, { wrapper: wrapWithServices(services()) });
    // Mock has 10 running sessions
    await waitFor(() => {
      expect(screen.getByTestId('topbar-chip-dim').textContent).toContain('10');
    });
  });

  it('renders chip icons', async () => {
    render(<RavnTopbar />, { wrapper: wrapWithServices(services()) });
    await waitFor(() => {
      expect(screen.getByTestId('ravn-topbar')).toBeInTheDocument();
    });
    // ok chip has ● icon, dim chip has ◷ icon
    const okChip = screen.getByTestId('topbar-chip-ok');
    const dimChip = screen.getByTestId('topbar-chip-dim');
    expect(okChip.textContent).toContain('●');
    expect(dimChip.textContent).toContain('◷');
  });
});
