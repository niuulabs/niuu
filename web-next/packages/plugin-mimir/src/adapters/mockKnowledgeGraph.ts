/**
 * The dev app's knowledge graph (`GET /memory/graph`, contract G1), built
 * from the mock page graph: every mount becomes a readable layer, and a
 * booking cut-over decision with its owner, tickets and services is added
 * with dates relative to `now`, so the scene always has a change "this week"
 * to show and a name worth typing ("booking cut-over").
 */
import type { GraphEdge, GraphNode, KnowledgeGraph, MimirGraph } from '../domain/api-types';

const DAY_MS = 24 * 60 * 60 * 1000;

export const MOCK_BOOKING_LAYER = 'project:booking';
export const MOCK_ORG_LAYER = 'org';

interface BookingPage {
  layer: string;
  path: string;
  title: string;
  kind: string;
  summary: string;
  firstSeenDaysAgo: number;
  changedDaysAgo: number | null;
}

const BOOKING_PAGES: readonly BookingPage[] = [
  {
    layer: MOCK_BOOKING_LAYER,
    path: 'projects/booking-cutover',
    title: 'Booking cut-over',
    kind: 'decision',
    summary:
      'Bookings move from the legacy API to booking-api v2. Ines Moreau owns it and makes the go/no-go call.',
    firstSeenDaysAgo: 9,
    changedDaysAgo: 0,
  },
  {
    layer: MOCK_ORG_LAYER,
    path: 'people/ines-moreau',
    title: 'Ines Moreau',
    kind: 'person',
    summary: 'Engineering lead for the booking platform.',
    firstSeenDaysAgo: 120,
    changedDaysAgo: 12,
  },
  {
    layer: MOCK_BOOKING_LAYER,
    path: 'sources/lin-482',
    title: 'LIN-482',
    kind: 'ticket',
    summary: '',
    firstSeenDaysAgo: 9,
    changedDaysAgo: null,
  },
  {
    layer: MOCK_BOOKING_LAYER,
    path: 'sources/lin-501',
    title: 'LIN-501',
    kind: 'ticket',
    summary: '',
    firstSeenDaysAgo: 0,
    changedDaysAgo: null,
  },
  {
    layer: MOCK_BOOKING_LAYER,
    path: 'services/booking-api-v2',
    title: 'booking-api v2',
    kind: 'service',
    summary: 'The new booking service, behind the gateway since October.',
    firstSeenDaysAgo: 60,
    changedDaysAgo: 2,
  },
  {
    layer: MOCK_BOOKING_LAYER,
    path: 'services/legacy-booking-api',
    title: 'Legacy booking API',
    kind: 'service',
    summary: 'The booking API being retired by the cut-over.',
    firstSeenDaysAgo: 300,
    changedDaysAgo: 40,
  },
  {
    layer: MOCK_ORG_LAYER,
    path: 'decisions/payments-freeze',
    title: 'Payments freeze',
    kind: 'decision',
    summary: 'No payment changes ship in the first two weeks of December.',
    firstSeenDaysAgo: 30,
    changedDaysAgo: 30,
  },
  {
    layer: MOCK_BOOKING_LAYER,
    path: 'runbooks/rollback',
    title: 'Rollback runbook',
    kind: 'runbook',
    summary: 'How to route bookings back to the legacy API within ten minutes.',
    firstSeenDaysAgo: 15,
    changedDaysAgo: 15,
  },
];

interface BookingLink {
  source: string;
  target: string;
  relation: string;
  sinceDaysAgo: number | null;
}

const id = (layer: string, path: string): string => `${layer}/${path}`;
const CUTOVER = id(MOCK_BOOKING_LAYER, 'projects/booking-cutover');

