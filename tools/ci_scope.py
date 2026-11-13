#!/usr/bin/env python3
"""Decide the PR validation scope: print exactly one value, ``full`` or ``code``.

Output contract (design §2.3): stdout carries the value alone — no ``name=``
prefix. The workflow wraps it as ``printf 'scope=%s\\n'`` into
``$GITHUB_OUTPUT``; a non-zero exit means the workflow must fail closed. Every
undeterminable outcome is closed here as ``full`` with exit 0, so a repository
that cannot be diffed falls back to the whole suite instead of skipping it.

Rules (design §3):

1. a version-bump file changed                      -> ``full``
2. any other changed path (generated ledgers aside) -> ``code``
3. undeterminable (empty diff, git error, bad rev,
   no single common ancestor)                       -> ``full``

Paths are taken from the common ancestor (``git merge-base``), not from a
straight ``base head`` diff: the PR base is main's tip, so a version file main
bumped after the fork would leak in, and one both sides bumped the same way
would cancel out.

The three generated ledgers are ignored: they ride along on nearly every code
PR and their integrity is checked by ``check_bundle.py`` and
``build_validation_bundle.py --check`` in every job. Rule 2 is the catch-all,
so a new or unfamiliar path is never skipped by accident.
"""
import argparse
from pathlib import Path
import subprocess
import sys

FULL_SCOPE_PATHS = frozenset({
    "skills/knuaf-doc/version.json",
    ".codex-plugin/plugin.json",
})
GENERATED_LEDGERS = frozenset({
    "tools/validation-bundle.json",
    "tools/runtime-changes.json",
    "tools/validation-provenance.json",
})
SCOPES = ("full", "code")


def _git(root, *args):
    """Run git, returning stdout bytes; any failure is undeterminable."""
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if result.returncode:
        return None
    return result.stdout


def merge_base(root, base, head):
    """The single merge base of two commits, or None when undeterminable.

    Comparing the two tips directly is wrong for a long-lived pull request:
    a version file main bumped after the branch point would look like a PR
    change (needless ``full``), and a version file both sides bumped the same
    way would cancel out (a ``full`` PR classified as ``code``).
    """
    for rev in (base, head):
        if not rev or _git(root, "rev-parse", "--verify", rev + "^{commit}") is None:
            return None
    raw = _git(root, "merge-base", "--all", base, head)
    if raw is None:
        return None
    bases = raw.decode("utf-8", "surrogateescape").split()
    if len(bases) != 1:          # none, or several (criss-cross): undeterminable
        return None
    return bases[0]


def changed_paths(root, base, head):
    """Paths this branch changed since it forked, or None when undeterminable.

    Renames are reported as delete+add (``--no-renames``) so the old path is
    classified too; a rename out of a version file must still force ``full``.
    """
    root = Path(root)
    ancestor = merge_base(root, base, head)
    if ancestor is None:
        return None
    raw = _git(root, "diff", "--name-only", "-z", "--no-renames",
               "--diff-filter=ACDMTUXB", ancestor, head, "--")
    if raw is None:
        return None
    return [path for path in raw.decode("utf-8", "surrogateescape").split("\0")
            if path]


def decide(paths):
    """Scope for one changed-path list; an empty list is undeterminable."""
    if not paths:
        return "full"
    interesting = [path for path in paths if path not in GENERATED_LEDGERS]
    if not interesting:
        # Only generated ledgers changed: nothing to validate, but an empty
        # effective diff is the undeterminable case, not a licence to skip.
        return "full"
    if any(path in FULL_SCOPE_PATHS for path in interesting):
        return "full"
    return "code"


def scope_for(root, base, head):
    paths = changed_paths(root, base, head)
    if paths is None:
        return "full"
    return decide(paths)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base", required=True, help="base commit (PR base SHA)")
    parser.add_argument("--head", required=True, help="head commit (PR head SHA)")
    parser.add_argument("--root", default=str(Path(__file__).resolve().parents[1]),
                        help="repository root (default: this checkout)")
    args = parser.parse_args(argv)
    scope = scope_for(args.root, args.base, args.head)
    if scope not in SCOPES:      # unreachable guard: the workflow contract
        print("ci_scope: refusing unknown scope %r" % (scope,), file=sys.stderr)
        return 2
    print(scope)
    return 0


if __name__ == "__main__":
    sys.exit(main())
