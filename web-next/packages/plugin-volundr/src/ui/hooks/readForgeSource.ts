export async function readForgeSource<T>(
  read: (signal: AbortSignal) => Promise<T>,
  signal: AbortSignal,
  timeoutMs?: number,
) {
  const controller = new AbortController();
  const cancel = () => controller.abort(signal.reason);
  if (signal.aborted) cancel();
  else signal.addEventListener('abort', cancel, { once: true });
  let timedOut = false;
  const timer =
    timeoutMs === undefined
      ? undefined
      : setTimeout(() => {
          timedOut = true;
          controller.abort();
        }, timeoutMs);
  try {
    return await read(controller.signal);
  } catch (error) {
    if (timedOut) {
      throw new Error('Connection timed out. Check this Forge in Guild.', { cause: error });
    }
    throw error;
  } finally {
    clearTimeout(timer);
    signal.removeEventListener('abort', cancel);
  }
}
