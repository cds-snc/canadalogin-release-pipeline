import sys

from acceptance.support.verify import (
    VerificationContext,
    VerificationError,
    verify_common,
)


def main() -> int:
    context = VerificationContext.from_environment()
    verify_common(context)
    print("Acceptance verification passed for failure-ecs.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except VerificationError as error:
        print(f"Verification failed: {error}", file=sys.stderr)
        raise SystemExit(1)
