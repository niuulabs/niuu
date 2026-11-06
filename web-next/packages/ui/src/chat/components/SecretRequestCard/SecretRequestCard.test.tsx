import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import type { SecretRequest, SecretRequestInput } from '@niuulabs/domain';
import { SecretRequestsContext, type SecretRequestsPort } from '../../../SecretRequests';
import { SECRET_REQUEST_POLL_MS, SecretRequestCard } from './SecretRequestCard';

const request: SecretRequestInput = {
  requestId: 'r-1',
  name: 'GITLAB_TOKEN',
  purpose: 'Push the release branch',
  target: 'gitlab.niuu.world',
};

function state(overrides: Partial<SecretRequest> = {}): SecretRequest {
  return {
    id: 'r-1',
    sessionId: 's-1',
    name: 'GITLAB_TOKEN',
    purpose: 'Push the release branch',
    target: 'gitlab.niuu.world',
    status: 'pending',
    keep: null,
    credentialName: null,
    ...overrides,
  };
}

function makePort(overrides: Partial<SecretRequestsPort> = {}): SecretRequestsPort {
  return {
    get: vi.fn(async () => state()),
    fulfill: vi.fn(async (_id, { keep }) =>
      state({
        status: 'fulfilled',
        keep,
        credentialName: keep === 'saved' ? 'gitlab-token' : null,
      }),
    ),
    decline: vi.fn(async () => state({ status: 'declined' })),
    ...overrides,
  };
}

async function renderCard(port?: SecretRequestsPort) {
  const view = render(
    <SecretRequestsContext.Provider value={port}>
      <SecretRequestCard request={request} />
    </SecretRequestsContext.Provider>,
  );
  if (port) await screen.findByRole('button', { name: 'Save securely' }).catch(() => undefined);
  return view;
}

const field = () => screen.getByLabelText('Secret') as HTMLInputElement;

