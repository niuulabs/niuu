import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { personaRoleSchema } from '@niuulabs/domain';
import { RavnAvatar } from './RavnAvatar';

const ALL_ROLES = personaRoleSchema.options;

describe('RavnAvatar', () => {
  it('renders with correct aria-label', () => {
    render(<RavnAvatar role="plan" rune="ᚱ" state="idle" />);
    expect(screen.getByLabelText('ravn plan idle')).toBeInTheDocument();
  });

  it('renders the rune character', () => {
    render(<RavnAvatar role="build" rune="ᚱ" state="running" />);
    // aria-hidden span — query by title
    expect(screen.getByTitle('ᚱ · build · running')).toBeInTheDocument();
  });

  it('renders the state dot', () => {
    const { container } = render(<RavnAvatar role="review" rune="ᛗ" state="failed" />);
    expect(container.querySelector('.niuu-state-dot')).toBeInTheDocument();
    expect(container.querySelector('.niuu-state-dot--failed')).toBeInTheDocument();
  });

  it('passes pulse to the state dot', () => {
    const { container } = render(<RavnAvatar role="ship" rune="ᚱ" state="running" pulse />);
    expect(container.querySelector('.niuu-state-dot--pulse')).toBeInTheDocument();
  });

  it('applies no pulse class when pulse is false', () => {
    const { container } = render(<RavnAvatar role="gate" rune="ᚱ" state="idle" pulse={false} />);
    expect(container.querySelector('.niuu-state-dot--pulse')).not.toBeInTheDocument();
  });

  it('renders the SVG shape', () => {
    const { container } = render(<RavnAvatar role="audit" rune="ᚱ" state="healthy" />);
    expect(container.querySelector('svg')).toBeInTheDocument();
  });

  it('accepts a custom title', () => {
    render(<RavnAvatar role="index" rune="ᛗ" state="observing" title="my ravn" />);
    expect(screen.getByTitle('my ravn')).toBeInTheDocument();
  });

  it('accepts a custom className', () => {
    render(<RavnAvatar role="report" rune="ᚱ" state="idle" className="extra" />);
    expect(screen.getByLabelText('ravn report idle')).toHaveClass('niuu-ravn-av', 'extra');
  });

  it('applies the correct size', () => {
    render(<RavnAvatar role="verify" rune="ᚱ" state="idle" size={40} />);
    const el = screen.getByLabelText('ravn verify idle');
    expect(el).toHaveStyle({ width: '40px', height: '40px' });
  });

  it.each(ALL_ROLES)('renders without error for role "%s"', (role) => {
    const { container } = render(<RavnAvatar role={role} rune="ᚱ" state="idle" />);
    expect(container.querySelector('.niuu-ravn-av')).toBeInTheDocument();
  });
});
