"""Meeting Atlas backend — indexes Meetily recording folders and serves the result.

Does no inference and never calls the broker. Re-transcription and summarisation are
owned by an external ingest task that writes sidecar files this package reads; see
INGEST.md for that contract.
"""
