import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { FlockToggle } from './FlockToggle';

const personas = [{ name: 'coordinator' }, { name: 'reviewer' }, { name: 'security-auditor' }];

describe('FlockToggle', () => {
  it('renders toggle switch', () => {
    render(
      <FlockToggle
        enabled={false}
        onToggle={vi.fn()}
        personas={personas}
        selectedPersonas={[]}
        onPersonasChange={vi.fn()}
      />
    );
    expect(screen.getByRole('switch', { name: /dispatch as flock/i })).toBeInTheDocument();
  });

  it('shows aria-checked=false when disabled', () => {
    render(
      <FlockToggle
        enabled={false}
        onToggle={vi.fn()}
        personas={personas}
        selectedPersonas={[]}
        onPersonasChange={vi.fn()}
      />
    );
    expect(screen.getByRole('switch')).toHaveAttribute('aria-checked', 'false');
  });

  it('shows aria-checked=true when enabled', () => {
    render(
      <FlockToggle
        enabled={true}
        onToggle={vi.fn()}
        personas={personas}
        selectedPersonas={[]}
        onPersonasChange={vi.fn()}
      />
    );
    expect(screen.getByRole('switch')).toHaveAttribute('aria-checked', 'true');
  });

  it('calls onToggle with negated value on click', () => {
    const onToggle = vi.fn();
    render(
      <FlockToggle
        enabled={false}
        onToggle={onToggle}
        personas={personas}
        selectedPersonas={[]}
        onPersonasChange={vi.fn()}
      />
    );
    fireEvent.click(screen.getByRole('switch'));
    expect(onToggle).toHaveBeenCalledWith(true);
  });

  it('hides persona list when disabled', () => {
    render(
      <FlockToggle
        enabled={false}
        onToggle={vi.fn()}
        personas={personas}
        selectedPersonas={[]}
        onPersonasChange={vi.fn()}
      />
    );
    expect(screen.queryByText('coordinator')).not.toBeInTheDocument();
  });

  it('shows persona chips when enabled', () => {
    render(
      <FlockToggle
        enabled={true}
        onToggle={vi.fn()}
        personas={personas}
        selectedPersonas={[]}
        onPersonasChange={vi.fn()}
      />
    );
    expect(screen.getByText('coordinator')).toBeInTheDocument();
    expect(screen.getByText('reviewer')).toBeInTheDocument();
  });

  it('marks selected personas as pressed', () => {
    render(
      <FlockToggle
        enabled={true}
        onToggle={vi.fn()}
        personas={personas}
        selectedPersonas={['reviewer']}
        onPersonasChange={vi.fn()}
      />
    );
    expect(screen.getByText('reviewer')).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByText('coordinator')).toHaveAttribute('aria-pressed', 'false');
  });

  it('adds persona to selection on click', () => {
    const onPersonasChange = vi.fn();
    render(
      <FlockToggle
        enabled={true}
        onToggle={vi.fn()}
        personas={personas}
        selectedPersonas={[]}
        onPersonasChange={onPersonasChange}
      />
    );
    fireEvent.click(screen.getByText('coordinator'));
    expect(onPersonasChange).toHaveBeenCalledWith(['coordinator']);
  });

  it('removes persona from selection on second click', () => {
    const onPersonasChange = vi.fn();
    render(
      <FlockToggle
        enabled={true}
        onToggle={vi.fn()}
        personas={personas}
        selectedPersonas={['coordinator']}
        onPersonasChange={onPersonasChange}
      />
    );
    fireEvent.click(screen.getByText('coordinator'));
    expect(onPersonasChange).toHaveBeenCalledWith([]);
  });
});
