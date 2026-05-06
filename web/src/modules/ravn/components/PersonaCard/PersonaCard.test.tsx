import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { PersonaCard } from './PersonaCard';
import type { PersonaSummary } from '../../api/types';

function mkPersona(overrides: Partial<PersonaSummary> = {}): PersonaSummary {
  return {
    name: 'coding-agent',
    permissionMode: 'workspace-write',
    allowedTools: ['file', 'git', 'terminal'],
    iterationBudget: 40,
    isBuiltin: true,
    hasOverride: false,
    producesEvent: '',
    consumesEvents: [],
    ...overrides,
  };
}

function wrap(element: React.ReactElement) {
  return render(<MemoryRouter>{element}</MemoryRouter>);
}

describe('PersonaCard', () => {
  it('renders persona name', () => {
    wrap(<PersonaCard persona={mkPersona()} />);
    expect(screen.getByText('coding-agent')).toBeInTheDocument();
  });

  it('renders built-in badge for built-in persona', () => {
    wrap(<PersonaCard persona={mkPersona({ isBuiltin: true })} />);
    expect(screen.getByText('built-in')).toBeInTheDocument();
  });

  it('does not render built-in badge for custom persona', () => {
    wrap(<PersonaCard persona={mkPersona({ isBuiltin: false })} />);
    expect(screen.queryByText('built-in')).not.toBeInTheDocument();
  });

  it('renders override badge when persona has override', () => {
    wrap(<PersonaCard persona={mkPersona({ isBuiltin: true, hasOverride: true })} />);
    expect(screen.getByText('override')).toBeInTheDocument();
  });

  it('renders permission mode', () => {
    wrap(<PersonaCard persona={mkPersona({ permissionMode: 'read-only' })} />);
    expect(screen.getByText('read-only')).toBeInTheDocument();
  });

  it('renders iteration budget', () => {
    wrap(<PersonaCard persona={mkPersona({ iterationBudget: 40 })} />);
    expect(screen.getByText('40')).toBeInTheDocument();
  });

  it('does not render budget when 0', () => {
    wrap(<PersonaCard persona={mkPersona({ iterationBudget: 0 })} />);
    expect(screen.queryByText('0')).not.toBeInTheDocument();
  });

  it('renders tool badges', () => {
    wrap(<PersonaCard persona={mkPersona({ allowedTools: ['file', 'git', 'terminal'] })} />);
    expect(screen.getByText('file')).toBeInTheDocument();
    expect(screen.getByText('git')).toBeInTheDocument();
    expect(screen.getByText('terminal')).toBeInTheDocument();
  });

  it('shows +N for extra tools beyond 4', () => {
    wrap(
      <PersonaCard
        persona={mkPersona({ allowedTools: ['file', 'git', 'terminal', 'web', 'todo'] })}
      />
    );
    expect(screen.getByText('+1')).toBeInTheDocument();
  });

  it('renders produces event', () => {
    wrap(<PersonaCard persona={mkPersona({ producesEvent: 'review.completed' })} />);
    expect(screen.getByText('review.completed')).toBeInTheDocument();
  });

  it('does not render produces when empty', () => {
    wrap(<PersonaCard persona={mkPersona({ producesEvent: '' })} />);
    expect(screen.queryByText('produces')).not.toBeInTheDocument();
  });

  it('renders link pointing to persona detail page', () => {
    wrap(<PersonaCard persona={mkPersona({ name: 'my-agent' })} />);
    const link = screen.getByRole('link');
    expect(link).toHaveAttribute('href', '/ravn/personas/my-agent');
  });

  it('shows dash for empty permission mode', () => {
    wrap(<PersonaCard persona={mkPersona({ permissionMode: '' })} />);
    expect(screen.getByText('—')).toBeInTheDocument();
  });
});
