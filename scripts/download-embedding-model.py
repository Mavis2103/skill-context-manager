"""Download embedding model for SCM.

Usage:
    python3 scripts/download-embedding-model.py
"""

from pathlib import Path

CACHE_DIR = Path.home() / ".scm" / "models"
MODEL_NAME = "all-MiniLM-L6-v2"


def main():
    print(f"📥 Downloading {MODEL_NAME}...")
    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer(MODEL_NAME, cache_folder=str(CACHE_DIR))
    model.save(str(CACHE_DIR / "all-MiniLM-L6-v2"))
    print(f"✅ Saved model to {CACHE_DIR / 'all-MiniLM-L6-v2'}")


if __name__ == "__main__":
    main()
