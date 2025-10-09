# scripts/publish.py
from pathlib import Path
import sys
import logging
import subprocess
import json  # Para cargar manifiesto y sel
from datetime import datetime  # ← Añadido para tiempos explícitos

# --- Imports de tus módulos internos ---
import find 
import download_assets
import build_youtube_metadata
import build_short
import movie_utils
import upload_youtube
import cleanup_temp
from movie_utils import mark_published  # ← Import directo

# --- Logging con timestamps (solo uno, outer) ---
logging.basicConfig(
    level=logging.INFO, 
    format='%(asctime)s | %(levelname)s: %(message)s',
    datefmt='%H:%M:%S'  # Hora local simple
)

# --- Paths base ---
ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
STATE = ROOT / "output" / "state"

if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

def main():
    # Quita el basicConfig inner – ya está global

    start_time = datetime.now()  # ← Tiempo global start para total

    logging.info("▶ Actualizando yt-dlp...")
    try:
        subprocess.run([sys.executable, "-m", "pip", "install", "--upgrade", "yt-dlp"], check=True)
        logging.info("✅ yt-dlp actualizado con éxito.")
    except subprocess.CalledProcessError as e:
        logging.error(f"❌ Fallo al actualizar yt-dlp: {e}")

    # --- Paso 0: Limpieza ---
    step_start = datetime.now()
    logging.info("▶ Paso 0: Limpieza de la ejecución anterior...")
    cleanup_temp.cleanup_on_start()
    logging.info(f"Fin Paso 0: {datetime.now().strftime('%H:%M:%S')} (duración: {int((datetime.now() - step_start).total_seconds())}s)")

    max_attempts = 5
    attempts = 0
    video_published = False

    # --- ✅ Variable persistente para mantener la última selección válida ---
    last_sel = None

    while attempts < max_attempts and not video_published:
        attempts += 1
        logging.info(f"▶ Intento de publicación {attempts}/{max_attempts}...")

        sel = None

        # --- Paso 1: Seleccionar película ---
        step_start = datetime.now()
        logging.info("▶ Paso 1: seleccionar siguiente película…")
        try:
            sel = find.find_and_select_next()
            if sel:
                last_sel = sel.copy()  # ✅ Guardamos copia persistente
            else:
                logging.warning("🛑 No se seleccionó ninguna película nueva. Terminando intentos.")
                break
        except Exception as e:
            logging.error(f"Error en la selección de película: {e}")
            continue
        logging.info(f"Fin Paso 1: {datetime.now().strftime('%H:%M:%S')} (duración: {int((datetime.now() - step_start).total_seconds())}s)")

        # --- Paso 2: Descargar assets ---
        step_start = datetime.now()
        logging.info("▶ Paso 2: descargar assets…")
        try:
            download_assets.main()
        except Exception as e:
            logging.error(f"Fallo en descarga de assets: {e}")
            continue
        logging.info(f"Fin Paso 2: {datetime.now().strftime('%H:%M:%S')} (duración: {int((datetime.now() - step_start).total_seconds())}s)")

        # --- Paso 2.5: Extraer clips ---
        step_start = datetime.now()
        logging.info("▶ Paso 2.5: extraer clips del tráiler...")
        try:
            subprocess.run([sys.executable, str(SCRIPTS / "extract_video_clips_from_trailer.py")], check=True, cwd=ROOT)
        except subprocess.CalledProcessError as e:
            logging.error(f"Fallo en extracción de clips: {e}")
            continue
        logging.info(f"Fin Paso 2.5: {datetime.now().strftime('%H:%M:%S')} (duración: {int((datetime.now() - step_start).total_seconds())}s)")

        # --- Paso 3: Generar metadata ---
        step_start = datetime.now()
        logging.info("▶ Paso 3: generar metadata de YouTube…")
        try:
            build_youtube_metadata.main()
        except Exception as e:
            logging.error(f"Fallo en generación de metadatos: {e}")
            continue
        logging.info(f"Fin Paso 3: {datetime.now().strftime('%H:%M:%S')} (duración: {int((datetime.now() - step_start).total_seconds())}s)")

        # --- Paso 4: Generar video short ---
        step_start = datetime.now()
        logging.info("▶ Paso 4: generar video short (MP4)…")
        mp4_path = build_short.main()

        logging.info(f"Fin Paso 4: {datetime.now().strftime('%H:%M:%S')} (duración: {int((datetime.now() - step_start).total_seconds())}s)")

        video_id = None
        if mp4_path:
            # --- Paso 5: Subir a YouTube ---
            step_start = datetime.now()
            logging.info("▶ Paso 5: subir a YouTube…")
            try:
                video_id = upload_youtube.main(mp4_path)
            except Exception as e:
                logging.error(f"Fallo en la subida a YouTube: {e}")
            logging.info(f"Fin Paso 5: {datetime.now().strftime('%H:%M:%S')} (duración: {int((datetime.now() - step_start).total_seconds())}s)")
        else:
            logging.error("🛑 La creación del vídeo falló. Intentando con otro candidato.")
            logging.info("🔄 Intentando reutilizar MP4 desde manifiesto...")
            try:
                manifest = json.loads((STATE / "assets_manifest.json").read_text(encoding="utf-8"))
                mp4_path = ROOT / manifest.get("mp4_path", "")
                if mp4_path.exists():
                    logging.info(f"✅ Reusando MP4 desde manifiesto: {mp4_path}")
                    video_id = upload_youtube.main(str(mp4_path))
                else:
                    logging.error("No se encontró MP4 en manifiesto.")
            except Exception as e:
                logging.error(f"Error cargando MP4 de manifiesto: {e}")

        # --- Paso 5.5: Marcar como publicada ---
        step_start = datetime.now()
        if video_id:
            # ✅ Recuperamos última selección si sel se perdió
            if sel is None and last_sel is not None:
                sel = last_sel
            elif sel is None:
                logging.error("🛑 sel no disponible para marcar como publicada, y no hay copia previa.")
                continue

            tmdb_id_to_publish = sel.get("tmdb_id")
            trailer_url_to_publish = sel.get("trailer_url")
            title_to_publish = sel.get("titulo")

            try:
                movie_utils.mark_published(  # ← Ya lo tienes
                    tmdb_id_to_publish,
                    trailer_url_to_publish,
                    title_to_publish
                )
                logging.info(f"✅ Marcada como publicada: {title_to_publish} (ID: {tmdb_id_to_publish})")

                # 🔧 Forzamos flush por si hay buffers pendientes
                sys.stdout.flush()

            except Exception as e:
                logging.error(f"Error al marcar como publicada: {e}")

            video_published = True
            logging.info(f"✅ Publicado exitosamente. Video: https://studio.youtube.com/video/{video_id}/edit")
        else:
            logging.error("🛑 La subida falló. Intentando con otro candidato.")
        logging.info(f"Fin Paso 5.5: {datetime.now().strftime('%H:%M:%S')} (duración: {int((datetime.now() - step_start).total_seconds())}s)")

    # --- Limpieza final ---
    step_start = datetime.now()
    if video_published:
        logging.info("▶ Paso 6: Limpieza final...")
        cleanup_temp.cleanup_on_end()
        logging.info("✅ Proceso completado.")
    else:
        logging.info("ℹ No se publicó ningún vídeo tras varios intentos.")
    logging.info(f"Fin Paso 6: {datetime.now().strftime('%H:%M:%S')} (duración: {int((datetime.now() - step_start).total_seconds())}s)")
    total_time = int((datetime.now() - start_time).total_seconds())
    logging.info(f"🎉 Total run: {total_time}s")

if __name__ == "__main__":
    main()