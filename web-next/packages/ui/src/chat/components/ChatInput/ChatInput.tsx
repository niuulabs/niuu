import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type ChangeEvent,
  type KeyboardEvent,
} from 'react';
import { ArrowUp, Paperclip, Square, X } from 'lucide-react';
import { cn } from '../../../utils/cn';
import { useFileAttachments, type FileAttachment } from '../../hooks/useFileAttachments';
import { useSlashMenu } from '../../hooks/useSlashMenu';
import { useMentionMenu } from '../../hooks/useMentionMenu';
import { SlashCommandMenu } from '../SlashCommandMenu';
import { MentionMenu } from '../MentionMenu';
import { MentionPill } from '../MentionPill';
import type { RoomParticipant, FileEntry } from '../../types';
import type { SlashCommand } from '../../utils/slashCommands';
import type { SelectedMention } from '../../hooks/useMentionMenu';
import './ChatInput.css';

const EMPTY_PARTICIPANTS: ReadonlyMap<string, RoomParticipant> = new Map();

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

function resolveInlineAgentMentions(
  input: string,
  participants: ReadonlyMap<string, RoomParticipant>,
): RoomParticipant[] {
  const byPersona = new Map(
    Array.from(participants.values())
      .filter((participant) => participant.participantType === 'ravn')
      .map((participant) => [participant.persona.toLowerCase(), participant] as const),
  );
  const seen = new Set<string>();
  const matches = input.matchAll(/(^|\s)@([^\s@]+)/g);
  const resolved: RoomParticipant[] = [];
  for (const match of matches) {
    const persona = match[2]?.toLowerCase();
    if (!persona) continue;
    const participant = byPersona.get(persona);
    if (!participant || seen.has(participant.peerId)) continue;
    seen.add(participant.peerId);
    resolved.push(participant);
  }
  return resolved;
}

