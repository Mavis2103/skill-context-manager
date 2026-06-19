"""Download and prepare embedding models for SCM.

Usage:
    python3 scripts/download-embedding-model.py          # Download + convert to ONNX
    python3 scripts/download-embedding-model.py --cpu    # PyTorch only (no ONNX)
"""

import argparse
import sys
from pathlib import Path

CACHE_DIR = Path.home() / ".scm" / "models"
MODEL_NAME = "BAAI/bge-base-en-v1.5"


def download_pytorch():
    """Download model via sentence-transformers."""
    print(f"📥 Downloading {MODEL_NAME}...")
    from sentence_transformers import SentenceTransformer
    model = SentenceTransformer(MODEL_NAME, cache_folder=str(CACHE_DIR))
    model.save(str(CACHE_DIR / "bge-base"))
    print(f"✅ Saved PyTorch model to {CACHE_DIR / 'bge-base'}")
    return model


def export_onnx(model_path: Path):
    """Export to ONNX and quantize to int8."""
    print("🔧 Exporting to ONNX...")
    try:
        from optimum.onnxruntime import ORTModelForFeatureExtraction, ORTQuantizer
        from optimum.onnxruntime.configuration import (
            AutoCalibrationConfig,
            AutoQuantizationConfig,
        )
        from transformers import AutoTokenizer
        from datasets import Dataset
    except ImportError as e:
        print(f"⚠️  optimum[onnxruntime] not installed: {e}")
        print("   Falling back to PyTorch model (slower but works)")
        return None

    onnx_path = CACHE_DIR / "bge-base-int8-onnx"
    if onnx_path.exists():
        print(f"✅ ONNX model already exists at {onnx_path}")
        return onnx_path

    # Export
    ort_model = ORTModelForFeatureExtraction.from_pretrained(
        str(model_path), export=True, provider="CPUExecutionProvider"
    )
    tokenizer = AutoTokenizer.from_pretrained(str(model_path))

    # Quantize to int8
    quantizer = ORTQuantizer.from_pretrained(ort_model)

    # Calibration with sample texts as a Dataset
    calibration_texts = [
        "deploy application to kubernetes",
        "monitoring alerting pagerduty",
        "database backup postgres",
        "build docker image",
        "configure ci cd pipeline",
        "set up prometheus grafana",
        "unit test python pytest",
        "terraform infrastructure as code",
    ]
    calibration_dataset = Dataset.from_dict({"text": calibration_texts})

    def preprocess(batch):
        return tokenizer(batch["text"], padding="max_length", truncation=True, max_length=128)

    calibration_dataset = calibration_dataset.map(preprocess, batched=True)
    # Remove raw text — ONNX model expects input_ids, attention_mask, not "text"
    calibration_dataset = calibration_dataset.remove_columns(["text"])
    # Set format to torch tensors for ONNX runtime
    calibration_dataset.set_format("torch", columns=["input_ids", "attention_mask", "token_type_ids"])
    calibration_config = AutoCalibrationConfig.minmax(calibration_dataset)

    # Step 1: Fit — compute calibration ranges
    calibration_tensors_range = quantizer.fit(
        dataset=calibration_dataset,
        calibration_config=calibration_config,
    )

    # Step 2: Quantize — apply int8 quantization
    qconfig = AutoQuantizationConfig.arm64(is_static=True, per_channel=True)
    quantizer.quantize(
        save_dir=str(onnx_path),
        quantization_config=qconfig,
        calibration_tensors_range=calibration_tensors_range,
    )
    print(f"✅ ONNX int8 model saved to {onnx_path}")
    return onnx_path


def main():
    parser = argparse.ArgumentParser(description="Download SCM embedding models")
    parser.add_argument("--cpu", action="store_true", help="PyTorch only (no ONNX)")
    args = parser.parse_args()

    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    print(f"📂 Model cache: {CACHE_DIR}")
    print(f"📦 Model: {MODEL_NAME}")

    model = download_pytorch()

    if not args.cpu:
        export_onnx(CACHE_DIR / "bge-base")

    print("\n✅ Setup complete!")
    print(f"   PyTorch model: {CACHE_DIR / 'bge-base'}")
    if not args.cpu:
        print(f"   ONNX int8:     {CACHE_DIR / 'bge-base-int8-onnx'}")
    print("\n   SCM will auto-detect and use the best available model.")


if __name__ == "__main__":
    main()
