"""POSIX recovery worker entry point; launched by gg_lock with -I -X utf8."""
import os
import sys


def main() -> int:
    if not sys.flags.isolated:
        return 1
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    if not all(os.path.isabs(entry) for entry in sys.path):
        return 1

    # Import the recovery modules (and their dependencies) before fchdir.
    # Legacy guard methods import fcntl locally, but it is already loaded.
    import fcntl  # noqa: F401
    import gg_lock

    return gg_lock._anchored_child_main(int(sys.argv[1]), int(sys.argv[2]))


if __name__ == "__main__":
    sys.exit(main())
