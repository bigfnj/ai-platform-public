"""Per-job subprocess entrypoint:  python run_job.py <job_id> <db_path>.

Spawned by the queue so each job's GPU memory is fully released on exit.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from dashboard.runner import execute_job  # noqa: E402
from dashboard.store import Store  # noqa: E402


def main() -> None:
    if len(sys.argv) != 3:
        print("usage: run_job.py <job_id> <db_path>", file=sys.stderr)
        sys.exit(2)
    job_id, db_path = sys.argv[1], sys.argv[2]
    store = Store(db_path)
    job = store.get_job(job_id)
    if not job:
        print(f"job not found: {job_id}", file=sys.stderr)
        sys.exit(2)
    execute_job(store, job)


if __name__ == "__main__":
    main()
