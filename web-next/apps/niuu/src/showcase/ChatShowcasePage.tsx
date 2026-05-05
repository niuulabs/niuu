import { useState, useCallback } from 'react';
import { SessionChat } from '@niuulabs/ui';
import type { ChatMessage } from '@niuulabs/ui';
import type { FileAttachment } from '@niuulabs/ui';

const TOOL_USE_MSG_ID = 'msg-tool-use';
const OUTCOME_MSG_ID = 'msg-outcome';

const INITIAL_MESSAGES: ChatMessage[] = [
  {
    id: 'msg-user-1',
    role: 'user',
    content: 'Run the tests and show me the output.',
    createdAt: new Date('2026-04-19T10:00:00Z'),
  },
  {
    id: TOOL_USE_MSG_ID,
    role: 'assistant',
    content: 'Running tests…',
    createdAt: new Date('2026-04-19T10:00:01Z'),
    parts: [
      {
        type: 'tool_use',
        id: 'call-1',
        name: 'Bash',
        input: { command: 'pnpm test' },
      },
      {
        type: 'tool_result',
        tool_use_id: 'call-1',
        content: '64 tests passed, 0 failed.',
      },
      {
        type: 'text',
        text: 'All 64 tests are passing.',
      },
    ],
  },
  {
    id: OUTCOME_MSG_ID,
    role: 'assistant',
    content: '```outcome\nstatus: success\ntests: 64\nfailed: 0\n```\n\nAll tests pass.',
    createdAt: new Date('2026-04-19T10:00:02Z'),
  },
];

export function ChatShowcasePage() {
  const [messages, setMessages] = useState<ChatMessage[]>(INITIAL_MESSAGES);
  const [streamingContent, setStreamingContent] = useState<string | undefined>(undefined);

  const handleSend = useCallback((text: string, _attachments: FileAttachment[]) => {
    const userMsg: ChatMessage = {
      id: `msg-${Date.now()}`,
      role: 'user',
      content: text,
      createdAt: new Date(),
    };
    setMessages((prev) => [...prev, userMsg]);

    // Simulate a short streaming response
    setStreamingContent('Thinking…');
    const words = `You said: "${text}". This is a mock streaming reply.`.split(' ');
    let i = 0;
    const iv = setInterval(() => {
      i++;
      setStreamingContent(words.slice(0, i).join(' '));
      if (i >= words.length) {
        clearInterval(iv);
        const assistantMsg: ChatMessage = {
          id: `msg-${Date.now()}-assistant`,
          role: 'assistant',
          content: words.join(' '),
          createdAt: new Date(),
        };
        setMessages((prev) => [...prev, assistantMsg]);
        setStreamingContent(undefined);
      }
    }, 80);
  }, []);

  return (
    <div className="niuu-h-screen niuu-flex niuu-flex-col" data-testid="chat-showcase">
      <SessionChat
        messages={messages}
        streamingContent={streamingContent}
        historyLoaded
        connected
        onSend={handleSend}
        sessionName="chat-showcase"
      />
    </div>
  );
}
