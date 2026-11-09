import { Eye, ListCollapse, SlidersHorizontal, Workflow } from 'lucide-react';
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
  /** The host's tool-call visibility, offered in the same menu rather than as its own button. */
  toolCalls?: { shown: boolean; onToggle: () => void };
  /** The host's workflow panel (a run's coordinator), shown or hidden from the same menu. */
  workflowPanel?: { shown: boolean; onToggle: () => void };
}

/**
 * One Display options menu, as Lexi has: whether each turn's work is folded, the host's
 * tool-call visibility, and avatars, message actions, timestamps and copy. The settings are
 * shared, so a host can place the menu in its own toolbar and every open conversation follows.
 */
export function ChatDisplayControls({
  className,
  toolCalls,
  workflowPanel,
}: ChatDisplayControlsProps) {
  const chatPrefs = useCompactUxChatPrefs();
  const conversationView = useConversationView();
  return (
    <div className={cn('niuu-chat-display-menu', className)}>
      <details className="niuu-chat-preferences">
        <summary title="Display options" aria-label="Display options">
          <SlidersHorizontal className="niuu-chat-control-icon" aria-hidden="true" />
          <span className="niuu-chat-preferences-label">Display</span>
        </summary>
        <div className="niuu-chat-preferences-panel">
          <button
            type="button"
            className="niuu-chat-preferences-toggle"
            onClick={() =>
              setConversationView(conversationView === 'compact' ? 'expanded' : 'compact')
            }
            title={conversationView === 'expanded' ? 'Compact view' : 'Expanded view'}
            aria-pressed={conversationView === 'expanded'}
            data-testid="conversation-view-toggle"
          >
            <ListCollapse className="niuu-chat-control-icon" aria-hidden="true" />
            Expand each turn&apos;s work
          </button>
          {toolCalls && (
            <button
              type="button"
              className="niuu-chat-preferences-toggle"
              onClick={toolCalls.onToggle}
              aria-pressed={toolCalls.shown}
              aria-label={
                toolCalls.shown ? 'Hide tool calls and results' : 'Show tool calls and results'
              }
              data-testid="tool-calls-toggle"
            >
              <Eye className="niuu-chat-control-icon" aria-hidden="true" />
              Tool calls and results
            </button>
          )}
          {workflowPanel && (
            <button
              type="button"
              className="niuu-chat-preferences-toggle"
              onClick={workflowPanel.onToggle}
              aria-pressed={workflowPanel.shown}
              aria-label={workflowPanel.shown ? 'Hide the workflow' : 'Show the workflow'}
              data-testid="workflow-panel-toggle"
            >
              <Workflow className="niuu-chat-control-icon" aria-hidden="true" />
              Workflow
            </button>
          )}
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
          <label>
            Tool calls{' '}
            <select
              value={chatPrefs.toolRuns}
              onChange={(event) =>
                setCompactUxChatPref('toolRuns', event.target.value as 'rows' | 'cards')
              }
            >
              <option value="rows">One row per burst</option>
              <option value="cards">Every call</option>
            </select>
          </label>
        </div>
      </details>
    </div>
  );
}
