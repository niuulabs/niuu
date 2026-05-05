import type { Greeting, IHelloService } from '../ports';

const seed: Greeting[] = [
  { id: '1', text: 'hello from the mock adapter', mood: 'warm' },
  { id: '2', text: 'plugin loaded through the shell', mood: 'curious' },
  { id: '3', text: 'ice-themed and composable', mood: 'cold' },
];

export function createMockHelloService(): IHelloService {
  return {
    async listGreetings() {
      await new Promise((r) => setTimeout(r, 200));
      return seed;
    },
  };
}
