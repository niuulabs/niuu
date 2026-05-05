import { describe, it, expect, vi } from 'vitest';
import { buildHelloHttpAdapter } from './http';
import type { IHelloService } from '../ports';

const greetings = [
  { id: '1', text: 'hello via HTTP', mood: 'warm' as const },
  { id: '2', text: 'second greeting', mood: 'cold' as const },
];

describe('buildHelloHttpAdapter', () => {
  it('calls GET /greetings on the provided client', async () => {
    const client = { get: vi.fn().mockResolvedValue(greetings) };
    await buildHelloHttpAdapter(client).listGreetings();
    expect(client.get).toHaveBeenCalledWith('/greetings');
  });

  it('returns the response as-is from the client', async () => {
    const client = { get: vi.fn().mockResolvedValue(greetings) };
    const result = await buildHelloHttpAdapter(client).listGreetings();
    expect(result).toEqual(greetings);
  });

  it('propagates errors from the HTTP client', async () => {
    const client = { get: vi.fn().mockRejectedValue(new Error('network error')) };
    await expect(buildHelloHttpAdapter(client).listGreetings()).rejects.toThrow('network error');
  });

  it('satisfies IHelloService interface', () => {
    const client = { get: vi.fn() };
    const service: IHelloService = buildHelloHttpAdapter(client);
    expect(typeof service.listGreetings).toBe('function');
  });
});
