import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, waitFor, act, fireEvent } from '@testing-library/react';
import { PersonasPage } from './PersonasPage';
import {
  createMockPersonaStore,
  createMockRavenStream,
  createMockTriggerStore,
  createMockSessionStream,
  createMockBudgetStream,
} from '../adapters/mock';
import { wrapWithServices } from '../testing/wrapWithRavn';

function allServices() {
  return {
    'ravn.personas': createMockPersonaStore(),
    'ravn.ravens': createMockRavenStream(),
    'ravn.sessions': createMockSessionStream(),
    'ravn.triggers': createMockTriggerStore(),
    'ravn.budget': createMockBudgetStream(),
  };
}

beforeEach(() => {
  localStorage.clear();
  vi.restoreAllMocks();
});

describe('PersonasPage', () => {
  it('renders the personas-page container and in-page sidebar', async () => {
    render(<PersonasPage />, { wrapper: wrapWithServices(allServices()) });
    expect(screen.getByTestId('personas-page')).toBeInTheDocument();

    await waitFor(() => expect(screen.getByTestId('personas-sidebar')).toBeInTheDocument());
    expect(screen.getByTestId('personas-directory')).toBeInTheDocument();
  });

  it('selects reviewer by default and shows the detail pane immediately', async () => {
    render(<PersonasPage />, { wrapper: wrapWithServices(allServices()) });

    await waitFor(() => expect(screen.getByTestId('persona-detail')).toBeInTheDocument());
    expect(screen.getByText('reviewer')).toBeInTheDocument();
    expect(localStorage.getItem('ravn.persona')).toBe('"reviewer"');
  });

  it('restores selected persona from localStorage on mount', async () => {
    localStorage.setItem('ravn.persona', '"architect"');
    render(<PersonasPage />, { wrapper: wrapWithServices(allServices()) });

    await waitFor(() => expect(screen.getByTestId('persona-detail')).toBeInTheDocument());
    expect(screen.getByRole('button', { name: /architect/i })).toHaveAttribute(
      'aria-current',
      'page',
    );
  });

  it('changes selection when a persona is clicked in the left rail', async () => {
    render(<PersonasPage />, { wrapper: wrapWithServices(allServices()) });

    await waitFor(() => expect(screen.getByTestId('personas-directory')).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: /coder/i }));

    await waitFor(() => expect(screen.getByTestId('persona-detail')).toBeInTheDocument());
    expect(localStorage.getItem('ravn.persona')).toBe('"coder"');
    expect(screen.getByRole('tab', { name: /form/i })).toHaveAttribute('aria-selected', 'true');
  });

  it('shows persona detail when ravn:persona-selected event is dispatched', async () => {
    render(<PersonasPage />, { wrapper: wrapWithServices(allServices()) });

    await waitFor(() => expect(screen.getByTestId('persona-detail')).toBeInTheDocument());

    act(() => {
      window.dispatchEvent(new CustomEvent('ravn:persona-selected', { detail: 'coder' }));
    });

    await waitFor(() => {
      expect(localStorage.getItem('ravn.persona')).toBe('"coder"');
      expect(screen.getByRole('button', { name: /coder/i })).toHaveAttribute(
        'aria-current',
        'page',
      );
    });
  });

  it('collapses and expands the sidebar with the shared rail pattern', async () => {
    render(<PersonasPage />, { wrapper: wrapWithServices(allServices()) });

    await waitFor(() => expect(screen.getByTestId('personas-sidebar')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: /collapse personas sidebar/i }));
    expect(screen.getByRole('button', { name: /expand personas sidebar/i })).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /expand personas sidebar/i }));
    expect(screen.getByRole('button', { name: /collapse personas sidebar/i })).toBeInTheDocument();
  });

  it('removes event listener on unmount', () => {
    const { unmount } = render(<PersonasPage />, { wrapper: wrapWithServices(allServices()) });
    unmount();

    expect(() => {
      window.dispatchEvent(new CustomEvent('ravn:persona-selected', { detail: 'coder' }));
    }).not.toThrow();
  });

  it('creates a new persona from the header action', async () => {
    vi.spyOn(window, 'prompt').mockReturnValue('streaming-reviewer');
    render(<PersonasPage />, { wrapper: wrapWithServices(allServices()) });

    await waitFor(() => expect(screen.getByRole('tab', { name: /form/i })).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: /\+ new persona/i }));

    await waitFor(() => {
      expect(screen.getByText('streaming-reviewer')).toBeInTheDocument();
      expect(localStorage.getItem('ravn.persona')).toBe('"streaming-reviewer"');
    });
  });

  it('forks the selected persona from the header action', async () => {
    vi.spyOn(window, 'prompt').mockReturnValue('reviewer-copy');
    render(<PersonasPage />, { wrapper: wrapWithServices(allServices()) });

    await waitFor(() => expect(screen.getByRole('tab', { name: /form/i })).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: /clone as…/i }));

    await waitFor(() => {
      expect(screen.getByText('reviewer-copy')).toBeInTheDocument();
      expect(localStorage.getItem('ravn.persona')).toBe('"reviewer-copy"');
    });
  });
});
