import { describe, it, expect } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import { HealthPage } from './HealthPage';
import { createMimirMockAdapter } from '../adapters/mock';
import type { IMimirService } from '../ports';
import { renderWithMimir } from '../testing/renderWithMimir';

const wrap = renderWithMimir;

describe('HealthPage', () => {
  it('stacks the doctor checklist above the lint detail', async () => {
    wrap(<HealthPage />, undefined, { tweaks: { activeMount: 'local' } });
    expect(screen.getByTestId('health-page')).toBeInTheDocument();
    // Doctor section (from the mock adapter's doctor report)
    await waitFor(() => expect(screen.getByRole('heading', { name: /doctor/i })).toBeVisible());
    // Lint section below it (LintPage exposes a labelled rules nav, not a heading)
    await waitFor(() => expect(screen.getByLabelText('Lint checks')).toBeVisible());
  });

  it('keeps both sections functional when the doctor backend is absent', async () => {
    const service: IMimirService = {
      ...createMimirMockAdapter(),
      mounts: {
        ...createMimirMockAdapter().mounts,
        getDoctor: async () => null,
      },
    };
    wrap(<HealthPage />, service, { tweaks: { activeMount: 'local' } });
    await waitFor(() => expect(screen.getByTestId('doctor-empty')).toBeInTheDocument());
    await waitFor(() => expect(screen.getByLabelText('Lint checks')).toBeVisible());
  });
});

it('asks for an instance instead of linting incompatible backends together', async () => {
  wrap(<HealthPage />);
  expect(await screen.findByText('Select an instance to run its health checks.')).toBeVisible();
  expect(screen.queryByLabelText('Lint checks')).not.toBeInTheDocument();
});

it('directs gbrain to its native maintenance results', async () => {
  const service = createMimirMockAdapter();
  service.mounts.inspectInstances = async () => [
    { mount: 'brain', backend: 'gbrain', metrics: {}, unavailable: [] },
  ];
  wrap(<HealthPage />, service, { tweaks: { activeMount: 'brain' } });
  expect(await screen.findByRole('heading', { name: 'gbrain maintenance' })).toBeVisible();
  expect(screen.queryByLabelText('Lint checks')).not.toBeInTheDocument();
});
