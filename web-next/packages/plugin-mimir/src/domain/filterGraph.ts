import type { MimirGraph } from './api-types';

/** Intersect facets and text, retaining only edges whose endpoints are visible. */
export function filterGraph(
  graph: MimirGraph,
  query: string,
  hidden: Set<string>,
  kind: string,
): MimirGraph {
  const terms = query.trim().toLocaleLowerCase().split(/\s+/).filter(Boolean);
  const nodes = graph.nodes.filter((node) => {
    const text = [
      node.title,
      node.path ?? node.id,
      node.summary,
      node.category,
      node.kind,
      node.mount,
    ]
      .join(' ')
      .toLocaleLowerCase();
    return (
      !hidden.has(node.category) &&
      (!kind || (node.kind ?? 'page') === kind) &&
      terms.every((term) => text.includes(term))
    );
  });
  const ids = new Set(nodes.map((node) => node.id));
  return {
    nodes,
    edges: graph.edges.filter((edge) => ids.has(edge.source) && ids.has(edge.target)),
  };
}
