import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { ConnectionLegend } from './ConnectionLegend';

describe('ConnectionLegend', () => {
  it('renders all 5 edge kind entries', () => {
    render(<ConnectionLegend />);
    expect(screen.getAllByRole('listitem')).toHaveLength(5);
  });

  it('renders each edge kind with a data-kind attribute', () => {
    render(<ConnectionLegend />);
    expect(screen.getByTestId('legend-solid')).toBeInTheDocument();
    expect(screen.getByTestId('legend-dashed-anim')).toBeInTheDocument();
    expect(screen.getByTestId('legend-dashed-long')).toBeInTheDocument();
    expect(screen.getByTestId('legend-soft')).toBeInTheDocument();
    expect(screen.getByTestId('legend-raid')).toBeInTheDocument();
  });

  it('has accessible list role with label', () => {
    render(<ConnectionLegend />);
    expect(screen.getByRole('list', { name: /connection types/i })).toBeInTheDocument();
  });

  it('renders web2-matching label text for each edge kind', () => {
    render(<ConnectionLegend />);
    expect(screen.getByText('Týr → Völundr')).toBeInTheDocument();
    expect(screen.getByText('Týr ⇝ raid coord')).toBeInTheDocument();
    expect(screen.getByText('Bifröst → ext. model')).toBeInTheDocument();
    expect(screen.getByText('ravn → Mímir')).toBeInTheDocument();
    expect(screen.getByText('raid cohesion')).toBeInTheDocument();
  });

  it('renders an SVG swatch for each edge kind', () => {
    const { container } = render(<ConnectionLegend />);
    const svgs = container.querySelectorAll('svg.obs-conn-legend__line-svg');
    expect(svgs).toHaveLength(5);
  });

  it('renders SVG markup correctly for animated kind', () => {
    render(<ConnectionLegend />);
    const animItem = screen.getByTestId('legend-dashed-anim');
    expect(animItem.querySelector('animate')).toBeInTheDocument();
  });

  it('renders raid kind with circles', () => {
    render(<ConnectionLegend />);
    const raidItem = screen.getByTestId('legend-raid');
    expect(raidItem.querySelector('circle')).toBeInTheDocument();
    expect(raidItem.querySelector('g')).toBeInTheDocument();
  });

  it('renders solid kind with a plain line', () => {
    render(<ConnectionLegend />);
    const solidItem = screen.getByTestId('legend-solid');
    expect(solidItem.querySelector('line')).toBeInTheDocument();
  });
});
