from pathlib import Path

# Repo root resolved from this file so asset lookups never depend on the
# process working directory.
REPO_ROOT = Path(__file__).resolve().parents[3]
ASSETS_DIR = REPO_ROOT / "assets"
