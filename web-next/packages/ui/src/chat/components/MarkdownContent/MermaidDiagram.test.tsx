import { Blob as NodeBlob } from 'node:buffer';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { MermaidDiagram } from './MermaidDiagram';
const mocks = vi.hoisted(() => ({ render: vi.fn(), initialize: vi.fn() }));
vi.mock('mermaid', () => ({ default: mocks }));
let created: Blob[];
beforeEach(() => {
  created = [];
  mocks.render.mockReset().mockResolvedValue({
    svg: '<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script><foreignObject>bad</foreignObject><text>Safe diagram</text></svg>',
  });
  mocks.initialize.mockClear();
  vi.stubGlobal('Blob', NodeBlob);
  vi.spyOn(URL, 'createObjectURL').mockImplementation((blob) => {
    created.push(blob as Blob);
    return 'blob:diagram';
  });
  vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => {});
});
afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe('Mermaid preview', () => {
  it('renders with strict security, sanitizes SVG and releases its image when closed', async () => {
    const { unmount } = render(<MermaidDiagram source="graph LR; A-->B" />);
    await screen.findByRole('img', { name: 'Mermaid diagram' });
    expect(mocks.initialize).toHaveBeenCalledWith(
      expect.objectContaining({ securityLevel: 'strict' }),
    );
    const svg = await created[0]!.text();
    expect(svg).toContain('Safe diagram');
    expect(svg).not.toMatch(/script|foreignObject/);
    fireEvent.click(screen.getByRole('button', { name: 'Expand diagram' }));
    expect(screen.getByRole('dialog', { name: 'Diagram preview' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Close', exact: true }));
    fireEvent.click(screen.getByRole('button', { name: 'Source', exact: true }));
    expect(screen.getByTestId('code-block')).toHaveTextContent('graph LR; A-->B');
    unmount();
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:diagram');
  });
  it('keeps incomplete streaming diagrams as source and exposes parse failures', async () => {
    const { rerender } = render(<MermaidDiagram source="graph LR;" isStreaming />);
    expect(screen.getByTestId('code-block')).toBeInTheDocument();
    expect(mocks.render).not.toHaveBeenCalled();
    mocks.render.mockRejectedValueOnce(new Error('Parse failed'));
    rerender(<MermaidDiagram source="graph LR; invalid" />);
    await screen.findByText(/Diagram preview unavailable/);
    expect(screen.getByTestId('code-block')).toHaveTextContent('invalid');
  });
  it('does not publish a stale render after the source changes', async () => {
    let finish!: (value: { svg: string }) => void;
    mocks.render.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    );
    const { rerender } = render(<MermaidDiagram source="graph LR; A-->B" />);
    await waitFor(() => expect(mocks.render).toHaveBeenCalled());
    rerender(<MermaidDiagram source="graph LR; C-->D" />);
    finish({ svg: '<svg><text>Stale</text></svg>' });
    await screen.findByRole('img', { name: 'Mermaid diagram' });
    expect(created).toHaveLength(1);
    expect(await created[0]!.text()).not.toContain('Stale');
  });
});
