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

## TLS: self-signed or private-CA registries

The digest lookup always connects over `https://` using [`httpx`](https://www.python-httpx.org/),
independently of both the Docker daemon and the host's own certificate trust. A registry behind a
self-signed certificate or a private CA needs to be trusted *inside lookout's own container*, not
wherever the daemon does its own `docker pull` from — and installing the CA into the container's
OS-level trust store (e.g. `update-ca-certificates`) isn't enough either: httpx's default TLS
context only consults `certifi`'s bundled CA list, not the OS trust store, unless overridden.

Set `SSL_CERT_FILE` to a PEM file containing your CA (append it to a copy of certifi's own bundle
first if lookout also needs to reach public registries), or `SSL_CERT_DIR` to a directory of
certificates in OpenSSL's hashed-symlink format (`c_rehash`):

```yaml
services:
  lookout:
    image: lookout
    environment:
      SSL_CERT_FILE: /certs/ca-bundle.pem
    volumes:
      - /etc/lookout/ca-bundle.pem:/certs/ca-bundle.pem:ro
      - /var/run/docker.sock:/var/run/docker.sock
```

Without one of these set, a registry behind a self-signed or private-CA certificate fails with an
`SSLCertVerificationError` regardless of what the Docker daemon itself already trusts.

## Registry propagation delay

Some registries enforce a short TTL on their own end — DigitalOcean's Container Registry is a
known example — so a fresh push can take a few minutes to become visible to a digest lookup made
right after it. If a container isn't detected as stale immediately after pushing, this is a common
cause, not a lookout bug: there's no visibility into a registry's own caching behavior, and nothing
to configure around it beyond waiting for the next poll.

## Not implemented

**Credential helpers** (`credHelpers`/`credsStore` in `config.json`) are not supported. This is
how AWS ECR, GCP Artifact Registry, and some other registries are typically authenticated without
static credentials in `config.json`. If your `config.json` only has a credential helper entry for
a registry (no plain `auths` entry), lookout falls back to anonymous access for that registry,
which will fail for anything private.

**Workaround**: swap the helper for a static credential where the registry supports one.
GCR/Artifact Registry accepts a service-account JSON key as a plain username/password pair —
`docker login -u _json_key -p "$(cat key.json)" gcr.io` writes a normal `auths` entry lookout can
read directly, and unlike an OAuth token it doesn't expire on its own. ECR has no equivalent
long-lived option: `aws ecr get-login-password` tokens are only valid for 12 hours, so a static
`config.json` entry for ECR needs something else (a cron job, a sidecar) to keep rewriting it —
there's no way around that without credential-helper support.

**`identitytoken` entries** — what `docker login` writes for some SSO-based flows instead of a
plain password — are also not handled. The base64 `auth` blob still decodes, but the password half
isn't a usable password, so authentication fails the same as an unsupported credential helper.
