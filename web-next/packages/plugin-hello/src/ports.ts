export interface Greeting {
  id: string;
  text: string;
  mood: 'warm' | 'cold' | 'curious';
}

export interface IHelloService {
  listGreetings(): Promise<Greeting[]>;
}
