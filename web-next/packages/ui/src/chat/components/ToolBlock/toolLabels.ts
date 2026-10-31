import { isForgeNotifyCall } from '@niuulabs/domain';

export type ToolCategory =
  'terminal' | 'file' | 'search' | 'web' | 'agent' | 'task' | 'mcp' | 'default';

const TOOL_LABEL_MAP: Record<string, string> = {
  Bash: 'Terminal',
  Read: 'Read File',
  Write: 'Write File',
  Edit: 'Edit File',
  Glob: 'Find Files',
  Grep: 'Search Files',
  WebSearch: 'Web Search',
  WebFetch: 'Web Fetch',
  Agent: 'Agent',
  Task: 'Task',
  TodoWrite: 'Update Tasks',
  TodoRead: 'Read Tasks',
};

const TOOL_CATEGORY_MAP: Record<string, ToolCategory> = {
  Bash: 'terminal',
  Read: 'file',
  Write: 'file',
  Edit: 'file',
  Glob: 'file',
  Grep: 'search',
  WebSearch: 'web',
  WebFetch: 'web',
  Agent: 'agent',
  Task: 'task',
  TodoWrite: 'task',
  TodoRead: 'task',
};

/** Codex MCP calls are transcribed as `server.tool` (e.g. `mimir.mimir_search`). */
const SERVER_DOT_TOOL = /^[\w-]+\.[\w-]+$/;

export function getToolLabel(toolName: string): string {
  if (TOOL_LABEL_MAP[toolName]) return TOOL_LABEL_MAP[toolName];
  if (isForgeNotifyCall(toolName)) return 'Notify';
  // Claude MCP tools use __ separators → display as namespace:tool
  if (toolName.includes('__')) return toolName.replace('__', ':');
  if (SERVER_DOT_TOOL.test(toolName)) return toolName.replace('.', ':');
  return toolName;
}

export function getToolCategory(toolName: string): ToolCategory {
  if (TOOL_CATEGORY_MAP[toolName]) return TOOL_CATEGORY_MAP[toolName];
  if (toolName.includes('__') || SERVER_DOT_TOOL.test(toolName)) return 'mcp';
  return 'default';
}
