#!/usr/bin/env bash
# Refresh the committed seed corpus from a LIVE recipe-book volume.
#
# The seed (../seed/recipes) is the initial source of truth that hydrates a fresh volume on
# first run. Once an install has been used, its owner's added/edited recipes live only in the
# volume, so the committed seed drifts stale. Run this to snapshot the live corpus back into the
# repo when you want the seed to be current, then commit the result.
#
#   deploy/refresh-seed.sh [container_name]   (default: platform-recipe-book-1)
set -euo pipefail
CONTAINER="${1:-platform-recipe-book-1}"
DEST="$(cd "$(dirname "$0")/.." && pwd)/seed/recipes"

echo "Refreshing seed from container '$CONTAINER' -> $DEST"
rm -rf "$DEST"
MSYS_NO_PATHCONV=1 docker cp "$CONTAINER:/srv/var/recipes" "$DEST"
# A renamed category folder (CATEGORY_RENAMES in src/recipe_book/catalog.py): a live volume keeps
# the spelling its cards were filed under, and the committed seed uses the corrected one. A card's
# id and category come out the same from either spelling, so the snapshot takes the corrected
# one. mv -n never overwrites, and rmdir then fails on anything left behind, which stops the run.
if [ -d "$DEST/Deserts" ]; then
  mkdir -p "$DEST/Desserts"
  mv -n "$DEST/Deserts"/* "$DEST/Desserts/"
  rmdir "$DEST/Deserts"
fi
echo "Done: $(find "$DEST" -name '*.md' | wc -l) cards. Review with 'git status' and commit."
