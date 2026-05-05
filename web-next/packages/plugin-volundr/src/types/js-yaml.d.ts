declare module 'js-yaml' {
  export function dump(value: unknown, options?: Record<string, unknown>): string;
  export function load(value: string, options?: Record<string, unknown>): unknown;

  const yaml: {
    dump: typeof dump;
    load: typeof load;
  };

  export default yaml;
}
