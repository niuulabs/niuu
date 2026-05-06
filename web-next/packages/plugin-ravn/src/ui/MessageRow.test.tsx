import { describe, it, expect } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { MessageRow } from './MessageRow';
import type { Message } from '../domain/message';

function msg(overrides: Partial<Message>): Message {
  return {
    id: '00000000-0000-4000-8000-000000000001',
    sessionId: 'sess-1',
    kind: 'user',
    content: 'hello',
    ts: '2026-04-15T09:12:35Z',
    ...overrides,
  };
}

describe('MessageRow', () => {
  describe('user kind', () => {
    it('renders content', () => {
      render(<MessageRow message={msg({ kind: 'user', content: 'hello world' })} />);
      expect(screen.getByText('hello world')).toBeInTheDocument();
    });

    it('has data-kind=user', () => {
      const { container } = render(<MessageRow message={msg({ kind: 'user' })} />);
      expect(container.querySelector('[data-kind="user"]')).toBeInTheDocument();
    });
  });

  describe('asst kind', () => {
    it('renders content', () => {
      render(<MessageRow message={msg({ kind: 'asst', content: 'assistant reply' })} />);
      expect(screen.getByText('assistant reply')).toBeInTheDocument();
    });

    it('has data-kind=asst', () => {
      const { container } = render(<MessageRow message={msg({ kind: 'asst' })} />);
      expect(container.querySelector('[data-kind="asst"]')).toBeInTheDocument();
    });
  });

  describe('system kind', () => {
    it('renders content with italic style class', () => {
      const { container } = render(
        <MessageRow message={msg({ kind: 'system', content: 'system context' })} />,
      );
      expect(screen.getByText('system context')).toBeInTheDocument();
      expect(container.querySelector('.rv-msg__body--italic')).toBeInTheDocument();
    });
  });

  describe('tool_call kind', () => {
    it('renders tool name', () => {
      render(
        <MessageRow
          message={msg({ kind: 'tool_call', toolName: 'file.read', content: '{"path":"x"}' })}
        />,
      );
      expect(screen.getByText('file.read')).toBeInTheDocument();
    });

    it('renders call badge', () => {
      render(
        <MessageRow message={msg({ kind: 'tool_call', toolName: 'file.read', content: '{}' })} />,
      );
      expect(screen.getByText('call')).toBeInTheDocument();
    });

    it('renders content as code', () => {
      const { container } = render(
        <MessageRow message={msg({ kind: 'tool_call', content: '{"path":"x"}' })} />,
      );
      expect(container.querySelector('pre')).toBeInTheDocument();
    });

    it('falls back to tool when toolName is absent', () => {
      render(<MessageRow message={msg({ kind: 'tool_call', content: '{}' })} />);
      expect(screen.getByText('tool')).toBeInTheDocument();
    });
  });

  describe('tool_result kind', () => {
    it('renders result badge', () => {
      render(<MessageRow message={msg({ kind: 'tool_result', content: '{}' })} />);
      expect(screen.getByText('result')).toBeInTheDocument();
    });
  });

  describe('emit kind', () => {
    it('renders emit badge', () => {
      render(
        <MessageRow
          message={msg({ kind: 'emit', content: '{"event":"code.changed","payload":{}}' })}
        />,
      );
      expect(screen.getByText('emit')).toBeInTheDocument();
    });

    it('renders event name from JSON', () => {
      render(
        <MessageRow
          message={msg({ kind: 'emit', content: '{"event":"code.changed","payload":{}}' })}
        />,
      );
      expect(screen.getByText('code.changed')).toBeInTheDocument();
    });

    it('handles invalid JSON gracefully', () => {
      render(<MessageRow message={msg({ kind: 'emit', content: 'not json' })} />);
      expect(screen.getByText('event')).toBeInTheDocument();
    });
  });

  describe('think kind', () => {
    it('renders collapsed by default', () => {
      render(<MessageRow message={msg({ kind: 'think', content: 'my reasoning' })} />);
      expect(screen.queryByText('my reasoning')).not.toBeInTheDocument();
      expect(screen.getByText('show reasoning')).toBeInTheDocument();
    });

    it('expands when toggle is clicked', () => {
      render(<MessageRow message={msg({ kind: 'think', content: 'my reasoning' })} />);
      fireEvent.click(screen.getByText('show reasoning'));
      expect(screen.getByText('my reasoning')).toBeInTheDocument();
      expect(screen.getByText('hide reasoning')).toBeInTheDocument();
    });

    it('collapses again on second click', () => {
      render(<MessageRow message={msg({ kind: 'think', content: 'my reasoning' })} />);
      fireEvent.click(screen.getByText('show reasoning'));
      fireEvent.click(screen.getByText('hide reasoning'));
      expect(screen.queryByText('my reasoning')).not.toBeInTheDocument();
    });

    it('has aria-expanded', () => {
      render(<MessageRow message={msg({ kind: 'think', content: 'my reasoning' })} />);
      const btn = screen.getByText('show reasoning').closest('button');
      expect(btn).toHaveAttribute('aria-expanded', 'false');
    });
  });
});
