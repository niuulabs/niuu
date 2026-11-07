import { openEventStream, type ApiClient } from '@niuulabs/query';
import type { RealmDetail, RealmSummary, RealmTrustGrant } from '../domain/governance';
import type {
  IRealmGovernanceService,
  RealmCreate,
  RealmDetailListener,
  TrustGrantCreate,
} from '../ports';

/**
 * Realm governance rides the shared niuu host (`<sharedApiBase>/realms`).
 * `realmsStreamBase` is the absolute base the authenticated SSE channel opens on.
 */
export function buildRealmGovernanceHttpAdapter(
  realmsClient: ApiClient,
  realmsStreamBase = '',
): IRealmGovernanceService {
  return {
    listRealms() {
      return realmsClient.get<RealmSummary[]>('/realms');
    },
    getRealm(slug: string) {
      return realmsClient.get<RealmSummary>(`/realms/${encodeURIComponent(slug)}`);
    },
    subscribeRealmDetail(slug: string, listener: RealmDetailListener) {
      if (!realmsStreamBase) {
        throw new Error('Realm detail stream URL is not configured');
      }
      const handle = openEventStream(
        `${realmsStreamBase}/realms/${encodeURIComponent(slug)}/stream`,
        {
          onMessage: () => {},
          onEvent: (frame) => {
            if (frame.event !== 'realm.detail') return;
            try {
              listener(JSON.parse(frame.data) as RealmDetail);
            } catch {
              // A malformed frame must not stop the authenticated stream reconnect loop.
            }
          },
        },
      );
      return () => handle.close();
    },
    createRealm(request: RealmCreate) {
      return realmsClient.post<RealmSummary>('/realms', request);
    },
    setRealmProject(slug: string, projectId: string | null) {
      return realmsClient.patch<RealmSummary>(`/realms/${encodeURIComponent(slug)}/project`, {
        project_id: projectId,
      });
    },
    async deleteRealm(slug: string) {
      await realmsClient.delete<void>(`/realms/${encodeURIComponent(slug)}`);
    },
    listTrustGrants(slug: string) {
      return realmsClient.get<RealmTrustGrant[]>(
        `/realms/${encodeURIComponent(slug)}/trust-grants`,
      );
    },
    createTrustGrant(slug: string, request: TrustGrantCreate) {
      return realmsClient.post<RealmTrustGrant>(
        `/realms/${encodeURIComponent(slug)}/trust-grants`,
        request,
      );
    },
  };
}
