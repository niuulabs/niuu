# Gateway Channels

The gateway enables Ravn to communicate through external messaging channels.
Each channel translates between the channel's native format and Ravn's
internal event stream.

## Supported Channels

| Channel | Transport | Use Case |
|---------|-----------|----------|
| HTTP | REST + WebSocket + SSE | Local API access, web integrations |
| Telegram | Bot API polling | Mobile/desktop messaging |
| Discord | Bot API | Team communication |
| Slack | Socket Mode | Workplace messaging |
| Matrix | Client-Server API | Federated messaging |
| WhatsApp | Meta Cloud API | WhatsApp Business |
| Skuld | WebSocket | Custom WebSocket broker |

## HTTP Gateway

The HTTP channel exposes a local API server:

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/v1/chat` | POST | Send a message, receive response. |
| `/v1/chat/stream` | POST | Send a message, receive SSE stream. |
| `/v1/ws` | WebSocket | Bidirectional conversation. |
| `/health` | GET | Health check. |

```yaml
gateway:
  channels:
    http:
      host: "0.0.0.0"
      port: 7477
```

### WebSocket Protocol

WebSocket connections use JSON frames:

```json
// Client → Server
{ "type": "message", "content": "explain this code" }

// Server → Client (streaming)
{ "type": "text_delta", "content": "The code..." }
{ "type": "tool_call", "name": "read_file", "input": {...} }
{ "type": "tool_result", "content": "..." }
{ "type": "message_done" }
```

### SSE Format

Server-sent events use the CLI `stream-json` format:

```
data: {"type": "text_delta", "content": "The code..."}
data: {"type": "tool_call", "name": "read_file"}
data: {"type": "message_done"}
```

## Telegram Bot

Poll-based Telegram bot integration:

```yaml
gateway:
  channels:
    telegram:
      token_env: "TELEGRAM_BOT_TOKEN"
      allowed_chat_ids: [123456789, 987654321]
      poll_timeout: 30
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `token_env` | str | `"TELEGRAM_BOT_TOKEN"` | Env var containing bot token. |
| `allowed_chat_ids` | list[int] | `[]` | Restrict to specific chat IDs. Empty = all. |
| `poll_timeout` | int | `30` | Long-poll timeout (seconds). |

Setup:

1. Create a bot via [@BotFather](https://t.me/BotFather)
2. Set the token in an environment variable
3. Get your chat ID (message the bot, check updates)
4. Add the chat ID to `allowed_chat_ids`

## Discord Bot

```yaml
gateway:
  channels:
    discord:
      token_env: "DISCORD_BOT_TOKEN"
      guild_id: "123456789"
      command_prefix: "!"
```

## Slack Bot

Uses Slack Socket Mode for real-time messaging:

```yaml
gateway:
  channels:
    slack:
      bot_token_env: "SLACK_BOT_TOKEN"
      app_token_env: "SLACK_APP_TOKEN"
      poll_interval: 1.0
```

## Matrix

Federated messaging with optional end-to-end encryption:

```yaml
gateway:
  channels:
    matrix:
      homeserver: "https://matrix.example.com"
      user_id_env: "MATRIX_USER_ID"
      access_token_env: "MATRIX_TOKEN"
      e2e: false
      sync_timeout_ms: 30000
```

## WhatsApp

WhatsApp Business API via Meta Cloud API:

```yaml
gateway:
  channels:
    whatsapp:
      api_key_env: "WHATSAPP_API_KEY"
      phone_number_id: "123456789"
      webhook_host: "0.0.0.0"
      webhook_port: 8443
```

## Skuld WebSocket Broker

Custom WebSocket broker for internal routing:

```yaml
gateway:
  channels:
    skuld:
      broker_url: "ws://skuld.ravn.svc:8080/ws"
```

## Event Translation

Each channel has an event translator that converts between Ravn's internal
`StreamEvent` format and the channel's native message format. The HTTP
channel's translator (`stream-json`) is the reference implementation.

## Per-Session Agents

Each incoming conversation creates a per-session agent instance. The gateway
manages session lifecycles — creating agents on first message, routing
subsequent messages to the correct session, and cleaning up idle sessions.
