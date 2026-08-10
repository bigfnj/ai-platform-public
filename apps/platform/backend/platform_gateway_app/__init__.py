"""Platform gateway — the unified front door.

Serves the single shell SPA, reverse-proxies ``/<app>/api/*`` to each app's own
independent backend (apps stay separately deployable), and owns ``/api/platform/*``
for the shared GPU/model status the top-bar widget shows. All GPU work still flows
through the broker via platform_core.BrokerClient.
"""
