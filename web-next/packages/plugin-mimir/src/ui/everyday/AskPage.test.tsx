import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { renderEveryday } from '../../testing/renderEveryday';
import { createMemoryGatewayMockAdapter } from '../../adapters/memoryGateway';
import type { IMemoryGatewayService } from '../../ports/IMemoryGateway';
import { AskPage } from './AskPage';

function gateway(overrides: Partial<IMemoryGatewayService> = {}): IMemoryGatewayService {
  return { ...createMemoryGatewayMockAdapter(), ...overrides };
}

describe('AskPage', () => {
  afterEach(cleanup);

  it('answers in plain words, says what is not settled and folds the sources', async () => {
    const user = userEvent.setup();
    renderEveryday(AskPage, '/mimir/ask');
    const box = await screen.findByLabelText('Ask what the team knows');
    await user.type(box, 'When is the booking cut-over?{Enter}');
    const answer = await screen.findByTestId('memory-answer');
    expect(answer).toHaveTextContent('weekend of 12 December');
    expect(screen.getByText(/Not settled/)).toBeInTheDocument();
    expect(screen.getByText('1 source')).toBeInTheDocument();
    expect(screen.getByText('Booking cut-over')).toBeInTheDocument();
    expect(screen.getByText(/a page in Booking platform/)).toBeInTheDocument();
  });

  it("asks within the project named in the address, and links a source's origin", async () => {
    const user = userEvent.setup();
    const ask = vi.fn().mockResolvedValue({
      answer: 'Ines decides.',
      gaps: [],
      sources: [
        {
          layer: 'project:p',
          layerName: 'Booking platform',
          ref: 'source:1',
          kind: 'source',
          title: 'LIN-501',
          url: 'https://linear.app/acme/issue/LIN-501',
        },
        {
          layer: 'org',
          layerName: 'the org',
          ref: 'fact:2',
          kind: 'fact',
          title: 'Ines Moreau',
          url: null,
        },
      ],
    });
    renderEveryday(AskPage, '/mimir/ask?project=p-1&q=who%20decides', gateway({ ask }));
    const box = await screen.findByLabelText('Ask what the team knows');
    expect(box).toHaveValue('who decides');
    await user.type(box, '?{Enter}');
    await screen.findByTestId('memory-answer');
    expect(ask).toHaveBeenCalledWith('who decides?', 'p-1');
    expect(screen.getByText('2 sources')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'LIN-501' })).toHaveAttribute(
      'href',
      'https://linear.app/acme/issue/LIN-501',
    );
    expect(screen.getByText(/what the org holds/)).toBeInTheDocument();
  });

  it('says when memory does not know, without sources', async () => {
    const user = userEvent.setup();
    const ask = vi.fn().mockResolvedValue({ answer: '', gaps: ['Nothing matches.'], sources: [] });
    renderEveryday(AskPage, '/mimir/ask', gateway({ ask }));
    await user.type(await screen.findByLabelText('Ask what the team knows'), 'zebra{Enter}');
    expect(await screen.findByText('Memory does not know this yet.')).toBeInTheDocument();
    expect(screen.getByText('Nothing matches.')).toBeInTheDocument();
    expect(screen.queryByText(/source/)).not.toBeInTheDocument();
  });

  it('shows a failed answer as an error and asks nothing for an empty question', async () => {
    const user = userEvent.setup();
    const ask = vi.fn().mockRejectedValue(new Error('the answer model failed'));
    renderEveryday(AskPage, '/mimir/ask', gateway({ ask }));
    const box = await screen.findByLabelText('Ask what the team knows');
    await user.type(box, '   {Enter}');
    expect(ask).not.toHaveBeenCalled();
    await user.type(box, 'x{Enter}');
    expect(await screen.findByRole('alert')).toHaveTextContent('the answer model failed');
  });

  it('shows that it is looking while the answer comes', async () => {
    const user = userEvent.setup();
    const ask = vi.fn().mockReturnValue(new Promise(() => {}));
    renderEveryday(AskPage, '/mimir/ask', gateway({ ask }));
    await user.type(await screen.findByLabelText('Ask what the team knows'), 'x{Enter}');
    expect(await screen.findByText('Looking…')).toBeInTheDocument();
  });

  it('shows a non-Error failure as text', async () => {
    const user = userEvent.setup();
    const ask = vi.fn().mockRejectedValue('down');
    renderEveryday(AskPage, '/mimir/ask', gateway({ ask }));
    await user.type(await screen.findByLabelText('Ask what the team knows'), 'x{Enter}');
    expect(await screen.findByRole('alert')).toHaveTextContent('down');
  });
});
