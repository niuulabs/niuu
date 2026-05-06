import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { MentionPill } from './MentionPill';
import type { SelectedMention } from '../useMentionMenu';
import type { RoomParticipant } from '@/modules/shared/hooks/useSkuldChat';

function makeParticipant(overrides: Partial<RoomParticipant> = {}): RoomParticipant {
  return {
    peerId: 'peer-1',
    persona: 'Ravn-Alpha',
    color: '#a855f7',
    participantType: 'ravn',
    status: 'idle',
    joinedAt: new Date(),
    ...overrides,
  };
}

const fileMention: SelectedMention = {
  kind: 'file',
  entry: { name: 'App.tsx', path: 'src/App.tsx', type: 'file' },
};

const dirMention: SelectedMention = {
  kind: 'file',
  entry: { name: 'src', path: 'src', type: 'directory' },
};

const agentMention: SelectedMention = {
  kind: 'agent',
  participant: makeParticipant(),
};

describe('MentionPill', () => {
  describe('file pills', () => {
    it('renders file path', () => {
      render(<MentionPill mention={fileMention} onRemove={vi.fn()} />);
      expect(screen.getByText('src/App.tsx')).toBeDefined();
    });

    it('renders directory path', () => {
      render(<MentionPill mention={dirMention} onRemove={vi.fn()} />);
      expect(screen.getByText('src')).toBeDefined();
    });

    it('calls onRemove with path when remove button clicked', () => {
      const onRemove = vi.fn();
      render(<MentionPill mention={fileMention} onRemove={onRemove} />);

      const removeBtn = screen.getByLabelText('Remove src/App.tsx');
      fireEvent.click(removeBtn);

      expect(onRemove).toHaveBeenCalledWith('src/App.tsx');
    });

    it('sets data-kind="file" attribute', () => {
      render(<MentionPill mention={fileMention} onRemove={vi.fn()} />);
      const pill = screen.getByTestId('mention-pill');
      expect(pill.dataset.kind).toBe('file');
    });

    it('sets data-type attribute for file', () => {
      render(<MentionPill mention={fileMention} onRemove={vi.fn()} />);
      const pill = screen.getByTestId('mention-pill');
      expect(pill.dataset.type).toBe('file');
    });

    it('sets data-type attribute for directory', () => {
      render(<MentionPill mention={dirMention} onRemove={vi.fn()} />);
      const pill = screen.getByTestId('mention-pill');
      expect(pill.dataset.type).toBe('directory');
    });
  });

  describe('agent pills', () => {
    it('renders persona name', () => {
      render(<MentionPill mention={agentMention} onRemove={vi.fn()} />);
      expect(screen.getByText('Ravn-Alpha')).toBeDefined();
    });

    it('sets data-kind="agent" attribute', () => {
      render(<MentionPill mention={agentMention} onRemove={vi.fn()} />);
      const pill = screen.getByTestId('mention-pill');
      expect(pill.dataset.kind).toBe('agent');
    });

    it('calls onRemove with peerId when remove button clicked', () => {
      const onRemove = vi.fn();
      render(<MentionPill mention={agentMention} onRemove={onRemove} />);

      const removeBtn = screen.getByLabelText('Remove @Ravn-Alpha');
      fireEvent.click(removeBtn);

      expect(onRemove).toHaveBeenCalledWith('peer-1');
    });

    it('applies participant color as CSS custom property', () => {
      render(<MentionPill mention={agentMention} onRemove={vi.fn()} />);
      const pill = screen.getByTestId('mention-pill');
      expect(pill.style.getPropertyValue('--pill-color')).toBe('#a855f7');
    });
  });
});
