import { describe, expect, it } from 'vitest';
import { remarkLinkifyLocalhost } from './remarkLinkifyLocalhost';

interface Node {
  type: string;
  value?: string;
  url?: string;
  children?: Node[];
}

function run(children: Node[]): Node[] {
  const tree: Node = { type: 'root', children: [{ type: 'paragraph', children }] };
  remarkLinkifyLocalhost()(tree);
  return tree.children![0]!.children!;
}

describe('remarkLinkifyLocalhost', () => {
  it('links bare loopback addresses in prose, keeping the sentence around them', () => {
    expect(
      run([{ type: 'text', value: 'Vite is on localhost:5173/docs. Also 127.0.0.1:8000' }]),
    ).toEqual([
      { type: 'text', value: 'Vite is on ' },
      {
        type: 'link',
        url: 'http://localhost:5173/docs',
        children: [{ type: 'text', value: 'localhost:5173/docs' }],
      },
      { type: 'text', value: '. Also ' },
      {
        type: 'link',
        url: 'http://127.0.0.1:8000',
        children: [{ type: 'text', value: '127.0.0.1:8000' }],
      },
    ]);
  });

  it('links a backticked address and leaves other code, links and hosts alone', () => {
    const code = { type: 'inlineCode', value: '0.0.0.0:3000' };
    expect(run([code])).toEqual([{ type: 'link', url: 'http://0.0.0.0:3000', children: [code] }]);
    expect(run([{ type: 'inlineCode', value: 'npm run dev' }])).toEqual([
      { type: 'inlineCode', value: 'npm run dev' },
    ]);
    const link = {
      type: 'link',
      url: 'http://localhost:5173',
      children: [{ type: 'text', value: 'localhost:5173' }],
    };
    expect(run([link])).toEqual([link]);
    expect(run([{ type: 'text', value: 'see example.com:80 or mylocalhost:80' }])).toEqual([
      { type: 'text', value: 'see example.com:80 or mylocalhost:80' },
    ]);
  });

  it('keeps the scheme an address already has and walks nested nodes', () => {
    const tree: Node = {
      type: 'root',
      children: [
        { type: 'emphasis', children: [{ type: 'inlineCode', value: 'https://localhost:8443/' }] },
      ],
    };
    remarkLinkifyLocalhost()(tree);
    expect(tree.children![0]!.children![0]!.url).toBe('https://localhost:8443/');
  });
});
