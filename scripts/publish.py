# scripts/publish.py
from pathlib import Path
import sys
import json
import logging
import os
import unicodedata
import tempfile
from moviepy.editor import VideoFileClip, concatenate_videoclips, ImageClip, CompositeVideoClip, AudioFileClip
from PIL import Image, ImageDraw, ImageFont
import numpy as np
from overlay import make_overlay_image
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

STATE = ROOT / "output" / "state"

def main():  
    # Paso 1: Seleccionar siguiente película (simulado)
    print("▶ Paso 1: seleccionar siguiente película…")
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
        print("🛑 No se seleccionó película. Proceso detenido.")
        return

    # Paso 2: Descargar assets (simulado)
    print("▶ Paso 2: descargar assets (vertical/letterbox, 8 backdrops)…")
    download_assets.main()

    # Paso 2.5: Extraer clips del tráiler
    print("▶ Paso 2.5: extraer clips del tráiler...")
    extract_video_clips_from_trailer.main()

    # Paso 3: Generar metadata de YouTube
    print("▶ Paso 3: generar metadata de YouTube…")
    build_youtube_metadata.main()

    # Paso 4: Generar video short (MP4)
    print("▶ Paso 4: generar video short (MP4)…")
    try:
        mp4_path = build_short.main()
    except Exception as e:  # Cambia SystemExit a Exception para capturar todo
        logging.error(f"Error al generar el video: {e}", exc_info=True)
        mp4_path = None

    if mp4_path and os.path.exists(mp4_path):
        meta = json.loads((STATE / "youtube_metadata.json").read_text(encoding="utf-8"))
        select_next_release.mark_published(int(meta["tmdb_id"]), simulate=True)
        print("✅ Video generado. Publicación simulada (espera 24h para subir a YouTube).")
    else:
        print("⚠️ Video generado con problemas o no completado. Revisa el log para detalles.")

    # # Paso 5: Subir a YouTube (descomentar cuando se levante la restricción)
    # if mp4_path and os.path.exists(mp4_path):
    #     print("▶ Paso 5: subir a YouTube…")
    #     video_id = upload_youtube.main(mp4_path)
    #     if video_id:
    #         meta = json.loads((STATE / "youtube_metadata.json").read_text(encoding="utf-8"))
    #         select_next_release.mark_published(int(meta["tmdb_id"]))
    #         print("✅ Publicado y marcado. Video:", f"https://studio.youtube.com/video/{video_id}/edit")
    #     else:
    #         print("🛑 La subida falló o se omitió. No se marcó como publicado.")
    # else:
    #     print("🛑 No se puede subir: video no generado o inválido.")

    # Limpieza final (solo si todo salió bien)
    if mp4_path and os.path.exists(mp4_path):
        cleanup_temp.main()

if __name__ == "__main__":
    main()