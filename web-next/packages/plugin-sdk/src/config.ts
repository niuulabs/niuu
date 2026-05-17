import { z } from 'zod';

const absoluteOrRootRelativeUrlSchema = z.string().refine((value) => {
  if (value.startsWith('/')) {
    return true;
  }
  try {
    new URL(value);
    return true;
  } catch {
    return false;
  }
}, 'Invalid url');

const absoluteOrRootRelativeWsUrlSchema = z.string().refine((value) => {
  if (value.startsWith('/')) {
    return true;
  }
  try {
    const parsed = new URL(value);
    return parsed.protocol === 'ws:' || parsed.protocol === 'wss:';
  } catch {
    return false;
  }
}, 'Invalid websocket url');

export const pluginConfigSchema = z.object({
  enabled: z.boolean().default(true),
  order: z.number().int().nonnegative().default(100),
  reason: z.string().optional(),
});

export const serviceConfigSchema = z
  .object({
    baseUrl: absoluteOrRootRelativeUrlSchema.optional(),
    /**
     * Optional WebSocket URL for services with a bidirectional stream
     * component (e.g. Völundr PTY). Accepts ws:// or wss://. When present it
     * overrides `baseUrl` for the streaming adapter only.
     */
    wsUrl: absoluteOrRootRelativeWsUrlSchema.optional(),
    mode: z.enum(['http', 'mock', 'ws']).default('http'),
  })
  .catchall(z.unknown());

export const authConfigSchema = z.object({
  issuer: z.string().url().optional(),
  clientId: z.string().optional(),
});

export const niuuConfigSchema = z.object({
  theme: z.enum(['ice', 'amber', 'spring']).default('ice'),
  plugins: z.record(z.string(), pluginConfigSchema).default({}),
  services: z.record(z.string(), serviceConfigSchema).default({}),
  auth: authConfigSchema.optional(),
});

export type NiuuConfig = z.infer<typeof niuuConfigSchema>;
export type PluginConfig = z.infer<typeof pluginConfigSchema>;
export type ServiceConfig = z.infer<typeof serviceConfigSchema>;
