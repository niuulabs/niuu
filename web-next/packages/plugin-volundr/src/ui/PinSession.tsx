import { Pin } from 'lucide-react';
import { usePinnedSessions } from './usePinnedSessions';
import './PinSession.css';

export function PinSession({ sessionId, name }: { sessionId: string; name: string }) {
  const { pinned, toggle } = usePinnedSessions();
  const active = pinned.has(sessionId);
  return (
    <button
      type="button"
      className="forge-session-pin"
      aria-label={`${active ? 'Unpin' : 'Pin'} ${name}`}
      aria-pressed={active}
      title={active ? 'Unpin session' : 'Pin session to the top'}
      onClick={() => toggle(sessionId)}
    >
      <Pin size={15} />
    </button>
  );
}
