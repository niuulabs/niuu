/* Types */
export * from './types';

/* Utils */
export { resolveParticipantColor } from './utils/participantColor';
export { compressImage } from './utils/compressImage';
export { buildCommandList } from './utils/slashCommands';
export type { SlashCommand } from './utils/slashCommands';

/* Transport */
export { forgeHistoryEndpoint, type ISessionHistoryLocator } from './hooks/historyPaging';
export { deriveTerminalWsUrl, normalizeSessionUrl, wsUrlToHttpBase } from './transport';

/* Hooks */
export { useWebSocket } from './hooks/useWebSocket';
export {
  useSkuldChat,
  transformTurns,
  participantsFromTurns,
  meshEventsFromTurns,
} from './hooks/useSkuldChat';
export type { ConversationTurn } from './hooks/useSkuldChat';
export { useFileAttachments } from './hooks/useFileAttachments';
export type { FileAttachment } from './hooks/useFileAttachments';
export { useSlashMenu } from './hooks/useSlashMenu';
export { useDictation, type Dictation } from './hooks/useDictation';
export { useMentionMenu } from './hooks/useMentionMenu';
export type { SelectedMention, MentionMenuItem } from './hooks/useMentionMenu';
export { useRoomState } from './hooks/useRoomState';
export type { UseRoomStateReturn } from './hooks/useRoomState';

/* Components */
export { ToolBlock, ToolGroupBlock, groupContentBlocks } from './components/ToolBlock';
export { OutcomeCard, WorkerOutcomeCard, extractOutcomeBlock } from './components/OutcomeCard';
export { MarkdownContent } from './components/MarkdownContent';
export { RenderedContent } from './components/RenderedContent';
export { MentionPill } from './components/MentionPill';
export { MentionMenu } from './components/MentionMenu';
export { SlashCommandMenu } from './components/SlashCommandMenu';
export {
  UserMessage,
  AssistantMessage,
  StreamingMessage,
  SystemMessage,
} from './components/ChatMessages';
export { ThreadGroup } from './components/ThreadGroup';
export { RoomMessage } from './components/RoomMessage';
export { SessionEmptyChat } from './components/ChatEmptyStates';
export { ParticipantFilter } from './components/ParticipantFilter';
export { MeshEventCard } from './components/MeshEventCard';
export { WorkersStrip } from './components/WorkersStrip';
export type { WorkersStripProps } from './components/WorkersStrip';
export { ProgressLane, progressSummary } from './components/ProgressLane';
export type { ProgressLaneProps } from './components/ProgressLane';
export { WorkerSheet } from './components/WorkerSheet';
export type { WorkerSheetProps } from './components/WorkerSheet';
export { HandoffRow, handoffParties } from './components/HandoffRow';
export type { HandoffRowProps } from './components/HandoffRow';
export { AttentionRow } from './components/AttentionRow';
export type { AttentionRowProps } from './components/AttentionRow';
export { VerdictPill } from './components/VerdictPill';
export type { VerdictPillProps } from './components/VerdictPill';
export { ChatInput } from './components/ChatInput';
export { SessionChat } from './components/SessionChat';
export { QuestionBand } from './components/QuestionBand';
export type { QuestionBandProps } from './components/QuestionBand';
export { PlanDock } from './components/PlanDock';
export type { PlanDockProps } from './components/PlanDock';
export { ChatDisplayControls } from './components/ChatDisplayControls';
export type { ChatDisplayControlsProps } from './components/ChatDisplayControls';
export { SubagentCard, ProviderErrorCard, CompactionMarker } from './components/TranscriptCards';
export type {
  SubagentCardProps,
  ProviderErrorCardProps,
  CompactionMarkerProps,
} from './components/TranscriptCards';
export { engineInk, engineOf } from './utils/engineInk';
export type { SessionChatProps } from './components/SessionChat';

export { repairCanonicalText } from './hooks/canonicalTextRepair';
export type { TextRepairIdentity } from './hooks/canonicalTextRepair';
export {
  foldPublicText,
  publicTextContent,
  upsertToolPart,
  validateTextReceipt,
} from './hooks/orderedPublicText';
export type { PublicTextEvent } from './hooks/orderedPublicText';

export {
  NotificationCard,
  notificationAnchorId,
  NotificationKindIcon,
  notificationTone,
  NOTIFICATION_KIND_LABELS,
  NOTIFICATION_SEVERITY_LABELS,
} from './components/NotificationCard';
export type {
  NotificationCardProps,
  NotificationKindIconProps,
  NotificationTone,
} from './components/NotificationCard';

export { SecretRequestCard, SECRET_REQUEST_POLL_MS } from './components/SecretRequestCard';
export type { SecretRequestCardProps } from './components/SecretRequestCard';

export {
  ConversationResourceProvider,
  ConversationLink,
  ConversationImage,
  PresentedFileCard,
} from './components/ConversationResources';
export type {
  ConversationResource,
  ConversationResourcePort,
} from './components/ConversationResources';

export { MarkdownCodeBlock } from './components/MarkdownContent/MarkdownCodeBlock';
export { MermaidDiagram } from './components/MarkdownContent/MermaidDiagram';
export { useCopyFeedback } from './hooks/useCopyFeedback';
export { useConversationResources } from './components/ConversationResources';

export { ImagePreview } from './components/ImagePreview';

export { externalResource } from './components/ConversationResources';
export { ExternalLinkPreview } from './components/ExternalLinkPreview';

export { ToolImagePreview } from './components/ToolImagePreview';
export type { ToolImageResource } from './components/ConversationResources';
export { roomWorkers, workerOutcomes, workerStages, verdictTone, verdictLabel } from './workers';
export type { Worker, WorkerOutcome, WorkerStage, WorkerStatus, OutcomeTone } from './workers';

export { ForgeSessionCard } from './components/TranscriptCards';
export type { ForgeSessionCardProps } from './components/TranscriptCards';
export { SessionLinksProvider, useSessionLinks, sessionOpenHandler } from './sessionLinks';
export type {
  SessionLink,
  SessionLinkTone,
  SessionLinksProviderProps,
  SessionLinksValue,
} from './sessionLinks';
export { classifyRelayedTurn } from './components/ChatMessages/relayedTurn';
export type { RelayedTurn, RelayKind } from './components/ChatMessages/relayedTurn';
