"""Console entry point for the ``edu-dashboard`` command (and the top-level serve.py dev
shim): serve the dashboard on http://127.0.0.1:8800."""
import uvicorn


def main() -> None:
    uvicorn.run("dashboard.app:app", host="127.0.0.1", port=8800, reload=False)


if __name__ == "__main__":
    main()
