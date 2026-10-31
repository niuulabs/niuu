import { useState } from 'react';
import { Mail } from 'lucide-react';
import {
  openHuddleForEnvironment,
  type HuddleSummary,
  type ValkyrieDashboard,
  type ValkyrieResident,
} from '../domain';
import {
  useJoinHuddle,
  useLeaveHuddle,
  useSendHuddleMessage,
} from '../application/useValkyrieDashboard';
import { errorMessage } from './copy';

export const OPERATOR_PARTICIPANT_ID = 'human:operator';

const MUTED = 'niuu:text-text-muted';
const BUTTON =
  'niuu:inline-flex niuu:items-center niuu:gap-1.5 niuu:rounded-md niuu:border niuu:border-solid niuu:border-border niuu:bg-bg-primary niuu:px-3 niuu:py-1.5 niuu:text-xs niuu:text-text-secondary niuu:hover:border-brand/70 niuu:disabled:cursor-not-allowed niuu:disabled:opacity-50';

function Composer({
  valkyrie,
  huddle,
  onClose,
}: {
  valkyrie: ValkyrieResident;
  huddle: HuddleSummary;
  onClose: () => void;
}) {
  const sendMessage = useSendHuddleMessage();
  const leaveHuddle = useLeaveHuddle();
  const [body, setBody] = useState('');

  return (
    <form
      data-testid="valkyrie-dm-composer"
      className="niuu:mt-3 niuu:flex niuu:flex-col niuu:gap-2"
      onSubmit={(event) => {
        event.preventDefault();
        const trimmed = body.trim();
        if (!trimmed) return;
        sendMessage.mutate(
          {
            huddleId: huddle.id,
            body: trimmed,
            directedTo: [valkyrie.id],
            authorId: OPERATOR_PARTICIPANT_ID,
          },
          { onSuccess: () => setBody('') },
        );
      }}
    >
      <textarea
        aria-label={`Direct message to ${valkyrie.name}`}
        value={body}
        rows={2}
        placeholder={`Delivered to ${valkyrie.name} via ${huddle.title}`}
        onChange={(event) => setBody(event.target.value)}
        className="niuu:w-full niuu:rounded-md niuu:border niuu:border-solid niuu:border-border niuu:bg-bg-secondary niuu:p-2 niuu:font-sans niuu:text-sm niuu:text-text-primary"
      />
      <div className="niuu:flex niuu:items-center niuu:gap-2">
        <button
          type="submit"
          data-testid="valkyrie-dm-send"
          disabled={sendMessage.isPending || !body.trim()}
          className={BUTTON}
        >
          {sendMessage.isPending ? 'Sending…' : 'Send'}
        </button>
        <button
          type="button"
          data-testid="valkyrie-leave-huddle"
          disabled={leaveHuddle.isPending}
          onClick={() => leaveHuddle.mutate(huddle.id, { onSuccess: onClose })}
          className={`niuu:text-xs ${MUTED}`}
        >
          Leave huddle
        </button>
        {sendMessage.isSuccess ? (
          <span data-testid="valkyrie-dm-sent" className="niuu:text-xs niuu:text-state-ok">
            Delivered to {valkyrie.name}.
          </span>
        ) : null}
        {sendMessage.isError ? (
          <span role="alert" className="niuu:text-xs niuu:text-critical">
            {errorMessage(sendMessage.error, 'Sending failed')}
          </span>
        ) : null}
        {leaveHuddle.isError ? (
          <span role="alert" className="niuu:text-xs niuu:text-critical">
            {errorMessage(leaveHuddle.error, 'Leaving the huddle failed')}
          </span>
        ) : null}
      </div>
    </form>
  );
}

/** Who this resident is and what it is doing, in one line, with one way to talk to it. */
export function ResidentHeader({
  dashboard,
  valkyrie,
  autonomy,
}: {
  dashboard: ValkyrieDashboard;
  valkyrie: ValkyrieResident;
  autonomy: string;
}) {
  const environment = dashboard.environments.find((entry) => entry.id === valkyrie.environmentId);
  const huddle = openHuddleForEnvironment(dashboard.huddles, valkyrie.environmentId);
  const joinHuddle = useJoinHuddle();
  const [composing, setComposing] = useState(false);
  const health = environment?.health ?? 'healthy';
  const state = valkyrie.wakefulness.charAt(0).toUpperCase() + valkyrie.wakefulness.slice(1);

  return (
    <header data-testid="valkyrie-console-hero">
      <div className="niuu:flex niuu:items-start niuu:justify-between niuu:gap-3">
        <div className="niuu:min-w-0">
          <h1 className="niuu:truncate niuu:font-mono niuu:text-xl niuu:font-semibold niuu:text-text-primary">
            {valkyrie.name}
          </h1>
          <p className="niuu:mt-0.5 niuu:text-sm niuu:text-text-secondary">
            Keeps {environment?.name ?? valkyrie.environmentId}. {state}, {health},{' '}
            {autonomy.toLowerCase()}.
          </p>
        </div>
        <button
          type="button"
          data-testid="valkyrie-direct-message"
          title={huddle ? `Message ${valkyrie.name}` : 'No open huddle for this environment'}
          disabled={!huddle || joinHuddle.isPending}
          aria-expanded={composing}
          onClick={() => {
            if (!huddle) return;
            if (huddle.joined) {
              setComposing((value) => !value);
              return;
            }
            // The backend requires a joined participant before it accepts messages.
            joinHuddle.mutate(
              {
                huddleId: huddle.id,
                participantId: OPERATOR_PARTICIPANT_ID,
                displayName: 'Operator',
                action: 'observe',
              },
              { onSuccess: () => setComposing(true) },
            );
          }}
          className={BUTTON}
        >
          <Mail size={13} aria-hidden="true" />
          Message
        </button>
      </div>
      {joinHuddle.isError ? (
        <p
          role="alert"
          data-testid="valkyrie-huddle-error"
          className="niuu:mt-2 niuu:text-xs niuu:text-critical"
        >
          {errorMessage(joinHuddle.error, 'Huddle action failed')}
        </p>
      ) : null}
      {composing && huddle?.joined ? (
        <Composer valkyrie={valkyrie} huddle={huddle} onClose={() => setComposing(false)} />
      ) : null}
    </header>
  );
}
