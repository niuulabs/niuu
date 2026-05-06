import { useState, useEffect } from 'react';
import { useService } from '@niuulabs/plugin-sdk';
import type { ILiveTopologyStream } from '../ports';
import type { Topology } from '../domain';

export function useTopology(): Topology | null {
  const stream = useService<ILiveTopologyStream>('observatory.topology');
  const [topology, setTopology] = useState<Topology | null>(() => stream.getSnapshot());

  useEffect(() => {
    return stream.subscribe(setTopology);
  }, [stream]);

  return topology;
}
