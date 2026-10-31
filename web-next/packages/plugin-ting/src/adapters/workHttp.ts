import type { ApiClient } from '@niuulabs/query';
import type { IWorkService } from '../ports';
import type { ReviewList, ReviewWidget, TicketReviews } from '../domain/review';
import type { WorkCollection, WorkDetail } from '../domain/work';

/** The Work API already uses the public camelCase read contract. */
export function buildWorkHttpAdapter(client: ApiClient): IWorkService {
  return {
    list(options) {
      const params = new URLSearchParams();
      if (options?.executionLimit !== undefined) {
        params.set('executionLimit', String(options.executionLimit));
      }
      if (options?.executionCursor !== undefined) {
        params.set('executionCursor', options.executionCursor);
      }
      const query = params.toString();
      return client.get<WorkCollection>(`/work${query ? `?${query}` : ''}`);
    },
    get(kind, id) {
      return client.get<WorkDetail>(`/work/${encodeURIComponent(kind)}/${encodeURIComponent(id)}`);
    },
    async decideGate(projectId, issueId, decision) {
      await client.post(
        `/work/project/${encodeURIComponent(projectId)}/tasks/${encodeURIComponent(issueId)}/gate-decision`,
        decision,
      );
    },
    async startTicket(projectId, issueId, columnId) {
      await client.post(
        `/work/project/${encodeURIComponent(projectId)}/tasks/${encodeURIComponent(issueId)}/start`,
        columnId ? { columnId } : {},
      );
    },
    reviews() {
      return client.get<ReviewList>('/work/reviews');
    },
    reviewWidget(module, name) {
      return client.get<ReviewWidget>(
        `/work/review-widgets/${encodeURIComponent(module)}/${encodeURIComponent(name)}`,
      );
    },
    ticketReviews(projectId, issueId) {
      return client.get<TicketReviews>(
        `/work/project/${encodeURIComponent(projectId)}/tasks/${encodeURIComponent(issueId)}/reviews`,
      );
    },
  };
}
