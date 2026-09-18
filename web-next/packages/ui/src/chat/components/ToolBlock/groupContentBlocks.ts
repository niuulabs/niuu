import { toolImages, type ToolImage } from '../../toolImages';
export interface ToolUseBlock {
  type: 'tool_use';
  id: string;
  name: string;
  input: Record<string, unknown>;
}

export interface ToolResultBlock {
  type: 'tool_result';
  tool_use_id: string;
  content?: unknown;
  is_error?: boolean;
  is_image?: boolean;
  mime_type?: string;
  img_w?: number;
  img_h?: number;
  image_previews?: Array<{ index: number; mime_type?: string; img_w?: number; img_h?: number }>;
  truncated?: boolean;
  preview?: string;
}

export interface TextBlock {
  type: 'text';
  text: string;
  id?: string;
  phase?: string;
  turn_id?: string;
  thread_id?: string;
  complete?: boolean;
}

export type ContentBlock = ToolUseBlock | ToolResultBlock | TextBlock | { type: string };

export type GroupedContent =
  | { kind: 'image'; image: ToolImage }
  | { kind: 'separator'; id?: string }
  | ({ kind: 'text' } & Omit<TextBlock, 'type'>)
  | { kind: 'single'; block: ToolUseBlock; result?: ToolResultBlock }
  | {
      kind: 'group';
      toolName: string;
      blocks: Array<{ block: ToolUseBlock; result?: ToolResultBlock }>;
    };

export function groupContentBlocks(blocks: ContentBlock[], hierarchical = false): GroupedContent[] {
  // Build a lookup from tool_use_id → tool_result for id-based matching
  const resultMap = new Map<string, ToolResultBlock>();
  for (const b of blocks) {
    if (b.type === 'tool_result') {
      const rb = b as ToolResultBlock;
      resultMap.set(rb.tool_use_id, rb);
    }
  }

  const uses = new Map(
    blocks
      .filter((block): block is ToolUseBlock => block.type === 'tool_use')
      .map((block) => [block.id, block]),
  );
  const images = new Map(
    [...resultMap].map(([id, result]) => [id, toolImages(result, uses.get(id))]),
  );
  const emittedImages = new Set<string>();
  const result: GroupedContent[] = [];
  let i = 0;

  while (i < blocks.length) {
    const block = blocks[i];
    if (!block) {
      i++;
      continue;
    }

    if (block.type === 'tool_separator') {
      result.push({ kind: 'separator', id: (block as { id?: string }).id });
      i++;
      continue;
    }

    if (block.type === 'text') {
      const { type: _type, ...text } = block as TextBlock;
      result.push({ kind: 'text', ...text });
      i++;
      continue;
    }

    const imageId =
      block.type === 'tool_use'
        ? (block as ToolUseBlock).id
        : block.type === 'tool_result'
          ? (block as ToolResultBlock).tool_use_id
          : undefined;
    const imageItems = imageId ? images.get(imageId) : undefined;
    if (imageId && imageItems?.length) {
      if (!emittedImages.has(imageId)) {
        result.push(...imageItems.map((image) => ({ kind: 'image' as const, image })));
        emittedImages.add(imageId);
      }
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
      if (blk.type !== 'tool_use') break;
      if (images.get((blk as ToolUseBlock).id)?.length) break;
      if (!hierarchical && (blk as ToolUseBlock).name !== toolName) break;
      if (j !== i && isPresentedFileTool((blk as ToolUseBlock).name)) break;
      if (j !== i && isPresentedFileTool(toolName)) break;
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

export function isPresentedFileTool(name: string): boolean {
  return ['present_file', 'senduserfile'].includes(name.toLowerCase());
}
