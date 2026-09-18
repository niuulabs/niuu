import { ListCollapse } from 'lucide-react';
import { cn } from '../../../utils/cn';
import {
  setCompactUxChatPref,
  setConversationView,
  useCompactUxChatPrefs,
  useConversationView,
} from '../../compactUxPrefs';
import './ChatDisplayControls.css';

export interface ChatDisplayControlsProps {
  className?: string;
}

/**
 * The conversation display preferences: expanded vs compact (folded) turns, and
 * the Display menu for avatars, message actions, timestamps and copy. They are
 * shared, so a host can place them in its own toolbar and every open
 * conversation follows.
 */
export function ChatDisplayControls({ className }: ChatDisplayControlsProps) {
  const chatPrefs = useCompactUxChatPrefs();
  const conversationView = useConversationView();
  return (
    <div className={cn('niuu-chat-display-menu', className)}>
      <button
        type="button"
        className={cn(
          'niuu-chat-control-btn',
          conversationView === 'expanded' && 'niuu-chat-control-btn--active',
        )}
        onClick={() => setConversationView(conversationView === 'compact' ? 'expanded' : 'compact')}
        title={conversationView === 'expanded' ? 'Compact view' : 'Expanded view'}
        aria-pressed={conversationView === 'expanded'}
        data-testid="conversation-view-toggle"
      >
        <ListCollapse className="niuu-chat-control-icon" />
      </button>
      <details className="niuu-chat-preferences">
        <summary>Display</summary>
        <div className="niuu-chat-preferences-panel">
          <label>
            <input
              type="checkbox"
              checked={chatPrefs.showAgentAvatar}
              onChange={(event) => setCompactUxChatPref('showAgentAvatar', event.target.checked)}
            />{' '}
            Agent avatars
          </label>
          <label>
            <input
              type="checkbox"
              checked={chatPrefs.showMessageActions}
              onChange={(event) => setCompactUxChatPref('showMessageActions', event.target.checked)}
            />{' '}
            Message actions
          </label>
          <label>
            Timestamps{' '}
            <select
              value={chatPrefs.timestamp}
              onChange={(event) =>
                setCompactUxChatPref(
                  'timestamp',
                  event.target.value as 'hover' | 'always' | 'never',
                )
              }
            >
              <option value="hover">On hover</option>
              <option value="always">Always</option>
              <option value="never">Never</option>
            </select>
          </label>
          <label>
            Copy button{' '}
            <select
              value={chatPrefs.copyMode}
              onChange={(event) =>
                setCompactUxChatPref('copyMode', event.target.value as 'hover' | 'inline')
              }
            >
              <option value="hover">On hover</option>
              <option value="inline">Always</option>
            </select>
          </label>
        </div>
      </details>
    </div>
  );
}
