/** Port for bi-directional PTY (pseudo-terminal) streaming to a session pod. */
export interface IPtyStream {
  /** Subscribe to terminal output for a session. Returns an unsubscribe function. */
  subscribe(sessionId: string, onData: (chunk: string) => void): () => void;
  /** Send a chunk of terminal input to the session pod. */
  send(sessionId: string, data: string): void;
}
