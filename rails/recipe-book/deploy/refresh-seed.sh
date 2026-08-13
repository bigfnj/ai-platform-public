#!/usr/bin/env bash
# Refresh the committed seed corpus from a LIVE recipe-book volume.
#
# The seed (../seed/recipes) is the initial source of truth that hydrates a fresh volume on
# first run. Once an install has been used, its owner's added/edited recipes live only in the
# volume, so the committed seed drifts stale. Run this to snapshot the live corpus back into the
# repo when you want the seed to be current, then commit the result.
#
#   deploy/refresh-seed.sh [container_name]   (default: platform-recipe-book-1)
#
# Set CONTAINER_CLI=podman when the stack runs on Podman (the container name is the same - both
# Compose implementations use <project>-<service>-1).
set -euo pipefail
CONTAINER="${1:-platform-recipe-book-1}"
CLI="${CONTAINER_CLI:-docker}"
DEST="$(cd "$(dirname "$0")/.." && pwd)/seed/recipes"

echo "Refreshing seed from container '$CONTAINER' via $CLI -> $DEST"
rm -rf "$DEST"
MSYS_NO_PATHCONV=1 "$CLI" cp "$CONTAINER:/srv/var/recipes" "$DEST"
echo "Done: $(find "$DEST" -name '*.md' | wc -l) cards. Review with 'git status' and commit."
