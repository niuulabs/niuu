import { act, renderHook } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { IPtyStream } from '../../ports/IPtyStream';
import { useExec } from './useExec';

function createStream() {
  const subscribers = new Set<(chunk: string) => void>();
  const send = vi.fn();
  const subscribe = vi.fn((_sessionId: string, onData: (chunk: string) => void) => {
    subscribers.add(onData);
    return () => subscribers.delete(onData);
  });

  const stream: IPtyStream = { send, subscribe };
  return {
    stream,
    send,
    subscribe,
    emit: (chunk: string) => subscribers.forEach((fn) => fn(chunk)),
  };
}

describe('useExec', () => {
  it('runs a command, streams output, and marks the entry as successful at the prompt', async () => {
    const { stream, send, emit } = createStream();
    const { result } = renderHook(() => useExec('sess-1', stream));

    let runPromise: Promise<void>;
    await act(async () => {
      runPromise = result.current.run('pwd');
    });

    expect(result.current.isRunning).toBe(true);
    expect(send).toHaveBeenCalledWith('sess-1', 'pwd\r');

    await act(async () => {
      emit('working directory\r\n');
      emit('/tmp/project\r\n$ ');
      await runPromise;
    });

    expect(result.current.isRunning).toBe(false);
    expect(result.current.history).toHaveLength(1);
    expect(result.current.history[0]).toMatchObject({
      command: 'pwd',
      output: 'working directory\r\n/tmp/project\r\n$ ',
      status: 'success',
    });
  });

  it('ignores a second run request while a command is already active', async () => {
    const { stream, send, emit } = createStream();
    const { result } = renderHook(() => useExec('sess-1', stream));

    let firstRun: Promise<void>;
    await act(async () => {
      firstRun = result.current.run('pwd');
    });

    await act(async () => {
      await result.current.run('ls');
    });

    expect(send).toHaveBeenCalledTimes(1);
    expect(send).toHaveBeenCalledWith('sess-1', 'pwd\r');

    await act(async () => {
      emit('/tmp/project\r\n$ ');
      await firstRun;
    });
  });

  it('marks the exec entry as errored when sending the command fails', async () => {
    const { stream, send } = createStream();
    send.mockImplementation(() => {
      throw new Error('send failed');
    });
    const { result } = renderHook(() => useExec('sess-1', stream));

    await act(async () => {
      await result.current.run('pwd');
    });

    expect(result.current.isRunning).toBe(false);
    expect(result.current.history).toHaveLength(1);
    expect(result.current.history[0]).toMatchObject({
      command: 'pwd',
      output: '',
      status: 'error',
    });
  });
});
