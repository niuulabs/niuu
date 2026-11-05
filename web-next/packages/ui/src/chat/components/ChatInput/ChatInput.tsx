import {
  useCallback,
  useEffect,
  useId,
  useLayoutEffect,
  useRef,
  useState,
  type ChangeEvent,
  type KeyboardEvent,
} from 'react';
import { AlertTriangle, ArrowUp, AudioLines, Plus, Slash, Square, X } from 'lucide-react';
import { cn } from '../../../utils/cn';
import { useFileAttachments, type FileAttachment } from '../../hooks/useFileAttachments';
import { useSlashMenu } from '../../hooks/useSlashMenu';
import { useDictation } from '../../hooks/useDictation';
import { mentionId, useMentionMenu } from '../../hooks/useMentionMenu';
import { SlashCommandMenu } from '../SlashCommandMenu';
import { MentionMenu } from '../MentionMenu';
import { MentionPill } from '../MentionPill';
import { ChipMenu } from './ChipMenu';
import type {
  AgentEventTarget,
  RoomParticipant,
  FileEntry,
  SessionCapabilities,
  SessionEffort,
  SessionRuntimeOptions,
} from '../../types';
import type { SlashCommand } from '../../utils/slashCommands';
import type { SelectedMention } from '../../hooks/useMentionMenu';
import './ChatInput.css';

const EMPTY_PARTICIPANTS: ReadonlyMap<string, RoomParticipant> = new Map();

/** Effort levels whose wire name is not how people say it. */
const EFFORT_LABELS: Record<string, string> = { xhigh: 'Extra high' };

