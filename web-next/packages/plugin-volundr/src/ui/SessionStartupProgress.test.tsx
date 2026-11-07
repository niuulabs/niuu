import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, render, screen, within } from '@testing-library/react';
import type { SessionStartup } from '../models/volundr.model';
import { SessionStartupHeadline, SessionStartupProgress } from './SessionStartupProgress';

const T0 = Date.parse('2026-11-06T12:00:00Z');
const iso = (seconds: number) => new Date(T0 + seconds * 1000).toISOString();

const podLaunch: SessionStartup = {
  startedAt: iso(0),
  progressError: null,
  stages: [
    {
      key: 'launch',
      label: 'Preparing launch',
      state: 'done',
      detail: null,
      startedAt: iso(0),
      finishedAt: iso(2),
    },
    {
      key: 'volumes',
      label: 'Attaching volumes',
      state: 'done',
      detail: 'AttachVolume.Attach succeeded for volume "pvc-91c2"',
      startedAt: iso(2),
      finishedAt: iso(40),
    },
    {
      key: 'containers',
      label: 'Starting containers',
      state: 'active',
      detail: 'Pulling image "ghcr.io/niuulabs/skuld:dev"',
      startedAt: iso(40),
      finishedAt: null,
    },
    {
      key: 'release_ready',
      label: 'Finishing the Helm release',
      state: 'pending',
      detail: null,
      startedAt: null,
      finishedAt: null,
    },
  ],
};

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(T0 + 46_000);
});

afterEach(() => {
  vi.useRealTimers();
});

describe('SessionStartupProgress', () => {
  it('lists every stage with its real duration and the backend detail', () => {
    render(<SessionStartupProgress startup={podLaunch} status="starting" />);

    expect(screen.getByText('Starting')).toBeInTheDocument();
    expect(screen.getByTestId('session-startup-elapsed')).toHaveTextContent('46s');
    const volumes = screen.getByTestId('startup-stage-volumes');
    expect(volumes).toHaveAttribute('data-state', 'done');
    expect(within(volumes).getByText('38s')).toBeInTheDocument();
    expect(within(volumes).getByText(/AttachVolume.Attach succeeded/)).toBeInTheDocument();
    const containers = screen.getByTestId('startup-stage-containers');
    expect(containers).toHaveAttribute('data-state', 'active');
    expect(within(containers).getByText('6s')).toBeInTheDocument();
    expect(within(containers).getByText(/in progress/)).toBeInTheDocument();
    expect(screen.getByTestId('startup-stage-release_ready')).toHaveAttribute(
      'data-state',
      'pending',
    );
  });

  it('keeps counting from the recorded start, once a second', () => {
    render(<SessionStartupProgress startup={podLaunch} status="provisioning" />);

    act(() => {
      vi.advanceTimersByTime(3_000);
    });

    expect(screen.getByTestId('session-startup-elapsed')).toHaveTextContent('49s');
  });

  it('shows a failed launch with the reason in place', () => {
    render(
      <SessionStartupProgress
        startup={podLaunch}
        status="failed"
        error="0/9 nodes are available: 9 Insufficient memory."
      />,
    );

    expect(screen.getByText('Startup failed')).toBeInTheDocument();
    const containers = screen.getByTestId('startup-stage-containers');
    expect(containers).toHaveAttribute('data-state', 'failed');
    expect(within(containers).getByText(/9 Insufficient memory/)).toBeInTheDocument();
    expect(screen.queryByTestId('startup-stage-release_ready')).not.toBeInTheDocument();
  });

  it('says when Forge could not read the progress', () => {
    render(
      <SessionStartupProgress
        startup={{ ...podLaunch, progressError: 'may not list events in namespace skuld' }}
        status="starting"
      />,
    );

    expect(screen.getByTestId('session-startup-progress-error')).toHaveTextContent(
      "Forge could not read this launch's progress: may not list events in namespace skuld",
    );
  });
});

describe('SessionStartupHeadline', () => {
  it('names the current stage and the launch time', () => {
    render(<SessionStartupHeadline startup={podLaunch} status="starting" />);

    expect(screen.getByTestId('session-startup-headline')).toHaveTextContent(
      'Starting containers · Pulling image "ghcr.io/niuulabs/skuld:dev" · 46s',
    );
  });

  it('renders nothing once the launch has no open stage', () => {
    const closed: SessionStartup = {
      ...podLaunch,
      stages: podLaunch.stages.slice(0, 2),
    };
    const { container } = render(<SessionStartupHeadline startup={closed} status="running" />);

    expect(container).toBeEmptyDOMElement();
  });
});
