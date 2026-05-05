import {
  Terminal,
  FileText,
  FilePlus,
  FileEdit,
  Search,
  Globe,
  Bot,
  CheckSquare,
  Wrench,
  FolderSearch,
  Layers,
} from 'lucide-react';
import type { LucideIcon } from 'lucide-react';

const TOOL_ICONS: Record<string, LucideIcon> = {
  Bash: Terminal,
  Read: FileText,
  Write: FilePlus,
  Edit: FileEdit,
  Glob: FolderSearch,
  Grep: Search,
  WebSearch: Globe,
  WebFetch: Globe,
  Agent: Bot,
  Task: Layers,
  TodoWrite: CheckSquare,
  TodoRead: CheckSquare,
};

interface ToolIconProps {
  toolName: string;
  className?: string;
}

export function ToolIcon({ toolName, className }: ToolIconProps) {
  const Icon = TOOL_ICONS[toolName] ?? Wrench;
  return <Icon className={className} />;
}
