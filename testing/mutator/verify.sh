#!/usr/bin/env bash
# Print enough about the watched container to tell whether lookout actually
# recreated it: a changed /version means it's genuinely a new container
# running the new image, not the same container still running.
set -euo pipefail

NAME="${LOOKOUT_TEST_CONTAINER:-lookout-test}"

echo "container id: $(docker inspect -f '{{.Id}}' "$NAME")"
echo "created:      $(docker inspect -f '{{.Created}}' "$NAME")"
echo "image id:     $(docker inspect -f '{{.Image}}' "$NAME")"
echo "version:      $(docker exec "$NAME" cat /version)"
