import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ForgeSessionCard } from './ForgeSessionCards';
import { SessionLinksProvider, type SessionLink, type SessionLinksValue } from '../../sessionLinks';
import type { ToolResultBlock, ToolUseBlock } from '../ToolBlock/groupContentBlocks';

const ID = '3f1c2d4e-5a6b-4c7d-8e9f-0a1b2c3d4e5f';

const call = (name: string, input: Record<string, unknown>): ToolUseBlock => ({
  type: 'tool_use',
  id: 'call-1',
  name,
  input,
});

const answer = (payload: unknown, is_error = false): ToolResultBlock => ({
  type: 'tool_result',
  tool_use_id: 'call-1',
  content: [
    { type: 'text', text: typeof payload === 'string' ? payload : JSON.stringify(payload) },
  ],
  ...(is_error ? { is_error } : {}),
});

const worker: SessionLink = {
  id: ID,
  name: 'ticket-1342-gates',
  stateLabel: 'Working',
  tone: 'ok',
  href: `/volundr/sessions/${ID}`,
};

function links(open = vi.fn()): SessionLinksValue {
  return {
    resolve: (key) => (key === ID || key === 'ticket-1342-gates' ? worker : null),
    open,
  };
}

const create = (name = 'mcp__forge__create_session') =>
  call(name, {
    name: 'ticket-1342-gates',
    objective: 'Refuse agent approval on human gates',
    model: 'claude-fable-5-1',
  });

const created = answer({ session: { id: ID, name: 'ticket-1342-gates', status: 'running' } });

describe('ForgeSessionCard: create_session / start_session', () => {
  it.each(['mcp__forge__create_session', 'forge.create_session'])(
    'says Started worker with the objective and model for %s',
    (name) => {
      render(<ForgeSessionCard block={create(name)} result={created} />);
      const card = screen.getByTestId('worker-card');
      expect(card).toHaveTextContent('Started worker ticket-1342-gates');
      expect(card).toHaveTextContent('Refuse agent approval on human gates');
      expect(card).toHaveTextContent('Claude Fable 5.1');
      expect(card).toHaveAttribute('data-action', 'create_session');
    },
  );

  it('shows the live state and an Open button when the session list knows the worker', async () => {
    const open = vi.fn();
    render(
      <SessionLinksProvider value={links(open)}>
        <ForgeSessionCard block={create()} result={created} />
      </SessionLinksProvider>,
    );
    expect(screen.getByTestId('worker-state')).toHaveTextContent('Working');
    expect(screen.getByTestId('worker-state')).toHaveAttribute('data-tone', 'ok');
    const openLink = screen.getByRole('link', { name: 'Open ticket-1342-gates' });
    expect(openLink).toHaveAttribute('href', worker.href);
    await userEvent.click(openLink);
    expect(open).toHaveBeenCalledWith(ID);
  });

  it('shows neither state nor Open without a provider', () => {
    render(<ForgeSessionCard block={create()} result={created} />);
    expect(screen.queryByTestId('worker-state')).toBeNull();
    expect(screen.queryByRole('link')).toBeNull();
  });

  it('finds the worker by name before the result names its id', () => {
    render(
      <SessionLinksProvider value={links()}>
        <ForgeSessionCard block={create()} isStreaming />
      </SessionLinksProvider>,
    );
    expect(screen.getByTestId('worker-card')).toHaveAttribute('data-status', 'running');
    expect(screen.getByTestId('worker-card')).toHaveTextContent('Starting worker');
    expect(screen.getByTestId('worker-state')).toHaveTextContent('Working');
  });

  it('names a started session from its call by the resolved session', () => {
    render(
      <SessionLinksProvider value={links()}>
        <ForgeSessionCard
          block={call('forge.start_session', { session_id: ID })}
          result={answer({ session: { id: ID, status: 'running' } })}
        />
      </SessionLinksProvider>,
    );
    expect(screen.getByTestId('worker-card')).toHaveTextContent('Started worker ticket-1342-gates');
    expect(screen.getByTestId('worker-card')).toHaveAttribute('data-action', 'start_session');
  });

  it('names an unresolved started session by the start of its id', () => {
    render(
      <ForgeSessionCard
        block={call('mcp__forge__start_session', { session_id: ID })}
        result={answer('not json')}
      />,
    );
    expect(screen.getByTestId('worker-card')).toHaveTextContent('Started worker 3f1c2d4e');
  });

  it('reads the model from the result when the call named none', () => {
    render(
      <ForgeSessionCard
        block={call('mcp__forge__create_session', { name: 'w' })}
        result={answer({ session: { id: ID, model: 'gpt-6-sol' } })}
      />,
    );
    expect(screen.getByTestId('worker-card')).toHaveTextContent('GPT-6 Sol');
  });

  it('says it could not start the worker and why when the call failed', () => {
    render(
      <ForgeSessionCard
        block={create()}
        result={answer('create_session failed: quota exceeded', true)}
      />,
    );
    const card = screen.getByTestId('worker-card');
    expect(card).toHaveAttribute('data-status', 'failed');
    expect(card).toHaveTextContent('Could not start worker ticket-1342-gates');
    expect(card).toHaveTextContent('quota exceeded');
  });

  it('opens the raw call from Details', async () => {
    render(<ForgeSessionCard block={create()} result={created} />);
    await userEvent.click(screen.getByRole('button', { name: 'Details' }));
    expect(await screen.findByRole('dialog')).toBeInTheDocument();
  });

  it('does not claim a call is running once its message is over without a result', () => {
    render(<ForgeSessionCard block={create()} isStreaming={false} />);
    expect(screen.getByTestId('worker-card')).toHaveAttribute('data-status', 'done');
  });
});

