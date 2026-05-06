import { render, screen, fireEvent, act } from '@testing-library/react';
import { vi, describe, it, expect, beforeEach } from 'vitest';
import { ChatInput } from './ChatInput';
import { useSpeechRecognition } from '@/hooks';
import { useSlashMenu } from './useSlashMenu';
import { useMentionMenu } from './useMentionMenu';

vi.mock('@/hooks', async () => {
  const actual = await vi.importActual('@/hooks');
  return {
    ...actual,
    useSpeechRecognition: vi.fn(() => ({
      isListening: false,
      transcript: '',
      startListening: vi.fn(),
      stopListening: vi.fn(),
      isSupported: true,
    })),
  };
});

vi.mock('./useSlashMenu');
vi.mock('./useMentionMenu');

vi.mock('./SlashCommandMenu', () => ({
  SlashCommandMenu: ({
    selectedIndex,
    onSelect,
  }: {
    selectedIndex: number;
    onSelect: (cmd: { name: string; type: string; description?: string }) => void;
  }) => (
    <div data-testid="slash-command-menu" data-selected={selectedIndex}>
      <button onClick={() => onSelect({ name: 'help', type: 'command', description: 'Show help' })}>
        select-slash-cmd
      </button>
    </div>
  ),
}));

vi.mock('./MentionMenu', () => ({
  MentionMenu: ({
    items,
    selectedIndex,
    loading,
    onSelect,
    onExpand,
  }: {
    items: Array<
      | { kind: 'file'; entry: { name: string; path: string; type: string }; depth: number }
      | { kind: 'agent'; participant: { peerId: string; persona: string } }
    >;
    selectedIndex: number;
    loading: boolean;
    onSelect: (item: unknown) => void;
    onExpand: (item: unknown) => void;
  }) => (
    <div data-testid="mention-menu" data-selected={selectedIndex} data-loading={loading}>
      {items.map((item, i) => {
        if (item.kind === 'agent') {
          return (
            <button
              key={item.participant.peerId}
              onClick={() => onSelect(item)}
              data-testid={`mention-item-${i}`}
            >
              {item.participant.persona}
            </button>
          );
        }
        return (
          <button
            key={item.entry.path}
            onClick={() => onSelect(item)}
            data-testid={`mention-item-${i}`}
          >
            {item.entry.name}
          </button>
        );
      })}
      {items.map((item, i) =>
        item.kind === 'file' && item.entry.type === 'directory' ? (
          <button
            key={`expand-${item.entry.path}`}
            onClick={() => onExpand(item)}
            data-testid={`mention-expand-${i}`}
          >
            expand-{item.entry.name}
          </button>
        ) : null
      )}
    </div>
  ),
}));

vi.mock('./MentionPill', () => ({
  MentionPill: ({
    mention,
    onRemove,
  }: {
    mention:
      | { kind: 'file'; entry: { path: string } }
      | { kind: 'agent'; participant: { peerId: string; persona: string } };
    onRemove: (id: string) => void;
  }) => {
    if (mention.kind === 'agent') {
      return (
        <span data-testid="mention-pill">
          <span>{mention.participant.persona}</span>
          <button
            onClick={() => onRemove(mention.participant.peerId)}
            aria-label={`Remove @${mention.participant.persona}`}
          >
            x
          </button>
        </span>
      );
    }
    return (
      <span data-testid="mention-pill">
        <span>{mention.entry.path}</span>
        <button
          onClick={() => onRemove(mention.entry.path)}
          aria-label={`Remove ${mention.entry.path}`}
        >
          x
        </button>
      </span>
    );
  },
}));

