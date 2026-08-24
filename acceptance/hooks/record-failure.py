import os
from pathlib import Path


def main() -> int:
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        Path(summary_path).write_text(
            "Acceptance failure hook executed successfully.\n",
            encoding="utf-8",
        )
    print("Acceptance failure hook executed successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
