# Notifications

lookout can send a run summary via [Apprise](https://github.com/caronc/apprise), which supports
dozens of services (Slack, Discord, email, generic webhooks, and more) behind one URL-based
interface.

## Settings

Set one or more Apprise service URLs as a JSON array in `LOOKOUT_NOTIFICATION_URLS` (there's no
CLI flag for this — see [Arguments](arguments.md)):

```bash
docker run -d \
  --name lookout \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -e LOOKOUT_NOTIFICATION_URLS='["slack://token_a/token_b/token_c", "mailto://user:pass@example.com"]' \
  lookout
```

See [github.com/caronc/apprise#popular-notification-services](https://github.com/caronc/apprise#popular-notification-services)
for the full list of supported services and their URL formats.

If `LOOKOUT_NOTIFICATION_URLS` is empty (the default), no notification is attempted and nothing is
sent — including on startup. lookout does not send a "started" notification the way Watchtower
does.

A notification is sent after **every** run, regardless of whether anything changed. lookout does
not currently have a "only notify when something happened" toggle.

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
whose registry check couldn't be completed (pinned-by-digest images, registry errors) appear under
"Skipped".

If a URL fails to parse or a delivery fails, lookout logs a warning and continues — a broken
notification target never fails the run itself.
