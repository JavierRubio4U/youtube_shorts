# scripts/download_assets.py
import json, logging
import re
from pathlib import Path
import requests

# Requiere Pillow
try:
    from PIL import Image
except Exception:
    raise SystemExit("[ERROR] Este script requiere Pillow. Instala: pip install Pillow")

ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = ROOT / "output" / "state"
TMP_DIR = ROOT / "assets" / "tmp"
TMP_DIR.mkdir(parents=True, exist_ok=True)
NEXT_FILE = TMP_DIR / "next_release.json"

ASSETS_DIR = ROOT / "assets"
POSTERS_DIR = ASSETS_DIR / "posters"

POSTERS_DIR.mkdir(parents=True, exist_ok=True)

MANIFEST_FILE = STATE_DIR / "assets_manifest.json"

def slugify(text: str, maxlen: int = 60) -> str:
    text = text.strip().lower()
    text = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE)
    text = re.sub(r"[\s_-]+", "-", text)
    text = re.sub(r"^-+|-+$", "", text)
    return text[:maxlen] or "title"

def http_get(url: str, timeout=30) -> bytes:
    r = requests.get(url, timeout=timeout)
    r.raise_for_status()
    return r.content

def save_binary(path: Path, content: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        f.write(content)
    return path

def ensure_jpg(path: Path) -> Path:
    return path if path.suffix.lower() in {".jpg", ".jpeg"} else path.with_suffix(".jpg")

def download_image(url: str, out_path: Path) -> Path | None:
    if not url:
        return None
    try:
        data = http_get(url)
        # Nueva modificación: Verificar si la imagen descargada tiene contenido válido (no placeholder vacío)
        if len(data) == 0:
            logging.warning(f"Imagen descargada de {url} está vacía (0 bytes). Omitiendo.")
            return None
        return save_binary(out_path, data)
    except Exception as e:
        logging.error(f"Fallo al descargar imagen de {url}: {e}")
        return None

def main():
    if not NEXT_FILE.exists():
        raise SystemExit(f"[ERROR] No existe {NEXT_FILE}. Ejecuta antes select_next_release.py")

    sel = json.loads(NEXT_FILE.read_text(encoding="utf-8"))
    tmdb_id = sel["tmdb_id"]
    title = sel["titulo"]
    slug = slugify(title)

    poster_main = sel.get("poster_principal")
    posters = sel.get("posters", []) or ([poster_main] if poster_main else [])

    out = {
        "tmdb_id": tmdb_id,
        "titulo": title,
        "slug": slug,
        "trailer_url": sel.get("trailer_url"),
        "poster": None,
        "video_clips": []  # Placeholder para clips (se llenará después en extract_video_clips)
    }

    # Póster
    if posters:
        p_url = posters[0]
        # Nueva modificación: Preferir la versión de mayor resolución disponible en TMDB (original en vez de w500 si aplica)
        p_url = p_url.replace("/w500/", "/original/") if "/w500/" in p_url else p_url  # Asumiendo URLs estándar de TMDB
        p_name = f"{tmdb_id}_poster.jpg"
        p_path = POSTERS_DIR / p_name
        p_saved = download_image(p_url, p_path)
        if p_saved:
            logging.info(f"Póster descargado en: {p_path}")
            out["poster"] = str(p_saved.relative_to(ROOT))
        else:
            logging.error(f"Fallo al descargar póster para tmdb_id {tmdb_id} desde {p_url}")

    MANIFEST_FILE.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST_FILE.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    print("✅ Descarga del póster lista.")
    print("→ Manifest:", MANIFEST_FILE)
    print("→ Trailer:", sel.get("trailer_url") or "No disponible")

if __name__ == "__main__":
    main()