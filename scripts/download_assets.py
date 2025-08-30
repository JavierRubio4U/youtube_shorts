# scripts/download_assets.py
import json
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
NEXT_FILE = STATE_DIR / "next_release.json"

ASSETS_DIR = ROOT / "assets"
POSTERS_DIR = ASSETS_DIR / "posters"
BACKDROPS_DIR = ASSETS_DIR / "backdrops"
POSTERS_V_DIR = ASSETS_DIR / "posters_vertical"      # póster 9:16 recortado
BACKDROPS_V_DIR = ASSETS_DIR / "backdrops_vertical"  # backdrops cuadrado centrado en 9:16

for d in (POSTERS_DIR, BACKDROPS_DIR, POSTERS_V_DIR, BACKDROPS_V_DIR):
    d.mkdir(parents=True, exist_ok=True)

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

# ---------- helpers de imagen ----------
def crop_9_16(img: Image.Image, out_size=(1080, 1920)) -> Image.Image:
    """Recorte centrado a 9:16 (póster)."""
    target = 9/16
    w, h = img.size
    r = w / h
    if r > target:
        new_w = int(h * target); x1 = (w - new_w)//2; box = (x1, 0, x1 + new_w, h)
    else:
        new_h = int(w / target); y1 = (h - new_h)//2; box = (0, y1, w, y1 + new_h)
    return img.crop(box).resize(out_size, Image.LANCZOS)

def square_center_on_9_16(img: Image.Image,
                          out_size=(1080, 1920),
                          square_size=1080,
                          bg=(0,0,0)) -> Image.Image:
    """
    Hace un recorte/escala a CUADRADO (1080x1080) centrado y lo pega
    en un lienzo 1080x1920. Resultado: bandas arriba/abajo fijas (420px).
    """
    W, H = out_size
    # Escala para que la dimensión menor sea >= square_size (permitimos upscaling para homogeneidad)
    w, h = img.size
    scale = max(square_size / w, square_size / h)
    new_w, new_h = int(w * scale), int(h * scale)
    resized = img.resize((new_w, new_h), Image.LANCZOS)

    # Recorte cuadrado centrado 1080x1080
    x1 = max(0, (new_w - square_size)//2)
    y1 = max(0, (new_h - square_size)//2)
    square = resized.crop((x1, y1, x1 + square_size, y1 + square_size))

    # Pegar en lienzo 1080x1920
    canvas = Image.new("RGB", (W, H), bg)
    x = (W - square_size)//2
    y = (H - square_size)//2  # 420px con H=1920 y square=1080
    canvas.paste(square, (x, y))
    return canvas
# ---------------------------------------

def download_image(url: str, out_path: Path) -> Path | None:
    if not url:
        return None
    try:
        data = http_get(url)
        out_path = ensure_jpg(out_path)
        return save_binary(out_path, data)
    except Exception as e:
        print(f"[WARN] No se pudo descargar {url}: {e}")
        return None

def make_poster_vertical(src: Path, dst: Path) -> Path | None:
    try:
        with Image.open(src) as im:
            im = im.convert("RGB")
            v = crop_9_16(im, (1080, 1920))          # póster = recorte 9:16 full
            v.save(dst, "JPEG", quality=92)
            return dst
    except Exception as e:
        print(f"[WARN] Falló póster vertical {src.name}: {e}")
        return None

def make_backdrop_vertical(src: Path, dst: Path) -> Path | None:
    try:
        with Image.open(src) as im:
            im = im.convert("RGB")
            v = square_center_on_9_16(im, (1080, 1920), 1080, (0,0,0))  # backdrops = cuadrado centrado
            v.save(dst, "JPEG", quality=92)
            return dst
    except Exception as e:
        print(f"[WARN] Falló backdrop vertical {src.name}: {e}")
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
    backdrops = (sel.get("backdrops") or [])[:8]  # SIEMPRE 8
    trailer_url = sel.get("trailer_url")

    out = {
        "tmdb_id": tmdb_id,
        "titulo": title,
        "slug": slug,
        "trailer_url": trailer_url,
        "poster": None,
        "poster_vertical": None,
        "backdrops": [],
        "backdrops_vertical": []
    }

    # Póster
    if posters:
        p_url = posters[0]
        p_name = f"{tmdb_id}_{slug}_poster.jpg"
        p_path = POSTERS_DIR / p_name
        p_saved = download_image(p_url, p_path)
        if p_saved:
            out["poster"] = str(p_saved.relative_to(ROOT))
            pv_path = POSTERS_V_DIR / p_name.replace("_poster", "_poster_v")
            pv_saved = make_poster_vertical(p_saved, pv_path)
            if pv_saved:
                out["poster_vertical"] = str(pv_saved.relative_to(ROOT))

    # Backdrops (ahora en cuadrado centrado sobre 9:16)
    for i, b_url in enumerate(backdrops, 1):
        b_name = f"{tmdb_id}_{slug}_bd{i:02d}.jpg"
        b_path = BACKDROPS_DIR / b_name
        b_saved = download_image(b_url, b_path)
        if b_saved:
            out["backdrops"].append(str(b_saved.relative_to(ROOT)))
            bv_path = BACKDROPS_V_DIR / b_name.replace("_bd", "_bd_v")
            bv_saved = make_backdrop_vertical(b_saved, bv_path)
            if bv_saved:
                out["backdrops_vertical"].append(str(bv_saved.relative_to(ROOT)))

    MANIFEST_FILE.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST_FILE.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    print("✅ Descarga + vertical (cuadrado centrado) lista.")
    print("→ Manifest:", MANIFEST_FILE)
    print("→ Trailer:", trailer_url or "No disponible")

if __name__ == "__main__":
    main()

