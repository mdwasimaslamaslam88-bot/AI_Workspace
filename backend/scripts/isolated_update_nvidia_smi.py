#!/usr/bin/env python3
"""Deterministic hardware fixture for the isolated browser release gate."""

from __future__ import annotations

import sys


_QUERY = "--query-gpu=name,memory.total,memory.free,compute_cap,driver_version"
_FORMAT = "--format=csv,noheader,nounits"


def output_for(arguments: tuple[str, ...]) -> str | None:
    if arguments == (_QUERY, _FORMAT):
        return "WORK STATION isolated validation GPU, 12288, 12288, 8.6, 0.0\n"
    if not arguments:
        return "| CUDA Version: 0.0 |\n"
    return None


def main() -> int:
    output = output_for(tuple(sys.argv[1:]))
    if output is None:
        return 2
    sys.stdout.write(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
