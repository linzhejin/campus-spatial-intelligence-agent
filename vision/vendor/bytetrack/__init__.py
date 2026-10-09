"""Minimal, NumPy/SciPy port of FoundationVision ByteTrack.

Upstream: https://github.com/FoundationVision/ByteTrack
Pinned upstream commit: d1bf0191adff59bc8fcfeaa0b33d3d1642552a99
Adaptations are recorded in THIRD_PARTY_NOTICES.md.
"""

from vision.vendor.bytetrack.byte_tracker import BYTETracker

__all__ = ["BYTETracker"]
