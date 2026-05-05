export interface ToolUseBlock {
  type: 'tool_use';
  id: string;
  name: string;
  input: Record<string, unknown>;
}

export interface ToolResultBlock {
  type: 'tool_result';
  tool_use_id: string;
  content?: string;
}

export interface TextBlock {
  type: 'text';
  text: string;
}

export type ContentBlock = ToolUseBlock | ToolResultBlock | TextBlock | { type: string };

export type GroupedContent =
  | { kind: 'text'; text: string }
  | { kind: 'single'; block: ToolUseBlock; result?: ToolResultBlock }
  | {
      kind: 'group';
      toolName: string;
      blocks: Array<{ block: ToolUseBlock; result?: ToolResultBlock }>;
    };

export function groupContentBlocks(blocks: ContentBlock[]): GroupedContent[] {
  // Build a lookup from tool_use_id → tool_result for id-based matching
  const resultMap = new Map<string, ToolResultBlock>();
  for (const b of blocks) {
    if (b.type === 'tool_result') {
      const rb = b as ToolResultBlock;
      resultMap.set(rb.tool_use_id, rb);
    }
  }

  const result: GroupedContent[] = [];
  let i = 0;

  while (i < blocks.length) {
    const block = blocks[i];
    if (!block) {
      i++;
      continue;
    }

    if (block.type === 'text') {
      result.push({ kind: 'text', text: (block as TextBlock).text });
      i++;
      continue;
    }

    if (block.type !== 'tool_use') {
      i++;
      continue;
    }

    const toolName = (block as ToolUseBlock).name;
    const group: Array<{ block: ToolUseBlock; result?: ToolResultBlock }> = [];
    let j = i;

    // Collect consecutive same-name tool_use blocks, skipping over paired tool_results
    while (j < blocks.length) {
      const blk = blocks[j];
      if (!blk) break;
      if (blk.type === 'tool_result') {
        const rb = blk as ToolResultBlock;
        // Skip tool_results that belong to uses already collected in this group
        if (group.some((g) => g.block.id === rb.tool_use_id)) {
          j++;
          continue;
        }
        break;
      }
      if (blk.type !== 'tool_use' || (blk as ToolUseBlock).name !== toolName) break;
      const tb = blk as ToolUseBlock;
      group.push({ block: tb, result: resultMap.get(tb.id) });
      j++;
    }

    const first = group[0];
    if (group.length === 1 && first) {
      result.push({ kind: 'single', block: first.block, result: first.result });
    } else {
      result.push({ kind: 'group', toolName, blocks: group });
    }
    i = j;
  }

  return result;
}
