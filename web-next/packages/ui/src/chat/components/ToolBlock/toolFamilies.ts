import {
  Bot,
  FileEdit,
  FileText,
  Globe,
  HelpCircle,
  ListChecks,
  Search,
  Terminal,
  Wrench,
} from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { getToolLabel } from './toolLabels';
import type { ToolResultBlock, ToolUseBlock } from './groupContentBlocks';

/**
 * What a tool call does, independent of which runtime named it. Claude and Codex
 * (and the Muse/Grok/OpenCode adapters) spell the same action differently; the
 * tool run row groups and words them by family.
 */
export type ToolFamily =
  'command' | 'edit' | 'read' | 'search' | 'web' | 'plan' | 'agent' | 'question' | 'other';

export interface ToolFamilyInfo {
  family: ToolFamily;
  icon: LucideIcon;
  /** "Running", shown on the live ticker while the call has no result. */
  present: string;
  /** "Ran", shown once the call has finished. */
  past: string;
}

const FAMILIES: Record<ToolFamily, ToolFamilyInfo> = {
  command: { family: 'command', icon: Terminal, present: 'Running', past: 'Ran' },
  edit: { family: 'edit', icon: FileEdit, present: 'Editing', past: 'Edited' },
  read: { family: 'read', icon: FileText, present: 'Reading', past: 'Read' },
  search: { family: 'search', icon: Search, present: 'Searching', past: 'Searched' },
  web: { family: 'web', icon: Globe, present: 'Looking up', past: 'Looked up' },
  plan: { family: 'plan', icon: ListChecks, present: 'Updating plan', past: 'Updated plan' },
  agent: { family: 'agent', icon: Bot, present: 'Delegating to', past: 'Delegated to' },
  question: { family: 'question', icon: HelpCircle, present: 'Asking', past: 'Asked' },
  other: { family: 'other', icon: Wrench, present: 'Calling', past: 'Called' },
};

/**
 * Lower-cased tool names as the runtimes emit them after Skuld's normalisation:
 * Claude (`Bash`, `MultiEdit`...), Codex (`shell`, `apply_patch`, `update_plan`,
 * `exec_command`, `fileChange` -> `Edit`, `webSearch` -> `WebSearch`) and the
 * Muse / Grok / OpenCode spellings (`run_terminal_command`, `search_replace`...).
 */
const FAMILY_BY_NAME: Readonly<Record<string, ToolFamily>> = {
  bash: 'command',
  shell: 'command',
  shell_command: 'command',
  exec_command: 'command',
  // Codex code mode: a JavaScript script in `input.input` that drives the tools.
  exec: 'command',
  write_stdin: 'command',
  'container.exec': 'command',
  run_shell: 'command',
  run_terminal_command: 'command',

  edit: 'edit',
  multiedit: 'edit',
  notebookedit: 'edit',
  write: 'edit',
  apply_patch: 'edit',
  patch: 'edit',
  str_replace_editor: 'edit',
  str_replace_based_edit_tool: 'edit',
  search_replace: 'edit',
  edit_file: 'edit',
  write_file: 'edit',
  create_file: 'edit',

  read: 'read',
  read_file: 'read',
  ls: 'read',
  list: 'read',
  list_dir: 'read',
  list_directory: 'read',
  view_image: 'read',

  glob: 'search',
  grep: 'search',
  search: 'search',
  search_files: 'search',
  find_files: 'search',

  webfetch: 'web',
  websearch: 'web',
  web_fetch: 'web',
  web_search: 'web',
  fetch_url: 'web',

  todowrite: 'plan',
  todoread: 'plan',
  todo_write: 'plan',
  update_todo: 'plan',
  update_plan: 'plan',

  task: 'agent',
  agent: 'agent',
  spawn_subagent: 'agent',

  askuserquestion: 'question',
  request_user_input: 'question',
  ask_user: 'question',
};

/** Codex names a non-spawn collaboration call `Agent:<tool>` (wait, send, close...). */
const AGENT_NAMESPACE = 'agent:';

export function toolFamily(toolName: string): ToolFamily {
  const name = toolName.toLowerCase();
  const family = FAMILY_BY_NAME[name];
  if (family) return family;
  if (name.startsWith(AGENT_NAMESPACE)) return 'agent';
  return 'other';
}

export function toolFamilyInfo(toolName: string): ToolFamilyInfo {
  return FAMILIES[toolFamily(toolName)];
}

export function familyInfo(family: ToolFamily): ToolFamilyInfo {
  return FAMILIES[family];
}