function effortLabel(level: string): string {
  return EFFORT_LABELS[level] ?? `${level.charAt(0).toUpperCase()}${level.slice(1)}`;
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

function resolveInlineAgentMentions(
  input: string,
  participants: ReadonlyMap<string, RoomParticipant>,
): RoomParticipant[] {
  const byPersona = new Map<string, RoomParticipant | null>();
  for (const participant of participants.values()) {
    if (participant.participantType !== 'ravn' || participant.status === 'left') continue;
    for (const label of [participant.persona, participant.displayName, participant.peerId]) {
      if (!label) continue;
      const key = label.toLowerCase();
      const existing = byPersona.get(key);
      byPersona.set(
        key,
        existing !== undefined && existing?.peerId !== participant.peerId ? null : participant,
      );
    }
  }
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
  onPublishEvent?: (target: AgentEventTarget, text: string) => void;
  eventRouting?: boolean;
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
  /** What the session's transport supports; `steer` changes the send hint while working. */
  capabilities?: SessionCapabilities;
  /** Reasoning effort choices; the chip is shown when `mutable` and `onSetEffort` is given. */
  effort?: SessionEffort | null;
  /** Model catalog and current choice; the chip is shown when `onSetRuntimeModel` is given. */
  runtimeOptions?: SessionRuntimeOptions | null;
  onSetEffort?: (effort: string) => void;
  onSetRuntimeModel?: (model: string) => void;
  /** Why the runtime refused to list its models; shown on the model chip in its place. */
  runtimeOptionsError?: string | null;
  /** Why the runtime refused to report its effort levels; shown on the effort chip. */
  effortError?: string | null;
}

export function ChatInput({
  onSend,
  onSendDirected,
  onPublishEvent,
  eventRouting = false,
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
  capabilities,
  effort = null,
  runtimeOptions = null,
  onSetEffort,
  onSetRuntimeModel,
  runtimeOptionsError = null,
  effortError = null,
}: ChatInputProps) {
  const [input, setInput] = useState('');
  // Lexi's microphone: each finished phrase lands at the end of the draft.
  const dictation = useDictation((text) =>
    setInput((draft) => (draft.trim() ? `${draft.replace(/\s+$/, '')} ${text}` : text)),
  );
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const slashMenuId = useId();
  const slashMenu = useSlashMenu(eventRouting ? undefined : availableCommands);
  const canBrowseCommands = !eventRouting && Boolean(availableCommands?.length);
  const mentionMenu = useMentionMenu(
    sessionId,
    sessionHost,
    chatEndpoint,
    participants,
    onFetchFiles,
    eventRouting,
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
  const selectedEventMention = mentionMenu.mentions.find(
    (mention): mention is Extract<SelectedMention, { kind: 'agent' }> & { eventType: string } =>
      mention.kind === 'agent' && Boolean(mention.eventType),
  );
  const canSend = hasContent;

  const resetTextareaHeight = useCallback(() => {
    const textarea = textareaRef.current;
    if (!textarea) return;
    textarea.style.height = 'auto';
    textarea.style.height = `${textarea.scrollHeight}px`;
  }, []);

  useLayoutEffect(() => {
    resetTextareaHeight();
  }, [input, resetTextareaHeight]);

  useEffect(() => {
    const textarea = textareaRef.current;
    if (!textarea) return;
    let previousWidth = textarea.getBoundingClientRect().width;
    const observer = new ResizeObserver(([entry]) => {
      if (!entry || entry.contentRect.width === previousWidth) return;
      previousWidth = entry.contentRect.width;
      resetTextareaHeight();
    });
    observer.observe(textarea);
    return () => observer.disconnect();
  }, [resetTextareaHeight]);

  useEffect(() => {
    if (isLoading || disabled) return;
    textareaRef.current?.focus();
  }, [isLoading, disabled]);

  const handleSend = useCallback(() => {
    const trimmed = input.trim();
    if (!trimmed || disabled) return;

    if (eventRouting && selectedEventMention) {
      if (!onPublishEvent) return;
      const eventPrefix = `@${selectedEventMention.eventType}`;
      const fullMessage = trimmed.startsWith(eventPrefix) ? trimmed : `${eventPrefix} ${trimmed}`;
      onPublishEvent(
        {
          participant: selectedEventMention.participant,
          eventType: selectedEventMention.eventType,
        },
        fullMessage,
      );
      setInput('');
      clearFileAttachments();
      for (const mention of mentionMenu.mentions) {
        mentionMenu.removeMention(mentionId(mention));
      }
      return;
    }

    const selectedAgentMentions = mentionMenu.mentions
      .filter((m): m is Extract<SelectedMention, { kind: 'agent' }> => m.kind === 'agent')
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
    slashMenu.close();
    for (const mention of mentionMenu.mentions) {
      mentionMenu.removeMention(mentionId(mention));
    }
  }, [
    input,
    disabled,
    onSend,
    onSendDirected,
    onPublishEvent,
    eventRouting,
    selectedEventMention,
    mentionMenu,
    fileAttachmentsList,
    clearFileAttachments,
    slashMenu,
    participants,
  ]);

  const handleKeyDown = useCallback(
    (e: KeyboardEvent<HTMLTextAreaElement>) => {
      if (e.nativeEvent.isComposing) return;
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

  const queueWhileWorking = isLoading && capabilities?.steer === true;
  const models = (runtimeOptions?.models ?? []).filter((model) => !model.hidden);
  const currentModel = runtimeOptions?.models.find(
    (model) => model.model === runtimeOptions.current.model,
  );
  const showModelChip = Boolean(runtimeOptions && onSetRuntimeModel);
  const showEffortChip = Boolean(effort?.mutable && onSetEffort);

  return (
    <div
      className={cn('niuu-chat-input-wrapper', className)}
      data-disabled={disabled || undefined}
      data-drag-over={isDragging || undefined}
      onDragOver={eventRouting ? undefined : handleDragOver}
      onDragLeave={eventRouting ? undefined : handleDragLeave}
      onDrop={eventRouting ? undefined : handleDrop}
      onPaste={eventRouting ? undefined : handlePaste}
      data-testid="chat-input"
    >
      <div className="niuu-chat-input-box">
        {(fileAttachmentsList.length > 0 || mentionMenu.mentions.length > 0) && (
          <div className="niuu-chat-input-attachments">
            {mentionMenu.mentions.map((mention) => (
              <MentionPill
                key={mentionId(mention)}
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
              id={slashMenuId}
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
            placeholder={
              disabled
                ? 'Start session to chat...'
                : eventRouting
                  ? 'Message the room, or select a participant with @...'
                  : 'Steer this session…'
            }
            disabled={disabled}
            aria-label="Message"
            aria-autocomplete={canBrowseCommands ? 'list' : undefined}
            aria-controls={slashMenu.isOpen ? slashMenuId : undefined}
            aria-activedescendant={
              slashMenu.isOpen ? `${slashMenuId}-${slashMenu.selectedIndex}` : undefined
            }
            rows={1}
            data-testid="chat-textarea"
          />
        </div>

        <div className="niuu-chat-input-actions">
          <div className="niuu-chat-input-left-actions">
            {!eventRouting && (
              <>
                <button
                  type="button"
                  className="niuu-chat-input-icon-btn"
                  onClick={handleAttachClick}
                  data-disabled={disabled || undefined}
                  aria-label="Attach file"
                  data-testid="attach-btn"
                >
                  <Plus className="niuu-chat-input-btn-icon" />
                </button>
                <input
                  ref={fileInputRef}
                  type="file"
                  className="niuu-chat-input-hidden"
                  accept={ACCEPTED_FILE_TYPES}
                  onChange={handleFileChange}
                  multiple
                />
              </>
            )}
            {canBrowseCommands && (
              <button
                type="button"
                className="niuu-chat-input-icon-btn"
                aria-label="Slash commands"
                aria-expanded={slashMenu.isOpen}
                aria-controls={slashMenu.isOpen ? slashMenuId : undefined}
                title="Slash commands · type / to browse"
                disabled={disabled || (input.length > 0 && !/^\/\S*$/.test(input))}
                onClick={() => {
                  if (slashMenu.isOpen) {
                    slashMenu.close();
                  } else {
                    const value = input || '/';
                    setInput(value);
                    slashMenu.handleChange(value);
                  }
                  textareaRef.current?.focus();
                }}
              >
                <Slash className="niuu-chat-input-btn-icon" />
              </button>
            )}
            {showModelChip && runtimeOptions && onSetRuntimeModel && (
              <ChipMenu
                name="Model"
                heading="Model · next turn"
                disabled={disabled}
                testId="model-chip"
                items={models.map((model) => ({
                  value: model.model,
                  label: model.name,
                  checked: model.model === runtimeOptions.current.model,
                }))}
                onSelect={onSetRuntimeModel}
              >
                <span>{currentModel?.name ?? runtimeOptions.current.model}</span>
              </ChipMenu>
            )}
            {!runtimeOptions && runtimeOptionsError && onSetRuntimeModel && (
              <UnavailableChip name="Model" reason={runtimeOptionsError} />
            )}
            {!effort && effortError && onSetEffort && (
              <UnavailableChip name="Effort" reason={effortError} />
            )}
            {showEffortChip && effort && onSetEffort && (
              <ChipMenu
                name="Effort"
                heading="Reasoning effort"
                disabled={disabled}
                testId="effort-chip"
                items={effort.levels.map((level) => ({
                  value: level,
                  label: effortLabel(level),
                  checked: level === effort.current,
                }))}
                onSelect={onSetEffort}
              >
                <span>Effort · {effort.current ? effortLabel(effort.current) : 'not set'}</span>
              </ChipMenu>
            )}
          </div>

          <div className="niuu-chat-input-right-actions">
            {dictation.supported && (
              <button
                type="button"
                className="niuu-chat-input-icon-btn niuu-chat-dictate-btn"
                onClick={() => {
                  dictation.toggle();
                  textareaRef.current?.focus();
                }}
                disabled={disabled}
                aria-pressed={dictation.listening}
                aria-label={dictation.listening ? 'Stop dictation' : 'Dictate'}
                title={dictation.listening ? 'Stop dictation' : 'Dictate'}
                data-testid="dictate-btn"
              >
                <AudioLines className="niuu-chat-input-btn-icon" />
              </button>
            )}
            {isLoading && (
              <button
                type="button"
                className="niuu-chat-stop-btn"
                onClick={onStop}
                disabled={stopDisabled}
                title={
                  stopDisabled
                    ? 'Interrupt not supported by this transport'
                    : 'Stop the current turn; the session keeps running'
                }
                data-testid="stop-btn"
              >
                <Square className="niuu-chat-stop-icon" />
                <span>Stop turn</span>
              </button>
            )}
            <button
              type="button"
              className="niuu-chat-send-btn"
              data-active={(canSend && !disabled) || undefined}
              onClick={handleSend}
              disabled={!canSend || disabled}
              aria-label="Send message"
              data-testid="send-btn"
            >
              <ArrowUp className="niuu-chat-send-icon" />
            </button>
          </div>
        </div>
      </div>

      {dictation.error && (
        <p className="niuu-chat-dictation-error" role="alert" data-testid="dictation-error">
          {dictation.error}
        </p>
      )}
      <div className="niuu-chat-input-hints" data-testid="chat-input-hints">
        <span>
          <kbd className="niuu-chat-kbd">↵</kbd>{' '}
          {queueWhileWorking ? 'queue for after this step' : 'send'}
        </span>
        <span>
          <kbd className="niuu-chat-kbd">/</kbd> commands
        </span>
        <span>
          <kbd className="niuu-chat-kbd">@</kbd> files
        </span>
      </div>
    </div>
  );
}

/** A composer control the runtime could not offer, with its reason on hover and to readers. */
function UnavailableChip({ name, reason }: { name: string; reason: string }) {
  return (
    <span
      className="niuu-chat-chip niuu-chat-chip--unavailable"
      title={reason}
      aria-label={`${name} unavailable: ${reason}`}
      data-testid={`${name.toLowerCase()}-chip-unavailable`}
    >
      <AlertTriangle className="niuu-chat-chip-chevron" aria-hidden="true" />
      <span>{name} unavailable</span>
    </span>
  );
}
