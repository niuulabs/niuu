import { CheckCircle, XCircle, AlertTriangle, ArrowRight, HelpCircle } from 'lucide-react';
import type {
  MeshEvent,
  MeshOutcomeEvent,
  MeshDelegationEvent,
  MeshNotificationEvent,
} from '@/modules/shared/hooks/useSkuldChat';
import { resolveParticipantColor } from '@/modules/shared/utils/participantColor';
import styles from './MeshEventCard.module.css';

interface MeshEventCardProps {
  event: MeshEvent;
}

function getVerdictIcon(verdict: string | undefined) {
  switch (verdict) {
    case 'pass':
      return <CheckCircle className={styles.verdictIcon} data-verdict="pass" />;
    case 'fail':
      return <XCircle className={styles.verdictIcon} data-verdict="fail" />;
    case 'needs_changes':
    case 'needs_review':
      return <AlertTriangle className={styles.verdictIcon} data-verdict="changes" />;
    default:
      return null;
  }
}

function OutcomeCard({ event }: { event: MeshOutcomeEvent }) {
  const color = resolveParticipantColor(event.participant.color);

  return (
    <div
      className={styles.card}
      data-event-type="outcome"
      style={{ '--participant-color': color } as React.CSSProperties}
    >
      <div className={styles.header}>
        <span className={styles.dot} />
        <span className={styles.persona}>{event.persona}</span>
        <span className={styles.eventType}>{event.eventType}</span>
        {getVerdictIcon(event.verdict)}
      </div>

      {event.summary && <div className={styles.summary}>{event.summary}</div>}

      {event.verdict && (
        <div className={styles.verdict} data-verdict={event.verdict}>
          {event.verdict === 'pass' && 'Passed'}
          {event.verdict === 'fail' && 'Failed'}
          {(event.verdict === 'needs_changes' || event.verdict === 'needs_review') &&
            'Changes Requested'}
        </div>
      )}

      <div className={styles.timestamp}>
        {event.timestamp.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
      </div>
    </div>
  );
}

function DelegationCard({ event }: { event: MeshDelegationEvent }) {
  const color = resolveParticipantColor(event.participant.color);

  return (
    <div
      className={styles.card}
      data-event-type="delegation"
      style={{ '--participant-color': color } as React.CSSProperties}
    >
      <div className={styles.header}>
        <span className={styles.dot} />
        <span className={styles.persona}>{event.fromPersona}</span>
        <ArrowRight className={styles.arrowIcon} />
        <span className={styles.eventType}>{event.eventType}</span>
      </div>

      {event.preview && (
        <div className={styles.preview}>
          {event.preview.length > 200 ? `${event.preview.slice(0, 200)}...` : event.preview}
        </div>
      )}

      <div className={styles.timestamp}>
        {event.timestamp.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
      </div>
    </div>
  );
}

function NotificationCard({ event }: { event: MeshNotificationEvent }) {
  const color = resolveParticipantColor(event.participant.color);
  const isUrgent = event.urgency > 0.7;

  return (
    <div
      className={styles.card}
      data-event-type="notification"
      data-urgent={isUrgent || undefined}
      style={{ '--participant-color': color } as React.CSSProperties}
    >
      <div className={styles.header}>
        <HelpCircle className={styles.helpIcon} />
        <span className={styles.persona}>{event.persona}</span>
        <span className={styles.notificationType}>{event.notificationType}</span>
      </div>

      <div className={styles.summary}>{event.summary}</div>

      {event.reason && <div className={styles.reason}>Reason: {event.reason}</div>}

      {event.recommendation && (
        <div className={styles.recommendation}>
          <strong>Suggestion:</strong> {event.recommendation}
        </div>
      )}

      <div className={styles.timestamp}>
        {event.timestamp.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
      </div>
    </div>
  );
}

export function MeshEventCard({ event }: MeshEventCardProps) {
  switch (event.type) {
    case 'outcome':
      return <OutcomeCard event={event} />;
    case 'mesh_message':
      return <DelegationCard event={event} />;
    case 'notification':
      return <NotificationCard event={event} />;
    default:
      return null;
  }
}
