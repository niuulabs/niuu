import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { personaRoleSchema } from '@niuulabs/domain';
import { PersonaAvatar, PERSONA_ROLE_SHAPE } from './PersonaAvatar';

const ALL_ROLES = personaRoleSchema.options;

describe('PERSONA_ROLE_SHAPE', () => {
  it('maps every role to a shape (deterministic)', () => {
    const shapes = ALL_ROLES.map((role) => PERSONA_ROLE_SHAPE[role]);
    // All roles must have a shape defined
    expect(shapes).toHaveLength(ALL_ROLES.length);
    shapes.forEach((shape) => expect(shape).toBeTruthy());
  });

  it('returns the same shape for the same role on repeated calls', () => {
    for (const role of ALL_ROLES) {
      expect(PERSONA_ROLE_SHAPE[role]).toBe(PERSONA_ROLE_SHAPE[role]);
    }
  });

  it('uses at most 9 distinct shapes across all roles', () => {
    const unique = new Set(ALL_ROLES.map((r) => PERSONA_ROLE_SHAPE[r]));
    // 9 shape primitives, some reused across related roles
    expect(unique.size).toBeLessThanOrEqual(9);
    expect(unique.size).toBeGreaterThan(0);
  });
});

describe('PersonaAvatar', () => {
  it('renders the role as aria-label', () => {
    render(<PersonaAvatar role="plan" letter="P" />);
    expect(screen.getByLabelText('plan persona')).toBeInTheDocument();
  });

  it('renders the letter uppercased inside the avatar', () => {
    render(<PersonaAvatar role="build" letter="b" />);
    // The letter span is aria-hidden; query by title fallback
    const el = screen.getByTitle('build · B');
    expect(el).toBeInTheDocument();
  });

  it('uses the first character only from multi-char letter prop', () => {
    render(<PersonaAvatar role="review" letter="AB" />);
    expect(screen.getByTitle('review · A')).toBeInTheDocument();
  });

  it('accepts a custom title', () => {
    render(<PersonaAvatar role="gate" letter="G" title="custom title" />);
    expect(screen.getByTitle('custom title')).toBeInTheDocument();
  });

  it('accepts a custom className', () => {
    render(<PersonaAvatar role="audit" letter="A" className="extra" />);
    expect(screen.getByLabelText('audit persona')).toHaveClass('niuu-persona-av', 'extra');
  });

  it.each(ALL_ROLES)('renders without error for role "%s"', (role) => {
    const { container } = render(<PersonaAvatar role={role} letter="X" />);
    expect(container.querySelector('.niuu-persona-av')).toBeInTheDocument();
  });

  it('renders the SVG shape element', () => {
    const { container } = render(<PersonaAvatar role="ship" letter="S" />);
    expect(container.querySelector('svg')).toBeInTheDocument();
  });

  it('applies the correct size', () => {
    render(<PersonaAvatar role="index" letter="I" size={40} />);
    const el = screen.getByLabelText('index persona');
    expect(el).toHaveStyle({ width: '40px', height: '40px' });
  });
});
