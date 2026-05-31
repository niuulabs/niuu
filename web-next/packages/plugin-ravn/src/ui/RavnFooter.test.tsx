import { describe, it, expect, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { RavnFooter } from './RavnFooter';
import {
  createMockRavenStream,
  createMockSessionStream,
  createMockPersonaStore,
  createMockTriggerStore,
  createMockBudgetStream,
} from '../adapters/mock';
import { wrapWithServices } from '../testing/wrapWithRavn';
import type { IRavenStream, ISessionStream } from '../ports';

function services() {
  return {
    'ravn.personas': createMockPersonaStore(),
    'ravn.ravens': createMockRavenStream(),
    'ravn.sessions': createMockSessionStream(),
    'ravn.triggers': createMockTriggerStore(),
    'ravn.budget': createMockBudgetStream(),
  };
}

function servicesWithState({
  ravens,
  sessions,
}: {
  ravens: Awaited<ReturnType<IRavenStream['listRavens']>>;
  sessions: Awaited<ReturnType<ISessionStream['listSessions']>>;
}) {
  return {
    'ravn.personas': createMockPersonaStore(),
    'ravn.ravens': {
      async listRavens() {
        return ravens;
      },
      async getRaven(id: string) {
        const raven = ravens.find((item) => item.id === id);
        if (!raven) throw new Error(`Ravn not found: ${id}`);
        return raven;
      },
    } satisfies IRavenStream,
    'ravn.sessions': {
      async listSessions() {
        return sessions;
      },
      async getSession(id: string) {
        const session = sessions.find((item) => item.id === id);
        if (!session) throw new Error(`Session not found: ${id}`);
        return session;
      },
      async getMessages() {
        return [];
      },
    } satisfies ISessionStream,
    'ravn.triggers': createMockTriggerStore(),
    'ravn.budget': createMockBudgetStream(),
  };
}

beforeEach(() => {
  localStorage.clear();
});

describe('RavnFooter', () => {
  it('renders the footer container', () => {
    render(<RavnFooter />, { wrapper: wrapWithServices(services()) });
    expect(screen.getByTestId('ravn-footer')).toBeInTheDocument();
  });

  it('shows ravens count', async () => {
    render(<RavnFooter />, { wrapper: wrapWithServices(services()) });
    await waitFor(() => {
      const chip = screen.getByTestId('footer-chip-ravens');
      expect(chip.textContent).toContain('ravens');
      expect(chip.textContent).toMatch(/\d+\/\d+/);
    });
  });

  it('shows sessions count', async () => {
    render(<RavnFooter />, { wrapper: wrapWithServices(services()) });
    await waitFor(() => {
      const chip = screen.getByTestId('footer-chip-sessions');
      expect(chip.textContent).toContain('active');
    });
  });

  it('shows warning states when there are no active ravns or running sessions', async () => {
    render(<RavnFooter />, {
      wrapper: wrapWithServices(
        servicesWithState({
          ravens: [],
          sessions: [],
        }),
      ),
    });

    await waitFor(() => {
      expect(
        screen.getByTestId('footer-chip-ravens').querySelector('[data-state]'),
      ).toHaveAttribute('data-state', 'warn');
      expect(
        screen.getByTestId('footer-chip-sessions').querySelector('[data-state]'),
      ).toHaveAttribute('data-state', 'warn');
    });
  });

  it('shows ok states when active ravns and running sessions exist', async () => {
    render(<RavnFooter />, {
      wrapper: wrapWithServices(
        servicesWithState({
          ravens: [
            {
              id: 'ravn-1',
              name: 'Ravn One',
              status: 'active',
              persona: 'mimir-warden',
              model: 'gpt-5.5',
              queueDepth: 1,
              lastBeatAt: '2026-01-01T00:00:00Z',
            },
          ],
          sessions: [
            {
              id: 'session-1',
              ravenId: 'ravn-1',
              title: 'Dream cycle',
              status: 'running',
              startedAt: '2026-01-01T00:00:00Z',
              updatedAt: '2026-01-01T00:00:00Z',
            },
          ],
        }),
      ),
    });

    await waitFor(() => {
      expect(
        screen.getByTestId('footer-chip-ravens').querySelector('[data-state]'),
      ).toHaveAttribute('data-state', 'ok');
      expect(
        screen.getByTestId('footer-chip-sessions').querySelector('[data-state]'),
      ).toHaveAttribute('data-state', 'ok');
    });
  });
});
