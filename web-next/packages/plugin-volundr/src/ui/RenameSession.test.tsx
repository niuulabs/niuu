import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import { describe, expect, it, vi } from 'vitest';
import { RenameSession } from './RenameSession';

function setup(updateSession = vi.fn().mockResolvedValue({ id: 'one', name: 'renamed' })) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  client.setQueryData(
    ['volundr', 'domain-sessions', null],
    [
      { id: 'one', name: 'original', state: 'running' },
      { id: 'two', name: 'other' },
    ],
  );
  client.setQueryData(['volundr', 'domain-session', 'one'], { id: 'one', name: 'original' });
  const view = render(
    <QueryClientProvider client={client}>
      <ServicesProvider services={{ volundr: { updateSession } }}>
        <RenameSession sessionId="one" name="original" />
      </ServicesProvider>
    </QueryClientProvider>,
  );
  fireEvent.click(screen.getByRole('button', { name: 'Rename original' }));
  return { client, updateSession, ...view };
}

describe('RenameSession', () => {
  it('saves a trimmed name and updates the cached header and list without touching other sessions', async () => {
    const { client, updateSession } = setup();
    const input = screen.getByRole('textbox', { name: 'Session name' });
    await waitFor(() => expect(input).toHaveFocus());
    expect(screen.getByRole('button', { name: 'Save', exact: true })).toBeDisabled();
    fireEvent.change(input, { target: { value: '  renamed  ' } });
    fireEvent.submit(screen.getByRole('form', { name: 'Rename session' }));
    await waitFor(() => expect(screen.queryByRole('textbox')).not.toBeInTheDocument());
    expect(updateSession).toHaveBeenCalledExactlyOnceWith('one', { name: 'renamed' });
    expect(client.getQueryData(['volundr', 'domain-sessions', null])).toEqual([
      { id: 'one', name: 'renamed', state: 'running' },
      { id: 'two', name: 'other' },
    ]);
    expect(client.getQueryData(['volundr', 'raw-session', 'one'])).toMatchObject({
      name: 'renamed',
    });
    expect(client.getQueryData(['volundr', 'domain-session', 'one'])).toMatchObject({
      name: 'renamed',
    });
  });

  it('validates the running Forge name contract and cancels without saving', () => {
    const { updateSession } = setup();
    const input = screen.getByRole('textbox', { name: 'Session name' });
    for (const value of [' ', 'New name', 'bad_name', '-name', 'a'.repeat(64)]) {
      fireEvent.change(input, { target: { value } });
      expect(screen.getByRole('alert')).toBeInTheDocument();
      expect(screen.getByRole('button', { name: 'Save', exact: true })).toBeDisabled();
      fireEvent.submit(screen.getByRole('form', { name: 'Rename session' }));
    }
    fireEvent.change(input, { target: { value: 'valid-name' } });
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(updateSession).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Rename original' }));
    expect(screen.getByRole('textbox')).toHaveValue('original');
  });

  it('keeps the draft after a failure and prevents duplicate saves while pending', async () => {
    let reject!: (error: Error) => void;
    const update = vi
      .fn()
      .mockImplementationOnce(
        () =>
          new Promise((_resolve, rejectPromise) => {
            reject = rejectPromise;
          }),
      )
      .mockResolvedValue({ id: 'one', name: 'renamed' });
    const { client } = setup(update);
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'renamed' } });
    const form = screen.getByRole('form', { name: 'Rename session' });
    fireEvent.submit(form);
    expect(screen.getByRole('button', { name: 'Saving…' })).toBeDisabled();
    fireEvent.submit(form);
    fireEvent.keyDown(screen.getByRole('textbox'), { key: 'Escape' });
    expect(screen.getByRole('textbox')).toBeInTheDocument();
    await act(async () => reject(new Error('Forge is unavailable')));
    expect(await screen.findByRole('alert')).toHaveTextContent('Forge is unavailable');
    expect(screen.getByRole('textbox')).toHaveValue('renamed');
    expect(client.getQueryData(['volundr', 'domain-session', 'one'])).toMatchObject({
      name: 'original',
    });
    expect(update).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole('button', { name: 'Save', exact: true }));
    await waitFor(() => expect(screen.queryByRole('textbox')).not.toBeInTheDocument());
    expect(update).toHaveBeenCalledTimes(2);
  });
});
