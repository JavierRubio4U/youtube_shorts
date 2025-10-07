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
import find 
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
    logging.info("▶ Actualizando yt-dlp...")
    try:
        subprocess.run([sys.executable, "-m", "pip", "install", "--upgrade", "yt-dlp"], check=True)
        logging.info("✅ yt-dlp actualizado con éxito.")
    except subprocess.CalledProcessError as e:
        logging.error(f"❌ Fallo al actualizar yt-dlp: {e}")
        # Puedes decidir si quieres continuar o salir aquí.


    logging.info("▶ Paso 0: Limpieza de la ejecución anterior y archivado de shorts anteriores")
    cleanup_temp.cleanup_on_start()

    max_attempts = 5  # Número de intentos para procesar películas
    attempts = 0
    video_published = False

    while attempts < max_attempts and not video_published:
        attempts += 1
        logging.info(f"▶ Intento de publicación {attempts}/{max_attempts}...")

        # Paso 1: Seleccionar siguiente película
        logging.info("▶ Paso 1: seleccionar siguiente película…")
        try:
            sel = find.find_and_select_next()
            if not sel:
                logging.info("🛑 No se seleccionó una nueva película. Proceso detenido.")
                break
        except Exception as e:
            logging.error(f"Error en la selección de película: {e}")
            continue

        # Paso 2: Descargar assets
        logging.info("▶ Paso 2: descargar assets (vertical/letterbox, 8 backdrops)…")
        try:
            download_assets.main()
        except Exception as e:
            logging.error(f"Fallo en la descarga de assets: {e}")
            continue

        # Paso 2.5: Extraer clips del tráiler
        logging.info("▶ Paso 2.5: extraer clips del tráiler (con logs verbose)...")
        try:
            # Capturamos la salida para saber si hay un error específico
            result = subprocess.run([sys.executable, str(ROOT / "scripts" / "extract_video_clips_from_trailer.py")],
                                    check=True, cwd=ROOT, capture_output=True, text=True)
            print("STDOUT de extracción:", result.stdout)
            if result.stderr:
                print("STDERR de extracción:", result.stderr)
        except subprocess.CalledProcessError as e:
            logging.error(f"Fallo en la extracción de clips: {e.stderr}")
            continue  # Si la extracción falla, el bucle pasa al siguiente candidato

        # Paso 3: generar metadata de YouTube…
        logging.info("▶ Paso 3: generar metadata de YouTube…")
        try:
            build_youtube_metadata.main()
        except Exception as e:
            logging.error(f"Fallo en la generación de metadatos: {e}")
            continue

        # Paso 4: generar video short (MP4)…
        logging.info("▶ Paso 4: generar video short (MP4)…")
        mp4_path = build_short.main()

        video_id = None # Inicializamos video_id para la limpieza

        # Paso 5: subir a YouTube…
        if mp4_path:
            logging.info("▶ Paso 5: subir a YouTube…")
            try:
                video_id = upload_youtube.main(mp4_path)
            except Exception as e:
                logging.error(f"Fallo en la subida a YouTube: {e}")
        else:
            logging.error("🛑 La creación del vídeo falló o se omitió. No se subirá a YouTube.")
            continue

        if video_id:
            # Ahora usa los datos de 'sel' que ya tenemos, es más seguro.
            tmdb_id_to_publish = sel["tmdb_id"]
            trailer_url_to_publish = sel["trailer_url"]
            select_next_release.mark_published(tmdb_id_to_publish, trailer_url_to_publish)
            
        else:
            logging.error("🛑 La subida falló o se omitió. No se marcará como publicado.")
            # Continuar en el bucle para intentar con el siguiente candidato

    # Paso 6: Limpieza final (solo si todo salió bien)
    if video_published:
        logging.info("▶ Paso 6: Limpieza final (solo si todo salió bien)")
        cleanup_temp.cleanup_on_end()
        logging.info("✅ Proceso completado. Archivos temporales eliminados.")
    else:
        logging.info("ℹ No se realizó limpieza final por fallo en la subida o la creación del vídeo.")

if __name__ == "__main__":
    main()