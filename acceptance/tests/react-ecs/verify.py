import sys

from acceptance.support.verify import (
    VerificationContext,
    VerificationError,
    verify_common,
    verify_react_site,
)


def main() -> int:
    context = VerificationContext.from_environment()
    resources = verify_common(context)
    verify_react_site(resources, context.release_sha)
    print("Acceptance verification passed for react-ecs.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except VerificationError as error:
        print(f"Verification failed: {error}", file=sys.stderr)
        raise SystemExit(1)
