import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import {
  UserMessage,
  AssistantMessage,
  StreamingMessage,
  SystemMessage,
  messageTime,
} from './ChatMessages';
import type { ChatMessage } from '../../types';
import { ChatAuthorshipProvider } from '../../authorship';

// The per-message action bar (copy/regenerate/bookmark) is opt-in and hidden by
// default (compactUxPrefs.showMessageActions). These tests assert that wiring,
// so enable it for the suite; clear after so other suites see the real default.
beforeEach(() => {
  localStorage.setItem('niuu.compactUx.showMessageActions', '1');
});
afterEach(() => {
  localStorage.clear();
});

const now = new Date('2024-01-01T12:00:00Z');

const userMsg: ChatMessage = {
  id: 'u1',
  role: 'user',
  content: 'Hello assistant',
  createdAt: now,
};

const assistantMsg: ChatMessage = {
  id: 'a1',
  role: 'assistant',
  content: 'Hello user',
  createdAt: now,
  metadata: { usage: { 'claude-sonnet': { inputTokens: 10, outputTokens: 20 } } },
};

const systemMsg: ChatMessage = {
  id: 's1',
  role: 'system',
  content: 'Session started',
  createdAt: now,
  metadata: { messageType: 'system' },
};

Object.assign(navigator, { clipboard: { writeText: vi.fn().mockResolvedValue(undefined) } });

describe('UserMessage', () => {
  it('renders user content', () => {
    render(<UserMessage message={userMsg} />);
    expect(screen.getByText('Hello assistant')).toBeInTheDocument();
    expect(screen.getByTestId('user-message')).toBeInTheDocument();
  });

  it('renders markdown in user content', () => {
    render(
      <UserMessage
        message={{
          ...userMsg,
          content: '# Run\n\n- update the README\n- add an ownership map',
        }}
      />,
    );

    expect(screen.getByRole('heading', { name: 'Run' })).toBeInTheDocument();
    expect(screen.getByText('update the README')).toBeInTheDocument();
    expect(screen.getByText('add an ownership map')).toBeInTheDocument();
  });

  it('renders attachment badges', () => {
    const msg = {
      ...userMsg,
      attachments: [
        { name: 'image.jpg', type: 'image' as const, size: 1024, contentType: 'image/jpeg' },
      ],
    };
    render(<UserMessage message={msg} />);
    expect(screen.getByText('image.jpg')).toBeInTheDocument();
    expect(screen.getByText('1.0KB')).toBeInTheDocument();
  });
});

describe('UserMessage author', () => {
  const alice = { userId: 'u-alice', displayName: 'Alice Liddell' };
  const bob = { userId: 'u-bob', displayName: 'Bob Stone' };

  function renderIn(
    message: ChatMessage,
    authorship: { viewerId?: string; multipleAuthors: boolean },
  ) {
    return render(
      <ChatAuthorshipProvider value={authorship}>
        <UserMessage message={message} />
      </ChatAuthorshipProvider>,
    );
  }

  it('keeps the current look for a single-person session', () => {
    renderIn({ ...userMsg, author: alice }, { viewerId: 'u-alice', multipleAuthors: false });
    expect(screen.queryByTestId('user-author')).not.toBeInTheDocument();
    expect(screen.getByText('Hello assistant')).toBeInTheDocument();
  });

  it('shows no author on a message that has none', () => {
    renderIn(userMsg, { viewerId: 'u-alice', multipleAuthors: true });
    expect(screen.queryByTestId('user-author')).not.toBeInTheDocument();
  });

  it('shows the avatar initials and name of someone other than the viewer', () => {
    renderIn({ ...userMsg, author: bob }, { viewerId: 'u-alice', multipleAuthors: false });
    const author = screen.getByTestId('user-author');
    expect(author).toHaveTextContent('Bob Stone');
    expect(author.querySelector('[data-initial]')).toHaveAttribute('data-initial', 'BS');
    // The name sits above the bubble.
    const wrapper = screen.getByTestId('user-message');
    expect(wrapper.firstElementChild).toBe(author);
  });

  it('names the viewer too once several people have spoken', () => {
    renderIn({ ...userMsg, author: alice }, { viewerId: 'u-alice', multipleAuthors: true });
    expect(screen.getByTestId('user-author')).toHaveTextContent('Alice Liddell');
  });

  it('shows no author outside a conversation that provides authorship', () => {
    render(<UserMessage message={{ ...userMsg, author: bob }} />);
    expect(screen.queryByTestId('user-author')).not.toBeInTheDocument();
  });

  it('names who answered a question the agent put to a person, and says it is an answer', () => {
    renderIn(
      { ...userMsg, id: 'pa_q-1', content: 'Use main', author: alice },
      { viewerId: 'u-alice', multipleAuthors: false },
    );
    const wrapper = screen.getByTestId('user-message');
    expect(wrapper).toHaveAttribute('id', 'chat-message-pa_q-1');
    expect(wrapper).toHaveAttribute('data-answers-question', 'q-1');
    expect(screen.getByTestId('user-author')).toHaveTextContent('Alice Liddell');
    expect(screen.getByTestId('person-answer-caption')).toHaveTextContent(
      "Answer to the agent's question",
    );
  });

  it('marks no other message as an answer', () => {
    renderIn({ ...userMsg, author: alice }, { viewerId: 'u-alice', multipleAuthors: false });
    expect(screen.queryByTestId('person-answer-caption')).not.toBeInTheDocument();
    expect(screen.getByTestId('user-message')).not.toHaveAttribute('data-answers-question');
  });
});

