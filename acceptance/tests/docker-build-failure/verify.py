import sys

from acceptance.support.verify import (
    VerificationContext,
    VerificationError,
    verify_build_failure,
)


def main() -> int:
    context = VerificationContext.from_environment()
    verify_build_failure(context)
    print("Acceptance verification passed for docker-build-failure.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except VerificationError as error:
        print(f"Verification failed: {error}", file=sys.stderr)
        raise SystemExit(1)
