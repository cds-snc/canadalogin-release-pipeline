import sys

from acceptance.support.verify import (
    VerificationContext,
    VerificationError,
    verify_common,
)


def main() -> int:
    verify_common(VerificationContext.from_environment())
    print("Acceptance verification passed for standard-ecs.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except VerificationError as error:
        print(f"Verification failed: {error}", file=sys.stderr)
        raise SystemExit(1)
