import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type ChangeEvent,
  type KeyboardEvent,
} from 'react';
import { ArrowUp, Mic, MicOff, Paperclip, Square, X } from 'lucide-react';
import { cn } from '@/utils';
import { useSpeechRecognition } from '@/hooks';
import type { RoomParticipant } from '@/modules/shared/hooks/useSkuldChat';
import type { SlashCommand } from './slashCommands';
import { SlashCommandMenu } from './SlashCommandMenu';
import { useSlashMenu } from './useSlashMenu';
import { MentionMenu } from './MentionMenu';
import { MentionPill } from './MentionPill';
import { useMentionMenu } from './useMentionMenu';
import type { SelectedMention } from './useMentionMenu';
import { useFileAttachments, type FileAttachment } from './useFileAttachments';
import styles from './ChatInput.module.css';

interface ChatInputProps {
  onSend: (text: string, attachments: FileAttachment[]) => void;
  onSendDirected?: (
    participants: RoomParticipant[],
    text: string,
    attachments: FileAttachment[]
  ) => void;
  isLoading: boolean;
  onStop: () => void;
  disabled?: boolean;
  stopDisabled?: boolean;
  className?: string;
  sessionId?: string | null;
  sessionHost?: string | null;
  chatEndpoint?: string | null;
  availableCommands?: readonly SlashCommand[];
  participants?: ReadonlyMap<string, RoomParticipant>;
}

const ACCEPTED_FILE_TYPES = [
  'image/*',
  'application/pdf',
  '.ts',
  '.tsx',
  '.js',
  '.jsx',
  '.py',
  '.rs',
  '.go',
  '.java',
  '.c',
  '.cpp',
  '.h',
  '.hpp',
  '.css',
  '.html',
  '.json',
  '.yaml',
  '.yml',
  '.toml',
  '.md',
  '.txt',
  '.sh',
  '.bash',
  '.sql',
].join(',');

