import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MOCK_SYSTEM } from '../adapters/mock';
import type { SetupState } from '../domain/setup';
import { RuntimeStep } from './RuntimeStep';

const state: SetupState = {
  enabled: true,
  mode: 'docker',
  completed: false,
  completedAt: null,
  steps: [],
  completedSteps: [],
};

describe('RuntimeStep', () => {
  it('shows network access with both addresses and the sign-in warning', () => {
    render(<RuntimeStep facts={MOCK_SYSTEM.host} state={state} />);
    expect(screen.getByTestId('setup-access-lan')).toBeInTheDocument();
    expect(screen.getByTestId('setup-runtime-image')).toHaveTextContent(
      'ghcr.io/niuulabs/skuld:dev',
    );
    expect(screen.getByTestId('setup-access-urls')).toHaveTextContent('http://127.0.0.1:8080');
    expect(screen.getByTestId('setup-access-urls')).toHaveTextContent('http://192.168.1.42:8080');
    expect(screen.getByText('Sign-in is off')).toBeInTheDocument();
    expect(screen.getByText(/Anyone who can reach the address above/)).toBeInTheDocument();
  });

  it('shows local-only access', () => {
    render(
      <RuntimeStep
        facts={{ ...MOCK_SYSTEM.host!, bind_host: '127.0.0.1' }}
        state={{ ...state, mode: 'mini' }}
      />,
    );
    expect(screen.getByTestId('setup-access-local')).toBeInTheDocument();
    expect(screen.getByText('This machine only')).toBeInTheDocument();
    expect(screen.getByTestId('setup-access-urls')).not.toHaveTextContent('192.168.1.42');
    expect(screen.getByText(/Fine for a single-user machine/)).toBeInTheDocument();
  });

  it('handles missing facts and an authenticated platform', () => {
    render(<RuntimeStep facts={null} state={{ ...state, mode: 'cluster' }} />);
    expect(screen.getByTestId('setup-access-unknown')).toBeInTheDocument();
    expect(screen.queryByTestId('setup-access-urls')).not.toBeInTheDocument();
    expect(screen.queryByTestId('setup-runtime-image')).not.toBeInTheDocument();
    expect(screen.getByText('Sign-in is on')).toBeInTheDocument();
  });
});
