import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { createMockSetupService } from '../adapters/mock';
import { RuntimeStep } from './RuntimeStep';

async function view(bindHost = '0.0.0.0') {
  const service = createMockSetupService({ latencyMs: 0, initialStack: { bindHost } });
  return service.getStack();
}

describe('RuntimeStep', () => {
  it('shows network access with the LAN address and warning, and stages a change', async () => {
    const onStage = vi.fn();
    render(
      <RuntimeStep
        stack={await view()}
        loading={false}
        unavailable={null}
        staging={false}
        stageError={null}
        onStage={onStage}
      />,
    );
    expect(screen.getByTestId('setup-runtime-docker')).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByTestId('setup-runtime-docker')).not.toBeDisabled();
    expect(screen.getByTestId('setup-runtime-openshell')).toBeDisabled();
    expect(screen.getByTestId('setup-runtime-image')).toHaveTextContent(
      'ghcr.io/niuulabs/skuld:dev',
    );
    expect(screen.getByTestId('setup-access-lan')).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByTestId('setup-access-lan')).toHaveTextContent('http://192.168.1.42:8080');
    expect(screen.getByTestId('setup-access-warning')).toBeInTheDocument();
    expect(screen.getByTestId('setup-access-public')).toBeDisabled();
    fireEvent.click(screen.getByTestId('setup-access-local'));
    expect(onStage).toHaveBeenCalledWith({ bind_host: '127.0.0.1' });
  });

  it('shows a staged change and local-only access', async () => {
    const service = createMockSetupService({ latencyMs: 0 });
    const staged = await service.stageStack({ bind_host: '127.0.0.1' });
    render(
      <RuntimeStep
        stack={staged}
        loading={false}
        unavailable={null}
        staging
        stageError={null}
        onStage={vi.fn()}
      />,
    );
    expect(screen.getByTestId('setup-access-local')).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByTestId('setup-access-local')).toBeDisabled();
    expect(screen.getByTestId('setup-access-staged')).toBeInTheDocument();
    expect(screen.queryByTestId('setup-access-warning')).not.toBeInTheDocument();
  });

  it('explains an install without the stack controller and shows errors', () => {
    render(
      <RuntimeStep
        stack={undefined}
        loading={false}
        unavailable={new Error('Stack changes are not available on this install')}
        staging={false}
        stageError={new Error('bind_host must be one of')}
        onStage={vi.fn()}
      />,
    );
    expect(screen.getByTestId('setup-access-unavailable')).toHaveTextContent('not available');
    expect(screen.queryByTestId('setup-access-lan')).not.toBeInTheDocument();
    expect(screen.getByRole('alert')).toHaveTextContent('bind_host');
  });
});