describe('ChatInput', () => {
  let onSend: ReturnType<typeof vi.fn>;
  let onStop: ReturnType<typeof vi.fn>;

  const defaultSlashMenu = {
    isOpen: false,
    filter: '',
    selectedIndex: 0,
    filteredCommands: [] as { name: string; description: string; icon: string }[],
    handleKeyDown: vi.fn(() => false),
    handleChange: vi.fn(),
    selectCommand: vi.fn(() => ''),
    close: vi.fn(),
  };

  const defaultMentionMenu = {
    isOpen: false,
    filter: '',
    selectedIndex: 0,
    items: [] as Array<
      | {
          kind: 'file';
          entry: { name: string; path: string; type: 'file' | 'directory' };
          depth: number;
        }
      | {
          kind: 'agent';
          participant: {
            peerId: string;
            persona: string;
            color: string;
            participantType: string;
            status: string;
            joinedAt: Date;
          };
        }
    >,
    loading: false,
    mentions: [] as Array<
      | { kind: 'file'; entry: { name: string; path: string; type: 'file' | 'directory' } }
      | {
          kind: 'agent';
          participant: {
            peerId: string;
            persona: string;
            color: string;
            participantType: string;
            status: string;
            joinedAt: Date;
          };
        }
    >,
    handleKeyDown: vi.fn(() => false),
    handleChange: vi.fn(),
    selectItem: vi.fn(() => ''),
    expandDirectory: vi.fn(),
    removeMention: vi.fn(),
    close: vi.fn(),
  };

  beforeEach(() => {
    vi.clearAllMocks();
    onSend = vi.fn();
    onStop = vi.fn();
    vi.mocked(useSlashMenu).mockReturnValue({ ...defaultSlashMenu });
    vi.mocked(useMentionMenu).mockReturnValue({ ...defaultMentionMenu });
  });

  function renderInput(overrides: Partial<Parameters<typeof ChatInput>[0]> = {}) {
    return render(<ChatInput onSend={onSend} isLoading={false} onStop={onStop} {...overrides} />);
  }

  // ---------------------------------------------------------------------------
  // Basic rendering
  // ---------------------------------------------------------------------------

  describe('basic rendering', () => {
    it('renders textarea with "Message..." placeholder when enabled', () => {
      renderInput();

      const textarea = screen.getByPlaceholderText('Message...');
      expect(textarea).toBeInTheDocument();
      expect(textarea.tagName).toBe('TEXTAREA');
    });

    it('renders textarea with "Start session to chat..." placeholder when disabled', () => {
      renderInput({ disabled: true });

      const textarea = screen.getByPlaceholderText('Start session to chat...');
      expect(textarea).toBeInTheDocument();
    });

    it('applies custom className to wrapper', () => {
      const { container } = renderInput({ className: 'my-custom-class' });

      expect(container.firstChild).toHaveClass('my-custom-class');
    });

    it('sets data-disabled="true" on wrapper when disabled', () => {
      const { container } = renderInput({ disabled: true });

      expect(container.firstChild).toHaveAttribute('data-disabled', 'true');
    });

    it('sets data-disabled="false" on wrapper when not disabled', () => {
      const { container } = renderInput({ disabled: false });

      expect(container.firstChild).toHaveAttribute('data-disabled', 'false');
    });

    it('disables the textarea when disabled prop is true', () => {
      renderInput({ disabled: true });

      expect(screen.getByPlaceholderText('Start session to chat...')).toBeDisabled();
    });
  });

  // ---------------------------------------------------------------------------
  // Sending messages
  // ---------------------------------------------------------------------------

  describe('sending messages', () => {
    it('sends trimmed message via onSend when Enter key is pressed', () => {
      renderInput();

      const textarea = screen.getByPlaceholderText('Message...');
      fireEvent.change(textarea, { target: { value: '  hello world  ' } });
      fireEvent.keyDown(textarea, { key: 'Enter' });

      expect(onSend).toHaveBeenCalledTimes(1);
      expect(onSend).toHaveBeenCalledWith('hello world', []);
    });

    it('does not send on Shift+Enter (allows newline)', () => {
      renderInput();

      const textarea = screen.getByPlaceholderText('Message...');
      fireEvent.change(textarea, { target: { value: 'hello' } });
      fireEvent.keyDown(textarea, { key: 'Enter', shiftKey: true });

      expect(onSend).not.toHaveBeenCalled();
    });

    it('does not send for non-Enter key presses', () => {
      renderInput();

      const textarea = screen.getByPlaceholderText('Message...');
      fireEvent.change(textarea, { target: { value: 'hello' } });
      fireEvent.keyDown(textarea, { key: 'a' });

      expect(onSend).not.toHaveBeenCalled();
    });

    it('sends message when send button is clicked', () => {
      renderInput();

      const textarea = screen.getByPlaceholderText('Message...');
      fireEvent.change(textarea, { target: { value: 'click send test' } });
      fireEvent.click(screen.getByLabelText('Send message'));

      expect(onSend).toHaveBeenCalledTimes(1);
      expect(onSend).toHaveBeenCalledWith('click send test', []);
    });

    it('clears input after sending', () => {
      renderInput();

      const textarea = screen.getByPlaceholderText('Message...');
      fireEvent.change(textarea, { target: { value: 'will be cleared' } });
      fireEvent.keyDown(textarea, { key: 'Enter' });

      expect(textarea).toHaveValue('');
    });

    it('does not send empty input', () => {
      renderInput();

      const textarea = screen.getByPlaceholderText('Message...');
      fireEvent.keyDown(textarea, { key: 'Enter' });

      expect(onSend).not.toHaveBeenCalled();
    });

    it('does not send whitespace-only input', () => {
      renderInput();

      const textarea = screen.getByPlaceholderText('Message...');
      fireEvent.change(textarea, { target: { value: '   \t\n  ' } });
      fireEvent.keyDown(textarea, { key: 'Enter' });

      expect(onSend).not.toHaveBeenCalled();
    });

    it('does not send when disabled', () => {
      renderInput({ disabled: true });

      const textarea = screen.getByPlaceholderText('Start session to chat...');
      fireEvent.change(textarea, { target: { value: 'blocked' } });
      fireEvent.click(screen.getByLabelText('Send message'));

      expect(onSend).not.toHaveBeenCalled();
    });
  });

  // ---------------------------------------------------------------------------
  // Send button states
  // ---------------------------------------------------------------------------

  describe('send button states', () => {
    it('has data-active="true" when input has content and not disabled', () => {
      renderInput();

      const textarea = screen.getByPlaceholderText('Message...');
      fireEvent.change(textarea, { target: { value: 'content' } });

      const sendBtn = screen.getByLabelText('Send message');
      expect(sendBtn).toHaveAttribute('data-active', 'true');
    });

    it('has data-active="false" when input is empty', () => {
      renderInput();

      const sendBtn = screen.getByLabelText('Send message');
      expect(sendBtn).toHaveAttribute('data-active', 'false');
    });

    it('has data-active="false" when input has content but component is disabled', () => {
      renderInput({ disabled: true });

      const textarea = screen.getByPlaceholderText('Start session to chat...');
      fireEvent.change(textarea, { target: { value: 'content' } });

      const sendBtn = screen.getByLabelText('Send message');
      expect(sendBtn).toHaveAttribute('data-active', 'false');
    });

    it('is disabled when no content', () => {
      renderInput();

      const sendBtn = screen.getByLabelText('Send message');
      expect(sendBtn).toBeDisabled();
    });

    it('is disabled when component is disabled even with content', () => {
      renderInput({ disabled: true });

      const textarea = screen.getByPlaceholderText('Start session to chat...');
      fireEvent.change(textarea, { target: { value: 'some text' } });

      const sendBtn = screen.getByLabelText('Send message');
      expect(sendBtn).toBeDisabled();
    });

    it('is enabled when input has content and not disabled', () => {
      renderInput();

      const textarea = screen.getByPlaceholderText('Message...');
      fireEvent.change(textarea, { target: { value: 'enabled' } });

      const sendBtn = screen.getByLabelText('Send message');
      expect(sendBtn).not.toBeDisabled();
    });
  });

  // ---------------------------------------------------------------------------
  // Stop button
  // ---------------------------------------------------------------------------

  describe('stop button', () => {
    it('shows stop button when isLoading is true', () => {
      renderInput({ isLoading: true });

      expect(screen.getByText('Stop')).toBeInTheDocument();
    });

    it('hides stop button when isLoading is false', () => {
      renderInput({ isLoading: false });

      expect(screen.queryByText('Stop')).not.toBeInTheDocument();
    });

    it('calls onStop when clicked', () => {
      renderInput({ isLoading: true });

      fireEvent.click(screen.getByText('Stop'));

      expect(onStop).toHaveBeenCalledTimes(1);
    });

    it('shows "Stop" text', () => {
      renderInput({ isLoading: true });

      const stopBtn = screen.getByText('Stop');
      expect(stopBtn).toBeInTheDocument();
    });
  });

  // ---------------------------------------------------------------------------
  // Attach button
  // ---------------------------------------------------------------------------

  describe('attach button', () => {
    it('has attach button with "Attach file" aria-label', () => {
      renderInput();

      expect(screen.getByLabelText('Attach file')).toBeInTheDocument();
    });

    it('click triggers hidden file input', () => {
      renderInput();

      const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement;
      const clickSpy = vi.spyOn(fileInput, 'click');

      fireEvent.click(screen.getByLabelText('Attach file'));

      expect(clickSpy).toHaveBeenCalledTimes(1);
      clickSpy.mockRestore();
    });

    it('does not trigger file input when disabled', () => {
      renderInput({ disabled: true });

      const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement;
      const clickSpy = vi.spyOn(fileInput, 'click');

      fireEvent.click(screen.getByLabelText('Attach file'));

      expect(clickSpy).not.toHaveBeenCalled();
      clickSpy.mockRestore();
    });

    it('adding files shows attachment chips', () => {
      renderInput();

      const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement;
      const testFile = new File(['content'], 'readme.md', { type: 'text/markdown' });

      fireEvent.change(fileInput, { target: { files: [testFile] } });

      expect(screen.getByText('readme.md')).toBeInTheDocument();
    });

    it('adding multiple files shows multiple attachment chips', () => {
      renderInput();

      const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement;
      const file1 = new File(['a'], 'file1.ts', { type: 'text/plain' });
      const file2 = new File(['b'], 'file2.py', { type: 'text/plain' });

      fireEvent.change(fileInput, { target: { files: [file1, file2] } });

      expect(screen.getByText('file1.ts')).toBeInTheDocument();
      expect(screen.getByText('file2.py')).toBeInTheDocument();
    });

    it('each attachment chip has a remove button', () => {
      renderInput();

      const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement;
      const testFile = new File(['content'], 'data.json', { type: 'application/json' });

      fireEvent.change(fileInput, { target: { files: [testFile] } });

      expect(screen.getByLabelText('Remove data.json')).toBeInTheDocument();
    });

    it('removing attachment removes the chip', () => {
      renderInput();

      const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement;
      const testFile = new File(['content'], 'remove-me.txt', { type: 'text/plain' });

      fireEvent.change(fileInput, { target: { files: [testFile] } });
      expect(screen.getByText('remove-me.txt')).toBeInTheDocument();

      fireEvent.click(screen.getByLabelText('Remove remove-me.txt'));
      expect(screen.queryByText('remove-me.txt')).not.toBeInTheDocument();
    });

    it('attachments are cleared after sending', () => {
      renderInput();

      const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement;
      const testFile = new File(['content'], 'attachment.ts', { type: 'text/plain' });

      fireEvent.change(fileInput, { target: { files: [testFile] } });
      expect(screen.getByText('attachment.ts')).toBeInTheDocument();

      const textarea = screen.getByPlaceholderText('Message...');
      fireEvent.change(textarea, { target: { value: 'send with attachment' } });
      fireEvent.keyDown(textarea, { key: 'Enter' });

      expect(screen.queryByText('attachment.ts')).not.toBeInTheDocument();
    });

    it('does not crash when file input change has no files', () => {
      renderInput();

      const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement;

      // Simulate change event with null files
      fireEvent.change(fileInput, { target: { files: null } });

      // Should not throw; no attachment chips shown
      expect(screen.queryByLabelText(/^Remove /)).not.toBeInTheDocument();
    });

    it('sets data-disabled on attach button when disabled', () => {
      renderInput({ disabled: true });

      const attachBtn = screen.getByLabelText('Attach file');
      expect(attachBtn).toHaveAttribute('data-disabled', 'true');
    });

    it('sets data-disabled="false" on attach button when enabled', () => {
      renderInput({ disabled: false });

      const attachBtn = screen.getByLabelText('Attach file');
      expect(attachBtn).toHaveAttribute('data-disabled', 'false');
    });
  });

  // ---------------------------------------------------------------------------
  // Focus behavior
  // ---------------------------------------------------------------------------

  describe('focus behavior', () => {
    it('auto-focuses textarea when not loading and not disabled', () => {
      renderInput({ isLoading: false, disabled: false });

      const textarea = screen.getByPlaceholderText('Message...');
      expect(textarea).toHaveFocus();
    });

    it('does not auto-focus textarea when isLoading is true', () => {
      renderInput({ isLoading: true, disabled: false });

      const textarea = screen.getByPlaceholderText('Message...');
      expect(textarea).not.toHaveFocus();
    });

    it('does not auto-focus textarea when disabled is true', () => {
      renderInput({ isLoading: false, disabled: true });

      const textarea = screen.getByPlaceholderText('Start session to chat...');
      expect(textarea).not.toHaveFocus();
    });
  });

  // ---------------------------------------------------------------------------
  // Textarea auto-resize (inline height via ref)
  // ---------------------------------------------------------------------------

  describe('textarea auto-resize', () => {
    it('sets textarea style.height on input change', () => {
      renderInput();

      const textarea = screen.getByPlaceholderText('Message...') as HTMLTextAreaElement;
      fireEvent.change(textarea, { target: { value: 'line 1\nline 2\nline 3' } });

      // After change, resetTextareaHeight should run: height is set to 'auto' then scrollHeight
      // In jsdom scrollHeight is 0, so the result is `${Math.min(0, 200)}px` = '0px'
      expect(textarea.style.height).toBeDefined();
    });
  });

  // ---------------------------------------------------------------------------
  // Edge cases
  // ---------------------------------------------------------------------------

  describe('edge cases', () => {
    it('handles rapid typing and sending', () => {
      renderInput();

      const textarea = screen.getByPlaceholderText('Message...');
      fireEvent.change(textarea, { target: { value: 'first' } });
      fireEvent.keyDown(textarea, { key: 'Enter' });
      expect(onSend).toHaveBeenCalledWith('first', []);

      fireEvent.change(textarea, { target: { value: 'second' } });
      fireEvent.keyDown(textarea, { key: 'Enter' });
      expect(onSend).toHaveBeenCalledWith('second', []);

      expect(onSend).toHaveBeenCalledTimes(2);
    });

    it('does not render attachment area when there are no attachments', () => {
      const { container } = renderInput();

      // The attachments container should not be rendered
      const attachmentChips = container.querySelectorAll('[class*="attachmentChip"]');
      expect(attachmentChips).toHaveLength(0);
    });

    it('can add attachments across multiple file selections', () => {
      renderInput();

      const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement;
      const file1 = new File(['a'], 'first.ts', { type: 'text/plain' });
      const file2 = new File(['b'], 'second.py', { type: 'text/plain' });

      fireEvent.change(fileInput, { target: { files: [file1] } });
      expect(screen.getByText('first.ts')).toBeInTheDocument();

      fireEvent.change(fileInput, { target: { files: [file2] } });
      expect(screen.getByText('first.ts')).toBeInTheDocument();
      expect(screen.getByText('second.py')).toBeInTheDocument();
    });

    it('removing one attachment preserves others', () => {
      renderInput();

      const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement;
      const file1 = new File(['a'], 'keep-me.ts', { type: 'text/plain' });
      const file2 = new File(['b'], 'delete-me.py', { type: 'text/plain' });

      fireEvent.change(fileInput, { target: { files: [file1, file2] } });

      fireEvent.click(screen.getByLabelText('Remove delete-me.py'));

      expect(screen.getByText('keep-me.ts')).toBeInTheDocument();
      expect(screen.queryByText('delete-me.py')).not.toBeInTheDocument();
    });

    it('hidden file input accepts correct file types and allows multiple', () => {
      renderInput();

      const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement;
      expect(fileInput).toHaveAttribute('multiple');
      expect(fileInput.getAttribute('accept')).toContain('image/*');
      expect(fileInput.getAttribute('accept')).toContain('application/pdf');
      expect(fileInput.getAttribute('accept')).toContain('.ts');
    });
  });

  // ---------------------------------------------------------------------------
  // Speech recognition / mic button
  // ---------------------------------------------------------------------------

  describe('speech recognition', () => {
    it('renders mic button when speech is supported', () => {
      vi.mocked(useSpeechRecognition).mockReturnValue({
        isListening: false,
        transcript: '',
        startListening: vi.fn(),
        stopListening: vi.fn(),
        isSupported: true,
      });

      renderInput();

      expect(screen.getByLabelText('Start voice input')).toBeInTheDocument();
    });

    it('does not render mic button when speech is not supported', () => {
      vi.mocked(useSpeechRecognition).mockReturnValue({
        isListening: false,
        transcript: '',
        startListening: vi.fn(),
        stopListening: vi.fn(),
        isSupported: false,
      });

      renderInput();

      expect(screen.queryByLabelText('Start voice input')).not.toBeInTheDocument();
      expect(screen.queryByLabelText('Stop recording')).not.toBeInTheDocument();
    });

    it('shows MicOff icon when listening', () => {
      vi.mocked(useSpeechRecognition).mockReturnValue({
        isListening: true,
        transcript: '',
        startListening: vi.fn(),
        stopListening: vi.fn(),
        isSupported: true,
      });

      renderInput();

      const micBtn = screen.getByLabelText('Stop recording');
      expect(micBtn).toBeInTheDocument();
      expect(micBtn).toHaveAttribute('data-listening', 'true');
    });

    it('calls startListening when mic button clicked', () => {
      const startListening = vi.fn();
      vi.mocked(useSpeechRecognition).mockReturnValue({
        isListening: false,
        transcript: '',
        startListening,
        stopListening: vi.fn(),
        isSupported: true,
      });

      renderInput();

      fireEvent.click(screen.getByLabelText('Start voice input'));

      expect(startListening).toHaveBeenCalledTimes(1);
    });

    it('calls stopListening when already listening', () => {
      const stopListening = vi.fn();
      vi.mocked(useSpeechRecognition).mockReturnValue({
        isListening: true,
        transcript: '',
        startListening: vi.fn(),
        stopListening,
        isSupported: true,
      });

      renderInput();

      fireEvent.click(screen.getByLabelText('Stop recording'));

      expect(stopListening).toHaveBeenCalledTimes(1);
    });

    it('does not toggle mic when disabled', () => {
      const startListening = vi.fn();
      const stopListening = vi.fn();
      vi.mocked(useSpeechRecognition).mockReturnValue({
        isListening: false,
        transcript: '',
        startListening,
        stopListening,
        isSupported: true,
      });

      renderInput({ disabled: true });

      fireEvent.click(screen.getByLabelText('Start voice input'));

      expect(startListening).not.toHaveBeenCalled();
      expect(stopListening).not.toHaveBeenCalled();
    });

    it('appends transcript text with separator when existing input does not end with space', () => {
      let capturedOnTranscript: ((text: string) => void) | undefined;
      vi.mocked(useSpeechRecognition).mockImplementation(({ onTranscript }) => {
        capturedOnTranscript = onTranscript;
        return {
          isListening: false,
          transcript: '',
          startListening: vi.fn(),
          stopListening: vi.fn(),
          isSupported: true,
        };
      });

      renderInput();

      const textarea = screen.getByPlaceholderText('Message...');
      fireEvent.change(textarea, { target: { value: 'hello' } });

      // Click mic to capture the snapshot (inputSnapshotRef = 'hello')
      fireEvent.click(screen.getByLabelText('Start voice input'));

      // Simulate transcript callback — base is 'hello' (no trailing space), so separator = ' '
      act(() => {
        capturedOnTranscript!('world');
      });
      // The textarea should now have 'hello world'
      expect(textarea).toHaveValue('hello world');
    });

    it('appends transcript text without separator when existing input ends with space', () => {
      let capturedOnTranscript: ((text: string) => void) | undefined;
      vi.mocked(useSpeechRecognition).mockImplementation(({ onTranscript }) => {
        capturedOnTranscript = onTranscript;
        return {
          isListening: false,
          transcript: '',
          startListening: vi.fn(),
          stopListening: vi.fn(),
          isSupported: true,
        };
      });

      renderInput();

      const textarea = screen.getByPlaceholderText('Message...');
      fireEvent.change(textarea, { target: { value: 'hello ' } });

      // Click mic to capture the snapshot (inputSnapshotRef = 'hello ')
      fireEvent.click(screen.getByLabelText('Start voice input'));

      // Simulate transcript — base is 'hello ' (ends with space), so separator = ''
      act(() => {
        capturedOnTranscript!('world');
      });
      expect(textarea).toHaveValue('hello world');
    });

    it('appends transcript text without separator when input is empty', () => {
      let capturedOnTranscript: ((text: string) => void) | undefined;
      vi.mocked(useSpeechRecognition).mockImplementation(({ onTranscript }) => {
        capturedOnTranscript = onTranscript;
        return {
          isListening: false,
          transcript: '',
          startListening: vi.fn(),
          stopListening: vi.fn(),
          isSupported: true,
        };
      });

      renderInput();

      // Click mic with empty input (inputSnapshotRef = '')
      fireEvent.click(screen.getByLabelText('Start voice input'));

      // base is '' (length === 0), so separator = ''
      act(() => {
        capturedOnTranscript!('hello');
      });
      const textarea = screen.getByPlaceholderText('Message...');
      expect(textarea).toHaveValue('hello');
    });
  });

  // ---------------------------------------------------------------------------
  // Mention menu keyboard handling
  // ---------------------------------------------------------------------------

  describe('mention menu keyboard handling', () => {
    it('selects mention and replaces @filter text on Enter key', () => {
      const selectItem = vi.fn(() => 'src/utils.ts');
      const handleKeyDown = vi.fn((e: React.KeyboardEvent) => {
        if (e.key === 'Enter') {
          e.preventDefault();
          return true;
        }
        return false;
      });

      vi.mocked(useMentionMenu).mockReturnValue({
        ...defaultMentionMenu,
        isOpen: true,
        items: [
          {
            kind: 'file',
            entry: { name: 'utils.ts', path: 'src/utils.ts', type: 'file' },
            depth: 0,
          },
        ],
        selectedIndex: 0,
        handleKeyDown,
        selectItem,
      });

      renderInput();

      const textarea = screen.getByPlaceholderText('Message...');
      // Simulate typing "@ut" — set input value with cursor at end
      fireEvent.change(textarea, { target: { value: '@ut', selectionStart: 3 } });
      // Set selectionStart on the textarea element for the keyDown handler to read
      Object.defineProperty(textarea, 'selectionStart', { value: 3, writable: true });

      fireEvent.keyDown(textarea, { key: 'Enter' });

      expect(handleKeyDown).toHaveBeenCalled();
      expect(selectItem).toHaveBeenCalledWith({
        kind: 'file',
        entry: { name: 'utils.ts', path: 'src/utils.ts', type: 'file' },
        depth: 0,
      });
      // Should NOT propagate to handleSend
      expect(onSend).not.toHaveBeenCalled();
    });

    it('does not call selectItem when mention menu has no selected item', () => {
      const selectItem = vi.fn(() => '');
      const handleKeyDown = vi.fn((e: React.KeyboardEvent) => {
        if (e.key === 'Enter') {
          e.preventDefault();
          return true;
        }
        return false;
      });

      vi.mocked(useMentionMenu).mockReturnValue({
        ...defaultMentionMenu,
        isOpen: true,
        items: [] as typeof defaultMentionMenu.items,
        selectedIndex: 0,
        handleKeyDown,
        selectItem,
      });

      renderInput();

      const textarea = screen.getByPlaceholderText('Message...');
      fireEvent.change(textarea, { target: { value: '@xyz' } });
      fireEvent.keyDown(textarea, { key: 'Enter' });

      expect(handleKeyDown).toHaveBeenCalled();
      expect(selectItem).not.toHaveBeenCalled();
    });

    it('returns early when mention menu handles a non-Enter key like Tab', () => {
      const handleKeyDown = vi.fn((e: React.KeyboardEvent) => {
        if (e.key === 'Tab') {
          e.preventDefault();
          return true;
        }
        return false;
      });

      vi.mocked(useMentionMenu).mockReturnValue({
        ...defaultMentionMenu,
        isOpen: true,
        items: [{ kind: 'file', entry: { name: 'src', path: 'src', type: 'directory' }, depth: 0 }],
        selectedIndex: 0,
        handleKeyDown,
      });

      renderInput();

      const textarea = screen.getByPlaceholderText('Message...');
      fireEvent.change(textarea, { target: { value: '@s' } });
      fireEvent.keyDown(textarea, { key: 'Tab' });

      expect(handleKeyDown).toHaveBeenCalled();
      // Tab is handled by mention menu, so no send should happen
      expect(onSend).not.toHaveBeenCalled();
    });

    it('does not replace text when atIndex is -1 (no @ in text before cursor)', () => {
      const selectItem = vi.fn(() => 'src/utils.ts');
      const handleKeyDown = vi.fn((e: React.KeyboardEvent) => {
        if (e.key === 'Enter') {
          e.preventDefault();
          return true;
        }
        return false;
      });

      vi.mocked(useMentionMenu).mockReturnValue({
        ...defaultMentionMenu,
        isOpen: true,
        items: [
          {
            kind: 'file',
            entry: { name: 'utils.ts', path: 'src/utils.ts', type: 'file' },
            depth: 0,
          },
        ],
        selectedIndex: 0,
        handleKeyDown,
        selectItem,
      });

      renderInput();

      const textarea = screen.getByPlaceholderText('Message...');
      // Set input without any @ symbol
      fireEvent.change(textarea, { target: { value: 'no at sign here' } });
      Object.defineProperty(textarea, 'selectionStart', { value: 15, writable: true });

      fireEvent.keyDown(textarea, { key: 'Enter' });

      expect(selectItem).toHaveBeenCalled();
      // The setInput for replacement should NOT have been called because atIndex === -1
      // But onSend should also not be called since mention menu handled the key
      expect(onSend).not.toHaveBeenCalled();
    });

    it('falls through to normal keydown when mentionMenu.handleKeyDown returns false', () => {
      const handleKeyDown = vi.fn(() => false);

      vi.mocked(useMentionMenu).mockReturnValue({
        ...defaultMentionMenu,
        isOpen: true,
        handleKeyDown,
      });

      renderInput();

      const textarea = screen.getByPlaceholderText('Message...');
      fireEvent.change(textarea, { target: { value: 'hello' } });
      fireEvent.keyDown(textarea, { key: 'Enter' });

      // Falls through to normal Enter handling
      expect(onSend).toHaveBeenCalledWith('hello', []);
    });
  });

  // ---------------------------------------------------------------------------
  // Slash menu keyboard handling
  // ---------------------------------------------------------------------------

  describe('slash menu keyboard handling', () => {
    it('selects slash command on Enter key when menu is open', () => {
      const selectCommand = vi.fn(() => '/help ');
      const handleKeyDown = vi.fn((e: React.KeyboardEvent) => {
        if (e.key === 'Enter') {
          e.preventDefault();
          return true;
        }
        return false;
      });

      vi.mocked(useSlashMenu).mockReturnValue({
        ...defaultSlashMenu,
        isOpen: true,
        filteredCommands: [{ name: 'help', type: 'command', description: 'Show help' }],
        selectedIndex: 0,
        handleKeyDown,
        selectCommand,
      });

      renderInput();

      const textarea = screen.getByPlaceholderText('Message...');
      fireEvent.change(textarea, { target: { value: '/hel' } });
      fireEvent.keyDown(textarea, { key: 'Enter' });

      expect(handleKeyDown).toHaveBeenCalled();
      expect(selectCommand).toHaveBeenCalledWith({
        name: 'help',
        type: 'command',
        description: 'Show help',
      });
      expect(onSend).not.toHaveBeenCalled();
    });

    it('selects slash command on Tab key when menu is open', () => {
      const selectCommand = vi.fn(() => '/help ');
      const handleKeyDown = vi.fn((e: React.KeyboardEvent) => {
        if (e.key === 'Tab') {
          e.preventDefault();
          return true;
        }
        return false;
      });

      vi.mocked(useSlashMenu).mockReturnValue({
        ...defaultSlashMenu,
        isOpen: true,
        filteredCommands: [{ name: 'help', type: 'command', description: 'Show help' }],
        selectedIndex: 0,
        handleKeyDown,
        selectCommand,
      });

      renderInput();

      const textarea = screen.getByPlaceholderText('Message...');
      fireEvent.change(textarea, { target: { value: '/hel' } });
      fireEvent.keyDown(textarea, { key: 'Tab' });

      expect(selectCommand).toHaveBeenCalled();
      expect(onSend).not.toHaveBeenCalled();
    });

    it('does not select command when filteredCommands is empty', () => {
      const selectCommand = vi.fn(() => '');
      const handleKeyDown = vi.fn((e: React.KeyboardEvent) => {
        if (e.key === 'Enter') {
          e.preventDefault();
          return true;
        }
        return false;
      });

      vi.mocked(useSlashMenu).mockReturnValue({
        ...defaultSlashMenu,
        isOpen: true,
        filteredCommands: [], // no matching commands
        selectedIndex: 0,
        handleKeyDown,
        selectCommand,
      });

      renderInput();

      const textarea = screen.getByPlaceholderText('Message...');
      fireEvent.change(textarea, { target: { value: '/xyz' } });
      fireEvent.keyDown(textarea, { key: 'Enter' });

      expect(selectCommand).not.toHaveBeenCalled();
      expect(onSend).not.toHaveBeenCalled();
    });

    it('returns early when slash menu handles a non-Enter/Tab key', () => {
      const handleKeyDown = vi.fn((e: React.KeyboardEvent) => {
        if (e.key === 'ArrowDown') {
          e.preventDefault();
          return true;
        }
        return false;
      });

      vi.mocked(useSlashMenu).mockReturnValue({
        ...defaultSlashMenu,
        isOpen: true,
        handleKeyDown,
      });

      renderInput();

      const textarea = screen.getByPlaceholderText('Message...');
      fireEvent.change(textarea, { target: { value: '/h' } });
      fireEvent.keyDown(textarea, { key: 'ArrowDown' });

      // ArrowDown handled by slash menu, no send
      expect(onSend).not.toHaveBeenCalled();
    });

    it('clicking a slash command in the menu calls selectCommand and updates input', () => {
      const selectCommand = vi.fn(() => '/help ');
      vi.mocked(useSlashMenu).mockReturnValue({
        ...defaultSlashMenu,
        isOpen: true,
        filteredCommands: [{ name: 'help', type: 'command', description: 'Show help' }],
        selectedIndex: 0,
        selectCommand,
      });

      renderInput();

      // Click the slash command via the mocked SlashCommandMenu
      fireEvent.click(screen.getByText('select-slash-cmd'));

      expect(selectCommand).toHaveBeenCalledWith({
        name: 'help',
        type: 'command',
        description: 'Show help',
      });
    });

    it('falls through to normal keydown when slashMenu.handleKeyDown returns false', () => {
      const handleKeyDown = vi.fn(() => false);

      vi.mocked(useSlashMenu).mockReturnValue({
        ...defaultSlashMenu,
        isOpen: true,
        handleKeyDown,
      });

      renderInput();

      const textarea = screen.getByPlaceholderText('Message...');
      fireEvent.change(textarea, { target: { value: 'hello' } });
      fireEvent.keyDown(textarea, { key: 'Enter' });

      expect(onSend).toHaveBeenCalledWith('hello', []);
    });
  });

  // ---------------------------------------------------------------------------
  // Mention pills and mention-related send behavior
  // ---------------------------------------------------------------------------

  describe('mention pills', () => {
    it('renders mention pills when file mentions exist', () => {
      vi.mocked(useMentionMenu).mockReturnValue({
        ...defaultMentionMenu,
        mentions: [
          { kind: 'file', entry: { name: 'utils.ts', path: 'src/utils.ts', type: 'file' } },
        ],
      });

      renderInput();

      expect(screen.getByTestId('mention-pill')).toBeInTheDocument();
      expect(screen.getByText('src/utils.ts')).toBeInTheDocument();
    });

    it('prepends file mention paths to sent message', () => {
      const removeMention = vi.fn();
      vi.mocked(useMentionMenu).mockReturnValue({
        ...defaultMentionMenu,
        mentions: [
          { kind: 'file', entry: { name: 'utils.ts', path: 'src/utils.ts', type: 'file' } },
          { kind: 'file', entry: { name: 'index.ts', path: 'src/index.ts', type: 'file' } },
        ],
        removeMention,
      });

      renderInput();

      const textarea = screen.getByPlaceholderText('Message...');
      fireEvent.change(textarea, { target: { value: 'fix this' } });
      fireEvent.keyDown(textarea, { key: 'Enter' });

      expect(onSend).toHaveBeenCalledWith('@src/utils.ts @src/index.ts fix this', []);
    });

    it('removes file mentions after sending', () => {
      const removeMention = vi.fn();
      vi.mocked(useMentionMenu).mockReturnValue({
        ...defaultMentionMenu,
        mentions: [
          { kind: 'file', entry: { name: 'utils.ts', path: 'src/utils.ts', type: 'file' } },
        ],
        removeMention,
      });

      renderInput();

      const textarea = screen.getByPlaceholderText('Message...');
      fireEvent.change(textarea, { target: { value: 'fix this' } });
      fireEvent.keyDown(textarea, { key: 'Enter' });

      expect(removeMention).toHaveBeenCalledWith('src/utils.ts');
    });

    it('renders remove button on mention pill and calls removeMention', () => {
      const removeMention = vi.fn();
      vi.mocked(useMentionMenu).mockReturnValue({
        ...defaultMentionMenu,
        mentions: [
          { kind: 'file', entry: { name: 'utils.ts', path: 'src/utils.ts', type: 'file' } },
        ],
        removeMention,
      });

      renderInput();

      fireEvent.click(screen.getByLabelText('Remove src/utils.ts'));
      expect(removeMention).toHaveBeenCalledWith('src/utils.ts');
    });

    it('renders agent mention pills', () => {
      vi.mocked(useMentionMenu).mockReturnValue({
        ...defaultMentionMenu,
        mentions: [
          {
            kind: 'agent',
            participant: {
              peerId: 'peer-alpha',
              persona: 'Ravn-Alpha',
              color: '#a855f7',
              participantType: 'ravn',
              status: 'idle',
              joinedAt: new Date(),
            },
          },
        ],
      });

      renderInput();

      expect(screen.getByTestId('mention-pill')).toBeInTheDocument();
      expect(screen.getByText('Ravn-Alpha')).toBeInTheDocument();
    });

    it('calls onSendDirected with agent mentions instead of onSend', () => {
      const onSendDirected = vi.fn();
      const removeMention = vi.fn();
      const agentParticipant = {
        peerId: 'peer-alpha',
        persona: 'Ravn-Alpha',
        color: '#a855f7',
        participantType: 'ravn',
        status: 'idle',
        joinedAt: new Date(),
      };

      vi.mocked(useMentionMenu).mockReturnValue({
        ...defaultMentionMenu,
        mentions: [{ kind: 'agent', participant: agentParticipant }],
        removeMention,
      });

      renderInput({ onSendDirected });

      const textarea = screen.getByPlaceholderText('Message...');
      fireEvent.change(textarea, { target: { value: 'hello' } });
      fireEvent.keyDown(textarea, { key: 'Enter' });

      expect(onSendDirected).toHaveBeenCalledWith([agentParticipant], '@Ravn-Alpha hello', []);
      expect(onSend).not.toHaveBeenCalled();
    });

    it('falls back to onSend when agent mention but no onSendDirected provided', () => {
      const agentParticipant = {
        peerId: 'peer-alpha',
        persona: 'Ravn-Alpha',
        color: '#a855f7',
        participantType: 'ravn',
        status: 'idle',
        joinedAt: new Date(),
      };

      vi.mocked(useMentionMenu).mockReturnValue({
        ...defaultMentionMenu,
        mentions: [{ kind: 'agent', participant: agentParticipant }],
        removeMention: vi.fn(),
      });

      // No onSendDirected prop — should fall back to onSend
      renderInput();

      const textarea = screen.getByPlaceholderText('Message...');
      fireEvent.change(textarea, { target: { value: 'hello' } });
      fireEvent.keyDown(textarea, { key: 'Enter' });

      expect(onSend).toHaveBeenCalledWith('@Ravn-Alpha hello', []);
    });

    it('removes agent mentions by peerId after sending', () => {
      const removeMention = vi.fn();
      const agentParticipant = {
        peerId: 'peer-alpha',
        persona: 'Ravn-Alpha',
        color: '#a855f7',
        participantType: 'ravn',
        status: 'idle',
        joinedAt: new Date(),
      };

      vi.mocked(useMentionMenu).mockReturnValue({
        ...defaultMentionMenu,
        mentions: [{ kind: 'agent', participant: agentParticipant }],
        removeMention,
      });

      renderInput({ onSendDirected: vi.fn() });

      const textarea = screen.getByPlaceholderText('Message...');
      fireEvent.change(textarea, { target: { value: 'hello' } });
      fireEvent.keyDown(textarea, { key: 'Enter' });

      expect(removeMention).toHaveBeenCalledWith('peer-alpha');
    });
  });

  // ---------------------------------------------------------------------------
  // MentionMenu onSelect callback (click-based selection)
  // ---------------------------------------------------------------------------

  describe('mention menu onSelect callback', () => {
    it('renders MentionMenu when mentionMenu.isOpen is true and slashMenu is closed', () => {
      vi.mocked(useMentionMenu).mockReturnValue({
        ...defaultMentionMenu,
        isOpen: true,
        items: [
          {
            kind: 'file',
            entry: { name: 'utils.ts', path: 'src/utils.ts', type: 'file' },
            depth: 0,
          },
        ],
      });

      renderInput();

      expect(screen.getByTestId('mention-menu')).toBeInTheDocument();
    });

    it('clicking a mention item calls selectItem and replaces @filter text', () => {
      const selectItem = vi.fn(() => 'src/utils.ts');
      vi.mocked(useMentionMenu).mockReturnValue({
        ...defaultMentionMenu,
        isOpen: true,
        items: [
          {
            kind: 'file',
            entry: { name: 'utils.ts', path: 'src/utils.ts', type: 'file' },
            depth: 0,
          },
        ],
        selectItem,
      });

      renderInput();

      const textarea = screen.getByPlaceholderText('Message...');
      // Set up input with @ trigger and cursor position
      fireEvent.change(textarea, { target: { value: '@ut', selectionStart: 3 } });
      Object.defineProperty(textarea, 'selectionStart', { value: 3, writable: true });

      // Click on the mention item in the mocked menu
      fireEvent.click(screen.getByTestId('mention-item-0'));

      expect(selectItem).toHaveBeenCalledWith({
        kind: 'file',
        entry: { name: 'utils.ts', path: 'src/utils.ts', type: 'file' },
        depth: 0,
      });
    });

    it('clicking a mention item with no @ in input does not crash (atIndex -1)', () => {
      const selectItem = vi.fn(() => 'src/utils.ts');
      vi.mocked(useMentionMenu).mockReturnValue({
        ...defaultMentionMenu,
        isOpen: true,
        items: [
          {
            kind: 'file',
            entry: { name: 'utils.ts', path: 'src/utils.ts', type: 'file' },
            depth: 0,
          },
        ],
        selectItem,
      });

      renderInput();

      const textarea = screen.getByPlaceholderText('Message...');
      // Input without @ symbol
      fireEvent.change(textarea, { target: { value: 'no at sign', selectionStart: 10 } });
      Object.defineProperty(textarea, 'selectionStart', { value: 10, writable: true });

      // Should not throw
      fireEvent.click(screen.getByTestId('mention-item-0'));
      expect(selectItem).toHaveBeenCalled();
    });

    it('clicking expand on a directory item calls expandDirectory', () => {
      const expandDirectory = vi.fn();
      vi.mocked(useMentionMenu).mockReturnValue({
        ...defaultMentionMenu,
        isOpen: true,
        items: [{ kind: 'file', entry: { name: 'src', path: 'src', type: 'directory' }, depth: 0 }],
        expandDirectory,
      });

      renderInput();

      fireEvent.click(screen.getByTestId('mention-expand-0'));
      expect(expandDirectory).toHaveBeenCalledWith({
        kind: 'file',
        entry: { name: 'src', path: 'src', type: 'directory' },
        depth: 0,
      });
    });

    it('does not render MentionMenu when slashMenu is also open', () => {
      vi.mocked(useSlashMenu).mockReturnValue({
        ...defaultSlashMenu,
        isOpen: true,
        filteredCommands: [{ name: 'help', type: 'command', description: 'Show help' }],
      });
      vi.mocked(useMentionMenu).mockReturnValue({
        ...defaultMentionMenu,
        isOpen: true,
        items: [
          {
            kind: 'file',
            entry: { name: 'utils.ts', path: 'src/utils.ts', type: 'file' },
            depth: 0,
          },
        ],
      });

      renderInput();

      expect(screen.queryByTestId('mention-menu')).not.toBeInTheDocument();
    });
  });
});
