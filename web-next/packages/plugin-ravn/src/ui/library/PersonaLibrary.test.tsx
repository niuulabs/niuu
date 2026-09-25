import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { mountRavn, ravnServices } from '../../testing/mountRavn';
import { makePersonaDetail, makePersonaSummary, makeRavn } from '../../testing/fixtures';
import { describeFanIn } from './PersonaSheet';

vi.mock('../workbench/LiveChat', () => ({ LiveChat: () => <div data-testid="live-chat" /> }));

beforeEach(() => localStorage.clear());

describe('PersonaLibrary — list', () => {
  it('shows a loading state, then groups personas by family', async () => {
    const services = ravnServices();
    mountRavn('/ravn/personas', services);
    const general = await screen.findByTestId('persona-family-general');
    expect(general).toHaveTextContent('General');
    expect(screen.getByTestId('persona-family-research')).toHaveTextContent('Research2');
    expect(screen.getByTestId('persona-row-research-framer')).toHaveTextContent(
      'emits review.completed',
    );
  });

  it('says why personas could not be loaded', async () => {
    const services = ravnServices();
    services['ravn.personas'].listPersonas = vi
      .fn()
      .mockRejectedValue(Object.assign(new Error('x'), { detail: 'registry offline' }));
    mountRavn('/ravn/personas', services);
    expect(await screen.findByText('registry offline')).toBeInTheDocument();
  });

  it('collapses a family and searches across all of them', async () => {
    mountRavn('/ravn/personas', ravnServices());
    const research = await screen.findByTestId('persona-family-research');
    fireEvent.click(research);
    expect(research).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByTestId('persona-row-research-framer')).not.toBeInTheDocument();
    fireEvent.change(screen.getByTestId('persona-search'), { target: { value: 'framer' } });
    expect(screen.getByTestId('persona-row-research-framer')).toBeInTheDocument();
    fireEvent.change(screen.getByTestId('persona-search'), { target: { value: 'zzz' } });
    expect(screen.getByText('No personas match.')).toBeInTheDocument();
  });

  it('filters to personas in use and to custom ones', async () => {
    mountRavn('/ravn/personas', ravnServices({ ravens: [makeRavn()] }));
    fireEvent.click(await screen.findByTestId('persona-filter-in-use'));
    expect(screen.getByTestId('persona-row-reviewer')).toHaveTextContent('● 1');
    expect(screen.queryByTestId('persona-row-research-framer')).not.toBeInTheDocument();
    fireEvent.click(screen.getByTestId('persona-filter-custom'));
    expect(screen.getByText(/No custom personas yet/)).toBeInTheDocument();
  });

  it('opens a persona into the URL', async () => {
    const router = mountRavn('/ravn/personas', ravnServices());
    fireEvent.click(await screen.findByTestId('persona-row-research-framer'));
    await waitFor(() =>
      expect(router.state.location.search).toMatchObject({ persona: 'research-framer' }),
    );
    expect(await screen.findByRole('heading', { level: 1, name: 'research-framer' })).toBeVisible();
  });
});

