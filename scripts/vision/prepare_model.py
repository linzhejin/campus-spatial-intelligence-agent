#!/usr/bin/env python3
"""Fetch a pinned research checkpoint and optionally export it to ONNX.

The checkpoint and exported ONNX file are written only to the ignored model
directory; they are never added to the application repository or deployment
archive. Run this on a development machine, not on the 2 GB production host.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path


MODEL_REPOSITORY = "dronefreak/visdrone-rtdetrv4-s"
MODEL_REVISION = "820fca3d962243f7d611dd35cf2a64d267635499"
MODEL_CHECKPOINT_SHA256 = "c58f69a63adcde600e2c4b755f7b72ef0a02ba9a9fa656271c8d32a9426a14c7"
EXPORT_REPOSITORY = "https://github.com/dronefreak/RT-DETRv4.git"
EXPORT_REVISION = "be440e87ca85927404e739c2dee17c502cd09939"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _verify_standalone_onnx(path: Path) -> str:
    """Validate the exported file is self-contained before treating it as deployable."""
    try:
        import onnx
    except ImportError as error:
        raise RuntimeError("导出环境缺少 onnx，无法验证模型文件") from error
    model = onnx.load(str(path), load_external_data=False)
    tensors = list(model.graph.initializer)
    tensors.extend(item.values for item in model.graph.sparse_initializer)
    external = [
        tensor.name for tensor in tensors
        if tensor.data_location == onnx.TensorProto.EXTERNAL or tensor.external_data
    ]
    if external:
        raise RuntimeError(
            "导出的 ONNX 引用了外部权重文件，不能按单文件部署："
            + ", ".join(external[:5])
        )
    onnx.checker.check_model(model)
    return _sha256(path)


def _download(relative_path: str, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_file() and (relative_path != "model.pth" or _sha256(target) == MODEL_CHECKPOINT_SHA256):
        return
    url = f"https://huggingface.co/{MODEL_REPOSITORY}/resolve/{MODEL_REVISION}/{relative_path}?download=true"
    partial = target.with_suffix(target.suffix + ".part")
    request = urllib.request.Request(url, headers={"User-Agent": "WHU-Walker-Academic-Prototype/1.0"})
    digest = hashlib.sha256()
    try:
        with urllib.request.urlopen(request, timeout=60) as response, partial.open("wb") as out:
            while block := response.read(1024 * 1024):
                digest.update(block)
                out.write(block)
        if relative_path == "model.pth" and digest.hexdigest() != MODEL_CHECKPOINT_SHA256:
            raise RuntimeError("模型 checkpoint SHA256 不匹配，已拒绝使用")
        partial.replace(target)
    finally:
        partial.unlink(missing_ok=True)


def _export(source_dir: Path, model_dir: Path) -> Path:
    source_dir = source_dir.resolve()
    head = subprocess.check_output(
        ["git", "-C", str(source_dir), "rev-parse", "HEAD"], text=True,
    ).strip()
    if head != EXPORT_REVISION:
        raise RuntimeError(f"RT-DETRv4 导出源码应为 {EXPORT_REVISION}，当前是 {head}")
    config_file = model_dir / "config.yaml"
    checkpoint = model_dir / "model.pth"
    exporter = source_dir / "tools" / "deployment" / "export_onnx.py"
    if not exporter.is_file():
        raise RuntimeError(f"找不到官方 ONNX 导出脚本：{exporter}")
    subprocess.run([
        sys.executable, str(exporter), "--check",
        "-c", str(config_file), "-r", str(checkpoint),
    ], cwd=source_dir, check=True)
    exported = checkpoint.with_suffix(".onnx")
    destination = model_dir / "visdrone-rtdetrv4-s.onnx"
    if not exported.is_file():
        raise RuntimeError("导出命令未生成 ONNX 权重")
    if exported != destination:
        os.replace(exported, destination)
    onnx_sha256 = _verify_standalone_onnx(destination)
    # Some exporter runs leave a stale external-data file even after ONNX
    # simplification embedded every tensor. Remove it only after verification.
    stale_data = checkpoint.with_suffix(".onnx.data")
    if stale_data.is_file():
        stale_data.unlink()
    if _sha256(destination) != onnx_sha256:
        raise RuntimeError("ONNX 模型校验期间文件发生变化")
    return destination


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-dir", type=Path, default=Path("models/vision"))
    parser.add_argument("--rtdetrv4-source", type=Path,
                        help="可选：已检出固定版本的 RT-DETRv4 源码目录，传入后会导出 ONNX")
    parser.add_argument("--download-only", action="store_true",
                        help="只获取并校验训练 checkpoint 与模型配置，不执行导出")
    args = parser.parse_args()

    model_dir = args.model_dir.resolve()
    model_dir.mkdir(parents=True, exist_ok=True)
    _download("config.yaml", model_dir / "config.yaml")
    _download("model.pth", model_dir / "model.pth")
    manifest = {
        "model_id": "visdrone-rtdetrv4-s",
        "model_repository": MODEL_REPOSITORY,
        "model_revision": MODEL_REVISION,
        "checkpoint_sha256": _sha256(model_dir / "model.pth"),
        "export_repository": EXPORT_REPOSITORY,
        "export_revision": EXPORT_REVISION,
        "dataset": "VisDrone2019-DET",
        "dataset_license": "CC BY-NC-SA 3.0 (academic/non-commercial research use)",
        "weights_distribution": "not bundled; private server-side file only",
        "accident_class_supported": False,
    }
    if not args.download_only and args.rtdetrv4_source:
        onnx_file = _export(args.rtdetrv4_source, model_dir)
        manifest["onnx_sha256"] = _sha256(onnx_file)
        manifest["onnx_path"] = onnx_file.name
        manifest["onnx_self_contained"] = True
    elif not args.download_only:
        print("已下载并校验固定版本 checkpoint；传入 --rtdetrv4-source 后可转换为 ONNX。")
    (model_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