/** Subagent calls render as their own card (`Task` for Claude, `Agent` for Codex spawn). */
export function isSubagentTool(toolName: string): boolean {
  const name = toolName.toLowerCase();
  return name === 'task' || name === 'agent';
}

/* ── Input readers ── */

function text(value: unknown): string {
  return typeof value === 'string' ? value : '';
}

function firstLine(value: string): string {
  return value.split('\n')[0] ?? '';
}

interface FileChange {
  path: string;
  diff: string;
}

/** Codex's `fileChange` item: `changes: [{ path, kind, diff }]`. */
function fileChanges(input: Record<string, unknown>): FileChange[] {
  if (!Array.isArray(input.changes)) return [];
  const changes: FileChange[] = [];
  for (const change of input.changes) {
    if (typeof change !== 'object' || change === null) continue;
    const entry = change as Record<string, unknown>;
    changes.push({ path: text(entry.path), diff: text(entry.diff) });
  }
  return changes;
}

function commandText(input: Record<string, unknown>): string {
  const command = input.command ?? input.cmd ?? input.input;
  if (Array.isArray(command)) return command.map(String).join(' ');
  return text(command);
}

/** The file paths a call touches; empty when the input carries none (or was elided). */
export function toolFilePaths(block: ToolUseBlock): string[] {
  const { input } = block;
  const own = text(input.file_path) || text(input.path) || text(input.notebook_path);
  if (own) return [own];
  return fileChanges(input)
    .map((change) => change.path)
    .filter(Boolean);
}

/** What the call acted on: the command, the file, the pattern, the URL. Not truncated. */
export function toolSubject(block: ToolUseBlock): string {
  const { input } = block;
  if (input._elided_input) return firstLine(text(input.preview));
  const family = toolFamily(block.name);
  switch (family) {
    case 'command':
      return firstLine(commandText(input));
    case 'edit':
    case 'read': {
      const paths = toolFilePaths(block);
      const [first] = paths;
      if (!first) return '';
      return paths.length > 1 ? `${first} and ${paths.length - 1} more` : first;
    }
    case 'search':
      return text(input.pattern) || text(input.query) || text(input.regex) || text(input.glob);
    case 'web': {
      const action = typeof input.action === 'object' && input.action !== null ? input.action : {};
      const nested = action as Record<string, unknown>;
      return text(input.query) || text(input.url) || text(nested.query) || text(nested.url) || '';
    }
    case 'plan':
      return '';
    case 'agent':
      return firstLine(text(input.description) || text(input.prompt));
    case 'question': {
      const questions = Array.isArray(input.questions) ? input.questions : [];
      const first = questions[0];
      if (typeof first === 'object' && first !== null) {
        const q = first as Record<string, unknown>;
        return text(q.question) || text(q.prompt);
      }
      return text(input.question) || text(input.prompt);
    }
    case 'other':
      return getToolLabel(block.name);
  }
}

/** "Running pytest tests/", "Editing src/gates.py": the ticker line for a call in flight. */
export function toolTicker(block: ToolUseBlock): string {
  const info = toolFamilyInfo(block.name);
  return `${info.present} ${toolSubject(block)}`.trim();
}

/* ── Results ── */

/** The result's text, whether the runtime sent a string or Anthropic-style content blocks. */
export function toolResultText(result: ToolResultBlock | undefined): string {
  const content = result?.content;
  if (typeof content === 'string') return content;
  if (Array.isArray(content)) {
    return content
      .map((part) => {
        if (typeof part === 'string') return part;
        if (typeof part === 'object' && part !== null)
          return text((part as { text?: unknown }).text);
        return '';
      })
      .filter(Boolean)
      .join('\n');
  }
  if (content == null) return text(result?.preview);
  return JSON.stringify(content, null, 2);
}

/* ── Diffstat ── */

export interface Diffstat {
  added: number;
  removed: number;
}

/** Lines in a block of text; a trailing newline does not start another line. */
function lineCount(value: string): number {
  if (!value) return 0;
  const lines = value.split('\n');
  return lines[lines.length - 1] === '' ? lines.length - 1 : lines.length;
}

function countPatchLines(patch: string, skipFileHeaders: boolean): Diffstat {
  let added = 0;
  let removed = 0;
  let inHunk = !skipFileHeaders;
  for (const line of patch.split('\n')) {
    if (line.startsWith('@@')) {
      inHunk = true;
      continue;
    }
    if (!inHunk) continue;
    if (line.startsWith('+')) added += 1;
    else if (line.startsWith('-')) removed += 1;
  }
  return { added, removed };
}

function patchText(input: Record<string, unknown>): string {
  for (const key of ['patch', 'input', 'command', 'cmd']) {
    const value = input[key];
    const candidate = Array.isArray(value) ? value.map(String).join('\n') : text(value);
    if (candidate.includes('*** Begin Patch')) return candidate;
  }
  return '';
}