describe('AssistantMessage', () => {
  it('renders assistant content', () => {
    render(<AssistantMessage message={assistantMsg} />);
    expect(screen.getByTestId('assistant-message')).toBeInTheDocument();
  });

  it('shows model badge and token info when enabled', () => {
    render(<AssistantMessage message={assistantMsg} showTokenUsage />);
    expect(screen.getByText('claude-sonnet')).toBeInTheDocument();
    expect(screen.getByText(/tok/)).toBeInTheDocument();
  });

  it('copies content on copy button click', () => {
    render(<AssistantMessage message={assistantMsg} />);
    // action bar is visible on hover — show it by focusing
    const wrapper = screen.getByTestId('assistant-message');
    fireEvent.mouseOver(wrapper);
    const copyBtn = screen.getAllByRole('button').find((b) => b.title === 'Copy');
    if (copyBtn) fireEvent.click(copyBtn);
    expect(navigator.clipboard.writeText).toHaveBeenCalledWith('Hello user');
  });

  it('only shows Copy when message actions have no integration handlers', () => {
    render(<AssistantMessage message={assistantMsg} />);
    expect(screen.getAllByRole('button').map((button) => button.title)).toEqual(['Copy']);
  });

  it('calls onRegenerate with message id', () => {
    const onRegenerate = vi.fn();
    render(<AssistantMessage message={assistantMsg} onRegenerate={onRegenerate} />);
    const regenBtn = screen.getAllByRole('button').find((b) => b.title === 'Regenerate');
    if (regenBtn) fireEvent.click(regenBtn);
    expect(onRegenerate).toHaveBeenCalledWith('a1');
  });

  it('shows Forward… only when onForward is given and passes the message', () => {
    const { unmount } = render(<AssistantMessage message={assistantMsg} />);
    expect(screen.queryByRole('button', { name: 'Forward…' })).toBeNull();
    unmount();

    const onForward = vi.fn();
    render(<AssistantMessage message={assistantMsg} onForward={onForward} />);
    fireEvent.click(screen.getByRole('button', { name: 'Forward…' }));
    expect(onForward).toHaveBeenCalledWith(assistantMsg);
  });

  it('toggles reasoning section when present', () => {
    const msgWithReasoning: ChatMessage = {
      ...assistantMsg,
      parts: [{ type: 'reasoning', text: 'My thinking process' }],
    };
    render(<AssistantMessage message={msgWithReasoning} />);
    const trigger = screen.getByRole('button', { name: /thinking/i });
    expect(screen.queryByText('My thinking process')).not.toBeInTheDocument();
    fireEvent.click(trigger);
    expect(screen.getByText('My thinking process')).toBeInTheDocument();
  });
});

describe('StreamingMessage', () => {
  it('shows thinking state when content empty', () => {
    render(<StreamingMessage content="" />);
    expect(screen.getByTestId('streaming-message')).toBeInTheDocument();
    expect(screen.getByText('Thinking...')).toBeInTheDocument();
  });

  it('shows generating state when content present', () => {
    render(<StreamingMessage content="Partial response..." />);
    expect(screen.getByText('Generating...')).toBeInTheDocument();
    expect(screen.getByText('Partial response...')).toBeInTheDocument();
  });

  it('shows model badge when model provided', () => {
    render(<StreamingMessage content="Streaming..." model="claude-opus-4-6" />);
    expect(screen.getByText('Claude Opus 4.6')).toBeInTheDocument();
  });

  it('shows reasoning content when parts contain reasoning', () => {
    const parts = [{ type: 'reasoning' as const, text: 'Reasoning here' }];
    render(<StreamingMessage content="" parts={parts} />);
    expect(screen.getByText('Reasoning here')).toBeInTheDocument();
  });

  it('renders tool blocks when parts contain tool_use', () => {
    const parts = [{ type: 'tool_use' as const, id: 't1', name: 'Bash', input: { command: 'ls' } }];
    render(<StreamingMessage content="partial" parts={parts} />);
    expect(screen.getByTestId('tool-group-block')).toBeInTheDocument();
  });

  it('renders tool blocks while streaming before text content arrives', () => {
    const parts = [{ type: 'tool_use' as const, id: 't1', name: 'Bash', input: { command: 'ls' } }];
    render(<StreamingMessage content="" parts={parts} />);
    expect(screen.getByText('Using tools...')).toBeInTheDocument();
    expect(screen.getByTestId('tool-group-block')).toBeInTheDocument();
  });
});

