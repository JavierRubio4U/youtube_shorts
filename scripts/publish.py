# scripts/publish.py
from pathlib import Path
import sys
import json
import logging
import os

# Añadir la carpeta de scripts al path
ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

# Módulos del proyecto
import select_next_release
import download_assets
import build_youtube_metadata
import build_short
import upload_youtube
import cleanup_temp
import subprocess

STATE = ROOT / "output" / "state"

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

def main():
    # Paso 0: Limpieza de la ejecución anterior y archivado de shorts anteriores
    logging.info("▶ Paso 0: Limpieza de la ejecución anterior y archivado de shorts anteriores")
    cleanup_temp.cleanup_on_start()

    # Paso 1: Seleccionar siguiente película
    logging.info("▶ Paso 1: seleccionar siguiente película…")
    sel = select_next_release.pick_next()
    if not sel:
        logging.info("🛑 No se seleccionó una nueva película. Proceso detenido.")
        return

    # Paso 2: Descargar assets
    logging.info("▶ Paso 2: descargar assets (vertical/letterbox, 8 backdrops)…")
    download_assets.main()

    # Paso 2.5: Extraer clips del tráiler
    logging.info("▶ Paso 2.5: extraer clips del tráiler (con logs verbose)...")
    result = subprocess.run(["python", str(ROOT / "scripts" / "extract_video_clips_from_trailer.py")], 
                            check=True, cwd=ROOT, capture_output=False, text=True)
    print("STDOUT de extracción:", result.stdout)
    if result.stderr:
        print("STDERR de extracción:", result.stderr)

    # Paso 3: generar metadata de YouTube…
    logging.info("▶ Paso 3: generar metadata de YouTube…")
    build_youtube_metadata.main()

    # Paso 4: generar video short (MP4)…
    logging.info("▶ Paso 4: generar video short (MP4)…")
    mp4_path = build_short.main()

    video_id = None # Inicializamos video_id para la limpieza

    # Paso 5: subir a YouTube…
    if mp4_path:
        logging.info("▶ Paso 5: subir a YouTube…")
        video_id = upload_youtube.main(mp4_path)
    else:
        logging.error("🛑 La creación del vídeo falló o se omitió. No se subirá a YouTube.")

    if video_id:
        # Marca como publicado
        meta = json.loads((STATE / "youtube_metadata.json").read_text(encoding="utf-8"))
        select_next_release.mark_published(int(meta["tmdb_id"]))

        logging.info(f"✅ Publicado y marcado. Video: https://studio.youtube.com/video/{video_id}/edit")
    else:
        logging.error("🛑 La subida falló o se omitió. No se marcará como publicado.")

    # Paso 6: Limpieza final (solo si todo salió bien)
    if mp4_path and video_id:
        logging.info("▶ Paso 6: Limpieza final (solo si todo salió bien)")
        cleanup_temp.cleanup_on_end()
        logging.info("✅ Proceso completado. Archivos temporales eliminados.")
    else:
        logging.info("ℹ No se realizará limpieza final por fallo en la subida o la creación del vídeo.")


if __name__ == "__main__":
    main()