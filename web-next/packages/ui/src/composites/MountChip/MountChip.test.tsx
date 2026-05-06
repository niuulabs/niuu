import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MountChip, MOUNT_KIND_GLYPH } from './MountChip';
import type { MountChipRole } from './MountChip';

const BINDING_ROLES: MountChipRole[] = ['primary', 'archive', 'ro'];
const KIND_ROLES: MountChipRole[] = ['local', 'shared', 'domain'];

describe('MountChip', () => {
  describe('binding roles (primary / archive / ro)', () => {
    it.each(BINDING_ROLES)('renders role "%s" with a dot indicator', (role) => {
      const { container } = render(<MountChip name="volundr-local" role={role} />);
      expect(container.querySelector('.niuu-mount-chip__dot')).toBeInTheDocument();
      expect(container.querySelector('.niuu-mount-chip__glyph')).not.toBeInTheDocument();
    });

    it('shows the mount name', () => {
      render(<MountChip name="my-mount" role="primary" />);
      expect(screen.getByText('my-mount')).toBeInTheDocument();
    });

    it('shows the role label', () => {
      render(<MountChip name="x" role="archive" />);
      expect(screen.getByText('archive')).toBeInTheDocument();
    });

    it('applies the role modifier class', () => {
      const { container } = render(<MountChip name="x" role="ro" />);
      expect(container.firstChild).toHaveClass('niuu-mount-chip--ro');
    });

    it('includes priority in the tooltip when provided', () => {
      render(<MountChip name="x" role="primary" priority={1} />);
      expect(screen.getByTitle('x (primary · p1)')).toBeInTheDocument();
    });

    it('omits priority from tooltip when not provided', () => {
      render(<MountChip name="x" role="primary" />);
      expect(screen.getByTitle('x (primary)')).toBeInTheDocument();
    });
  });

  describe('mount-kind roles (local / shared / domain)', () => {
    it.each(KIND_ROLES)('renders role "%s" with the glyph indicator', (role) => {
      const { container } = render(<MountChip name="well" role={role} />);
      expect(container.querySelector('.niuu-mount-chip__glyph')).toBeInTheDocument();
      expect(container.querySelector('.niuu-mount-chip__dot')).not.toBeInTheDocument();
    });

    it.each(KIND_ROLES as ('local' | 'shared' | 'domain')[])(
      'shows the correct glyph for "%s"',
      (role) => {
        render(<MountChip name="well" role={role} />);
        expect(screen.getByText(MOUNT_KIND_GLYPH[role])).toBeInTheDocument();
      },
    );

    it('applies the role modifier class', () => {
      const { container } = render(<MountChip name="well" role="domain" />);
      expect(container.firstChild).toHaveClass('niuu-mount-chip--domain');
    });
  });

  describe('common behaviour', () => {
    it('accepts a custom className', () => {
      const { container } = render(<MountChip name="x" role="primary" className="extra" />);
      expect(container.firstChild).toHaveClass('niuu-mount-chip', 'extra');
    });

    it.each([...BINDING_ROLES, ...KIND_ROLES])('renders without error for role "%s"', (role) => {
      const { container } = render(<MountChip name="test" role={role} />);
      expect(container.querySelector('.niuu-mount-chip')).toBeInTheDocument();
    });
  });
});
