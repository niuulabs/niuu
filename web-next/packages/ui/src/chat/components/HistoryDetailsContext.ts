import { createContext, useContext, useEffect, useState } from 'react';
import { getAuthHeaders } from '@niuulabs/query';
import { conversationUrl } from '../hooks/historyPaging';
import type { ToolUseBlock, ToolResultBlock } from './ToolBlock/groupContentBlocks';

export const HistoryDetailsContext = createContext<string | null>(null);

export function useLazyToolDetail(
  block: ToolUseBlock,
  result: ToolResultBlock | undefined,
  open: boolean,
) {
  const socketUrl = useContext(HistoryDetailsContext);
  const [detail, setDetail] = useState<{ input?: Record<string, unknown>; content?: string }>();
  const [error, setError] = useState<string>();
  const [attempt, setAttempt] = useState(0);
  const elided = block.input?._elided_input === true || result?.truncated === true;
  useEffect(() => {
    if (!open || !elided || detail) return;
    if (!socketUrl) return;
    const controller = new AbortController();
    const url = conversationUrl(socketUrl);
    url.pathname = url.pathname.endsWith('/api/conversation/history')
      ? url.pathname.replace(/history$/, `tool-result/${encodeURIComponent(block.id)}`)
      : url.pathname.replace(/conversation$/, `tool-result/${encodeURIComponent(block.id)}`);
    void fetch(url.href, { headers: getAuthHeaders(), signal: controller.signal })
      .then(async (response) => {
        if (!response.ok) throw new Error(`Could not load tool details (HTTP ${response.status}).`);
        const data = await response.json();
        if (data.tool_use_id !== block.id)
          throw new Error('The tool detail did not match this call.');
        if (!controller.signal.aborted) setDetail(data);
      })
      .catch((failure: unknown) => {
        if (!controller.signal.aborted)
          setError(failure instanceof Error ? failure.message : 'Could not load tool details.');
      });
    return () => controller.abort();
  }, [open, elided, detail, socketUrl, block.id, attempt]);
  return {
    block: detail?.input ? { ...block, input: detail.input } : block,
    result: detail
      ? {
          type: 'tool_result' as const,
          tool_use_id: block.id,
          content:
            typeof detail.content === 'string' ? detail.content : JSON.stringify(detail.content),
        }
      : result,
    loading: open && elided && !detail && !error && Boolean(socketUrl),
    error:
      error ??
      (open && elided && !socketUrl
        ? 'Tool details are unavailable without a session connection.'
        : undefined),
    retry: () => {
      setError(undefined);
      setAttempt((value) => value + 1);
    },
  };
}
