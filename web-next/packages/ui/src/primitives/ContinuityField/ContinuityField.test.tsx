import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { ContinuityField } from './ContinuityField';
import { BRANCHES, COMPANION_LINES, TRANSITIONS } from './continuityFieldPaths';

describe('ContinuityField', () => {
  it('is decorative', () => {
    render(<ContinuityField />);
    expect(screen.getByTestId('continuity-field')).toHaveAttribute('aria-hidden', 'true');
  });

  it('defaults to the centred composition', () => {
    render(<ContinuityField />);
    expect(screen.getByTestId('continuity-field')).not.toHaveClass('niuu-continuity--hero');
  });

  it('clears the left for a claim in the hero composition', () => {
    render(<ContinuityField composition="hero" />);
    expect(screen.getByTestId('continuity-field')).toHaveClass('niuu-continuity--hero');
  });

  it('merges a custom className', () => {
    render(<ContinuityField className="extra" />);
    expect(screen.getByTestId('continuity-field')).toHaveClass('niuu-continuity', 'extra');
  });

  it('draws one bright path over its companion lines', () => {
    const { container } = render(<ContinuityField />);
    const mainPathLayers = 3;
    expect(container.querySelectorAll('g path')).toHaveLength(
      COMPANION_LINES.length + BRANCHES.length + mainPathLayers,
    );
  });

  it('marks every transition and where each branch starts', () => {
    const { container } = render(<ContinuityField />);
    const circlesPerTransition = 2;
    expect(container.querySelectorAll('circle')).toHaveLength(
      BRANCHES.length + TRANSITIONS.length * circlesPerTransition,
    );
  });

  it('takes every colour from a token', () => {
    const { container } = render(<ContinuityField />);
    const hardCoded = [...container.querySelectorAll('path, circle')].flatMap((node) =>
      [node.getAttribute('stroke'), node.getAttribute('fill')].filter((colour) =>
        colour?.startsWith('#'),
      ),
    );
    expect(hardCoded).toEqual([]);
  });

  it('does not animate', () => {
    const { container } = render(<ContinuityField />);
    expect(container.querySelector('animate, animateTransform, animateMotion')).toBeNull();
  });
});
