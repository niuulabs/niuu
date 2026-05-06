export type ToolCategory =
  | 'terminal'
  | 'file'
  | 'search'
  | 'web'
  | 'agent'
  | 'task'
  | 'mcp'
  | 'default';

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

export function getToolLabel(toolName: string): string {
  if (TOOL_LABEL_MAP[toolName]) return TOOL_LABEL_MAP[toolName];
  // MCP tools use __ separator → display as namespace:tool
  if (toolName.includes('__')) return toolName.replace('__', ':');
  return toolName;
}

export function getToolCategory(toolName: string): ToolCategory {
  if (TOOL_CATEGORY_MAP[toolName]) return TOOL_CATEGORY_MAP[toolName];
  if (toolName.includes('__')) return 'mcp';
  return 'default';
}
