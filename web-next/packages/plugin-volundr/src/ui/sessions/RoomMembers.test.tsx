import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { MemberRow, engineLabel } from './RoomMembers';

describe('engineLabel', () => {
  it('names the harness a person knows', () => {
    expect(engineLabel({ definition: 'skuldClaude', model: 'claude-sonnet' })).toBe('Claude Code');
    expect(engineLabel({ definition: 'skuldCodex', model: 'gpt-5' })).toBe('Codex');
    expect(engineLabel({ definition: null, model: 'claude-opus' })).toBe('Claude Code');
  });

  it('names an engine that is neither by its definition, else its model', () => {
    expect(engineLabel({ definition: 'skuldGemini', model: 'gemini-pro' })).toBe('skuldGemini');
    expect(engineLabel({ definition: null, model: 'mistral-large' })).not.toBe('');
  });
});

describe('MemberRow', () => {
  it('selects when it can be selected', () => {
    const onSelect = vi.fn();
    render(
      <MemberRow
        testId="row"
        title="Coder: open"
        name="Coder"
        color="#fff"
        selected
        onSelect={onSelect}
      />,
    );
    const row = screen.getByTestId('row');
    expect(row.tagName).toBe('BUTTON');
    expect(row).toHaveAttribute('aria-current', 'true');
    fireEvent.click(row);
    expect(onSelect).toHaveBeenCalledTimes(1);
  });

  it('is only shown when there is nothing to select, and carries its parts', () => {
    render(
      <MemberRow
        testId="row"
        title="Sam"
        name="Sam K."
        role="Reviewer"
        roleTestId="row-role"
        detail="invited"
        badge="Leads"
        status="Online"
        statusKind="working"
        avatar={{ kind: 'person', glyph: 'SK' }}
        color="#fff"
      />,
    );
    const row = screen.getByTestId('row');
    expect(row.tagName).toBe('DIV');
    expect(row).not.toHaveAttribute('aria-current');
    expect(screen.getByTestId('row-role')).toHaveTextContent('Reviewer');
    expect(row).toHaveTextContent('Sam K.');
    expect(row).toHaveTextContent('invited');
    expect(row).toHaveTextContent('Leads');
    expect(row).toHaveTextContent('Online');
    expect(row).toHaveTextContent('SK');
  });
});
