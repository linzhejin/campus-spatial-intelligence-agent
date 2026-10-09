# Third-party notices

This directory contains a minimal NumPy/SciPy adaptation of the tracking code
from [FoundationVision/ByteTrack](https://github.com/FoundationVision/ByteTrack),
pinned to upstream commit `d1bf0191adff59bc8fcfeaa0b33d3d1642552a99`.

The upstream project is distributed under the MIT License; see `LICENSE`.
This adaptation replaces the upstream `lap` and `cython_bbox` association
helpers with SciPy's linear assignment and NumPy IoU calculations, removes the
PyTorch-facing detector interface, and carries detection indices through the
tracker so IDs can be attached to this application's observations. The
application also stabilizes boxes into a segment anchor using its existing
camera-motion homographies before association.

These changes preserve the ByteTrack two-stage association and Kalman tracking
flow, but are not a verbatim copy of the upstream package. No upstream model
weights or training data are included.
