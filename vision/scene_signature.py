"""Small perceptual signatures used to reject obviously different camera views."""
from __future__ import annotations

import re
from pathlib import Path

_SIGNATURE_RE = re.compile(r"^[0-9a-f]{16}$")


def normalize_signature(value: str) -> str:
    signature = str(value or "").strip().lower()
    if not _SIGNATURE_RE.fullmatch(signature):
        raise ValueError("画面指纹必须是 16 位十六进制字符串。")
    return signature


def signature_hamming_distance(left: str, right: str) -> int:
    """Return the differing-bit count for two 64-bit difference hashes."""
    a = int(normalize_signature(left), 16)
    b = int(normalize_signature(right), 16)
    return (a ^ b).bit_count()


def difference_hash(image) -> str:
    """Compute a 64-bit dHash from a PIL image or compatible RGB array."""
    from PIL import Image

    if not isinstance(image, Image.Image):
        image = Image.fromarray(image)
    grayscale = image.convert("L").resize((9, 8), Image.Resampling.BILINEAR)
    pixels = list(grayscale.getdata())
    bits = 0
    for y in range(8):
        row = y * 9
        for x in range(8):
            bits = (bits << 1) | int(pixels[row + x] > pixels[row + x + 1])
    return f"{bits:016x}"


def media_signature(path: str | Path, media_kind: str, *, frame_seconds: float = 0) -> str:
    """Hash the selected image or a video frame; raise instead of accepting bad media."""
    source = Path(path)
    if media_kind == "image":
        from PIL import Image

        with Image.open(source) as image:
            image.verify()
        with Image.open(source) as image:
            return difference_hash(image)
    if media_kind != "video":
        raise ValueError("影像类型无效。")
    try:
        import cv2
        from PIL import Image
    except ImportError as error:
        raise RuntimeError("视频画面匹配依赖暂不可用。") from error
    capture = cv2.VideoCapture(str(source))
    if not capture.isOpened():
        capture.release()
        raise ValueError("无法读取视频画面，请重新选择影像。")
    try:
        if frame_seconds > 0:
            capture.set(cv2.CAP_PROP_POS_MSEC, float(frame_seconds) * 1000)
        ok, frame = capture.read()
        if not ok or frame is None:
            raise ValueError("无法读取场景匹配所需的视频帧。")
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        return difference_hash(Image.fromarray(rgb))
    finally:
        capture.release()
