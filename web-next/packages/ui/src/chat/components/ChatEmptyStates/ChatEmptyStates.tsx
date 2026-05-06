import { Hammer } from 'lucide-react';
import './ChatEmptyStates.css';

const SUGGESTIONS = [
  'Review the code and suggest improvements',
  'Run the test suite and fix failures',
  'Explain the architecture of this module',
] as const;

interface SessionEmptyChatProps {
  sessionName: string;
  onSuggestionClick: (text: string) => void;
}

export function SessionEmptyChat({ sessionName, onSuggestionClick }: SessionEmptyChatProps) {
  return (
    <div className="niuu-chat-empty-wrapper" data-testid="session-empty-chat">
      <div className="niuu-chat-empty-inner">
        <div className="niuu-chat-empty-icon-box">
          <Hammer className="niuu-chat-empty-icon" />
        </div>
        <div className="niuu-chat-empty-title">{sessionName}</div>
        <div className="niuu-chat-empty-subtitle">
          Start working — ask a question or give an instruction.
        </div>
        <div className="niuu-chat-empty-suggestions">
          {SUGGESTIONS.map((text) => (
            <button
              key={text}
              type="button"
              className="niuu-chat-empty-suggestion"
              onClick={() => onSuggestionClick(text)}
            >
              {text}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