describe('ForgeSessionCard: send_message', () => {
  const send = (name = 'mcp__forge__send_message') =>
    call(name, { session_id: ID, content: 'Rebase on dev first.\nThen rerun the gates.' });

  it.each(['mcp__forge__send_message', 'forge.send_message'])(
    'is one row: the worker, the first line of the message and Delivered for %s',
    (name) => {
      render(
        <SessionLinksProvider value={links()}>
          <ForgeSessionCard
            block={send(name)}
            result={answer({ session_id: ID, request_id: 'r1', status: 'delivered' })}
          />
        </SessionLinksProvider>,
      );
      const row = screen.getByTestId('worker-message');
      expect(row).toHaveTextContent('ticket-1342-gates');
      expect(row).toHaveTextContent('Rebase on dev first.');
      expect(row).not.toHaveTextContent('Then rerun');
      expect(screen.getByTestId('worker-delivery')).toHaveTextContent('Delivered');
      expect(row).toHaveAttribute('data-delivery', 'delivered');
    },
  );

  it('says Pending while Forge still drives the delivery', () => {
    render(
      <ForgeSessionCard
        block={send()}
        result={answer({
          session_id: ID,
          request_id: 'r1',
          status: 'pending',
          delivery: 'pending',
        })}
      />,
    );
    expect(screen.getByTestId('worker-delivery')).toHaveTextContent('Pending');
  });

  it('says Sending while the call has no result and Failed for an error', () => {
    const { rerender } = render(<ForgeSessionCard block={send()} isStreaming />);
    expect(screen.getByTestId('worker-delivery')).toHaveTextContent('Sending');
    rerender(<ForgeSessionCard block={send()} result={answer('refused', true)} />);
    expect(screen.getByTestId('worker-delivery')).toHaveTextContent('Failed');
  });

  it('shows no state it cannot read and names an unknown target by its id', () => {
    render(<ForgeSessionCard block={send()} result={answer('cut off {"sess')} />);
    expect(screen.queryByTestId('worker-delivery')).toBeNull();
    expect(screen.getByTestId('worker-message')).toHaveTextContent('3f1c2d4e');
  });

  it('opens the whole message from the preview', async () => {
    render(<ForgeSessionCard block={send()} result={answer({ status: 'delivered' })} />);
    await userEvent.click(screen.getByRole('button', { name: /Open message to/ }));
    expect(await screen.findByRole('dialog')).toBeInTheDocument();
  });
});

describe('ForgeSessionCard: stop_session', () => {
  const stop = call('mcp__forge__stop_session', { session_id: ID });

  it('says Stopped with the worker', () => {
    render(
      <SessionLinksProvider value={links()}>
        <ForgeSessionCard
          block={stop}
          result={answer({ session: { id: ID, status: 'stopped' } })}
        />
      </SessionLinksProvider>,
    );
    expect(screen.getByTestId('worker-stopped')).toHaveTextContent(/^Stopped\s*ticket-1342-gates$/);
  });

  it('says Stopping while running and why it could not stop on failure', () => {
    const { rerender } = render(<ForgeSessionCard block={stop} isStreaming />);
    expect(screen.getByTestId('worker-stopped')).toHaveTextContent('Stopping');
    rerender(
      <ForgeSessionCard
        block={stop}
        result={answer('stop_session targets your own session', true)}
      />,
    );
    expect(screen.getByTestId('worker-stopped')).toHaveTextContent('Could not stop');
    expect(screen.getByTestId('worker-stopped')).toHaveTextContent('your own session');
  });
});

describe('ForgeSessionCard: other tools', () => {
  it('renders nothing for a tool that is not a session call', () => {
    const { container } = render(
      <ForgeSessionCard block={call('mcp__forge__list_sessions', {})} />,
    );
    expect(container).toBeEmptyDOMElement();
  });
});
