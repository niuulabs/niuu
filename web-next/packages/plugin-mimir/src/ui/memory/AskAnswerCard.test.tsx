import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { AskAnswerCard } from './AskAnswerCard';
import { FAKE_PAGES, fakeNodeId } from '../../testing/fakeMimirService';
import type { QuotedFact } from '../../domain/quoteFacts';
import type { FactEvidence } from '../../domain/evidence';

const PAGE = FAKE_PAGES.find((p) => p.path === '/platform/gateway-routing')!;
const NODE_ID = fakeNodeId('platform', '/platform/gateway-routing');

const EVIDENCE: FactEvidence = {
  fact: 'The route tables live in values-cedar.yaml, which wins over values-niuu.yaml.',
  proofCount: 2,
  trend: 'stable',
  latestSupport: '2026-01-01',
  supportingDates: ['2026-01-01'],
  sourceProofCount: 1,
};

const QUOTED: QuotedFact[] = [
  {
    page: PAGE,
    fact: EVIDENCE.fact,
    position: 1,
    evidence: EVIDENCE,
  },
];

function setup(overrides: Partial<React.ComponentProps<typeof AskAnswerCard>> = {}) {
  const props: React.ComponentProps<typeof AskAnswerCard> = {
    question: 'gateway routing',
    quoted: QUOTED,
    rankByPath: new Map([['/platform/gateway-routing', 1]]),
    nodeIdByPath: new Map([['/platform/gateway-routing', NODE_ID]]),
    resultCount: 1,
    isLoading: false,
    isError: false,
    onAsk: vi.fn(),
    onFocusPage: vi.fn(),
    ...overrides,
  };
  render(<AskAnswerCard {...props} />);
  return props;
}

describe('AskAnswerCard', () => {
  it('shows the ask bar pre-filled with the question', () => {
    setup();
    expect(screen.getByLabelText('Ask memory')).toHaveValue('gateway routing');
  });

  it('re-asks from the ask bar', async () => {
    const props = setup();
    await userEvent.clear(screen.getByLabelText('Ask memory'));
    await userEvent.type(screen.getByLabelText('Ask memory'), 'cedar authorization');
    await userEvent.click(screen.getByRole('button', { name: 'Ask memory' }));
    expect(props.onAsk).toHaveBeenCalledWith('cedar authorization');
  });

  it('shows the quoted fact, its rank badge, and proof', () => {
    setup();
    expect(
      screen.getByText(
        'The route tables live in values-cedar.yaml, which wins over values-niuu.yaml.',
      ),
    ).toBeInTheDocument();
    expect(screen.getByText('[1]')).toBeInTheDocument();
    expect(screen.getByTestId('proof-pill')).toBeInTheDocument();
  });

  it('focuses the answering page by its graph node id on click', async () => {
    const props = setup();
    await userEvent.click(screen.getByText('Gateway routing on ymir →'));
    expect(props.onFocusPage).toHaveBeenCalledWith(NODE_ID);
  });

  it('shows a loading state while searching', () => {
    setup({ isLoading: true });
    expect(screen.getByText('searching memory…')).toBeInTheDocument();
  });

  it('shows an error state', () => {
    setup({ isError: true });
    expect(screen.getByRole('alert')).toHaveTextContent('Search failed.');
  });

  it('says memory has nothing on the question when there are no results', () => {
    setup({ quoted: [], resultCount: 0 });
    expect(screen.getByText('Memory has nothing on "gateway routing".')).toBeInTheDocument();
  });

  it('says the answering pages carry no facts yet when there are results but no facts', () => {
    setup({ quoted: [], resultCount: 2 });
    expect(screen.getByText('The answering pages carry no written facts yet.')).toBeInTheDocument();
  });
});