interface ChatInputProps {
  onSend: (text: string, attachments: FileAttachment[]) => void;
  onSendDirected?: (
    participants: RoomParticipant[],
    text: string,
    attachments: FileAttachment[],
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
  onFetchFiles?: (path: string, apiBase: string) => Promise<FileEntry[]>;
}

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
  participants = EMPTY_PARTICIPANTS,
  onFetchFiles,
}: ChatInputProps) {
  const [input, setInput] = useState('');
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const slashMenu = useSlashMenu(availableCommands as SlashCommand[] | undefined);
  const mentionMenu = useMentionMenu(
    sessionId,
    sessionHost,
    chatEndpoint,
    participants,
    onFetchFiles,
  );
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
    if (!textarea) return;
    textarea.style.height = 'auto';
    textarea.style.height = `${Math.min(textarea.scrollHeight, 200)}px`;
  }, []);

  useEffect(() => {
    resetTextareaHeight();
  }, [input, resetTextareaHeight]);

  useEffect(() => {
    if (isLoading || disabled) return;
    textareaRef.current?.focus();
  }, [isLoading, disabled]);

  const handleSend = useCallback(() => {
    const trimmed = input.trim();
    if (!trimmed || disabled) return;

    const selectedAgentMentions = mentionMenu.mentions
      .filter((m): m is { kind: 'agent'; participant: RoomParticipant } => m.kind === 'agent')
      .map((m) => m.participant);
    const inlineAgentMentions = resolveInlineAgentMentions(trimmed, participants);
    const agentMentions = Array.from(
      new Map(
        [...selectedAgentMentions, ...inlineAgentMentions].map((participant) => [
          participant.peerId,
          participant,
        ]),
      ).values(),
    );

    const fileMentions = mentionMenu.mentions.filter(
      (m): m is Extract<SelectedMention, { kind: 'file' }> => m.kind === 'file',
    );

    const agentPrefixes = selectedAgentMentions.map((p) => `@${p.persona}`);
    const filePaths = fileMentions.map((m) => `@${m.entry.path}`);
    const allPrefixes = [...agentPrefixes, ...filePaths];
    const fullMessage = allPrefixes.length > 0 ? `${allPrefixes.join(' ')} ${trimmed}` : trimmed;

    if (agentMentions.length > 0 && onSendDirected) {
      onSendDirected(agentMentions, fullMessage, fileAttachmentsList);
    } else {
      onSend(fullMessage, fileAttachmentsList);
    }

    setInput('');
    clearFileAttachments();
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
    participants,
  ]);

  const handleKeyDown = useCallback(
    (e: KeyboardEvent<HTMLTextAreaElement>) => {
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

      if (mentionMenu.isOpen) {
        const handled = mentionMenu.handleKeyDown(e);
        if (handled) {
          if (e.key === 'Enter' || e.key === 'Tab') {
            const selected = mentionMenu.items[mentionMenu.selectedIndex];
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

      if (e.key !== 'Enter') return;
      if (e.shiftKey) return;
      e.preventDefault();
      handleSend();
    },
    [handleSend, slashMenu, mentionMenu, input],
  );

  const handleChange = useCallback(
    (e: ChangeEvent<HTMLTextAreaElement>) => {
      const value = e.target.value;
      const cursorPos = e.target.selectionStart;
      setInput(value);
      slashMenu.handleChange(value);
      mentionMenu.handleChange(value, cursorPos);
    },
    [slashMenu, mentionMenu],
  );

  const handleAttachClick = useCallback(() => {
    if (disabled) return;
    fileInputRef.current?.click();
  }, [disabled]);

  const handleFileChange = useCallback(
    (e: ChangeEvent<HTMLInputElement>) => {
      const files = e.target.files;
      if (!files) return;
      addFiles(files);
      e.target.value = '';
    },
    [addFiles],
  );

  return (
    <div
      className={cn('niuu-chat-input-wrapper', className)}
      data-disabled={disabled || undefined}
      data-drag-over={isDragging || undefined}
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
      onDrop={handleDrop}
      onPaste={handlePaste}
      data-testid="chat-input"
    >
      {(fileAttachmentsList.length > 0 || mentionMenu.mentions.length > 0) && (
        <div className="niuu-chat-input-attachments">
          {mentionMenu.mentions.map((mention) => (
            <MentionPill
              key={mention.kind === 'file' ? mention.entry.path : mention.participant.peerId}
              mention={mention}
              onRemove={mentionMenu.removeMention}
            />
          ))}
          {fileAttachmentsList.map((attachment) => (
            <span key={attachment.id} className="niuu-chat-attachment-chip">
              {attachment.previewUrl && (
                <img
                  src={attachment.previewUrl}
                  alt={attachment.name}
                  className="niuu-chat-attachment-thumbnail"
                />
              )}
              <span className="niuu-chat-attachment-chip-name">{attachment.name}</span>
              <button
                type="button"
                className="niuu-chat-attachment-remove"
                onClick={() => removeAttachment(attachment.id)}
                aria-label={`Remove ${attachment.name}`}
              >
                <X className="niuu-chat-attachment-remove-icon" />
              </button>
            </span>
          ))}
        </div>
      )}

      <div className="niuu-chat-input-area">
        {slashMenu.isOpen && (
          <SlashCommandMenu
            selectedIndex={slashMenu.selectedIndex}
            commands={slashMenu.filteredCommands}
            onSelect={(cmd) => {
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
            onSelect={(item) => {
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
            onExpand={(item) => {
              mentionMenu.expandDirectory(item);
              textareaRef.current?.focus();
            }}
          />
        )}
        <textarea
          ref={textareaRef}
          className="niuu-chat-textarea"
          value={input}
          onChange={handleChange}
          onKeyDown={handleKeyDown}
          placeholder={disabled ? 'Start session to chat...' : 'Message...'}
          disabled={disabled}
          rows={1}
          data-testid="chat-textarea"
        />
      </div>

      <div className="niuu-chat-input-bottom-bar">
        <div className="niuu-chat-input-left-actions">
          <button
            type="button"
            className="niuu-chat-input-icon-btn"
            onClick={handleAttachClick}
            data-disabled={disabled || undefined}
            aria-label="Attach file"
            data-testid="attach-btn"
          >
            <Paperclip className="niuu-chat-input-btn-icon" />
          </button>
          <input
            ref={fileInputRef}
            type="file"
            className="niuu-chat-input-hidden"
            accept={ACCEPTED_FILE_TYPES}
            onChange={handleFileChange}
            multiple
          />
        </div>

        <div className="niuu-chat-input-right-actions">
          {isLoading && (
            <button
              type="button"
              className="niuu-chat-stop-btn"
              onClick={onStop}
              disabled={stopDisabled}
              title={stopDisabled ? 'Interrupt not supported by this transport' : 'Stop generation'}
              data-testid="stop-btn"
            >
              <Square className="niuu-chat-stop-icon" />
              <span>Stop</span>
            </button>
          )}
          <button
            type="button"
            className="niuu-chat-send-btn"
            data-active={(hasContent && !disabled) || undefined}
            onClick={handleSend}
            disabled={!hasContent || disabled}
            aria-label="Send message"
            data-testid="send-btn"
          >
            <ArrowUp className="niuu-chat-send-icon" />
          </button>
        </div>
      </div>
    </div>
  );
}
