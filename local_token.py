"""Print a short-lived local JWT for console testing.

This is a development helper only. It never contacts the database and never
prints the configured secret.
"""

from __future__ import annotations

import argparse

from app.core.auth import create_access_token
from app.core.config import settings


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a local Tavonza access token")
    parser.add_argument("--role", default="manager")
    parser.add_argument("--branch-id", type=int, default=8)
    parser.add_argument("--user-id", type=int, default=1)
    args = parser.parse_args()
    if args.branch_id < 1 or args.user_id < 1:
        parser.error("branch-id and user-id must be positive")
    print(
        create_access_token(
            {
                "sub": args.user_id,
                "org_id": 1,
                "branch_id": args.branch_id,
                "role": args.role,
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
