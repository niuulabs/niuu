import { useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { useNavigate } from '@tanstack/react-router';
import { useService } from '@niuulabs/plugin-sdk';
import type { IVolundrService } from '../../ports/IVolundrService';
import type {
  SessionDefinition,
  SessionSource,
  VolundrSession,
  VolundrTarget,
} from '../../models/volundr.model';
import { definitionToTaskType, slugifySessionName } from '../launchWizardModel';
import { errorText } from '../errorText';

const DEFAULT_SESSION_NAME = 'forge-session';

/**
 * The session name to use: the operator's own when they typed one, otherwise
 * derived from the last segment of the repository URL or folder path.
 */
export function quickLaunchName(explicitName: string, sourcePath: string): string {
  const explicit = slugifySessionName(explicitName);
  if (explicit) return explicit;
  const lastSegment = (sourcePath || '').split('/').filter(Boolean).at(-1) ?? '';
  const fromPath = slugifySessionName(lastSegment.replace(/\.git$/, '').replace(/^~/, 'home'));
  return fromPath || DEFAULT_SESSION_NAME;
}

/** A git clone or an in-place mount of a folder on the Forge host. */
export function quickLaunchSource(local: boolean, path: string, branch: string): SessionSource {
  const trimmed = path.trim();
  if (!local) return { type: 'git', repo: trimmed, branch: branch.trim() };
  return {
    type: 'local_mount',
    local_path: trimmed,
    paths: [{ host_path: trimmed, mount_path: '/workspace', read_only: false }],
  };
}

/** The Forge the operator has not chosen between: the default one, else the first. */
export function defaultTargetId(targets: VolundrTarget[]): string | undefined {
  return targets.find((target) => target.isDefault)?.id ?? targets[0]?.id;
}

export interface QuickLaunchRequest {
  name: string;
  source: SessionSource;
  definition?: SessionDefinition;
  instanceId?: string;
  initialPrompt?: string;
  personaName?: string;
  /** The model to run; the engine's own default when omitted. */
  model?: string;
  /** Credentials and integrations to attach; omitted means the server's default set. */
  integrationIds?: string[];
}

export interface QuickLaunchOptions {
  /** Runs once the session exists and the caches are refreshed, before navigating. */
  onCreated?: (session: VolundrSession) => void;
  /** Where to land instead of the new session's page (the launch page's `back`). */
  returnTo?: string;
}

/**
 * Starts a session from the small set of choices the quick launch dialog and the
 * Simple-mode launch page both collect, then takes the operator to it.
 */
export function useQuickLaunch() {
  const volundr = useService<IVolundrService>('volundr');
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function launch(
    request: QuickLaunchRequest,
    options: QuickLaunchOptions = {},
  ): Promise<VolundrSession | null> {
    setError(null);
    setCreating(true);
    try {
      const definition = request.definition;
      const session = await volundr.startSession({
        name: request.name,
        source: request.source,
        instanceId: request.instanceId,
        model: request.model || (definition?.defaultModel ?? ''),
        definition: definition?.key,
        taskType: definition ? definitionToTaskType(definition.key) : undefined,
        initialPrompt: request.initialPrompt?.trim() || undefined,
        personaName: request.personaName?.trim() || undefined,
        integrationIds: request.integrationIds,
        terminalRestricted: false,
        workloadConfig: {},
      });
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['volundr', 'sessions'] }),
        queryClient.invalidateQueries({ queryKey: ['volundr', 'stats'] }),
        queryClient.invalidateQueries({ queryKey: ['volundr', 'domain-sessions'] }),
      ]);
      options.onCreated?.(session);
      if (options.returnTo) {
        void navigate({ to: options.returnTo as never });
        return session;
      }
      void navigate({
        to: '/volundr/sessions/$sessionId',
        params: { sessionId: session.id },
      });
      return session;
    } catch (e) {
      setError(errorText(e, 'Failed to create session'));
      return null;
    } finally {
      setCreating(false);
    }
  }

  return { launch, creating, error };
}
