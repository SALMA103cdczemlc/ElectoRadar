import argparse, shutil, zipfile
from pathlib import Path
ROOT = Path(__file__).parent
DIST = ROOT / "dist"

def zip_dir(src, dest):
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(src.rglob("*")):
            if f.is_file() and "__pycache__" not in f.parts and f.name != "local.settings.json":
                z.write(f, f.relative_to(src))
    print(f"  {dest.relative_to(ROOT)} ({dest.stat().st_size // 1024} Ko)")

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--vm-api-key", default="REMPLACER-PAR-UNE-CLE-SECRETE")
    a = ap.parse_args()
    shutil.rmtree(DIST, ignore_errors=True); DIST.mkdir()
    d = ROOT / "azure/vm-modele"
    s = (d / "custom-data.template.sh").read_text()
    s = (s.replace("__API_KEY__", a.vm_api_key).replace("__SERVER_PY__", (d / "server.py").read_text().rstrip())
          .replace("__TRAIN_PY__", (d / "train.py").read_text().rstrip())
          .replace("__REQUIREMENTS__", (d / "requirements.txt").read_text().rstrip())
          .replace("__TRAIN_CSV__", (ROOT / "data/entrainement.csv").read_text(encoding="utf-8").rstrip()))
    (DIST / "vm-custom-data.sh").write_text(s, encoding="utf-8")
    print(f"  dist/vm-custom-data.sh ({len(s) // 1024} Ko)")
    zip_dir(ROOT / "azure/fonctions", DIST / "azure-fonctions.zip")
    zip_dir(ROOT / "azure/api", DIST / "azure-api.zip")

if __name__ == "__main__":
    main()
