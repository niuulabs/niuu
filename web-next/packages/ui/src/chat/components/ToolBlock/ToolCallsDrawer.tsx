import { Drawer, DrawerContent } from '../../../primitives/Drawer';
import { ToolBlock } from './ToolBlock';
import type { ToolResultBlock, ToolUseBlock } from './groupContentBlocks';
import './ToolRun.css';

export interface ToolCall {
  block: ToolUseBlock;
  result?: ToolResultBlock;
}

/** Width of the right-hand sheet: wide enough for a diff or a command's output. */
const DRAWER_WIDTH_PX = 560;

export interface ToolCallsDrawerProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description?: string;
  calls: readonly ToolCall[];
}

/**
 * Right-hand sheet listing every call of a tool run as a `ToolBlock`. The last call
 * is open by default; each block lazy-loads its full result when opened.
 */
export function ToolCallsDrawer({
  open,
  onOpenChange,
  title,
  description,
  calls,
}: ToolCallsDrawerProps) {
  return (
    <Drawer open={open} onOpenChange={onOpenChange}>
      <DrawerContent title={title} description={description} width={DRAWER_WIDTH_PX}>
        <div className="niuu-chat-toolrun-drawer-list" data-testid="tool-calls-drawer">
          {calls.map(({ block, result }, index) => (
            <ToolBlock
              key={block.id}
              block={block}
              result={result}
              defaultOpen={index === calls.length - 1}
            />
          ))}
        </div>
      </DrawerContent>
    </Drawer>
  );
}