describe('PersonaLibrary — sheet', () => {
  it('reads as a character sheet', async () => {
    mountRavn('/ravn/personas?persona=reviewer', ravnServices({ ravens: [makeRavn()] }));
    const sheet = await screen.findByTestId('persona-sheet');
    const flow = within(sheet).getByTestId('persona-flow');
    expect(flow).toHaveTextContent('code.changed');
    expect(flow).toHaveTextContent('review.completed');
    expect(flow).toHaveTextContent('fan-in: all must pass → review.verdict');
    expect(flow).toHaveTextContent('verdict: string');
    expect(within(sheet).getByTitle('Forbidden')).toHaveTextContent('cascade');
    expect(
      await within(sheet).findByText('Nothing starts this persona on a schedule or hook.'),
    ).toBeInTheDocument();
    expect(within(sheet).getByTestId('persona-prompt')).toHaveAttribute('data-open', 'false');
    fireEvent.click(within(sheet).getByRole('button', { name: 'Expand' }));
    expect(within(sheet).getByTestId('persona-prompt')).toHaveAttribute('data-open', 'true');
  });

  it('jumps to a ravn that uses it', async () => {
    const router = mountRavn('/ravn/personas?persona=reviewer', ravnServices());
    const usedBy = await screen.findByTestId('persona-used-by');
    fireEvent.click(within(usedBy).getByRole('button', { name: /Muninn/ }));
    await waitFor(() => expect(router.state.location.pathname).toBe('/ravn'));
    expect(router.state.location.search).toMatchObject({ tab: 'chat' });
  });

  it('offers to deploy an unused persona', async () => {
    const router = mountRavn('/ravn/personas?persona=research-framer', ravnServices());
    const usedBy = await screen.findByTestId('persona-used-by');
    fireEvent.click(within(usedBy).getByRole('button', { name: 'Deploy one' }));
    await waitFor(() =>
      expect(router.state.location.search).toMatchObject({ deploy: 'research-framer' }),
    );
  });

  it('lists its triggers or says why it cannot', async () => {
    const services = ravnServices();
    services['ravn.triggers'].listTriggers = vi.fn().mockResolvedValue([
      {
        id: 't1',
        kind: 'cron',
        personaName: 'reviewer',
        spec: '0 3 * * *',
        enabled: false,
        createdAt: '2026-09-01T00:00:00Z',
      },
    ]);
    mountRavn('/ravn/personas?persona=reviewer', services);
    expect(await screen.findByText('0 3 * * *')).toBeInTheDocument();
    expect(screen.getByText('cron · paused')).toBeInTheDocument();
  });

  it('says triggers are unavailable in the server’s words', async () => {
    const services = ravnServices();
    services['ravn.triggers'].listTriggers = vi
      .fn()
      .mockRejectedValue(
        Object.assign(new Error('x'), { detail: 'Ravn trigger persistence is unavailable' }),
      );
    mountRavn('/ravn/personas?persona=reviewer', services);
    expect(await screen.findByTestId('persona-triggers-unavailable')).toHaveTextContent(
      'Ravn trigger persistence is unavailable',
    );
  });

  it('shows the YAML and the editor', async () => {
    mountRavn('/ravn/personas?persona=reviewer', ravnServices());
    fireEvent.click(await screen.findByTestId('persona-view-yaml'));
    expect(await screen.findByTestId('persona-body-yaml')).toBeInTheDocument();
    fireEvent.click(screen.getByTestId('persona-edit'));
    expect(await screen.findByTestId('persona-form')).toBeInTheDocument();
    expect(screen.getByRole('status')).toHaveTextContent('saving writes an override');
    fireEvent.click(screen.getByRole('button', { name: 'Done' }));
    expect(await screen.findByTestId('persona-sheet')).toBeInTheDocument();
  });

  it('says why a persona could not be loaded', async () => {
    const services = ravnServices();
    services['ravn.personas'].getPersona = vi
      .fn()
      .mockRejectedValue(Object.assign(new Error('x'), { detail: 'Persona not found: reviewer' }));
    mountRavn('/ravn/personas?persona=reviewer', services);
    expect(await screen.findByText('Persona not found: reviewer')).toBeInTheDocument();
  });

  it('describes fan-in without a target', () => {
    expect(describeFanIn(undefined)).toBeNull();
    expect(describeFanIn({ strategy: 'first_wins', params: {} })).toBe('first wins');
  });
});