const BOOKING_LINKS: readonly BookingLink[] = [
  {
    source: id(MOCK_ORG_LAYER, 'people/ines-moreau'),
    target: CUTOVER,
    relation: 'owns',
    sinceDaysAgo: 9,
  },
  {
    source: CUTOVER,
    target: id(MOCK_BOOKING_LAYER, 'sources/lin-482'),
    relation: 'planned in',
    sinceDaysAgo: 9,
  },
  {
    source: id(MOCK_BOOKING_LAYER, 'sources/lin-501'),
    target: CUTOVER,
    relation: 'moved',
    sinceDaysAgo: 0,
  },
  {
    source: CUTOVER,
    target: id(MOCK_BOOKING_LAYER, 'services/booking-api-v2'),
    relation: 'changes',
    sinceDaysAgo: 2,
  },
  {
    source: CUTOVER,
    target: id(MOCK_BOOKING_LAYER, 'services/legacy-booking-api'),
    relation: 'replaces',
    sinceDaysAgo: 9,
  },
  {
    source: CUTOVER,
    target: id(MOCK_ORG_LAYER, 'decisions/payments-freeze'),
    relation: 'waits for',
    sinceDaysAgo: 3,
  },
  {
    source: CUTOVER,
    target: id(MOCK_BOOKING_LAYER, 'runbooks/rollback'),
    relation: 'covered by',
    sinceDaysAgo: 7,
  },
];

function isoDaysAgo(now: number, days: number): string {
  return new Date(now - days * DAY_MS).toISOString();
}

function bookingNode(page: BookingPage, now: number): GraphNode {
  const firstSeen = isoDaysAgo(now, page.firstSeenDaysAgo);
  const changedAt = page.changedDaysAgo === null ? null : isoDaysAgo(now, page.changedDaysAgo);
  return {
    id: id(page.layer, page.path),
    title: page.title,
    category: page.path.split('/')[0]!,
    path: page.path,
    kind: page.kind,
    summary: page.summary,
    mount: page.layer,
    layer: page.layer,
    updatedAt: changedAt ?? firstSeen,
    firstSeen,
    changedAt,
    confidence: null,
  };
}

function layerName(layer: string): string {
  if (layer === MOCK_BOOKING_LAYER) return 'Booking platform';
  if (layer === MOCK_ORG_LAYER) return 'Org';
  return layer.charAt(0).toUpperCase() + layer.slice(1);
}

/**
 * `base`'s pages, one layer per mount, plus the booking cut-over cluster —
 * whose pages also link to the two best-connected base pages, so the
 * cluster sits inside the cloud rather than beside it.
 */
export function buildMockKnowledgeGraph(base: MimirGraph, now: number): KnowledgeGraph {
  const baseNodes: GraphNode[] = base.nodes.map((node) => ({
    ...node,
    layer: node.mount,
    changedAt: node.updatedAt || null,
  }));
  const bookingNodes = BOOKING_PAGES.map((page) => bookingNode(page, now));

  const degree = new Map<string, number>();
  for (const edge of base.edges) {
    degree.set(edge.source, (degree.get(edge.source) ?? 0) + 1);
    degree.set(edge.target, (degree.get(edge.target) ?? 0) + 1);
  }
  const hubs = [...degree.entries()].sort((a, b) => b[1] - a[1]).map(([nodeId]) => nodeId);
  const bridges: GraphEdge[] = [];
  const [firstHub, secondHub] = hubs;
  if (firstHub) {
    bridges.push({
      source: id(MOCK_BOOKING_LAYER, 'services/booking-api-v2'),
      target: firstHub,
      relation: 'depends on',
      since: null,
    });
  }
  if (secondHub) {
    bridges.push({
      source: id(MOCK_ORG_LAYER, 'decisions/payments-freeze'),
      target: secondHub,
      relation: 'mentions',
      since: null,
    });
  }

  const nodes = [...baseNodes, ...bookingNodes];
  const layers = [...new Set(nodes.map((node) => node.layer!))]
    .sort()
    .map((layer) => ({ id: layer, name: layerName(layer) }));

  return {
    layers,
    nodes,
    edges: [
      ...base.edges.map((edge) => ({ ...edge, since: null })),
      ...BOOKING_LINKS.map((link) => ({
        source: link.source,
        target: link.target,
        relation: link.relation,
        since: link.sinceDaysAgo === null ? null : isoDaysAgo(now, link.sinceDaysAgo).slice(0, 10),
      })),
      ...bridges,
    ],
    unresolved: [{ source: CUTOVER, targetSlug: 'projects/booking-cutover-v1' }],
  };
}
