import type { CSSProperties } from 'react';
import {
  UserMessage,
  AssistantMessage,
  StreamingMessage,
  SystemMessage,
  hasNativeMessageParts,
} from '../ChatMessages';
import { OutcomeSpeakerProvider } from '../OutcomeCard/OutcomeSpeaker';
import type { ChatMessage } from '../../types';
import { coordinatorSpeaker, speakerFor, type RoomHost, type Speaker } from './speaker';
import './RoomMessage.css';

interface RoomMessageProps {
  message: ChatMessage;
  /** Open the sheet of the worker who spoke; the speaker's name is a button only with this. */
  onShowDetail?: (peerId: string) => void;
  onCopy?: (text: string) => void;
  onRegenerate?: (messageId: string) => void;
  onBookmark?: (messageId: string, bookmarked: boolean) => void;
  bookmarked?: boolean;
  /**
   * The room has workers: the session's own voice (the lead, or an assistant message
   * with no participant) speaks as the Coordinator.
   */
  workerRoom?: boolean;
  /** Show the speaker header. Consecutive messages from one speaker share the first one's. */
  showHeader?: boolean;
  /** The session whose room this is; only it speaks as the lead. */
  host?: RoomHost;
}

function messageSpeaker(
  message: ChatMessage,
  workerRoom: boolean,
  host: RoomHost | undefined,
): Speaker | null {
  const { participant } = message;
  if (participant) return workerRoom ? speakerFor(participant, host) : plainSpeaker(participant);
  if (workerRoom && message.role === 'assistant') return coordinatorSpeaker(host?.label);
  return null;
}

/** Outside a room of workers the participant keeps its persona label. */
function plainSpeaker(participant: NonNullable<ChatMessage['participant']>): Speaker {
  return { ...speakerFor(participant), name: participant.persona };
}

/** The speaker's initial on their colour, then their name; each message keeps its own time. */
function SpeakerHeader({
  speaker,
  peerId,
  onShowDetail,
}: {
  speaker: Speaker;
  peerId?: string;
  onShowDetail?: (peerId: string) => void;
}) {
  const name = <span className="niuu-chat-room-persona-name">{speaker.name}</span>;
  return (
    <div className="niuu-chat-room-label">
      <span
        className="niuu-chat-room-avatar"
        aria-hidden="true"
        data-initial={speaker.name.trim().charAt(0).toUpperCase()}
      />
      {onShowDetail && peerId ? (
        <button
          type="button"
          className="niuu-chat-room-persona-btn"
          onClick={() => onShowDetail(peerId)}
          aria-label={`Open ${speaker.name}`}
        >
          {name}
        </button>
      ) : (
        name
      )}
    </div>
  );
}

export function RoomMessage({
  message,
  onShowDetail,
  onCopy,
  onRegenerate,
  onBookmark,
  bookmarked,
  workerRoom = false,
  showHeader = true,
  host,
}: RoomMessageProps) {
  const speaker = messageSpeaker(message, workerRoom, host);
  const style = speaker ? ({ '--niuu-persona-color': speaker.color } as CSSProperties) : undefined;
  const body = (
    <>
      {message.metadata?.messageType === 'system' && <SystemMessage message={message} />}
      {message.metadata?.messageType !== 'system' && message.role === 'user' && (
        <UserMessage message={message} />
      )}
      {message.metadata?.messageType !== 'system' &&
        message.role === 'assistant' &&
        message.status === 'running' &&
        !hasNativeMessageParts(message.parts) && (
          <StreamingMessage content={message.content} parts={message.parts} />
        )}
      {message.metadata?.messageType !== 'system' &&
        message.role === 'assistant' &&
        (message.status !== 'running' || hasNativeMessageParts(message.parts)) && (
          <AssistantMessage
            message={message}
            onCopy={onCopy}
            onRegenerate={onRegenerate}
            onBookmark={onBookmark}
            bookmarked={bookmarked}
          />
        )}
    </>
  );

  return (
    <div
      className="niuu-chat-room-message"
      data-testid="room-message"
      data-speaker={speaker ? true : undefined}
      style={style}
    >
      {speaker && showHeader && (
        <SpeakerHeader
          speaker={speaker}
          peerId={message.participant?.peerId}
          onShowDetail={onShowDetail}
        />
      )}
      {speaker ? (
        <OutcomeSpeakerProvider value={{ name: speaker.name, color: speaker.color }}>
          {body}
        </OutcomeSpeakerProvider>
      ) : (
        body
      )}
    </div>
  );
}
