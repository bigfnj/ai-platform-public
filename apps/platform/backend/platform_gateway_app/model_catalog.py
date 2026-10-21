"""Re-export shim. The catalog now lives in `platform_core`.

It moved because the BROKER needs it: capability-preserving model fallback asks "what category
is this model" on the resolution path, and the broker cannot import the gateway. It must resolve
`@chat` on a cold boot with the gateway down, and `deploy/Dockerfile.gateway` copies only
`platform_core` anyway.

Kept as a shim rather than rewritten at every call site because `from platform_gateway_app import
model_catalog` is the spelling in main.py and platform_maintenance.py, and a mechanical rename
across both would be a bigger diff than the thing it enables.
"""
from platform_core.model_catalog import *          # noqa: F401,F403
from platform_core.model_catalog import (          # noqa: F401  explicit, for the names used
    CATEGORIES,
    VALID_CATEGORIES,
    category_of,
    curated,
    fallback,
)
