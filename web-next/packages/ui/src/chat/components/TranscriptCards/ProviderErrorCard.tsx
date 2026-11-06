import { AlertCircle } from 'lucide-react';
import './TranscriptCards.css';

export interface ProviderErrorCardProps {
  /** The error as the broker or provider worded it, code and request id included. */
  message: string;
}

/** The turn failed on the model side; the message is shown exactly as received. */
export function ProviderErrorCard({ message }: ProviderErrorCardProps) {
  return (
    <div className="niuu-chat-error-card" role="alert" data-testid="provider-error-card">
      <div className="niuu-chat-card-head">
        <AlertCircle className="niuu-chat-card-icon" aria-hidden="true" />
        <b className="niuu-chat-card-title">The model couldn&apos;t run this turn</b>
      </div>
      <p className="niuu-chat-error-card-message">{message}</p>
    </div>
  );
}
