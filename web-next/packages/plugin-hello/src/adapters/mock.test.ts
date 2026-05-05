import { describe, it, expect } from 'vitest';
import { createMockHelloService } from './mock';

describe('mock hello service', () => {
  it('returns a seeded list of greetings', async () => {
    const svc = createMockHelloService();
    const greetings = await svc.listGreetings();
    expect(greetings.length).toBeGreaterThan(0);
    expect(greetings[0]).toHaveProperty('id');
    expect(greetings[0]).toHaveProperty('mood');
  });
});
