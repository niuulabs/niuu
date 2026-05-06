import { CheckCircle, XCircle, AlertTriangle, ArrowRight, HelpCircle } from 'lucide-react';
import { resolveParticipantColor } from '../../utils/participantColor';
import type {
  MeshEvent,
  MeshOutcomeEvent,
  MeshDelegationEvent,
  MeshNotificationEvent,
} from '../../types';
import './MeshEventCard.css';

function getVerdictIcon(verdict: string | undefined) {
  switch (verdict) {
    case 'pass':
      return <CheckCircle className="niuu-chat-mesh-verdict-icon" data-verdict="pass" />;
    case 'fail':
      return <XCircle className="niuu-chat-mesh-verdict-icon" data-verdict="fail" />;
    case 'needs_changes':
    case 'needs_review':
      return <AlertTriangle className="niuu-chat-mesh-verdict-icon" data-verdict="changes" />;
    default:
      return null;
  }
}

function OutcomeCard({
  event,
  onShowDetails,
}: {
  event: MeshOutcomeEvent;
  onShowDetails?: (event: MeshOutcomeEvent) => void;
}) {
  const color = resolveParticipantColor(event.participantId, event.participant.color);

  return (
    <div
      className="niuu-chat-mesh-card"
      data-event-type="outcome"
      style={{ '--niuu-participant-color': color } as React.CSSProperties}
    >
      <div className="niuu-chat-mesh-card-header">
        <span className="niuu-chat-mesh-dot" />
        <span className="niuu-chat-mesh-persona">{event.persona}</span>
        <span className="niuu-chat-mesh-event-type">{event.eventType}</span>
        {getVerdictIcon(event.verdict)}
      </div>
      <div className="niuu-chat-mesh-card-body">
        {event.summary && <div className="niuu-chat-mesh-summary">{event.summary}</div>}
        {event.verdict && (
          <div className="niuu-chat-mesh-verdict" data-verdict={event.verdict}>
            {event.verdict === 'pass' && 'Passed'}
            {event.verdict === 'fail' && 'Failed'}
            {(event.verdict === 'needs_changes' || event.verdict === 'needs_review') &&
              'Changes Requested'}
          </div>
        )}
        <div className="niuu-chat-mesh-card-footer">
          <button
            type="button"
            className="niuu-chat-mesh-detail-link"
            onClick={(clickEvent) => {
              clickEvent.stopPropagation();
              onShowDetails?.(event);
            }}
          >
            Show details
          </button>
          <div className="niuu-chat-mesh-timestamp">
            {event.timestamp.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
          </div>
        </div>
      </div>
    </div>
  );
}

function DelegationCard({ event }: { event: MeshDelegationEvent }) {
  const color = resolveParticipantColor(event.participantId, event.participant.color);

  return (
    <div
      className="niuu-chat-mesh-card"
      data-event-type="delegation"
      style={{ '--niuu-participant-color': color } as React.CSSProperties}
    >
      <div className="niuu-chat-mesh-card-header">
        <span className="niuu-chat-mesh-dot" />
        <span className="niuu-chat-mesh-persona">{event.fromPersona}</span>
        <ArrowRight className="niuu-chat-mesh-arrow-icon" />
        <span className="niuu-chat-mesh-event-type">{event.eventType}</span>
      </div>
      <div className="niuu-chat-mesh-card-body">
        {event.preview && (
          <div className="niuu-chat-mesh-preview">
            {event.preview.length > 200 ? `${event.preview.slice(0, 200)}...` : event.preview}
          </div>
        )}
        <div className="niuu-chat-mesh-card-footer">
          <div className="niuu-chat-mesh-timestamp">
            {event.timestamp.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
          </div>
        </div>
      </div>
    </div>
  );
}

function NotificationCard({ event }: { event: MeshNotificationEvent }) {
  const color = resolveParticipantColor(event.participantId, event.participant.color);
  const isUrgent = event.urgency > 0.7;

  return (
    <div
      className="niuu-chat-mesh-card"
      data-event-type="notification"
      data-urgent={isUrgent || undefined}
      style={{ '--niuu-participant-color': color } as React.CSSProperties}
    >
      <div className="niuu-chat-mesh-card-header">
        <HelpCircle className="niuu-chat-mesh-help-icon" />
        <span className="niuu-chat-mesh-persona">{event.persona}</span>
        <span className="niuu-chat-mesh-event-type">{event.notificationType}</span>
      </div>
      <div className="niuu-chat-mesh-card-body">
        <div className="niuu-chat-mesh-summary">{event.summary}</div>
        {event.reason && <div className="niuu-chat-mesh-reason">Reason: {event.reason}</div>}
        {event.recommendation && (
          <div className="niuu-chat-mesh-recommendation">
            <strong>Suggestion:</strong> {event.recommendation}
          </div>
        )}
        <div className="niuu-chat-mesh-card-footer">
          <div className="niuu-chat-mesh-timestamp">
            {event.timestamp.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
          </div>
        </div>
      </div>
    </div>
  );
}

interface MeshEventCardProps {
  event: MeshEvent;
  onShowDetails?: (event: MeshOutcomeEvent) => void;
}

export function MeshEventCard({ event, onShowDetails }: MeshEventCardProps) {
  switch (event.type) {
    case 'outcome':
      return <OutcomeCard event={event} onShowDetails={onShowDetails} />;
    case 'mesh_message':
      return <DelegationCard event={event} />;
    case 'notification':
      return <NotificationCard event={event} />;
    default:
      return null;
  }
}
