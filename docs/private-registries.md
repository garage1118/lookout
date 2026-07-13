# Private registries

lookout reads registry credentials from a Docker CLI-style `config.json`'s plain `auths` section —
the same file `docker login` writes. If you'd rather not mount a config file at all (e.g.
deploying via a UI like Portainer, or on a host you haven't run `docker login` on), a single
fallback credential pair via environment variables works too — see
[Environment variable fallback](#environment-variable-fallback) below.

## Environment variable fallback

Set `LOOKOUT_REGISTRY_HOST`, `LOOKOUT_REGISTRY_USERNAME`, and `LOOKOUT_REGISTRY_PASSWORD` to a
single credential pair scoped to one registry, tried only for images on that registry when
`config.json` has no matching entry for it (or there's no `config.json` at all):

```bash
docker run -d \
  --name lookout \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -e LOOKOUT_REGISTRY_HOST=registry.example.com \
  -e LOOKOUT_REGISTRY_USERNAME=you \
  -e LOOKOUT_REGISTRY_PASSWORD=your-password \
  lookout
```

`LOOKOUT_REGISTRY_HOST` is required for the fallback to be used at all — without it, lookout logs a
warning and never applies the credentials. This scoping matters: an earlier version applied the
username/password to *any* registry lacking a config.json entry, which included public ones like
Docker Hub. Docker Hub's token endpoint rejects a bad Basic-auth attempt outright — unlike an
anonymous request, which succeeds for public images — so that broke pulls for every public image
being watched alongside the private one. Setting `LOOKOUT_REGISTRY_HOST` is what keeps the fallback
scoped to just the registry it's meant for.

If you have more than one private registry with different credentials, use `config.json` instead —
it can hold entries for as many registries as you need, and always takes precedence over the
fallback when both exist.

## Using your existing docker login

If you've already run `docker login` on the host, mount `~/.docker/config.json` (or the directory
containing it, see below) into the lookout container:

```bash
docker run -d \
  --name lookout \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v $HOME/.docker/config.json:/root/.docker/config.json:ro \
  lookout
```

## Docker config path

lookout looks for `config.json` at `~/.docker/config.json` inside its own container by default.
Set `DOCKER_CONFIG` (a directory, not a file path — matching the Docker CLI's own convention) to
change where it looks:

```yaml
services:
  lookout:
    image: lookout
    environment:
      DOCKER_CONFIG: /config
    volumes:
      - /etc/lookout/config/:/config/
      - /var/run/docker.sock:/var/run/docker.sock
```

## Creating a config.json manually

```json
{
  "auths": {
    "<REGISTRY_HOST>": {
      "auth": "XXXXXXX"
    }
  }
}
```

`<REGISTRY_HOST>` is the registry's hostname (e.g. `ghcr.io`, or
`my-registry.example.org:5000`). The `auth` value is base64-encoded `username:password`:

```bash
echo -n 'username:password' | base64
```

For Docker Hub, `<REGISTRY_HOST>` should be `https://index.docker.io/v1/` — lookout recognizes
that historical key (and `registry-1.docker.io`/`docker.io`) as referring to Docker Hub, matching
what `docker login` itself writes.

## How the digest lookup authenticates

For each image, lookout probes the registry's `/v2/` endpoint. If it comes back `200`, the
registry is public and no credentials are used. If it returns a `Bearer` challenge (Docker Hub,
GHCR, ECR, and most others), lookout exchanges any configured credentials for a short-lived token
via the realm in that challenge — the same flow the `docker` CLI itself uses. If the registry has
no bearer challenge at all, any configured credentials are sent as HTTP Basic auth directly.

## Not implemented

**Credential helpers** (`credHelpers`/`credsStore` in `config.json`) are not supported. This is
how AWS ECR, GCP Artifact Registry, and some other registries are typically authenticated without
static credentials in `config.json`. If your `config.json` only has a credential helper entry for
a registry (no plain `auths` entry), lookout falls back to anonymous access for that registry,
which will fail for anything private.