describe('AssistantMessage — extended', () => {
  it('renders with tool_use parts', () => {
    const msg = {
      ...assistantMsg,
      parts: [
        { type: 'tool_use' as const, id: 't1', name: 'Read', input: { file_path: '/foo.ts' } },
      ],
    };
    render(<AssistantMessage message={msg} />);
    expect(screen.getByTestId('tool-group-block')).toBeInTheDocument();
  });

  it('calls onBookmark when bookmark button clicked', () => {
    const onBookmark = vi.fn();
    render(<AssistantMessage message={assistantMsg} onBookmark={onBookmark} />);
    const bookmarkBtn = screen.getAllByRole('button').find((b) => b.title === 'Bookmark');
    if (bookmarkBtn) fireEvent.click(bookmarkBtn);
    expect(onBookmark).toHaveBeenCalledWith('a1', true);
  });

  it('shows large file size in MB', () => {
    const msg = {
      ...assistantMsg,
      id: 'u2',
      role: 'user' as const,
      attachments: [
        {
          name: 'video.mp4',
          type: 'file' as const,
          size: 2 * 1024 * 1024,
          contentType: 'video/mp4',
        },
      ],
    };
    render(<UserMessage message={msg} />);
    expect(screen.getByText('2.0MB')).toBeInTheDocument();
  });

  it('renders group of tool blocks', () => {
    const msg = {
      ...assistantMsg,
      parts: [
        { type: 'tool_use' as const, id: 't1', name: 'Read', input: { file_path: '/a.ts' } },
        { type: 'tool_use' as const, id: 't2', name: 'Read', input: { file_path: '/b.ts' } },
      ],
    };
    render(<AssistantMessage message={msg} />);
    expect(screen.getByTestId('tool-group-block')).toBeInTheDocument();
  });
});

describe('SystemMessage', () => {
  it('renders system content', () => {
    render(<SystemMessage message={systemMsg} />);
    expect(screen.getByTestId('system-message')).toBeInTheDocument();
    expect(screen.getByText('Session started')).toBeInTheDocument();
  });
  it('renders restored assistant image attachments', () => {
    render(
      <AssistantMessage
        message={{
          id: 'image-answer',
          role: 'assistant',
          content: 'Screenshot',
          createdAt: new Date(),
          attachments: [
            {
              name: 'screenshot',
              type: 'image',
              size: 8,
              contentType: 'image/png',
              previewUrl: 'data:image/png;base64,aGVsbG8=',
            },
          ],
        }}
      />,
    );
    expect(screen.getByRole('img', { name: 'screenshot' })).toHaveAttribute(
      'src',
      'data:image/png;base64,aGVsbG8=',
    );
  });
});

it('hides usage by default and rounds counts with a spaced arrow when enabled', () => {
  const message = {
    ...assistantMsg,
    metadata: { usage: { astra: { inputTokens: 150344, outputTokens: 234 } } },
  };
  const view = render(<AssistantMessage message={message} />);
  expect(screen.queryByText(/tokens/)).not.toBeInTheDocument();
  view.rerender(<AssistantMessage message={message} showTokenUsage />);
  expect(screen.getByText('150k → 234 tokens')).toBeInTheDocument();
});

