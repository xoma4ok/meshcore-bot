# Telegram Alert Service

The Telegram Alert Service monitors public Telegram channels and relays matching messages to a MeshCore mesh channel. This is the **reverse** of the [Telegram Bridge](telegram-bridge.md) — messages flow from Telegram into MeshCore.

**Features:**
- One-way message flow (Telegram → MeshCore)
- Monitor multiple Telegram channels simultaneously
- Regex-based message filtering (multiple patterns = OR logic, case-insensitive)
- Long messages split on word boundaries with configurable chunk delay
- User-account API — works on any public channel without admin access
- Disabled by default (opt-in)

---

## How It Works

The service uses the [Telethon](https://github.com/LonamiWebs/Telethon) library to connect to Telegram's MTProto API as a regular user account. When a new message arrives in any of the monitored channels and its text matches at least one of the configured regex patterns, the message is relayed to the configured MeshCore channel, split into chunks if necessary.

### Why a user account (not a bot token)?

Telegram bots can only receive messages from chats where they are added as an administrator. Reading **public** channels without admin rights requires a regular Telegram user account via the MTProto API. A standard bot token is not sufficient for this use case.

---

## Quick Start

### 1. Get API credentials

1. Go to [https://my.telegram.org/apps](https://my.telegram.org/apps) and log in with your Telegram phone number.
2. Create an application (any name and platform).
3. Copy **App api_id** (integer) and **App api_hash** (hex string).

### 2. Install Telethon

```bash
pip install telethon
# or if using the project virtualenv:
.venv/bin/pip install telethon
```

### 3. Configure

Edit `config.ini`:

```ini
[Tg_Alert_Service]
enabled = true

tg_alert_service_api_id     = 12345678
tg_alert_service_api_hash   = 0123456789abcdef0123456789abcdef
tg_alert_service_phone      = +15551234567
tg_alert_service_channels   = @channelname, @another_channel
tg_alert_service_meshcore_channel = #warning
tg_alert_service_patterns   = emergency, wildfire, evacuation
```

### 4. First-run authentication

Start the bot. Telethon will prompt you in the terminal for the OTP sent to your Telegram account:

```
Please enter the code you received: 12345
```

After a successful login a session file (`tg_alert_session.session`) is saved. **On all subsequent runs the session file is reused — no phone number or OTP is needed again.** You may then remove `tg_alert_service_phone` from `config.ini`.

### 5. Verify

Send a message in one of the monitored Telegram channels that matches a configured pattern. It should appear in the configured MeshCore channel within a few seconds.

---

## Configuration Reference

All keys live in the `[Tg_Alert_Service]` section.

| Key | Required | Default | Description |
|-----|----------|---------|-------------|
| `enabled` | Yes | `false` | Set to `true` to enable the service |
| `tg_alert_service_api_id` | Yes | — | Integer API ID from [my.telegram.org](https://my.telegram.org/apps) |
| `tg_alert_service_api_hash` | Yes | — | Hex API hash from [my.telegram.org](https://my.telegram.org/apps) |
| `tg_alert_service_phone` | First run only | — | Phone number in international format, e.g. `+15551234567`. Required for initial authentication; can be removed after the session file is created. |
| `tg_alert_service_channels` | Yes | — | Comma-separated list of Telegram channels to monitor. Use `@username` for public channels or a numeric ID (e.g. `-1001234567890`) for private ones. |
| `tg_alert_service_meshcore_channel` | Yes | — | MeshCore channel to relay matching messages to (e.g. `#warning`) |
| `tg_alert_service_patterns` | Yes | — | Comma-separated regex patterns. A message is relayed if it matches **any** pattern (OR logic). Matching is case-insensitive. |
| `tg_alert_service_session_name` | No | `tg_alert_session` | Telethon session file name (without `.session` extension) |
| `tg_alert_service_max_chunk_len` | No | `120` | Maximum characters per MeshCore message. Longer messages are split on word boundaries. |
| `tg_alert_service_chunk_delay` | No | `2` | Seconds to wait between consecutive chunks of a split message. |

### Example — emergency alerts

```ini
[Tg_Alert_Service]
enabled = true

tg_alert_service_api_id     = 12345678
tg_alert_service_api_hash   = 0123456789abcdef0123456789abcdef
; phone only needed on first run — remove after session file is created
; tg_alert_service_phone    = +15551234567

tg_alert_service_channels   = @usgs_earthquakes, @nws_alerts
tg_alert_service_meshcore_channel = #warning
tg_alert_service_patterns   = earthquake, tsunami, tornado, evacuation, wildfire

tg_alert_service_max_chunk_len = 120
tg_alert_service_chunk_delay   = 2
```

---

## Message Splitting

MeshCore channel messages have a maximum length. When a Telegram message exceeds `tg_alert_service_max_chunk_len` characters, it is split into multiple chunks:

- Splits are made on word boundaries (spaces) wherever possible.
- If a single word is longer than the limit, it is split at the character limit.
- Each chunk after the first is delayed by `tg_alert_service_chunk_delay` seconds to avoid flooding the mesh channel.

---

## Security & Privacy

### Protect your credentials

- `api_id`, `api_hash`, and the session file together grant full access to your Telegram account.
- **Never commit `config.ini` or `*.session` files to version control.**
- Both are already in `.gitignore`.
- If credentials are exposed, revoke the application at [my.telegram.org/apps](https://my.telegram.org/apps) and delete the session file.

### Session file

The session file (`tg_alert_session.session` by default) is stored in the bot's working directory. It allows Telethon to reconnect without re-authentication. Keep it secure and back it up if needed.

---

## Troubleshooting

### Service not starting

```bash
grep -i tgalert meshcore_bot.log
```

Common causes:
- `telethon` not installed — `pip install telethon`
- Missing required config keys (`api_id`, `api_hash`, `channels`, `meshcore_channel`, `patterns`)
- Invalid regex in `tg_alert_service_patterns` — check the log for a parse error

### Authentication prompt not appearing

The OTP prompt is printed to stdout. If you run the bot as a systemd service, it cannot prompt interactively. Run it manually from a terminal for the first-time authentication:

```bash
.venv/bin/python meshcore_bot.py
```

Once the session file is created, the service can run unattended (including as a systemd service).

### Messages not relayed to MeshCore

1. Confirm the Telegram message text matches at least one pattern (patterns are regex, not plain substrings — e.g. `wildfire` matches the word anywhere in the text).
2. Check `tg_alert_service_meshcore_channel` matches a channel the bot is configured to transmit on.
3. Enable `DEBUG` logging (`log_level = DEBUG` in `[Bot]`) and look for `TgAlertService: match` log lines.

### FloodWaitError

Telethon encountered Telegram's flood-wait limit. The service will pause for the required duration and reconnect automatically. No action needed.

### Reconnect loop

If the service repeatedly disconnects, check logs for the error message. The service waits 30 seconds before each reconnect attempt. Persistent failures are usually caused by an expired session (delete the `.session` file and re-authenticate) or an invalid `api_id`/`api_hash`.

---

## FAQ

**Q: Can I relay messages from MeshCore to Telegram?**  
A: That direction is handled by the [Telegram Bridge](telegram-bridge.md) service.

**Q: Can I monitor private channels?**  
A: Yes, if the Telegram account used for authentication is a member of the private channel. Use the numeric chat ID (e.g. `-1001234567890`) in `tg_alert_service_channels`.

**Q: How do I get the numeric ID of a private channel?**  
A: Forward any message from the channel to [@userinfobot](https://t.me/userinfobot) in a private chat. The reply includes the Chat ID.

**Q: What if a pattern is invalid regex?**  
A: The service logs an error and sets itself to disabled. Fix the pattern and restart the bot.

**Q: Can I use the same Telegram account as for the Telegram Bridge?**  
A: The Telegram Bridge uses a bot token (Bot API), while this service uses a user account (MTProto). They are independent and can run simultaneously.

**Q: Will this work without a phone number after the first run?**  
A: Yes. Once the session file exists, you can remove `tg_alert_service_phone` from the config. The session file handles re-authentication automatically.

---

## Implementation Details

- **Service class**: `TgAlertService` in `modules/service_plugins/tg_alert_service.py`
- **Base class**: `BaseServicePlugin` (`modules/service_plugins/base_service.py`)
- **Config section**: `[Tg_Alert_Service]`
- **Library**: [Telethon](https://github.com/LonamiWebs/Telethon) (MTProto user-account API)
- **Reconnect strategy**: Infinite retry loop with 30-second backoff on error
- **Tests**: `tests/test_tg_alert_service.py` (47 tests)

**Dependencies:** Requires `telethon` (`pip install telethon`). Not included in the base `requirements.txt` — install separately.
