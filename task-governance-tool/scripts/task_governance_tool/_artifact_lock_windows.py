"""Windows native one-byte lock operations on an already-open descriptor."""

from __future__ import annotations


def acquire(descriptor: int) -> None:
    import msvcrt

    msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)


def release(descriptor: int) -> None:
    import msvcrt

    msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
