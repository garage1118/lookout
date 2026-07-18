#!/usr/bin/env bash
# Start (or restart from scratch) the container lookout will watch during testing.
set -euo pipefail

IMAGE="${1:-${LOOKOUT_TEST_IMAGE:-localhost:5000/lookout-test:latest}}"
NAME="${LOOKOUT_TEST_CONTAINER:-lookout-test}"

docker rm -f "$NAME" >/dev/null 2>&1 || true
docker run -d --name "$NAME" "$IMAGE" >/dev/null

echo "started $NAME from $IMAGE"
