import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import { LiveChat } from './LiveChat';

const useSkuldChat = vi.fn();

vi.mock('@niuulabs/ui', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@niuulabs/ui')>()),
  useSkuldChat: (...args: unknown[]) => useSkuldChat(...args),
  SessionChat: (props: { chatEndpoint: string; sessionName: string; eventRouting: boolean }) => (
    <div
      data-testid="session-chat"
      data-endpoint={props.chatEndpoint}
      data-name={props.sessionName}
      data-routing={String(props.eventRouting)}
    />
  ),
}));

function chat() {
  return {
    messages: [],
    connected: true,
    historyLoaded: true,
    sendMessage: vi.fn(),
  };
}

describe('LiveChat', () => {
  it('opens the Skuld chat on the endpoint with socket history for residents', () => {
    useSkuldChat.mockReturnValue(chat());
    render(
      <ServicesProvider services={{}}>
        <LiveChat chatEndpoint="ws://h/s/a/session" name="Muninn" socketHistory eventRouting />
      </ServicesProvider>,
    );
    expect(useSkuldChat).toHaveBeenCalledWith('ws://h/s/a/session', {
      historyMode: 'none',
      historyEndpoint: null,
    });
    const view = screen.getByTestId('session-chat');
    expect(view).toHaveAttribute('data-endpoint', 'ws://h/s/a/session');
    expect(view).toHaveAttribute('data-name', 'Muninn');
    expect(view).toHaveAttribute('data-routing', 'true');
  });

  it('reads session history through Forge when a locator is wired', () => {
    useSkuldChat.mockReturnValue(chat());
    const historyEndpoint = vi.fn().mockReturnValue('/api/v1/forge/sessions/a/log');
    render(
      <ServicesProvider services={{ 'forge.history': { historyEndpoint } }}>
        <LiveChat
          chatEndpoint="ws://h/s/a/session"
          name="Muninn"
          socketHistory={false}
          eventRouting={false}
        />
      </ServicesProvider>,
    );
    expect(historyEndpoint).toHaveBeenCalledWith('ws://h/s/a/session');
    expect(useSkuldChat).toHaveBeenLastCalledWith('ws://h/s/a/session', {
      historyMode: 'session',
      historyEndpoint: '/api/v1/forge/sessions/a/log',
    });
  });
});
