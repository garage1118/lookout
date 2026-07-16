# Notifications

lookout can send a run summary via [Apprise](https://github.com/caronc/apprise), which supports
dozens of services (Slack, Discord, email, generic webhooks, and more) behind one URL-based
interface.

## Settings

Set one or more Apprise service URLs as a comma-separated string in `LOOKOUT_NOTIFICATION_URLS`
(there's no CLI flag for this — see [Arguments](arguments.md)):

```bash
docker run -d \
  --name lookout \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -e LOOKOUT_NOTIFICATION_URLS='slack://token_a/token_b/token_c,mailto://user:pass@example.com' \
  lookout
```

See [github.com/caronc/apprise#popular-notification-services](https://github.com/caronc/apprise#popular-notification-services)
for the full list of supported services and their URL formats. A few worth calling out explicitly,
since they came up repeatedly as feature requests against Watchtower and need zero lookout code
since Apprise already ships them: Pushover (`pover://`), Bark (`bark://`), MQTT
(`mqtt://`/`mqtts://`), a generic JSON webhook (`json://`), and syslog (`syslog://`).

### Telegram

Telegram is a good choice if you don't have a Google Workspace account — Google Chat's incoming
webhooks require one and aren't available on personal Gmail accounts.

1. Message [`@BotFather`](https://t.me/BotFather) in Telegram, send `/newbot`, and follow the
   prompts. You'll get a `bot_token` that looks like `123456789:AAAAbcdefg_hijklmnop`.
2. Send any message to your new bot, then visit
   `https://api.telegram.org/bot<bot_token>/getUpdates` in a browser — the JSON response contains
   a `chat.id` field.
3. Set the Apprise URL to `tgram://<bot_token>/<chat_id>`:

```bash
-e LOOKOUT_NOTIFICATION_URLS='tgram://123456789:AAAAbcdefg_hijklmnop/12315544'
```

If `LOOKOUT_NOTIFICATION_URLS` is empty (the default), no notification is attempted and nothing is
sent.

A notification is sent after **every** run, regardless of whether anything changed, unless
`--notify-only-on-change` / `LOOKOUT_NOTIFY_ONLY_ON_CHANGE` is set — in that case a run with
nothing updated, failed, or stale is skipped entirely (containers skipped because they're
pinned-by-digest or their registry check failed don't count as "activity" by themselves, since a
registry-check failure is already logged locally at error level).

Note that `stale` alone counts as activity here, deliberately: a container stuck stale — most
commonly `--monitor-only`, or `--no-pull` with no newer local image available yet — is actionable
state an operator should keep hearing about, not a one-time event. This means
`--notify-only-on-change` will fire on **every** poll for as long as that container stays stale,
not just the first time it's noticed. If that's too noisy for a permanently-monitor-only container,
either resolve the staleness (update it by hand, or pin it by digest) or filter it out of lookout's
monitored set — there's no separate flag to silence a specific container's stale notifications
while still checking it.

lookout can also send a one-time notification when it starts, separate from the per-run summary —
set `--notify-on-startup` / `LOOKOUT_NOTIFY_ON_STARTUP` (default: off). It fires once per process
start, in both `--run-once` and daemon mode (so `--run-once` with the flag set sends two messages:
startup, then the run summary), and is independent of `--notify-only-on-change`, which only gates
the per-run summary. The message is fixed and minimal — `lookout v0.1.0 started` — matching the
run summary's non-templated report format below.

## Report format

The message body is a fixed, plain-text summary — there is no template customization (Watchtower
supports Go templates for this; lookout does not). It looks like:

```text
lookout run summary: 1 updated, 0 failed, 1 stale, 0 skipped

Updated:
  - web (myapp:latest)

Stale (not updated):
  - worker (myapp:latest)
```

Sections only appear if they have entries. "Stale (not updated)" covers containers found out of
date but left alone — typically because `--monitor-only` (globally or via label) applies to them.
Containers that failed mid-update appear under "Failed" with the error instead, and containers
whose registry check couldn't be completed appear under "Skipped" with a reason: `(pinned)` for
pinned-by-digest images (permanent, expected) or `(check failed)` for a registry check that errored
(transient, actionable — also logged locally at error level).

If a URL fails to parse or a delivery fails, lookout logs a warning and continues — a broken
notification target never fails the run itself.
