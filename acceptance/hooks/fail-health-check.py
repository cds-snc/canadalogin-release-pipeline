import os
import sys


def main() -> int:
    deployment_sha = os.environ.get("RELEASE_DEPLOYMENT_SHA", "")
    if len(deployment_sha) != 40:
        print("The deployment SHA was not provided to the health hook.", file=sys.stderr)
        return 1
    print("Intentional acceptance-test health-hook failure after deployment.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
