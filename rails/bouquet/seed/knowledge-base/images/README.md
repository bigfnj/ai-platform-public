# Reference Image Library

Licensed reference photographs used to make flower identification reliable. Each
Tier-1 flower has its own folder (`<slug>/`) with **4 vetted photos** chosen to
show the bloom form, color range, and identifying features.

## Licensing & attribution
Every image was downloaded from **Wikimedia Commons** and filtered to
**reusable licenses only**: Public Domain / CC0 / CC-BY / CC-BY-SA /
"No restrictions". Non-free and fair-use images are excluded.

**Full per-image attribution** (author, license, Commons source page) lives in
[`image-manifest.json`](image-manifest.json). Each flower profile embeds its four
images with a short credit line; the manifest is the authoritative record.

CC-BY and CC-BY-SA require crediting the author and (for BY-SA) sharing
derivatives under the same license — the manifest preserves what's needed to
comply.

## Quality control
Images are spot-checked visually. Rejected on review and replaced: novelty
composites (e.g. a face inside a flower), historical engravings/book plates
(not photographs), and misfiled look-alikes (e.g. a poppy tagged as a
carnation). The fetch pipeline excludes engravings, book scans, paintings, and
known look-alike terms by keyword.

## Regenerating / extending
The downloader scripts live in the session scratchpad (`fetch_flower_images*.py`).
To add a flower: add its slug + search queries, run the fetch (polite rate-
limited Commons client), spot-check, then run `inject_images.py` to embed the new
images into that flower's profile. Numbering is `<slug>-01..NN`.