describe('PersonaLibrary — changes', () => {
  it('forks with a validated name and opens the copy', async () => {
    const services = ravnServices();
    services['ravn.personas'].forkPersona = vi
      .fn()
      .mockResolvedValue(makePersonaDetail({ name: 'strict-reviewer' }));
    const router = mountRavn('/ravn/personas?persona=reviewer', services);
    fireEvent.click(await screen.findByTestId('persona-fork'));
    const dialog = await screen.findByRole('dialog', { name: 'Fork reviewer' });
    const input = within(dialog).getByTestId('persona-name-input');
    expect(input).toHaveValue('reviewer-copy');
    fireEvent.change(input, { target: { value: 'reviewer' } });
    fireEvent.click(within(dialog).getByTestId('persona-name-submit'));
    expect(within(dialog).getByRole('alert')).toHaveTextContent('already exists');
    fireEvent.change(input, { target: { value: 'strict-reviewer' } });
    fireEvent.click(within(dialog).getByTestId('persona-name-submit'));
    await waitFor(() =>
      expect(services['ravn.personas'].forkPersona).toHaveBeenCalledWith('reviewer', {
        newName: 'strict-reviewer',
      }),
    );
    await waitFor(() =>
      expect(router.state.location.search).toMatchObject({ persona: 'strict-reviewer' }),
    );
  });

  it('creates a persona from an empty sheet', async () => {
    const services = ravnServices();
    services['ravn.personas'].createPersona = vi
      .fn()
      .mockResolvedValue(makePersonaDetail({ name: 'night-watch' }));
    const router = mountRavn('/ravn/personas', services);
    fireEvent.click(await screen.findByTestId('persona-new'));
    const dialog = await screen.findByRole('dialog', { name: 'New persona' });
    fireEvent.change(within(dialog).getByTestId('persona-name-input'), {
      target: { value: 'night-watch' },
    });
    fireEvent.click(within(dialog).getByTestId('persona-name-submit'));
    await waitFor(() =>
      expect(services['ravn.personas'].createPersona).toHaveBeenCalledWith(
        expect.objectContaining({ name: 'night-watch' }),
      ),
    );
    await waitFor(() =>
      expect(router.state.location.search).toMatchObject({ persona: 'night-watch' }),
    );
  });

  it('shows why a new persona could not be saved', async () => {
    const services = ravnServices();
    services['ravn.personas'].createPersona = vi
      .fn()
      .mockRejectedValue(Object.assign(new Error('x'), { detail: 'read-only registry' }));
    mountRavn('/ravn/personas', services);
    fireEvent.click(await screen.findByTestId('persona-new'));
    fireEvent.change(await screen.findByTestId('persona-name-input'), {
      target: { value: 'night-watch' },
    });
    fireEvent.click(screen.getByTestId('persona-name-submit'));
    expect(await screen.findByText('read-only registry')).toBeInTheDocument();
  });

  it('deletes a custom persona', async () => {
    const services = ravnServices();
    services['ravn.personas'].listPersonas = vi
      .fn()
      .mockResolvedValue([makePersonaSummary({ name: 'mine', isBuiltin: false })]);
    services['ravn.personas'].getPersona = vi
      .fn()
      .mockResolvedValue(makePersonaDetail({ name: 'mine', isBuiltin: false }));
    mountRavn('/ravn/personas?persona=mine', services);
    fireEvent.click(await screen.findByRole('button', { name: 'Delete persona' }));
    const dialog = await screen.findByRole('dialog', { name: 'Delete mine' });
    fireEvent.click(within(dialog).getByTestId('persona-delete-confirm'));
    await waitFor(() =>
      expect(services['ravn.personas'].deletePersona).toHaveBeenCalledWith('mine'),
    );
  });

  it('resets an overridden built-in', async () => {
    const services = ravnServices();
    services['ravn.personas'].getPersona = vi
      .fn()
      .mockResolvedValue(makePersonaDetail({ hasOverride: true, overrideSource: '~/p.yaml' }));
    services['ravn.personas'].deletePersona = vi
      .fn()
      .mockRejectedValue(Object.assign(new Error('x'), { detail: 'no override file' }));
    mountRavn('/ravn/personas?persona=reviewer', services);
    expect(await screen.findByText('Built-in · overridden')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Reset to built-in' }));
    const dialog = await screen.findByRole('dialog', { name: 'Reset reviewer' });
    fireEvent.click(within(dialog).getByTestId('persona-delete-confirm'));
    expect(await within(dialog).findByText('no override file')).toBeInTheDocument();
  });

  it('goes back to the list on narrow screens', async () => {
    const router = mountRavn('/ravn/personas?persona=reviewer', ravnServices());
    fireEvent.click(await screen.findByRole('button', { name: 'Personas' }));
    await waitFor(() => expect(router.state.location.search).toEqual({}));
  });
});
