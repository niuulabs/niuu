import { describe, expect, it, vi } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import { renderWithSetup } from '../testing/renderWithSetup';
import { createMockSetupService } from '../adapters/mock';
import { SetupGate, isSetupPath } from './SetupGate';

describe('isSetupPath', () => {
  it('recognises the wizard and ready routes', () => {
    expect(isSetupPath('/setup')).toBe(true);
    expect(isSetupPath('/setup/step')).toBe(true);
    expect(isSetupPath('/ready')).toBe(true);
    expect(isSetupPath('/')).toBe(false);
    expect(isSetupPath('/settings')).toBe(false);
  });
});

describe('SetupGate', () => {
  it('renders children when no setup service is registered', () => {
    renderWithSetup(
      <SetupGate pathname="/">
        <div>app</div>
      </SetupGate>,
      { service: null },
    );
    expect(screen.getByText('app')).toBeInTheDocument();
  });

  it('redirects to /setup when setup is enabled and incomplete', async () => {
    const redirect = vi.fn();
    renderWithSetup(
      <SetupGate pathname="/" redirect={redirect}>
        <div>app</div>
      </SetupGate>,
    );
    expect(screen.getByText('app')).toBeInTheDocument();
    await waitFor(() => expect(redirect).toHaveBeenCalledWith('/setup'));
    expect(screen.queryByText('app')).not.toBeInTheDocument();
  });

  it('does not redirect on the wizard itself', async () => {
    const redirect = vi.fn();
    renderWithSetup(
      <SetupGate pathname="/setup" redirect={redirect}>
        <div>wizard</div>
      </SetupGate>,
    );
    await waitFor(() => expect(screen.getByText('wizard')).toBeInTheDocument());
    await new Promise((resolve) => setTimeout(resolve, 10));
    expect(redirect).not.toHaveBeenCalled();
  });

  it('does not redirect when setup is completed or disabled', async () => {
    const redirect = vi.fn();
    const completed = createMockSetupService({ latencyMs: 0, initialState: { completed: true } });
    renderWithSetup(
      <SetupGate pathname="/" redirect={redirect}>
        <div>app</div>
      </SetupGate>,
      { service: completed },
    );
    await new Promise((resolve) => setTimeout(resolve, 10));
    expect(redirect).not.toHaveBeenCalled();

    const disabled = createMockSetupService({ latencyMs: 0, initialState: { enabled: false } });
    renderWithSetup(
      <SetupGate pathname="/" redirect={redirect}>
        <div>app2</div>
      </SetupGate>,
      { service: disabled },
    );
    await new Promise((resolve) => setTimeout(resolve, 10));
    expect(redirect).not.toHaveBeenCalled();
  });

  it('falls back to window.location.replace and keeps the query string', async () => {
    const replace = vi.fn();
    vi.stubGlobal('location', {
      ...window.location,
      replace,
      pathname: '/',
      search: '?config=default',
    });
    renderWithSetup(
      <SetupGate pathname="/">
        <div>app</div>
      </SetupGate>,
    );
    await waitFor(() => expect(replace).toHaveBeenCalledWith('/setup?config=default'));
    vi.unstubAllGlobals();
  });

  it('keeps the app usable when the state request fails', async () => {
    const redirect = vi.fn();
    const broken = {
      ...createMockSetupService({ latencyMs: 0 }),
      getState: async () => {
        throw new Error('down');
      },
    };
    renderWithSetup(
      <SetupGate pathname="/" redirect={redirect}>
        <div>app</div>
      </SetupGate>,
      { service: broken },
    );
    await new Promise((resolve) => setTimeout(resolve, 10));
    expect(screen.getByText('app')).toBeInTheDocument();
    expect(redirect).not.toHaveBeenCalled();
  });
});
