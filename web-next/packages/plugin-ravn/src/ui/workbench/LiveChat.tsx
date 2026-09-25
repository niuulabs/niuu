import { useMemo } from 'react';
import { SessionChat, type ISessionHistoryLocator, useSkuldChat } from '@niuulabs/ui';
import { useOptionalService } from '@niuulabs/plugin-sdk';

/**
 * The live conversation with a running ravn: the same Skuld chat Forge uses,
 * so approvals, steering, interrupt and slash commands behave identically.
 */
export function LiveChat({
  chatEndpoint,
  name,
  socketHistory,
  eventRouting,
}: {
  chatEndpoint: string;
  name: string;
  /** Residents replay their own history over the socket. */
  socketHistory: boolean;
  /** Flock members route events to peers. */
  eventRouting: boolean;
}) {
  const historyLocator = useOptionalService<ISessionHistoryLocator>('forge.history');
  const historyEndpoint = useMemo(
    () => historyLocator?.historyEndpoint(chatEndpoint) ?? null,
    [chatEndpoint, historyLocator],
  );
  const chat = useSkuldChat(chatEndpoint, {
    historyMode: socketHistory ? 'none' : 'session',
    historyEndpoint,
  });

  return (
    <div className="rw-chat__session" data-testid="ravn-live-chat">
      <SessionChat
        messages={chat.messages}
        streamingContent={chat.streamingContent}
        streamingParts={chat.streamingParts}
        streamingModel={chat.streamingModel}
        connected={chat.connected}
        historyLoaded={chat.historyLoaded}
        historyError={chat.historyError}
        onRetryHistory={chat.retryHistory}
        hasOlderHistory={chat.hasOlderHistory}
        loadingOlderHistory={chat.loadingOlderHistory}
        olderHistoryError={chat.olderHistoryError}
        onLoadOlderHistory={chat.loadOlderHistory}
        participants={chat.participants}
        meshEvents={chat.meshEvents}
        agentEvents={chat.agentEvents}
        pendingPermissions={chat.pendingPermissions}
        pendingInputRequests={chat.pendingInputRequests}
        availableCommands={chat.availableCommands}
        capabilities={chat.capabilities}
        chatEndpoint={chatEndpoint}
        historyEndpoint={historyEndpoint}
        sessionName={name}
        eventRouting={eventRouting}
        onSend={chat.sendMessage}
        onSendDirected={chat.sendDirectedMessages}
        onPublishEvent={chat.publishEvent}
        onStop={chat.sendInterrupt}
        onPermissionRespond={chat.respondToPermission}
        onInputRespond={chat.respondToInput}
        onSetInternalVisibility={chat.sendSetInternalVisibility}
      />
    </div>
  );
}
