import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { renderEveryday } from '../../testing/renderEveryday';
import { createMemoryGatewayMockAdapter } from '../../adapters/memoryGateway';
import type { IMemoryGatewayService } from '../../ports/IMemoryGateway';
import { SearchPage } from './SearchPage';

function gateway(overrides: Partial<IMemoryGatewayService> = {}): IMemoryGatewayService {
  return { ...createMemoryGatewayMockAdapter(), ...overrides };
}

const LABEL = 'Search what the team knows';

describe('SearchPage', () => {
  afterEach(cleanup);

  it('lists hits from every memory, linking a page to its reader and showing a source ref', async () => {
    const user = userEvent.setup();
    renderEveryday(SearchPage, '/mimir/search');
    await user.type(await screen.findByLabelText(LABEL), 'cut-over{Enter}');
    const hits = await screen.findAllByTestId('memory-hit');
    expect(hits).toHaveLength(2);
    const link = screen.getByRole('link', { name: 'Booking cut-over' });
    const href = link.getAttribute('href') ?? '';
    expect(href).toContain('/mimir/read');
    expect(decodeURIComponent(href)).toContain('path=projects/booking-cutover');
    expect(decodeURIComponent(href)).toContain('mount=project:booking');
    expect(hits[0]).toHaveTextContent('weekend of 12 December');
    expect(hits[0]).toHaveTextContent('Booking platform · page');
    expect(hits[1]).toHaveTextContent('Cut-over planning thread');
    expect(hits[1]).toHaveTextContent('the org · source, source:src_cutover_thread');
    expect(
      screen.queryByRole('link', { name: 'Cut-over planning thread' }),
    ).not.toBeInTheDocument();
  });

  it('says plainly when nothing matches', async () => {
    const user = userEvent.setup();
    renderEveryday(SearchPage, '/mimir/search');
    await user.type(await screen.findByLabelText(LABEL), 'zebra{Enter}');
    expect(await screen.findByText('Nothing in memory matches that.')).toBeInTheDocument();
    expect(screen.queryByTestId('memory-hit')).not.toBeInTheDocument();
  });

  it('narrows to the project in the address and searches the query it carries', async () => {
    const user = userEvent.setup();
    const search = vi.fn().mockResolvedValue({ query: 'who decides', hits: [] });
    renderEveryday(SearchPage, '/mimir/search?project=p-1&q=who%20decides', gateway({ search }));
    const box = await screen.findByLabelText(LABEL);
    expect(box).toHaveValue('who decides');
    await screen.findByText('Nothing in memory matches that.');
    expect(search).toHaveBeenCalledWith('who decides', { projectId: 'p-1' });
    await user.type(box, '?{Enter}');
    await screen.findByText('Nothing in memory matches that.');
    expect(search).toHaveBeenLastCalledWith('who decides?', { projectId: 'p-1' });
  });

  it('searches nothing for an empty box', async () => {
    const user = userEvent.setup();
    const search = vi.fn();
    renderEveryday(SearchPage, '/mimir/search', gateway({ search }));
    await user.type(await screen.findByLabelText(LABEL), '   {Enter}');
    expect(search).not.toHaveBeenCalled();
    expect(screen.queryByText('Nothing in memory matches that.')).not.toBeInTheDocument();
  });

  it('shows that it is looking while the hits come', async () => {
    const user = userEvent.setup();
    const search = vi.fn().mockReturnValue(new Promise(() => {}));
    renderEveryday(SearchPage, '/mimir/search', gateway({ search }));
    await user.type(await screen.findByLabelText(LABEL), 'x{Enter}');
    expect(await screen.findByText('Looking…')).toBeInTheDocument();
  });

  it('shows a failed search as an error', async () => {
    const user = userEvent.setup();
    const search = vi.fn().mockRejectedValue(new Error('memory is unavailable'));
    renderEveryday(SearchPage, '/mimir/search', gateway({ search }));
    await user.type(await screen.findByLabelText(LABEL), 'x{Enter}');
    expect(await screen.findByRole('alert')).toHaveTextContent('memory is unavailable');
  });

  it('shows a non-Error failure as text', async () => {
    const user = userEvent.setup();
    const search = vi.fn().mockRejectedValue('down');
    renderEveryday(SearchPage, '/mimir/search', gateway({ search }));
    await user.type(await screen.findByLabelText(LABEL), 'x{Enter}');
    expect(await screen.findByRole('alert')).toHaveTextContent('down');
  });
});
