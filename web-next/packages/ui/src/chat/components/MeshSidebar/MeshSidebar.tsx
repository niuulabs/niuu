import { useState } from 'react';
import { cn } from '../../../utils/cn';
import type { RoomParticipant } from '../../types';
import './MeshSidebar.css';

interface MeshSidebarProps {
  participants: ReadonlyMap<string, RoomParticipant>;
  selectedPeerId: string | null;
  onSelectPeer: (peerId: string) => void;
  collapsed?: boolean;
  onToggleCollapsed?: () => void;
}

interface PeerCardProps {
  participant: RoomParticipant;
  isSelected: boolean;
  onSelect: () => void;
}

interface GatewaySectionProps {
  gateway: string;
  latencyMs?: number;
  region?: string;
}

function isMeshVisibleParticipant(participant: RoomParticipant): boolean {
  return participant.participantType === 'ravn' || participant.participantType === 'skuld';
}

function formatParticipantLabel(participant: RoomParticipant): string {
  const role = participant.participantType === 'skuld' ? 'observer' : participant.persona;
  const baseName = participant.displayName ?? participant.persona ?? participant.peerId;
  if (!baseName) return role || participant.peerId;
  if (participant.participantType === 'skuld' || participant.displayName) {
    return role ? `${baseName} (${role})` : baseName;
  }
  return baseName;
}

function latencyClass(ms: number | undefined): string {
  if (ms === undefined) return '';
  if (ms < 100) return 'niuu-chat-peer-gw-latency--ok';
  if (ms < 500) return 'niuu-chat-peer-gw-latency--warn';
  return 'niuu-chat-peer-gw-latency--err';
}

function parseGatewayUri(uri: string): { proto: string; vendor: string; model?: string } | null {
  const m = uri.match(/^([a-z0-9+.-]+):\/\/([^/]+)(?:\/(.+))?$/i);
  if (!m) return null;
  const [, proto, vendor, model] = m;
  return { proto: proto!, vendor: vendor!, model };
}

function GatewaySection({ gateway, latencyMs, region }: GatewaySectionProps) {
  const parsed = parseGatewayUri(gateway);

  return (
    <div className="niuu-chat-peer-gateway" data-testid="peer-gateway-section">
      <span className="niuu-chat-peer-meta-label">Gateway</span>
      <div className="niuu-chat-peer-gw-breadcrumb">
        {parsed ? (
          <>
            <span className="niuu-chat-peer-gw-seg niuu-chat-peer-gw-proto">{parsed.proto}</span>
            <span className="niuu-chat-peer-gw-sep">›</span>
            <span className="niuu-chat-peer-gw-seg niuu-chat-peer-gw-vendor">{parsed.vendor}</span>
            {parsed.model && (
              <>
                <span className="niuu-chat-peer-gw-sep">›</span>
                <span className="niuu-chat-peer-gw-seg niuu-chat-peer-gw-model">
                  {parsed.model}
                </span>
              </>
            )}
          </>
        ) : (
          <span className="niuu-chat-peer-gw-seg">{gateway}</span>
        )}
      </div>
      {(region || latencyMs !== undefined) && (
        <div className="niuu-chat-peer-gw-meta">
          {region && (
            <span className="niuu-chat-peer-gw-region" data-testid="peer-gateway-region">
              {region}
            </span>
          )}
          {latencyMs !== undefined && (
            <span
              className={cn('niuu-chat-peer-gw-latency', latencyClass(latencyMs))}
              data-testid="peer-gateway-latency"
            >
              {latencyMs}ms
            </span>
          )}
        </div>
      )}
    </div>
  );
}

