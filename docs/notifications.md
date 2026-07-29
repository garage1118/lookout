# Notifications

lookout can send a run summary via [Apprise](https://github.com/caronc/apprise), which supports
dozens of services (Slack, Discord, email, generic webhooks, and more) behind one URL-based
interface.

## Settings

Set one or more Apprise service URLs as a comma-separated string in `LOOKOUT_NOTIFICATION_URLS`
(there is no CLI flag for this — see [Arguments](arguments.md)):

```bash
docker run -d \
  --name lookout \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -e LOOKOUT_NOTIFICATION_URLS='slack://token_a/token_b/token_c,mailto://user:pass@example.com' \
  lookout
```

See [github.com/caronc/apprise#popular-notification-services](https://github.com/caronc/apprise#popular-notification-services)
for the full list of supported services and their URL formats. A few are worth calling out
explicitly. They came up repeatedly as feature requests against Watchtower, and need zero lookout
code because Apprise already ships them: Pushover (`pover://`), Bark (`bark://`), MQTT
(`mqtt://`/`mqtts://`), a generic JSON webhook (`json://`), and syslog (`syslog://`).

### Telegram

Telegram is a good choice if you do not have a Google Workspace account. Google Chat's incoming
webhooks require one, and are not available on personal Gmail accounts.

1. Message [`@BotFather`](https://t.me/BotFather) in Telegram, send `/newbot`, and follow the
   prompts. You get a `bot_token` that looks like `123456789:AAAAbcdefg_hijklmnop`.
2. Send any message to your new bot, then visit
   `https://api.telegram.org/bot<bot_token>/getUpdates` in a browser — the JSON response contains
   a `chat.id` field.
3. Set the Apprise URL to `tgram://<bot_token>/<chat_id>`:

```bash
-e LOOKOUT_NOTIFICATION_URLS='tgram://123456789:AAAAbcdefg_hijklmnop/12315544'
```

If `LOOKOUT_NOTIFICATION_URLS` is empty (the default), lookout attempts no notification and sends
nothing.

lookout sends a notification after **every** run, regardless of whether anything changed, unless
`--notify-only-on-change` / `LOOKOUT_NOTIFY_ONLY_ON_CHANGE` is set. With that flag set, lookout
skips a run entirely when nothing was updated, failed, or stale. Containers skipped because they
are pinned-by-digest, or because their registry check failed, do not count as "activity" by
themselves — a registry-check failure is already logged locally at error level.

`stale` alone counts as activity here, deliberately. A container stuck stale — most commonly under
`--monitor-only`, or `--no-pull` with no newer local image available yet — is actionable state an
operator should keep hearing about, not a one-time event. This means `--notify-only-on-change`
fires on **every** poll for as long as that container stays stale, not just the first time lookout
notices it. If that is too noisy for a permanently-monitor-only container, either resolve the
staleness — update it by hand, or pin it by digest — or filter it out of lookout's monitored set.
There is no separate flag to silence a specific container's stale notifications while still
checking it.

lookout can also send a one-time notification when it starts, separate from the per-run summary.
Set `--notify-on-startup` / `LOOKOUT_NOTIFY_ON_STARTUP` (default: off) to enable it. It fires once
per process start, in both `--run-once` and daemon mode, so `--run-once` with the flag set sends
two messages: startup, then the run summary. It is independent of `--notify-only-on-change`, which
only gates the per-run summary. The message is fixed and minimal — `lookout v0.1.0 started` —
matching the run summary's non-templated report format below.

## Report format

The message body is a fixed, plain-text summary. There is no template customization — Watchtower
supports Go templates for this, but lookout does not. It looks like:

```text
lookout run summary: 1 updated, 0 failed, 1 stale, 0 skipped

Updated:
  - web (myapp:latest)

Stale (not updated):
  - worker (myapp:latest)
```

Sections only appear if they have entries. "Stale (not updated)" covers containers found out of
date but left alone, typically because `--monitor-only` (globally or via label) applies to them.
Containers that failed mid-update appear under "Failed" with the error instead. Containers whose
registry check could not complete appear under "Skipped" with a reason: `(pinned)` for
pinned-by-digest images (permanent, expected), or `(check failed)` for a registry check that
errored (transient, actionable — also logged locally at error level).

If a URL fails to parse or a delivery fails, lookout logs a warning and continues — a broken
notification target never fails the run itself.
