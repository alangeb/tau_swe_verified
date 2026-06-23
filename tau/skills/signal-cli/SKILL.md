---
name: signal-cli
description: Signal CLI and JSON-RPC API — send/receive messages, daemon setup, account management (also load: shell_scripting)
category: integrations
---

# signal-cli

## When
"send signal message", "signal CLI", "signal daemon", "receive signal", "signal JSON-RPC"

## Daemon Setup
```bash
signal-cli daemon --http --receive-mode=manual --send-read-receipts
```

## JSON-RPC API
- Endpoint: `POST http://localhost:8080/api/v1/rpc`
- Content-Type: `application/json`

### Send
```json
{"jsonrpc":"2.0","method":"send","params":{"message":"Hello","recipients":["+1234567890"]},"id":1}
```
**Note**: Uses `recipients`, NOT `numbers`.

### Receive
```json
{"jsonrpc":"2.0","method":"receive","params":{},"id":1}
```
Returns array. Envelope types: `dataMessage`, `receiptMessage`, `expirationMessage`.

### Other Methods
- `version` — get version
- `listAccounts` — list registered accounts

## CLI Examples
```bash
signal-cli -a +1234567890 send -m "Hello" +1987654321
signal-cli -a +1234567890 receive
signal-cli listAccounts
signal-cli -a +1234567890 getUserStatus +1987654321
```

## Gotchas
- **Config locked**: When daemon runs, CLI commands fail with "Config file is in use"
- **Receive**: Use JSON-RPC POST, NOT HTTP GET
- **Multi-account**: Daemon starts in multi-account mode when multiple accounts configured
- **Read receipts**: Enable with `--send-read-receipts`

## SQLite DB
Location: `~/.local/share/signal-cli/data/[ACCOUNT_ID]/account.db`

| Table | Purpose |
|-------|---------|
| `recipient` | Contacts (number, ACI, PNI, name, blocked/archived) |
| `session` | Protocol sessions per recipient/device |
| `identity` | Trusted identity keys |
| `pre_key` | Pre-signals keys |
| `message_send_log` | Outgoing message queue |
| `key_value` | Account metadata |
| `group_v1`, `group_v2` | Group membership |

## Account Readiness
- Account registered in `accounts.json`
- Profile name set (profile_sharing=ENABLED)
- Session exists for target contact
- Identity trusted
- Pre-keys available (`SELECT COUNT(*) FROM pre_key`)
- No unregistered contacts in recipient table

## Related Skills
- `shell_scripting` — automate signal workflows
- `background` — run signal daemon in background
