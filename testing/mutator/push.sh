#!/usr/bin/env bash
# Build and push a new version of the mutator image under the same tag, so
# the registry digest changes but the image reference lookout watches does not.
set -euo pipefail

IMAGE="${1:-${LOOKOUT_TEST_IMAGE:-localhost:5000/lookout-test:latest}}"
VERSION="$(date -u +%Y%m%dT%H%M%SZ)"
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

docker build --build-arg VERSION="$VERSION" -t "$IMAGE" "$DIR"
docker push "$IMAGE"

echo "pushed $IMAGE @ version $VERSION"
