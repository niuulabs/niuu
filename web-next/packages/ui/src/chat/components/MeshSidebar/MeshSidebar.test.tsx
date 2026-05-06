import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { MeshSidebar } from './MeshSidebar';
import type { RoomParticipant } from '../../types';

const participants: ReadonlyMap<string, RoomParticipant> = new Map([
  [
    'peer-1',
    { peerId: 'peer-1', persona: 'Ada', participantType: 'ravn', status: 'idle', color: '#38bdf8' },
  ],
  [
    'peer-2',
    {
      peerId: 'peer-2',
      persona: 'Björk',
      participantType: 'ravn',
      status: 'thinking',
      color: '#a78bfa',
    },
  ],
  ['peer-3', { peerId: 'peer-3', persona: 'Skuld', participantType: 'skuld', status: 'idle' }],
]);

describe('MeshSidebar', () => {
  it('renders null when no ravn participants', () => {
    const noRavn: ReadonlyMap<string, RoomParticipant> = new Map([
      ['peer-3', { peerId: 'peer-3', persona: 'Skuld', participantType: 'skuld' }],
    ]);
    const { container } = render(
      <MeshSidebar participants={noRavn} selectedPeerId={null} onSelectPeer={vi.fn()} />,
    );
    expect(container.firstChild).toBeNull();
  });

  it('renders ravn peers plus the skuld observer when a flock exists', () => {
    render(
      <MeshSidebar participants={participants} selectedPeerId={null} onSelectPeer={vi.fn()} />,
    );
    expect(screen.getByTestId('peer-card-peer-1')).toBeInTheDocument();
    expect(screen.getByTestId('peer-card-peer-2')).toBeInTheDocument();
    expect(screen.getByTestId('peer-card-peer-3')).toBeInTheDocument();
    expect(screen.getByText('Skuld (observer)')).toBeInTheDocument();
  });

  it('shows peer count', () => {
    render(
      <MeshSidebar participants={participants} selectedPeerId={null} onSelectPeer={vi.fn()} />,
    );
    expect(screen.getByText('3')).toBeInTheDocument();
  });

  it('calls onSelectPeer when peer clicked', () => {
    const onSelectPeer = vi.fn();
    render(
      <MeshSidebar participants={participants} selectedPeerId={null} onSelectPeer={onSelectPeer} />,
    );
    fireEvent.click(screen.getByTestId('peer-card-peer-1'));
    expect(onSelectPeer).toHaveBeenCalledWith('peer-1');
  });

  it('marks selected peer', () => {
    render(
      <MeshSidebar participants={participants} selectedPeerId="peer-1" onSelectPeer={vi.fn()} />,
    );
    expect(screen.getByTestId('peer-card-peer-1').className).toContain('selected');
  });

  it('renders a collapsed rail and expands from the header control', () => {
    const onToggleCollapsed = vi.fn();
    render(
      <MeshSidebar
        participants={participants}
        selectedPeerId="peer-1"
        onSelectPeer={vi.fn()}
        collapsed
        onToggleCollapsed={onToggleCollapsed}
      />,
    );

    expect(screen.getByRole('button', { name: /expand mesh peers sidebar/i })).toBeInTheDocument();
    expect(screen.queryByText('Mesh Peers')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /expand mesh peers sidebar/i }));
    expect(onToggleCollapsed).toHaveBeenCalledTimes(1);
  });

  it('shows expand toggle when participant has metadata', () => {
    const withMeta: ReadonlyMap<string, RoomParticipant> = new Map([
      [
        'peer-1',
        {
          peerId: 'peer-1',
          persona: 'Ada',
          participantType: 'ravn',
          tools: ['bash', 'read_file'],
          subscribesTo: ['task_done'],
        },
      ],
    ]);
    render(<MeshSidebar participants={withMeta} selectedPeerId={null} onSelectPeer={vi.fn()} />);
    expect(screen.getByText('show details')).toBeInTheDocument();
  });

  it('expands metadata when toggle clicked', () => {
    const withMeta: ReadonlyMap<string, RoomParticipant> = new Map([
      [
        'peer-1',
        {
          peerId: 'peer-1',
          persona: 'Ada',
          participantType: 'ravn',
          tools: ['bash'],
        },
      ],
    ]);
    render(<MeshSidebar participants={withMeta} selectedPeerId={null} onSelectPeer={vi.fn()} />);
    fireEvent.click(screen.getByText('show details'));
    expect(screen.getByText('bash')).toBeInTheDocument();
    expect(screen.getByText('hide details')).toBeInTheDocument();
  });

  describe('gateway section', () => {
    it('shows expand toggle when participant has a gateway', () => {
      const withGateway: ReadonlyMap<string, RoomParticipant> = new Map([
        [
          'peer-1',
          {
            peerId: 'peer-1',
            persona: 'Ada',
            participantType: 'ravn',
            gateway: 'bifrost://anthropic/claude-sonnet',
          },
        ],
      ]);
      render(
        <MeshSidebar participants={withGateway} selectedPeerId={null} onSelectPeer={vi.fn()} />,
      );
      expect(screen.getByText('show details')).toBeInTheDocument();
    });

    it('renders gateway section when expanded', () => {
      const withGateway: ReadonlyMap<string, RoomParticipant> = new Map([
        [
          'peer-1',
          {
            peerId: 'peer-1',
            persona: 'Ada',
            participantType: 'ravn',
            gateway: 'bifrost://anthropic/claude-sonnet',
            gatewayLatencyMs: 84,
            gatewayRegion: 'us-east-1',
          },
        ],
      ]);
      render(
        <MeshSidebar participants={withGateway} selectedPeerId={null} onSelectPeer={vi.fn()} />,
      );
      fireEvent.click(screen.getByText('show details'));
      expect(screen.getByTestId('peer-gateway-section')).toBeInTheDocument();
    });

    it('renders gateway breadcrumb segments', () => {
      const withGateway: ReadonlyMap<string, RoomParticipant> = new Map([
        [
          'peer-1',
          {
            peerId: 'peer-1',
            persona: 'Ada',
            participantType: 'ravn',
            gateway: 'bifrost://anthropic/claude-sonnet',
          },
        ],
      ]);
      render(
        <MeshSidebar participants={withGateway} selectedPeerId={null} onSelectPeer={vi.fn()} />,
      );
      fireEvent.click(screen.getByText('show details'));
      expect(screen.getByText('bifrost')).toBeInTheDocument();
      expect(screen.getByText('anthropic')).toBeInTheDocument();
      expect(screen.getByText('claude-sonnet')).toBeInTheDocument();
    });

    it('shows latency in green for <100ms', () => {
      const withGateway: ReadonlyMap<string, RoomParticipant> = new Map([
        [
          'peer-1',
          {
            peerId: 'peer-1',
            persona: 'Ada',
            participantType: 'ravn',
            gateway: 'bifrost://anthropic/claude-sonnet',
            gatewayLatencyMs: 84,
          },
        ],
      ]);
      render(
        <MeshSidebar participants={withGateway} selectedPeerId={null} onSelectPeer={vi.fn()} />,
      );
      fireEvent.click(screen.getByText('show details'));
      const latency = screen.getByTestId('peer-gateway-latency');
      expect(latency).toHaveTextContent('84ms');
      expect(latency.className).toContain('ok');
    });

    it('shows latency in amber for 100-499ms', () => {
      const withGateway: ReadonlyMap<string, RoomParticipant> = new Map([
        [
          'peer-1',
          {
            peerId: 'peer-1',
            persona: 'Ada',
            participantType: 'ravn',
            gateway: 'bifrost://anthropic/claude-sonnet',
            gatewayLatencyMs: 312,
          },
        ],
      ]);
      render(
        <MeshSidebar participants={withGateway} selectedPeerId={null} onSelectPeer={vi.fn()} />,
      );
      fireEvent.click(screen.getByText('show details'));
      const latency = screen.getByTestId('peer-gateway-latency');
      expect(latency).toHaveTextContent('312ms');
      expect(latency.className).toContain('warn');
    });

    it('shows latency in red for >=500ms', () => {
      const withGateway: ReadonlyMap<string, RoomParticipant> = new Map([
        [
          'peer-1',
          {
            peerId: 'peer-1',
            persona: 'Ada',
            participantType: 'ravn',
            gateway: 'bifrost://anthropic/claude-sonnet',
            gatewayLatencyMs: 600,
          },
        ],
      ]);
      render(
        <MeshSidebar participants={withGateway} selectedPeerId={null} onSelectPeer={vi.fn()} />,
      );
      fireEvent.click(screen.getByText('show details'));
      const latency = screen.getByTestId('peer-gateway-latency');
      expect(latency).toHaveTextContent('600ms');
      expect(latency.className).toContain('err');
    });

    it('shows region when provided', () => {
      const withGateway: ReadonlyMap<string, RoomParticipant> = new Map([
        [
          'peer-1',
          {
            peerId: 'peer-1',
            persona: 'Ada',
            participantType: 'ravn',
            gateway: 'bifrost://anthropic/claude-sonnet',
            gatewayRegion: 'eu-west-1',
          },
        ],
      ]);
      render(
        <MeshSidebar participants={withGateway} selectedPeerId={null} onSelectPeer={vi.fn()} />,
      );
      fireEvent.click(screen.getByText('show details'));
      expect(screen.getByTestId('peer-gateway-region')).toHaveTextContent('eu-west-1');
    });
  });
});