function PeerCard({ participant, isSelected, onSelect }: PeerCardProps) {
  const [expanded, setExpanded] = useState(false);

  const hasMetadata =
    (participant.subscribesTo && participant.subscribesTo.length > 0) ||
    (participant.emits && participant.emits.length > 0) ||
    (participant.tools && participant.tools.length > 0) ||
    !!participant.gateway;

  return (
    <div
      className={cn('niuu-chat-peer-card', isSelected && 'niuu-chat-peer-card--selected')}
      data-participant-color={participant.color}
      onClick={onSelect}
      data-testid={`peer-card-${participant.peerId}`}
    >
      <div className="niuu-chat-peer-header">
        <span className="niuu-chat-peer-status-dot" data-status={participant.status} />
        <span className="niuu-chat-peer-name">{formatParticipantLabel(participant)}</span>
        <span className="niuu-chat-peer-status-label">{participant.status}</span>
      </div>

      {hasMetadata && (
        <button
          type="button"
          className="niuu-chat-peer-expand-toggle"
          onClick={(e) => {
            e.stopPropagation();
            setExpanded((v) => !v);
          }}
        >
          {expanded ? 'hide details' : 'show details'}
        </button>
      )}

      {expanded && hasMetadata && (
        <div className="niuu-chat-peer-meta">
          {participant.subscribesTo && participant.subscribesTo.length > 0 && (
            <>
              <span className="niuu-chat-peer-meta-label">Subscribes</span>
              <div className="niuu-chat-peer-meta-tags">
                {participant.subscribesTo.map((evt) => (
                  <span key={evt} className="niuu-chat-peer-meta-tag" data-variant="subscribe">
                    {evt}
                  </span>
                ))}
              </div>
            </>
          )}
          {participant.emits && participant.emits.length > 0 && (
            <>
              <span className="niuu-chat-peer-meta-label">Emits</span>
              <div className="niuu-chat-peer-meta-tags">
                {participant.emits.map((evt) => (
                  <span key={evt} className="niuu-chat-peer-meta-tag" data-variant="emit">
                    {evt}
                  </span>
                ))}
              </div>
            </>
          )}
          {participant.tools && participant.tools.length > 0 && (
            <>
              <span className="niuu-chat-peer-meta-label">Tools</span>
              <div className="niuu-chat-peer-meta-tags">
                {participant.tools.map((tool) => (
                  <span key={tool} className="niuu-chat-peer-meta-tag" data-variant="tool">
                    {tool}
                  </span>
                ))}
              </div>
            </>
          )}
          {participant.gateway && (
            <GatewaySection
              gateway={participant.gateway}
              latencyMs={participant.gatewayLatencyMs}
              region={participant.gatewayRegion}
            />
          )}
        </div>
      )}
    </div>
  );
}

export function MeshSidebar({
  participants,
  selectedPeerId,
  onSelectPeer,
  collapsed = false,
  onToggleCollapsed,
}: MeshSidebarProps) {
  const ravnPeers = Array.from(participants.values()).filter((p) => p.participantType === 'ravn');
  const peers = Array.from(participants.values()).filter(isMeshVisibleParticipant);

  if (ravnPeers.length === 0 || peers.length === 0) return null;

  if (collapsed) {
    return (
      <aside
        className="niuu-chat-mesh-sidebar niuu-chat-mesh-sidebar--collapsed"
        data-testid="mesh-sidebar"
      >
        <button
          type="button"
          className="niuu-chat-mesh-sidebar-collapse-toggle"
          onClick={onToggleCollapsed}
          aria-label="Expand mesh peers sidebar"
          title="Expand mesh peers sidebar"
        >
          ›
        </button>
        <div className="niuu-chat-mesh-sidebar-collapsed-list">
          {peers.map((peer) => (
            <button
              key={peer.peerId}
              type="button"
              className={cn(
                'niuu-chat-mesh-sidebar-collapsed-peer',
                selectedPeerId === peer.peerId && 'niuu-chat-mesh-sidebar-collapsed-peer--selected',
              )}
              onClick={() => onSelectPeer(peer.peerId)}
              title={formatParticipantLabel(peer)}
              aria-label={`Focus ${formatParticipantLabel(peer)}`}
            >
              <span className="niuu-chat-peer-status-dot" data-status={peer.status} />
            </button>
          ))}
        </div>
      </aside>
    );
  }

  return (
    <aside className="niuu-chat-mesh-sidebar" data-testid="mesh-sidebar">
      <div className="niuu-chat-mesh-sidebar-header">
        <div className="niuu-chat-mesh-sidebar-header-meta">
          <span className="niuu-chat-mesh-sidebar-title">Mesh Peers</span>
          <span className="niuu-chat-mesh-sidebar-count">{peers.length}</span>
        </div>
        <button
          type="button"
          className="niuu-chat-mesh-sidebar-collapse-toggle"
          onClick={onToggleCollapsed}
          aria-label="Collapse mesh peers sidebar"
          title="Collapse mesh peers sidebar"
        >
          ‹
        </button>
      </div>
      <div className="niuu-chat-mesh-sidebar-peer-list">
        {peers.map((peer) => (
          <PeerCard
            key={peer.peerId}
            participant={peer}
            isSelected={selectedPeerId === peer.peerId}
            onSelect={() => onSelectPeer(peer.peerId)}
          />
        ))}
      </div>
    </aside>
  );
}
