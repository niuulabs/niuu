import { useQuery } from '@tanstack/react-query';
import { useService } from '@niuulabs/plugin-sdk';
import type { IHelloService } from '../ports';

export function useGreetings() {
  const service = useService<IHelloService>('hello');
  return useQuery({
    queryKey: ['hello', 'greetings'],
    queryFn: () => service.listGreetings(),
  });
}
