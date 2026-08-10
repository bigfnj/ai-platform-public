# Curriculum intake — moved

Source curriculum PDFs now live in the suite-wide pool at the repo root:

    /content/<grade>/<unit-name>-MM-DD-YYYY/Week N/

Drop new units there and run `python scripts/ingest_content.py` to refresh
`content/manifest.json`. See `content/README.md`.

(teachtown's interactive site still serves its own worksheet copies from
`interactive-html/public/worksheets/`.)
