# scripts/publish.py
from pathlib import Path
import sys
import json
import logging
import os
import unicodedata
import re  # Para slugify
#from moviepy import VideoFileClip, concatenate_videoclips, ImageClip, CompositeVideoClip, AudioFileClip  # Imports actualizados para v2.2.1
from moviepy.editor import (VideoFileClip, ImageClip, AudioFileClip, AudioClip,CompositeVideoClip, CompositeAudioClip,concatenate_videoclips, concatenate_audioclips, afx)
from PIL import Image, ImageDraw, ImageFont
import numpy as np
# from overlay import make_overlay_image
from ai_narration import generate_narration

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
import select_next_release
import download_assets
import build_youtube_metadata
import build_short
import extract_video_clips_from_trailer
import cleanup_temp
import upload_youtube
import subprocess
from slugify import slugify

STATE = ROOT / "output" / "state"

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

def slugify(text: str, maxlen: int = 50) -> str:
    """Slug simple para nombres de clips."""
    s = (text or "").lower().strip()
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"[\s_-]+", "-", s).strip("-")
    return s[:maxlen]

def main():
    # Paso 1: Seleccionar siguiente película
    logging.info("▶ Paso 1: seleccionar siguiente película…")
    sel = select_next_release.pick_next()
    if sel:
        tmdb_id = str(sel.get("tmdb_id"))
        clips_dir = ROOT / "assets" / "video_clips"
        for file in clips_dir.iterdir():
            if file.is_file() and not file.name.startswith(tmdb_id):
                try:
                    file.unlink()
                    logging.info(f"Clip viejo eliminado: {file.name}")
                except Exception as e:
                    logging.warning(f"No se pudo eliminar {file.name}: {e}")
    else:
        logging.error("🛑 No se seleccionó película. Proceso detenido.")
        return

    # Paso 2: Descargar assets
    logging.info("▶ Paso 2: descargar assets (vertical/letterbox, 8 backdrops)…")
    download_assets.main()  # Usa el módulo original

    # Paso 2.5: Extraer clips del tráiler
    logging.info("▶ Paso 2.5: extraer clips del tráiler (con logs verbose)...")
    result = subprocess.run(["python", str(ROOT / "scripts" / "extract_video_clips_from_trailer.py")], 
                            check=True, cwd=ROOT, capture_output=False, text=True)
    print("STDOUT de extracción:", result.stdout)
    if result.stderr:
        print("STDERR de extracción:", result.stderr)

    # Chequeo de clips (de manifiesto)
    manifest_path = STATE / "assets_manifest.json"
    clips = []
    if manifest_path.exists():
        with open(manifest_path, 'r', encoding='utf-8') as f:
            manifest = json.load(f)
        clips = manifest.get("video_clips", [])
        print("Clips en manifiesto:", clips)  # Log extra
    if not clips:
        logging.warning("Sin clips válidos. Continuando sin borrar next_release.json para permitir retries.")
        # No borrar: Dejamos el archivo para depuración o reintento
        return  # Salta si no clips, pero sin borrar
    
    logging.info("✅ Paso 2.5 completado. Revisa logs arriba para detalles.")

    # Paso 3: Generar metadata de YouTube
    logging.info("▶ Paso 3: generar metadata de YouTube…")
    build_youtube_metadata.main()  # Usa el módulo original

    # Paso 4: Generar video short (MP4)
    logging.info("▶ Paso 4: generar video short (MP4)…")
    try:
        mp4_path = build_short.main()  # Integra clips del manifiesto
    except Exception as e:  # Captura todo, como original
        logging.error(f"Error al generar el video: {e}", exc_info=True)
        mp4_path = None
    if mp4_path and os.path.exists(mp4_path):
        meta = json.loads((STATE / "youtube_metadata.json").read_text(encoding="utf-8"))
        select_next_release.mark_published(int(meta["tmdb_id"]), simulate=True)
        logging.info("✅ Video generado. Publicación simulada (espera 24h para subir a YouTube).")
    else:
        logging.error("⚠️ Video generado con problemas o no completado. Revisa el log para detalles.")
        return  # Salta upload si no MP4

    # Paso 5: Subir a YouTube
    if mp4_path and os.path.exists(mp4_path):
        logging.info("▶ Paso 5: subir a YouTube…")
        video_id = upload_youtube.main(mp4_path)  # Sube y retorna ID (usa youtube_token.json en STATE)
        if video_id:
            meta = json.loads((STATE / "youtube_metadata.json").read_text(encoding="utf-8"))
            select_next_release.mark_published(int(meta["tmdb_id"]))  # Marca real si éxito
            logging.info(f"✅ Publicado y marcado. Video: https://studio.youtube.com/video/{video_id}/edit")
        else:
            logging.error("🛑 La subida falló o se omitió. No se marcó como publicado.")
    else:
        logging.error("🛑 No se puede subir: video no generado o inválido.")

    # Limpieza final (solo si todo salió bien)
    if mp4_path and os.path.exists(mp4_path) and video_id:
        cleanup_temp.main()

if __name__ == "__main__":
    main()