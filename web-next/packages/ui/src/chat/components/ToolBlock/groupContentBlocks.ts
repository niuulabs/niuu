import { FORGE_NOTIFICATION_TOOL_NAME, SECRET_REQUEST_TOOL_NAME } from '@niuulabs/domain';
import { toolImages, type ToolImage } from '../../toolImages';
import { PERSON_QUESTION_TOOL_NAME } from '../../personQuestions';
import { isSubagentTool } from './toolFamilies';
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

/** A context-compaction boundary; the runtime may attach token figures (Muse: tokensBefore/After). */
export interface CompactionBlock {
  type: 'compaction';
  [field: string]: unknown;
}

export type ContentBlock =
  ToolUseBlock | ToolResultBlock | TextBlock | CompactionBlock | { type: string };

export type GroupedContent =
  | { kind: 'image'; image: ToolImage }
  | { kind: 'separator'; id?: string }
  | { kind: 'compaction'; part: CompactionBlock }
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

    if (block.type === 'compaction') {
      result.push({ kind: 'compaction', part: block as CompactionBlock });
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
      if (j !== i && isOwnCardTool((blk as ToolUseBlock).name)) break;
      if (j !== i && isOwnCardTool(toolName)) break;
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

/** The broker's inline notification part (contract §1), rendered as a card. */
export function isNotificationCardTool(name: string): boolean {
  return name === FORGE_NOTIFICATION_TOOL_NAME;
}

/** The broker's secret request part: a masked card, never a plain tool call. */
export function isSecretRequestTool(name: string): boolean {
  return name === SECRET_REQUEST_TOOL_NAME;
}

/** A question the agent put to one person: a card the addressee answers, never a tool call. */
export function isPersonQuestionTool(name: string): boolean {
  return name === PERSON_QUESTION_TOOL_NAME;
}

/** What a coordinator does to another session with the Forge MCP. */
export type ForgeSessionAction =
  'create_session' | 'start_session' | 'stop_session' | 'send_message';

// Claude names MCP tools `mcp__<server>__<tool>`; Codex transcribes them `forge.<tool>`;
// older brokers used the other separators. A bare `send_message` is not claimed: other
// tools (Claude's agent teams) use that name for something else.
const FORGE_SESSION_CALL =
  /^(?:mcp__forge__|forge__|forge\.|forge:|forge\/)(create_session|start_session|stop_session|send_message)$/;

/** The Forge session tool a call is, or null for any other tool. */
export function forgeSessionAction(name: string): ForgeSessionAction | null {
  const match = FORGE_SESSION_CALL.exec(name.trim().toLowerCase());
  return (match?.[1] as ForgeSessionAction | undefined) ?? null;
}

/** The Forge MCP calls that run sessions: each renders as a worker card. */
export function isForgeSessionTool(name: string): boolean {
  return forgeSessionAction(name) !== null;
}

/** Standalone cards plus subagent calls: neither joins a burst of tool calls in a row. */
function isOwnCardTool(name: string): boolean {
  return isStandaloneCardTool(name) || isSubagentTool(name);
}

/** Tool parts that render as their own card and never join a tool group. */
export function isStandaloneCardTool(name: string): boolean {
  return (
    isPresentedFileTool(name) ||
    isNotificationCardTool(name) ||
    isSecretRequestTool(name) ||
    isPersonQuestionTool(name) ||
    isForgeSessionTool(name)
  );
}
