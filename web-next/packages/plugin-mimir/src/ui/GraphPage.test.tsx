import { describe, it, expect } from 'vitest';
import { screen, fireEvent, waitFor } from '@testing-library/react';
import { GraphPage, layoutCategoryRadial } from './GraphPage';
import { createMimirMockAdapter } from '../adapters/mock';
import type { IMimirService } from '../ports';
import { renderWithMimir } from '../testing/renderWithMimir';
import type { GraphNode } from '../domain/api-types';

const wrap = renderWithMimir;

describe('GraphPage', () => {
  it('shows loading state initially', () => {
    wrap(<GraphPage />);
    expect(screen.getByText(/loading graph/)).toBeInTheDocument();
  });

  it('renders the SVG graph canvas', async () => {
    wrap(<GraphPage />);
    await waitFor(() =>
      expect(screen.getByRole('img', { name: /knowledge graph/i })).toBeInTheDocument(),
    );
  });

  it('renders graph legend with category label', async () => {
    wrap(<GraphPage />);
    await waitFor(() => screen.getByRole('img', { name: /knowledge graph/i }));
    expect(screen.getByLabelText(/graph legend/i)).toBeInTheDocument();
    expect(screen.getByText('Category')).toBeInTheDocument();
  });

  it('renders graph info card with counts', async () => {
    wrap(<GraphPage />);
    await waitFor(() => screen.getByTestId('graph-info'));
    expect(screen.getByText(/pages/)).toBeInTheDocument();
    expect(screen.getByText(/edges/)).toBeInTheDocument();
  });

  it('SVG contains glow filter definition', async () => {
    wrap(<GraphPage />);
    await waitFor(() => screen.getByRole('img', { name: /knowledge graph/i }));
    const svg = screen.getByRole('img', { name: /knowledge graph/i });
    expect(svg.querySelector('filter#niuu-node-glow')).toBeInTheDocument();
  });

  it('unfocused edges have low-opacity class', async () => {
    wrap(<GraphPage />);
    await waitFor(() => screen.getByRole('img', { name: /knowledge graph/i }));
    const svg = screen.getByRole('img', { name: /knowledge graph/i });
    const lines = svg.querySelectorAll('line');
    expect(lines.length).toBeGreaterThan(0);
    expect(lines[0]!.classList.toString()).toContain('niuu-opacity-15');
  });

  it('clicking a node toggles focus', async () => {
    wrap(<GraphPage />);
    await waitFor(() => screen.getByRole('img', { name: /knowledge graph/i }));
    const svg = screen.getByRole('img', { name: /knowledge graph/i });
    const nodeGroups = svg.querySelectorAll('g[role="button"]');
    expect(nodeGroups.length).toBeGreaterThan(0);
    fireEvent.click(nodeGroups[0]!);
    expect(nodeGroups[0]!.getAttribute('aria-pressed')).toBe('true');
  });

  it('shows error state when graph load fails', async () => {
    const failing: IMimirService = {
      ...createMimirMockAdapter(),
      pages: {
        ...createMimirMockAdapter().pages,
        getGraph: async () => {
          throw new Error('graph service unavailable');
        },
      },
    };
    wrap(<GraphPage />, failing);
    await waitFor(() => expect(screen.getByText('graph service unavailable')).toBeInTheDocument());
  });

  it('legend shows Edges section', async () => {
    wrap(<GraphPage />);
    await waitFor(() => screen.getByRole('img', { name: /knowledge graph/i }));
    expect(screen.getByText('Edges')).toBeInTheDocument();
    expect(screen.getByText('shared source')).toBeInTheDocument();
    expect(screen.getByText('wikilink')).toBeInTheDocument();
  });
});

describe('layoutCategoryRadial', () => {
  it('returns empty array for empty input', () => {
    expect(layoutCategoryRadial([])).toEqual([]);
  });

  it('returns single node at center', () => {
    const nodes: GraphNode[] = [{ id: 'n1', title: 'Node 1', category: 'a' }];
    const result = layoutCategoryRadial(nodes);
    expect(result).toHaveLength(1);
    expect(result[0]!.x).toBe(550);
    expect(result[0]!.y).toBe(375);
  });

  it('positions nodes for multiple categories separately', () => {
    const nodes: GraphNode[] = [
      { id: 'a1', title: 'A1', category: 'alpha' },
      { id: 'a2', title: 'A2', category: 'alpha' },
      { id: 'b1', title: 'B1', category: 'beta' },
    ];
    const result = layoutCategoryRadial(nodes);
    expect(result).toHaveLength(3);

    for (const pos of result) {
      expect(pos.x).toBeGreaterThan(0);
      expect(pos.x).toBeLessThan(1100);
      expect(pos.y).toBeGreaterThan(0);
      expect(pos.y).toBeLessThan(750);
    }
  });

  it('produces deterministic output for the same input', () => {
    const nodes: GraphNode[] = [
      { id: 'n1', title: 'Node 1', category: 'cat-a' },
      { id: 'n2', title: 'Node 2', category: 'cat-b' },
      { id: 'n3', title: 'Node 3', category: 'cat-a' },
    ];
    const r1 = layoutCategoryRadial(nodes);
    const r2 = layoutCategoryRadial(nodes);
    expect(r1).toEqual(r2);
  });

  it('respects custom center and radius parameters', () => {
    const nodes: GraphNode[] = [
      { id: 'n1', title: 'A', category: 'x' },
      { id: 'n2', title: 'B', category: 'y' },
    ];
    const result = layoutCategoryRadial(nodes, 100, 100, 50);
    for (const pos of result) {
      const dx = pos.x - 100;
      const dy = pos.y - 100;
      const dist = Math.sqrt(dx * dx + dy * dy);
      expect(dist).toBeLessThan(50 + 1);
    }
  });

  it('nodes in same category are clustered in same angular sector', () => {
    const nodes: GraphNode[] = [
      { id: 'a1', title: 'A1', category: 'alpha' },
      { id: 'a2', title: 'A2', category: 'alpha' },
      { id: 'a3', title: 'A3', category: 'alpha' },
      { id: 'b1', title: 'B1', category: 'beta' },
      { id: 'b2', title: 'B2', category: 'beta' },
      { id: 'b3', title: 'B3', category: 'beta' },
    ];
    const result = layoutCategoryRadial(nodes);
    const byId = new Map(result.map((p) => [p.node.id, p]));

    const angleOf = (id: string) => {
      const p = byId.get(id)!;
      return Math.atan2(p.y - 375, p.x - 550);
    };

    const alphaAngles = ['a1', 'a2', 'a3'].map(angleOf);
    const betaAngles = ['b1', 'b2', 'b3'].map(angleOf);

    const avgAlpha = alphaAngles.reduce((s, a) => s + a, 0) / alphaAngles.length;
    const avgBeta = betaAngles.reduce((s, a) => s + a, 0) / betaAngles.length;

    const separation = Math.abs(avgAlpha - avgBeta);
    expect(separation).toBeGreaterThan(0.3);
  });
});
