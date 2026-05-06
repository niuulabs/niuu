import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import type { VolundrSession, VolundrModel } from '@/modules/volundr/models';
import { SessionCard } from './SessionCard';

describe('SessionCard', () => {
  const runningSession: VolundrSession = {
    id: 'forge-7f3a2b1c',
    name: 'printer-firmware-thermal',
    source: {
      type: 'git',
      repo: 'kanuckvalley/printer-firmware',
      branch: 'feature/thermal-calibration',
    },
    status: 'running',
    model: 'qwen3-coder:70b',
    lastActive: Date.now() - 1000 * 60 * 5,
    messageCount: 47,
    tokensUsed: 156420,
    podName: 'skuld-7f3a2b1c-xkj2p',
  };

  const stoppedSession: VolundrSession = {
    id: 'forge-2c5d9e7b',
    name: 'nalir-truenas-adapter',
    source: { type: 'git', repo: 'kanuckvalley/nalir', branch: 'feature/truenas-integration' },
    status: 'stopped',
    model: 'qwen3-coder:32b',
    lastActive: Date.now() - 1000 * 60 * 60 * 3,
    messageCount: 89,
    tokensUsed: 287650,
  };

  const errorSession: VolundrSession = {
    id: 'forge-8e2f4a6c',
    name: 'kaolin-support-gen',
    source: {
      type: 'git',
      repo: 'kanuckvalley/kaolin-supports',
      branch: 'feature/fenics-cohesive',
    },
    status: 'error',
    model: 'glm-4.7-flash',
    lastActive: Date.now() - 1000 * 60 * 30,
    messageCount: 56,
    tokensUsed: 178300,
    error: 'OOMKilled - exceeded memory limit',
  };

  const localModel: VolundrModel = {
    name: 'Qwen3 70B',
    provider: 'local',
    tier: 'execution',
    color: '#22c55e',
    vram: '42GB',
  };

  const cloudModel: VolundrModel = {
    name: 'Claude Opus',
    provider: 'cloud',
    tier: 'frontier',
    color: '#a855f7',
    cost: '$15/M',
  };

  it('renders session name', () => {
    render(<SessionCard session={runningSession} />);
    expect(screen.getByText('printer-firmware-thermal')).toBeInTheDocument();
  });

  it('renders repository', () => {
    render(<SessionCard session={runningSession} />);
    expect(screen.getByText('kanuckvalley/printer-firmware')).toBeInTheDocument();
  });

  it('renders branch', () => {
    render(<SessionCard session={runningSession} />);
    expect(screen.getByText('feature/thermal-calibration')).toBeInTheDocument();
  });

  it('renders status badge', () => {
    render(<SessionCard session={runningSession} />);
    expect(screen.getByText('running')).toBeInTheDocument();
  });

  it('renders message count', () => {
    render(<SessionCard session={runningSession} />);
    expect(screen.getByText('47')).toBeInTheDocument();
  });

  it('renders token count formatted', () => {
    render(<SessionCard session={runningSession} />);
    expect(screen.getByText('156.4k tokens')).toBeInTheDocument();
  });

  it('renders model badge when model is provided', () => {
    render(<SessionCard session={runningSession} model={localModel} />);
    expect(screen.getByText('Qwen3 70B')).toBeInTheDocument();
  });

  it('renders GPU label for local model', () => {
    render(<SessionCard session={runningSession} model={localModel} />);
    expect(screen.getByText('GPU')).toBeInTheDocument();
  });

  it('renders API label for cloud model', () => {
    render(<SessionCard session={runningSession} model={cloudModel} />);
    expect(screen.getByText('API')).toBeInTheDocument();
  });

  it('does not render model badge when model is not provided', () => {
    render(<SessionCard session={runningSession} />);
    expect(screen.queryByText('Qwen3 70B')).not.toBeInTheDocument();
  });

  it('renders error message when session has error', () => {
    render(<SessionCard session={errorSession} />);
    expect(screen.getByText('OOMKilled - exceeded memory limit')).toBeInTheDocument();
  });

  it('does not render error when session has no error', () => {
    render(<SessionCard session={runningSession} />);
    expect(screen.queryByText('OOMKilled')).not.toBeInTheDocument();
  });

  it('applies running status style', () => {
    const { container } = render(<SessionCard session={runningSession} />);
    const card = container.firstChild as HTMLElement;
    expect(card.className).toMatch(/running/);
  });

  it('applies stopped status style', () => {
    const { container } = render(<SessionCard session={stoppedSession} />);
    const card = container.firstChild as HTMLElement;
    expect(card.className).toMatch(/stopped/);
  });

  it('applies error status style', () => {
    const { container } = render(<SessionCard session={errorSession} />);
    const card = container.firstChild as HTMLElement;
    expect(card.className).toMatch(/error/);
  });

  it('applies selected style when selected', () => {
    const { container } = render(<SessionCard session={runningSession} selected={true} />);
    const card = container.firstChild as HTMLElement;
    expect(card.className).toMatch(/selected/);
  });

  it('does not apply selected style by default', () => {
    const { container } = render(<SessionCard session={runningSession} />);
    const card = container.firstChild as HTMLElement;
    expect(card.className).not.toMatch(/selected/);
  });

  it('calls onClick when clicked', () => {
    const handleClick = vi.fn();
    render(<SessionCard session={runningSession} onClick={handleClick} />);

    const card = screen.getByRole('button');
    fireEvent.click(card);

    expect(handleClick).toHaveBeenCalledTimes(1);
  });

  it('has button role when onClick is provided', () => {
    render(<SessionCard session={runningSession} onClick={() => {}} />);
    expect(screen.getByRole('button')).toBeInTheDocument();
  });

  it('does not have button role when onClick is not provided', () => {
    render(<SessionCard session={runningSession} />);
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
  });

  it('applies custom className', () => {
    const { container } = render(<SessionCard session={runningSession} className="custom-class" />);
    expect(container.firstChild).toHaveClass('custom-class');
  });

  it('renders last active time', () => {
    render(<SessionCard session={runningSession} />);
    expect(screen.getByText('5m')).toBeInTheDocument();
  });

  it('applies model color to badge via CSS custom property', () => {
    const { container } = render(<SessionCard session={runningSession} model={localModel} />);
    const badge = container.querySelector('[class*="modelBadge"]');
    expect(badge).toHaveAttribute('style', expect.stringContaining('--model-color: #22c55e'));
  });

  it('renders Tracker issue badge when session has a linked issue', () => {
    const sessionWithIssue: VolundrSession = {
      ...runningSession,
      trackerIssue: {
        id: 'issue-1',
        identifier: 'NIU-44',
        title: 'Fix calibration',
        status: 'in_progress',
        url: 'https://linear.app/niuu/issue/NIU-44',
        priority: 2,
      },
    };
    render(<SessionCard session={sessionWithIssue} />);
    expect(screen.getByText('NIU-44')).toBeInTheDocument();
  });

  // ── Manual session (origin = 'manual') ──────────────────────

  const manualSession: VolundrSession = {
    id: 'forge-manual-01',
    name: 'manual-debug',
    source: { type: 'git', repo: 'kanuckvalley/debug', branch: 'main' },
    status: 'running',
    model: 'claude-opus-4-6',
    lastActive: Date.now() - 1000 * 60 * 2,
    messageCount: 12,
    tokensUsed: 45000,
    origin: 'manual',
    hostname: 'my-laptop.local',
  };

  it('renders hostname for manual sessions', () => {
    render(<SessionCard session={manualSession} />);
    expect(screen.getByText('my-laptop.local')).toBeInTheDocument();
  });

  it('renders manual badge for manual sessions', () => {
    render(<SessionCard session={manualSession} />);
    expect(screen.getByText('manual')).toBeInTheDocument();
  });

  it('does not render model badge for manual session without model prop', () => {
    render(<SessionCard session={manualSession} />);
    expect(screen.queryByText('GPU')).not.toBeInTheDocument();
    expect(screen.queryByText('API')).not.toBeInTheDocument();
  });

  // ── Non-git source in full mode ───────────────────────────────

  it('renders source label without branch for non-git source', () => {
    const localSession: VolundrSession = {
      ...runningSession,
      source: { type: 'local_mount', paths: [] },
    };
    render(<SessionCard session={localSession} />);
    expect(screen.queryByText('feature/thermal-calibration')).not.toBeInTheDocument();
  });

  describe('compact mode', () => {
    it('renders session name in compact mode', () => {
      render(<SessionCard session={runningSession} compact={true} />);
      expect(screen.getByText('printer-firmware-thermal')).toBeInTheDocument();
    });

    it('renders message count in compact mode', () => {
      render(<SessionCard session={runningSession} compact={true} />);
      expect(screen.getByText('47')).toBeInTheDocument();
    });

    it('renders last active time in compact mode', () => {
      render(<SessionCard session={runningSession} compact={true} />);
      expect(screen.getByText('5m')).toBeInTheDocument();
    });

    it('applies compact style class', () => {
      const { container } = render(<SessionCard session={runningSession} compact={true} />);
      const card = container.firstChild as HTMLElement;
      expect(card.className).toMatch(/compact/);
    });

    it('applies status style in compact mode', () => {
      const { container } = render(<SessionCard session={runningSession} compact={true} />);
      const card = container.firstChild as HTMLElement;
      expect(card.className).toMatch(/running/);
    });

    it('applies stopped status style in compact mode', () => {
      const { container } = render(<SessionCard session={stoppedSession} compact={true} />);
      const card = container.firstChild as HTMLElement;
      expect(card.className).toMatch(/stopped/);
    });

    it('applies error status style in compact mode', () => {
      const { container } = render(<SessionCard session={errorSession} compact={true} />);
      const card = container.firstChild as HTMLElement;
      expect(card.className).toMatch(/error/);
    });

    it('applies selected style in compact mode', () => {
      const { container } = render(
        <SessionCard session={runningSession} compact={true} selected={true} />
      );
      const card = container.firstChild as HTMLElement;
      expect(card.className).toMatch(/selected/);
    });

    it('does not apply selected style by default in compact mode', () => {
      const { container } = render(<SessionCard session={runningSession} compact={true} />);
      const card = container.firstChild as HTMLElement;
      expect(card.className).not.toMatch(/selected/);
    });

    it('renders repository link for git source in compact mode', () => {
      render(<SessionCard session={runningSession} compact={true} />);
      const repoLink = screen.getByTitle('Open repository');
      expect(repoLink).toBeInTheDocument();
      expect(repoLink).toHaveAttribute('href', 'kanuckvalley/printer-firmware');
      expect(repoLink).toHaveAttribute('target', '_blank');
      expect(repoLink).toHaveAttribute('rel', 'noopener noreferrer');
    });

    it('does not render repository link for non-git source in compact mode', () => {
      const localMountSession: VolundrSession = {
        ...runningSession,
        source: {
          type: 'local_mount',
          paths: [{ host_path: '/data', mount_path: '/workspace', read_only: false }],
        },
      };
      render(<SessionCard session={localMountSession} compact={true} />);
      expect(screen.queryByTitle('Open repository')).not.toBeInTheDocument();
    });

    it('renders tracker issue link in compact mode', () => {
      const sessionWithIssue: VolundrSession = {
        ...runningSession,
        trackerIssue: {
          id: 'issue-2',
          identifier: 'NIU-99',
          title: 'Thermal runaway fix',
          status: 'in_progress',
          url: 'https://linear.app/niuu/issue/NIU-99',
          priority: 1,
        },
      };
      render(<SessionCard session={sessionWithIssue} compact={true} />);
      const issueLink = screen.getByTitle('NIU-99');
      expect(issueLink).toBeInTheDocument();
      expect(issueLink).toHaveAttribute('href', 'https://linear.app/niuu/issue/NIU-99');
      expect(issueLink).toHaveAttribute('target', '_blank');
    });

    it('does not render tracker issue link when no issue is linked', () => {
      render(<SessionCard session={runningSession} compact={true} />);
      expect(screen.queryByTitle('NIU-99')).not.toBeInTheDocument();
    });

    it('stops propagation on repository link click', () => {
      const handleClick = vi.fn();
      render(<SessionCard session={runningSession} compact={true} onClick={handleClick} />);

      const repoLink = screen.getByTitle('Open repository');
      fireEvent.click(repoLink);

      expect(handleClick).not.toHaveBeenCalled();
    });

    it('stops propagation on tracker issue link click', () => {
      const sessionWithIssue: VolundrSession = {
        ...runningSession,
        trackerIssue: {
          id: 'issue-3',
          identifier: 'NIU-55',
          title: 'Board sensor',
          status: 'todo',
          url: 'https://linear.app/niuu/issue/NIU-55',
          priority: 3,
        },
      };
      const handleClick = vi.fn();
      render(<SessionCard session={sessionWithIssue} compact={true} onClick={handleClick} />);

      const issueLink = screen.getByTitle('NIU-55');
      fireEvent.click(issueLink);

      expect(handleClick).not.toHaveBeenCalled();
    });

    it('calls onClick when compact card is clicked', () => {
      const handleClick = vi.fn();
      render(<SessionCard session={runningSession} compact={true} onClick={handleClick} />);

      const card = screen.getByRole('button');
      fireEvent.click(card);

      expect(handleClick).toHaveBeenCalledTimes(1);
    });

    it('has button role when onClick is provided in compact mode', () => {
      render(<SessionCard session={runningSession} compact={true} onClick={() => {}} />);
      expect(screen.getByRole('button')).toBeInTheDocument();
    });

    it('does not have button role without onClick in compact mode', () => {
      render(<SessionCard session={runningSession} compact={true} />);
      expect(screen.queryByRole('button')).not.toBeInTheDocument();
    });

    it('renders status dot with activity state data attribute', () => {
      const activeSession: VolundrSession = {
        ...runningSession,
        activityState: 'idle',
      };
      const { container } = render(<SessionCard session={activeSession} compact={true} />);
      const dot = container.querySelector('[class*="statusDot"]');
      expect(dot).toHaveAttribute('data-activity', 'idle');
    });

    it('defaults activity to active for running sessions without activityState', () => {
      const { container } = render(<SessionCard session={runningSession} compact={true} />);
      const dot = container.querySelector('[class*="statusDot"]');
      expect(dot).toHaveAttribute('data-activity', 'active');
    });

    it('does not set activity attribute for non-running sessions without activityState', () => {
      const { container } = render(<SessionCard session={stoppedSession} compact={true} />);
      const dot = container.querySelector('[class*="statusDot"]');
      expect(dot).not.toHaveAttribute('data-activity');
    });

    it('applies custom className in compact mode', () => {
      const { container } = render(
        <SessionCard session={runningSession} compact={true} className="my-compact" />
      );
      expect(container.firstChild).toHaveClass('my-compact');
    });
  });
});