describe('Forge notification turns', () => {
  const notificationTurn: ChatMessage = {
    id: 'nt_1',
    role: 'assistant',
    content: 'Tests are green',
    createdAt: now,
    status: 'done',
    parts: [
      {
        type: 'tool_use',
        id: 'nt_1',
        name: 'forge_notification',
        input: {
          notification_id: 'n-1',
          kind: 'milestone',
          severity: 'success',
          title: 'Tests are green',
          body: 'All suites pass.',
          links: [],
        },
      },
    ],
  };

  it('renders a notification turn as a card without repeating its title as prose', () => {
    render(<AssistantMessage message={notificationTurn} />);
    const card = screen.getByTestId('notification-card');
    expect(card).toHaveTextContent('Tests are green');
    expect(card).toHaveTextContent('All suites pass.');
    expect(screen.getAllByText('Tests are green')).toHaveLength(1);
    expect(screen.queryByTestId('tool-block')).toBeNull();
  });

  it('falls back to a generic tool block when the notification input is unreadable', () => {
    render(
      <AssistantMessage
        message={{
          ...notificationTurn,
          parts: [{ type: 'tool_use', id: 'nt_2', name: 'forge_notification', input: {} }],
        }}
      />,
    );
    expect(screen.queryByTestId('notification-card')).toBeNull();
    expect(screen.getByTestId('tool-block')).toBeInTheDocument();
  });

  it('collapses the raw Claude notify call into a quiet, expandable line', () => {
    render(
      <AssistantMessage
        message={{
          id: 'a-notify',
          role: 'assistant',
          content: '',
          createdAt: now,
          status: 'done',
          parts: [
            {
              type: 'tool_use',
              id: 'call-1',
              name: 'mcp__forge__notify',
              input: { kind: 'milestone', title: 'Tests are green', body: 'All suites pass.' },
            },
            {
              type: 'tool_result',
              tool_use_id: 'call-1',
              content: '{"notification_id":"n-1","state":"committed"}',
            },
          ],
        }}
      />,
    );
    const block = screen.getByTestId('tool-block');
    expect(block).toHaveAttribute('data-tool-kind', 'forge-notify');
    const header = screen.getByRole('button', { expanded: false });
    expect(header).toHaveTextContent('Notified');
    expect(header).toHaveTextContent('Tests are green');
    expect(screen.queryByText('All suites pass.')).toBeNull();
    fireEvent.click(header);
    expect(screen.getByText('All suites pass.')).toBeInTheDocument();
    expect(screen.getByText(/"state":"committed"/)).toBeInTheDocument();
  });

  it('flags a failed Codex notify call so its missing card is explained', () => {
    render(
      <AssistantMessage
        message={{
          id: 'a-notify-codex',
          role: 'assistant',
          content: '',
          createdAt: now,
          status: 'done',
          parts: [
            {
              type: 'tool_use',
              id: 'call-2',
              name: 'notify',
              input: { kind: 'attention', title: 'Need a decision' },
            },
            {
              type: 'tool_result',
              tool_use_id: 'call-2',
              content: 'broker unavailable',
              is_error: true,
            },
          ],
        }}
      />,
    );
    expect(screen.getByTestId('tool-block')).toHaveTextContent('Notification failed');
    expect(screen.getByTestId('tool-block')).toHaveTextContent('Need a decision');
  });
});

describe('messageTime', () => {
  const now = new Date(2026, 10, 5, 18, 0);
  it('dates a message as Lexi does: today, yesterday, then the day', () => {
    expect(messageTime(new Date(2026, 10, 5, 13, 8), now)).toBe('Today at 1:08 PM');
    expect(messageTime(new Date(2026, 10, 4, 9, 54), now)).toBe('Yesterday at 9:54 AM');
    expect(messageTime(new Date(2026, 9, 31, 9, 54), now)).toBe('Oct 31, 2026 at 9:54 AM');
  });
});

describe('secret request turns', () => {
  const secretTurn: ChatMessage = {
    id: 'sr_1',
    role: 'assistant',
    content: 'Secret requested: GITLAB_TOKEN',
    createdAt: now,
    status: 'done',
    parts: [
      {
        type: 'tool_use',
        id: 'sr_1',
        name: 'secret_request',
        input: {
          request_id: 'r-1',
          name: 'GITLAB_TOKEN',
          purpose: 'Push the branch',
          target: 'gitlab.niuu.world',
        },
      },
    ],
  };

  it('renders a read-only card without a host and does not repeat the content as prose', () => {
    render(<AssistantMessage message={secretTurn} />);
    const card = screen.getByTestId('secret-request-card');
    expect(card).toHaveTextContent('Push the branch');
    expect(card).toHaveTextContent('Answer this in Forge');
    expect(screen.queryByText('Secret requested: GITLAB_TOKEN')).toBeNull();
    expect(screen.queryByTestId('tool-block')).toBeNull();
  });

  it('falls back to a generic tool block when the input is unreadable', () => {
    render(
      <AssistantMessage
        message={{
          ...secretTurn,
          parts: [{ type: 'tool_use', id: 'sr_2', name: 'secret_request', input: {} }],
        }}
      />,
    );
    expect(screen.queryByTestId('secret-request-card')).toBeNull();
    expect(screen.getByTestId('tool-block')).toBeInTheDocument();
  });
});