describe('SecretRequestCard', () => {
  it('shows the title, name, purpose and target, and the masked form with its defaults', async () => {
    await renderCard(makePort());
    expect(screen.getByRole('heading', { name: 'Gitlab token' })).toBeInTheDocument();
    expect(screen.getByText('GITLAB_TOKEN')).toBeInTheDocument();
    expect(screen.getByText('Push the release branch')).toBeInTheDocument();
    expect(screen.getByText('for gitlab.niuu.world')).toBeInTheDocument();
    expect(field()).toHaveAttribute('type', 'password');
    expect(field()).toHaveAttribute('autocomplete', 'off');
    expect(screen.getByLabelText('This session only')).toBeChecked();
    expect(screen.getByLabelText('Save to my credentials')).not.toBeChecked();
    expect(screen.getByRole('button', { name: 'Decline' })).toBeInTheDocument();
    expect(screen.getByText('Never shown to the agent')).toBeInTheDocument();
  });

  it('marks the chosen lifetime on its pill, since the reset hides the native radio', async () => {
    await renderCard(makePort());
    const session = screen.getByLabelText('This session only');
    const saved = screen.getByLabelText('Save to my credentials');
    expect(session).toHaveClass('niuu:sr-only');
    expect(session.closest('label')).toHaveClass('niuu:border-brand');
    expect(saved.closest('label')).not.toHaveClass('niuu:border-brand');

    fireEvent.click(saved);

    expect(saved).toBeChecked();
    expect(saved.closest('label')).toHaveClass('niuu:border-brand');
    expect(session.closest('label')).not.toHaveClass('niuu:border-brand');
  });

  it('omits the target line when there is no target', async () => {
    const port = makePort({ get: vi.fn(async () => state({ target: null })) });
    render(
      <SecretRequestsContext.Provider value={port}>
        <SecretRequestCard request={{ ...request, target: null, purpose: '' }} />
      </SecretRequestsContext.Provider>,
    );
    await screen.findByRole('button', { name: 'Save securely' });
    expect(screen.queryByText(/^for /)).toBeNull();
  });

  it('shows a loading line until the request is read', () => {
    render(
      <SecretRequestsContext.Provider value={makePort({ get: () => new Promise(() => {}) })}>
        <SecretRequestCard request={request} />
      </SecretRequestsContext.Provider>,
    );
    expect(screen.getByText('Checking this request...')).toBeInTheDocument();
    expect(screen.getByTestId('secret-request-card')).toHaveAttribute('data-state', 'loading');
  });

  it('reveals and hides the value with the eye toggle', async () => {
    await renderCard(makePort());
    fireEvent.change(field(), { target: { value: 'glpat-123' } });
    fireEvent.click(screen.getByRole('button', { name: 'Show secret' }));
    expect(field()).toHaveAttribute('type', 'text');
    expect(field().value).toBe('glpat-123');
    fireEvent.click(screen.getByRole('button', { name: 'Hide secret' }));
    expect(field()).toHaveAttribute('type', 'password');
  });

  it('refuses an empty submit inline without calling the host', async () => {
    const port = makePort();
    await renderCard(port);
    fireEvent.click(screen.getByRole('button', { name: 'Save securely' }));
    expect(await screen.findByText('Enter the secret first')).toBeInTheDocument();
    fireEvent.change(field(), { target: { value: '   ' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save securely' }));
    expect(await screen.findByText('Enter the secret first')).toBeInTheDocument();
    expect(port.fulfill).not.toHaveBeenCalled();
    fireEvent.change(field(), { target: { value: 'x' } });
    expect(screen.queryByText('Enter the secret first')).toBeNull();
  });

  it('sends the value with keep=session and shows the session-only outcome', async () => {
    const port = makePort();
    await renderCard(port);
    fireEvent.change(field(), { target: { value: 'glpat-123' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save securely' }));
    expect(
      await screen.findByText('Saved. The value was never shown to the agent.'),
    ).toBeInTheDocument();
    expect(port.fulfill).toHaveBeenCalledExactlyOnceWith('r-1', {
      value: 'glpat-123',
      keep: 'session',
    });
    expect(screen.getByText('Deleted when this session stops.')).toBeInTheDocument();
    expect(screen.queryByLabelText('Secret')).toBeNull();
    expect(document.body.innerHTML).not.toContain('glpat-123');
  });

  it('sends keep=saved when chosen and names the credential', async () => {
    const port = makePort();
    await renderCard(port);
    fireEvent.change(field(), { target: { value: 'glpat-123' } });
    fireEvent.click(screen.getByLabelText('Save to my credentials'));
    fireEvent.click(screen.getByRole('button', { name: 'Save securely' }));
    expect(
      await screen.findByText('Kept in your credentials as gitlab-token.'),
    ).toBeInTheDocument();
    expect(port.fulfill).toHaveBeenCalledWith('r-1', { value: 'glpat-123', keep: 'saved' });
  });

  it('names no credential when Forge reports none for a saved value', async () => {
    const port = makePort({
      get: vi.fn(async () => state({ status: 'delivered', keep: 'saved' })),
    });
    await renderCard(port);
    expect(await screen.findByText('Kept in your credentials.')).toBeInTheDocument();
  });

  it('counts a delivered request as saved', async () => {
    const port = makePort({
      get: vi.fn(async () => state({ status: 'delivered', keep: 'session' })),
    });
    await renderCard(port);
    expect(
      await screen.findByText('Saved. The value was never shown to the agent.'),
    ).toBeInTheDocument();
    expect(screen.getByText('Deleted when this session stops.')).toBeInTheDocument();
  });

  it('clears the typed value once it is sent', async () => {
    let finish: (value: SecretRequest) => void = () => {};
    const port = makePort({
      fulfill: vi.fn(() => new Promise<SecretRequest>((resolve) => (finish = resolve))),
    });
    await renderCard(port);
    fireEvent.change(field(), { target: { value: 'glpat-123' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save securely' }));
    await waitFor(() => expect(field()).toBeDisabled());
    // A second submit while the first is in flight is ignored.
    fireEvent.submit(field().closest('form')!);
    expect(port.fulfill).toHaveBeenCalledTimes(1);
    await act(async () => finish(state({ status: 'fulfilled', keep: 'session' })));
    expect(document.body.innerHTML).not.toContain('glpat-123');
  });

  it('shows a fulfil error inline and keeps the form', async () => {
    const port = makePort({
      fulfill: vi.fn(async () => {
        throw new Error('Credential store unavailable');
      }),
    });
    await renderCard(port);
    fireEvent.change(field(), { target: { value: 'glpat-123' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save securely' }));
    expect(await screen.findByText('Credential store unavailable')).toBeInTheDocument();
    expect(field()).not.toBeDisabled();
    expect(field().value).toBe('glpat-123');
    expect(screen.getByRole('button', { name: 'Save securely' })).toBeEnabled();
  });

  it('reports a non-Error rejection as text', async () => {
    const port = makePort({
      decline: vi.fn(() => Promise.reject('nope')),
    });
    await renderCard(port);
    fireEvent.click(screen.getByRole('button', { name: 'Decline' }));
    expect(await screen.findByText('nope')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Decline' })).toBeEnabled();
  });

  it('declines the request and says so', async () => {
    const port = makePort();
    await renderCard(port);
    fireEvent.click(screen.getByRole('button', { name: 'Decline' }));
    expect(
      await screen.findByText('Declined. The agent was told no secret is coming.'),
    ).toBeInTheDocument();
    expect(port.decline).toHaveBeenCalledWith('r-1');
  });

  it('shows a request already declined or withdrawn', async () => {
    const { unmount } = await renderCard(
      makePort({ get: vi.fn(async () => state({ status: 'declined' })) }),
    );
    expect(
      await screen.findByText('Declined. The agent was told no secret is coming.'),
    ).toBeInTheDocument();
    unmount();
    await renderCard(makePort({ get: vi.fn(async () => state({ status: 'cancelled' })) }));
    expect(await screen.findByText('This request was withdrawn.')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Save securely' })).toBeNull();
  });

  it('is read-only without a provider', () => {
    render(<SecretRequestCard request={request} />);
    expect(screen.getByRole('heading', { name: 'Gitlab token' })).toBeInTheDocument();
    expect(screen.getByText('Push the release branch')).toBeInTheDocument();
    expect(screen.getByText('Answer this in Forge')).toBeInTheDocument();
    expect(screen.queryByLabelText('Secret')).toBeNull();
    expect(screen.queryByRole('button')).toBeNull();
  });

  describe('loading', () => {
    beforeEach(() => vi.useFakeTimers());
    afterEach(() => vi.useRealTimers());

    it('refetches while pending and stops once the request is settled', async () => {
      const get = vi
        .fn<SecretRequestsPort['get']>()
        .mockResolvedValueOnce(state())
        .mockResolvedValueOnce(state())
        .mockResolvedValue(state({ status: 'cancelled' }));
      render(
        <SecretRequestsContext.Provider value={makePort({ get })}>
          <SecretRequestCard request={request} />
        </SecretRequestsContext.Provider>,
      );
      await act(async () => {});
      expect(get).toHaveBeenCalledTimes(1);
      await act(async () => {
        await vi.advanceTimersByTimeAsync(SECRET_REQUEST_POLL_MS);
      });
      expect(get).toHaveBeenCalledTimes(2);
      await act(async () => {
        await vi.advanceTimersByTimeAsync(SECRET_REQUEST_POLL_MS);
      });
      expect(screen.getByText('This request was withdrawn.')).toBeInTheDocument();
      await act(async () => {
        await vi.advanceTimersByTimeAsync(SECRET_REQUEST_POLL_MS * 3);
      });
      expect(get).toHaveBeenCalledTimes(3);
    });

    it('stops polling when the card goes away', async () => {
      const get = vi.fn(async () => state());
      const { unmount } = render(
        <SecretRequestsContext.Provider value={makePort({ get })}>
          <SecretRequestCard request={request} />
        </SecretRequestsContext.Provider>,
      );
      await act(async () => {});
      unmount();
      await vi.advanceTimersByTimeAsync(SECRET_REQUEST_POLL_MS * 3);
      expect(get).toHaveBeenCalledTimes(1);
    });

    it('shows a read failure and retries on request', async () => {
      const get = vi
        .fn<SecretRequestsPort['get']>()
        .mockRejectedValueOnce(new Error('Forge unreachable'))
        .mockResolvedValue(state());
      render(
        <SecretRequestsContext.Provider value={makePort({ get })}>
          <SecretRequestCard request={request} />
        </SecretRequestsContext.Provider>,
      );
      await act(async () => {});
      expect(screen.getByRole('alert')).toHaveTextContent('Forge unreachable');
      expect(screen.getByTestId('secret-request-card')).toHaveAttribute('data-state', 'error');
      fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
      await act(async () => {});
      expect(screen.queryByText('Forge unreachable')).toBeNull();
      expect(screen.getByRole('button', { name: 'Save securely' })).toBeInTheDocument();
    });

    it('does not apply a read that finishes after the card went away', async () => {
      let finish: (value: SecretRequest) => void = () => {};
      const get = vi.fn(() => new Promise<SecretRequest>((resolve) => (finish = resolve)));
      const { unmount } = render(
        <SecretRequestsContext.Provider value={makePort({ get })}>
          <SecretRequestCard request={request} />
        </SecretRequestsContext.Provider>,
      );
      unmount();
      await act(async () => finish(state()));
      await vi.advanceTimersByTimeAsync(SECRET_REQUEST_POLL_MS * 2);
      expect(get).toHaveBeenCalledTimes(1);
    });

    it('ignores a read failure that arrives after the card went away', async () => {
      let fail: (error: Error) => void = () => {};
      const get = vi.fn(() => new Promise<SecretRequest>((_, reject) => (fail = reject)));
      const { unmount } = render(
        <SecretRequestsContext.Provider value={makePort({ get })}>
          <SecretRequestCard request={request} />
        </SecretRequestsContext.Provider>,
      );
      unmount();
      await act(async () => fail(new Error('late')));
      expect(get).toHaveBeenCalledTimes(1);
    });
  });
});
