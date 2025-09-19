# scripts/separate_narration.py
from pathlib import Path
import logging
import json
from ai_narration import generate_narration
from slugify import slugify
import tempfile
import shutil

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "output" / "state"
NARRATION_DIR = ROOT / "assets" / "narration"
NARRATION_DIR.mkdir(parents=True, exist_ok=True)
SEL_FILE = STATE / "next_release.json"

def main():
    if not SEL_FILE.exists():
        logging.error("Falta next_release.json.")
        return None, None

    sel = json.loads(SEL_FILE.read_text(encoding="utf-8"))
    tmdb_id = sel.get("tmdb_id", "unknown")
    title = sel.get("titulo") or sel.get("title") or ""
    slug = slugify(title)

    # Chequear si ya existe narración
    voice_path = NARRATION_DIR / f"{tmdb_id}_{slug}_narracion.wav"
    if voice_path.exists():
        logging.info(f"Narración ya existe: {voice_path}. Reusando para ahorrar recursos.")
        return "Narración reutilizada (de archivo existente)", voice_path

    # Generar nueva narración si no existe
    logging.info("Generando nueva narración...")
    # Crear un directorio temporal para la generación de audio
    tmpdir = Path(tempfile.mkdtemp(prefix=f"narration_{tmdb_id}_"))
    try:
        # Pasar el directorio temporal a la función
        narracion, new_voice_path = generate_narration(sel, tmdb_id, slug, tmpdir)
        if new_voice_path:
            # Salvar para futuro uso
            saved_path = NARRATION_DIR / f"{tmdb_id}_{slug}_narracion.wav"
            shutil.copy(new_voice_path, saved_path)
            logging.info(f"Narración generada y salvada en: {saved_path}")
            return narracion, saved_path
        else:
            logging.error("Fallo al generar narración.")
            return None, None
    finally:
        # Limpiar siempre el directorio temporal
        shutil.rmtree(tmpdir, ignore_errors=True)

if __name__ == "__main__":
    main()