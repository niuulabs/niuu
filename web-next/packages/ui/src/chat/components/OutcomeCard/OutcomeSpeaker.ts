import { createContext, useContext } from 'react';

/** The worker an outcome block belongs to, supplied by the message that carries it. */
export interface OutcomeSpeaker {
  name: string;
  color: string;
}

const OutcomeSpeakerContext = createContext<OutcomeSpeaker | null>(null);

export const OutcomeSpeakerProvider = OutcomeSpeakerContext.Provider;

export function useOutcomeSpeaker(): OutcomeSpeaker | null {
  return useContext(OutcomeSpeakerContext);
}