export function ChatInput({
  onSend,
  onSendDirected,
  isLoading,
  onStop,
  disabled = false,
  stopDisabled = false,
  className,
  sessionId = null,
  sessionHost = null,
  chatEndpoint = null,
  availableCommands,
  participants = new Map(),
}: ChatInputProps) {
  const [input, setInput] = useState('');
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const inputSnapshotRef = useRef('');
  const handleTranscript = useCallback((text: string) => {
    const base = inputSnapshotRef.current;
    const separator = base.length > 0 && !base.endsWith(' ') ? ' ' : '';
    setInput(base + separator + text);
  }, []);

  const {
    isListening,
    startListening: rawStartListening,
    stopListening,
    isSupported: speechSupported,
  } = useSpeechRecognition({ onTranscript: handleTranscript });

  // Capture the current input when recording starts so appended text is relative
  const startListening = useCallback(() => {
    inputSnapshotRef.current = input;
    rawStartListening();
  }, [input, rawStartListening]);

  const slashMenu = useSlashMenu(availableCommands as SlashCommand[] | undefined);
  const mentionMenu = useMentionMenu(sessionId, sessionHost, chatEndpoint, participants);
  const {
    attachments: fileAttachmentsList,
    isDragging,
    addFiles,
    removeAttachment,
    clearAttachments: clearFileAttachments,
    handleDragOver,
    handleDragLeave,
    handleDrop,
    handlePaste,
  } = useFileAttachments();

  const hasContent = input.trim().length > 0 || fileAttachmentsList.length > 0;

  const resetTextareaHeight = useCallback(() => {
    const textarea = textareaRef.current;
    if (!textarea) {
      return;
    }
    textarea.style.height = 'auto';
    textarea.style.height = `${Math.min(textarea.scrollHeight, 200)}px`;
  }, []);

  useEffect(() => {
    resetTextareaHeight();
  }, [input, resetTextareaHeight]);

  useEffect(() => {
    if (isLoading || disabled) {
      return;
    }
    textareaRef.current?.focus();
  }, [isLoading, disabled]);

  const handleSend = useCallback(() => {
    const trimmed = input.trim();
    if (!trimmed || disabled) {
      return;
    }

    // Separate agent mentions from file mentions
    const agentMentions = mentionMenu.mentions
      .filter((m): m is { kind: 'agent'; participant: RoomParticipant } => m.kind === 'agent')
      .map(m => m.participant);

    const fileMentions = mentionMenu.mentions.filter(
      (m): m is Extract<SelectedMention, { kind: 'file' }> => m.kind === 'file'
    );

    const agentPrefixes = agentMentions.map(p => `@${p.persona}`);
    const filePaths = fileMentions.map(m => `@${m.entry.path}`);
    const allPrefixes = [...agentPrefixes, ...filePaths];
    const fullMessage = allPrefixes.length > 0 ? `${allPrefixes.join(' ')} ${trimmed}` : trimmed;

    if (agentMentions.length > 0 && onSendDirected) {
      onSendDirected(agentMentions, fullMessage, fileAttachmentsList);
    } else {
      onSend(fullMessage, fileAttachmentsList);
    }

    setInput('');
    clearFileAttachments();
    // Clear all mentions after send
    for (const m of mentionMenu.mentions) {
      const id = m.kind === 'file' ? m.entry.path : m.participant.peerId;
      mentionMenu.removeMention(id);
    }
  }, [
    input,
    disabled,
    onSend,
    onSendDirected,
    mentionMenu,
    fileAttachmentsList,
    clearFileAttachments,
  ]);

  const handleKeyDown = useCallback(
    (e: KeyboardEvent<HTMLTextAreaElement>) => {
      // Let slash menu handle navigation keys first
      if (slashMenu.isOpen) {
        const handled = slashMenu.handleKeyDown(e);
        if (handled) {
          if (e.key === 'Tab' || e.key === 'Enter') {
            const selected = slashMenu.filteredCommands[slashMenu.selectedIndex];
            if (selected) {
              const newInput = slashMenu.selectCommand(selected);
              setInput(newInput);
            }
          }
          return;
        }
      }

      // Let mention menu handle navigation keys
      if (mentionMenu.isOpen) {
        const handled = mentionMenu.handleKeyDown(e);
        if (handled) {
          if (e.key === 'Enter') {
            const selected = mentionMenu.items[mentionMenu.selectedIndex];
            // For files: directories expand on Enter; only select files
            // For agents: select immediately
            if (selected) {
              const isDirectory = selected.kind === 'file' && selected.entry.type === 'directory';
              if (!isDirectory) {
                const selectedLabel = mentionMenu.selectItem(selected);
                const textarea = textareaRef.current;
                if (textarea) {
                  const cursorPos = textarea.selectionStart;
                  const before = input.slice(0, cursorPos);
                  const atIndex = before.lastIndexOf('@');
                  if (atIndex !== -1) {
                    const after = input.slice(cursorPos);
                    setInput(before.slice(0, atIndex) + '@' + selectedLabel + ' ' + after);
                  }
                }
              }
            }
          }
          return;
        }
      }

      if (e.key !== 'Enter') {
        return;
      }
      if (e.shiftKey) {
        return;
      }
      e.preventDefault();
      handleSend();
    },
    [handleSend, slashMenu, mentionMenu, input]
  );

  const handleChange = useCallback(
    (e: ChangeEvent<HTMLTextAreaElement>) => {
      const value = e.target.value;
      const cursorPos = e.target.selectionStart;
      setInput(value);
      slashMenu.handleChange(value);
      mentionMenu.handleChange(value, cursorPos);
    },
    [slashMenu, mentionMenu]
  );

  const handleAttachClick = useCallback(() => {
    if (disabled) {
      return;
    }
    fileInputRef.current?.click();
  }, [disabled]);

  const handleFileChange = useCallback(
    (e: ChangeEvent<HTMLInputElement>) => {
      const files = e.target.files;
      if (!files) {
        return;
      }
      addFiles(files);
      // Reset the input so the same file can be re-attached
      e.target.value = '';
    },
    [addFiles]
  );

  const handleMicToggle = useCallback(() => {
    if (disabled) {
      return;
    }
    if (isListening) {
      stopListening();
      return;
    }
    startListening();
  }, [disabled, isListening, startListening, stopListening]);

  return (
    <div
      className={cn(styles.wrapper, className)}
      data-disabled={disabled}
      data-drag-over={isDragging}
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
      onDrop={handleDrop}
      onPaste={handlePaste}
    >
      {(fileAttachmentsList.length > 0 || mentionMenu.mentions.length > 0) && (
        <div className={styles.attachments}>
          {mentionMenu.mentions.map(mention => (
            <MentionPill
              key={mention.kind === 'file' ? mention.entry.path : mention.participant.peerId}
              mention={mention}
              onRemove={mentionMenu.removeMention}
            />
          ))}
          {fileAttachmentsList.map(attachment => (
            <span key={attachment.id} className={styles.attachmentChip}>
              {attachment.previewUrl && (
                <img
                  src={attachment.previewUrl}
                  alt={attachment.name}
                  className={styles.attachmentThumbnail}
                />
              )}
              <span className={styles.attachmentName}>{attachment.name}</span>
              <button
                type="button"
                className={styles.attachmentRemove}
                onClick={() => removeAttachment(attachment.id)}
                aria-label={`Remove ${attachment.name}`}
              >
                <X className={styles.attachmentRemoveIcon} />
              </button>
            </span>
          ))}
        </div>
      )}

      <div className={styles.inputArea}>
        {slashMenu.isOpen && (
          <SlashCommandMenu
            selectedIndex={slashMenu.selectedIndex}
            commands={slashMenu.filteredCommands}
            onSelect={cmd => {
              const newInput = slashMenu.selectCommand(cmd);
              setInput(newInput);
              textareaRef.current?.focus();
            }}
          />
        )}
        {mentionMenu.isOpen && !slashMenu.isOpen && (
          <MentionMenu
            items={mentionMenu.items}
            selectedIndex={mentionMenu.selectedIndex}
            loading={mentionMenu.loading}
            onSelect={item => {
              const selectedLabel = mentionMenu.selectItem(item);
              const textarea = textareaRef.current;
              if (textarea) {
                const cursorPos = textarea.selectionStart;
                const before = input.slice(0, cursorPos);
                const atIndex = before.lastIndexOf('@');
                if (atIndex !== -1) {
                  const after = input.slice(cursorPos);
                  setInput(before.slice(0, atIndex) + '@' + selectedLabel + ' ' + after);
                }
              }
              textareaRef.current?.focus();
            }}
            onExpand={item => {
              mentionMenu.expandDirectory(item);
              textareaRef.current?.focus();
            }}
          />
        )}
        <textarea
          ref={textareaRef}
          className={styles.textarea}
          value={input}
          onChange={handleChange}
          onKeyDown={handleKeyDown}
          placeholder={disabled ? 'Start session to chat...' : 'Message...'}
          disabled={disabled}
          rows={1}
        />
      </div>

      <div className={styles.bottomBar}>
        <div className={styles.leftActions}>
          <button
            type="button"
            className={styles.iconBtn}
            onClick={handleAttachClick}
            data-disabled={disabled}
            aria-label="Attach file"
          >
            <Paperclip className={styles.btnIcon} />
          </button>
          <input
            ref={fileInputRef}
            type="file"
            className={styles.hiddenInput}
            accept={ACCEPTED_FILE_TYPES}
            onChange={handleFileChange}
            multiple
          />
          {speechSupported && (
            <button
              type="button"
              className={cn(styles.iconBtn, isListening && styles.micActive)}
              onClick={handleMicToggle}
              data-disabled={disabled}
              data-listening={isListening}
              aria-label={isListening ? 'Stop recording' : 'Start voice input'}
            >
              {isListening ? (
                <MicOff className={styles.btnIcon} />
              ) : (
                <Mic className={styles.btnIcon} />
              )}
            </button>
          )}
        </div>

        <div className={styles.rightActions}>
          {isLoading && (
            <button
              type="button"
              className={styles.stopBtn}
              onClick={onStop}
              disabled={stopDisabled}
              title={stopDisabled ? 'Interrupt not supported by this transport' : 'Stop generation'}
              data-testid="stop-btn"
            >
              <Square className={styles.stopIcon} />
              <span>Stop</span>
            </button>
          )}

          <button
            type="button"
            className={styles.sendBtn}
            data-active={hasContent && !disabled}
            onClick={handleSend}
            disabled={!hasContent || disabled}
            aria-label="Send message"
          >
            <ArrowUp className={styles.sendIcon} />
          </button>
        </div>
      </div>
    </div>
  );
}