/** Lines added and removed by one call, from the edit inputs the runtime sent. */
export function toolDiffstat(block: ToolUseBlock): Diffstat {
  const { input } = block;
  const none = { added: 0, removed: 0 };
  if (input._elided_input) return none;
  const name = block.name.toLowerCase();

  const patch = patchText(input);
  if (patch) {
    const body = patch
      .split('\n')
      .filter((line) => !line.startsWith('***'))
      .join('\n');
    return countPatchLines(body, false);
  }

  const changes = fileChanges(input);
  if (changes.length > 0) {
    return changes.reduce<Diffstat>(
      (total, change) => {
        const stat = countPatchLines(change.diff, change.diff.includes('@@'));
        return { added: total.added + stat.added, removed: total.removed + stat.removed };
      },
      { added: 0, removed: 0 },
    );
  }

  if (name === 'multiedit' && Array.isArray(input.edits)) {
    return input.edits.reduce<Diffstat>(
      (total, edit) => {
        if (typeof edit !== 'object' || edit === null) return total;
        const entry = edit as Record<string, unknown>;
        return {
          added: total.added + lineCount(text(entry.new_string)),
          removed: total.removed + lineCount(text(entry.old_string)),
        };
      },
      { added: 0, removed: 0 },
    );
  }

  if (toolFamily(block.name) !== 'edit') return none;
  if (typeof input.content === 'string') return { added: lineCount(input.content), removed: 0 };
  return {
    added: lineCount(text(input.new_string)),
    removed: lineCount(text(input.old_string)),
  };
}

export function sumDiffstat(blocks: readonly ToolUseBlock[]): Diffstat {
  return blocks.reduce<Diffstat>(
    (total, block) => {
      const stat = toolDiffstat(block);
      return { added: total.added + stat.added, removed: total.removed + stat.removed };
    },
    { added: 0, removed: 0 },
  );
}

/* ── Run summary ── */

function plural(count: number, one: string, many: string): string {
  return `${count} ${count === 1 ? one : many}`;
}

function familyPhrase(family: ToolFamily, count: number): string {
  switch (family) {
    case 'command':
      return plural(count, 'command', 'commands');
    case 'edit':
      return plural(count, 'file edited', 'files edited');
    case 'read':
      return plural(count, 'file read', 'files read');
    case 'search':
      return plural(count, 'search', 'searches');
    case 'web':
      return plural(count, 'web lookup', 'web lookups');
    case 'plan':
      return plural(count, 'plan update', 'plan updates');
    case 'agent':
      return plural(count, 'agent call', 'agent calls');
    case 'question':
      return plural(count, 'question', 'questions');
    case 'other':
      return plural(count, 'tool call', 'tool calls');
  }
}

export interface SummarySegment {
  /** Family key, or the tool's own label for `other` calls. */
  key: string;
  family: ToolFamily;
  phrase: string;
}

/**
 * "3 commands · 1 file edited · 2 files read": one segment per family, in the order
 * each family first appears. Edits and reads count distinct files when the inputs name
 * them; `other` (MCP) calls are counted per tool label, e.g. "2 mimir:mimir_search".
 */
export function summarizeRun(blocks: readonly ToolUseBlock[]): SummarySegment[] {
  const order: string[] = [];
  const counts = new Map<string, { family: ToolFamily; calls: number; files: Set<string> }>();
  for (const block of blocks) {
    const family = toolFamily(block.name);
    const key = family === 'other' ? getToolLabel(block.name) : family;
    let entry = counts.get(key);
    if (!entry) {
      entry = { family, calls: 0, files: new Set() };
      counts.set(key, entry);
      order.push(key);
    }
    entry.calls += 1;
    if (family === 'edit' || family === 'read') {
      for (const path of toolFilePaths(block)) entry.files.add(path);
    }
  }
  return order.map((key) => {
    const entry = counts.get(key)!;
    if (entry.family === 'other') {
      return { key, family: entry.family, phrase: `${entry.calls} ${key}` };
    }
    const count = entry.files.size > 0 ? entry.files.size : entry.calls;
    return { key, family: entry.family, phrase: familyPhrase(entry.family, count) };
  });
}

/** Distinct families in first-appearance order. */
export function runFamilies(blocks: readonly ToolUseBlock[]): ToolFamily[] {
  const seen: ToolFamily[] = [];
  for (const block of blocks) {
    const family = toolFamily(block.name);
    if (!seen.includes(family)) seen.push(family);
  }
  return seen;
}
