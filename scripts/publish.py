# scripts/publish.py
from pathlib import Path
import sys
import logging
import subprocess
import json
from datetime import datetime

# --- Imports ---
import find 
import download_assets
import build_youtube_metadata
import build_short
import movie_utils
import upload_youtube
import cleanup_temp

logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(levelname)s: %(message)s', datefmt='%H:%M:%S')

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
STATE = ROOT / "output" / "state"

if str(SCRIPTS) not in sys.path: sys.path.insert(0, str(SCRIPTS))

def main():
    start_time = datetime.now()
    logging.info("▶ Actualizando yt-dlp...")
    try:
        subprocess.run([sys.executable, "-m", "pip", "install", "--upgrade", "yt-dlp"], 
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) # Silenciado
    except: pass

    # --- Paso 0: Limpieza ---
    cleanup_temp.cleanup_on_start()
    
    max_attempts = 5
    attempts = 0
    video_published = False
    last_sel = None

    while attempts < max_attempts and not video_published:
        attempts += 1
        logging.info(f"\n🎬 === INTENTO {attempts}/{max_attempts} ===")

        sel = None

        # --- Paso 1: Seleccionar ---
        try:
            sel = find.find_and_select_next() # Find ya tiene sus logs limpios
            if sel: last_sel = sel.copy()
            else: break
        except Exception as e:
            logging.error(f"Error selección: {e}")
            continue

        # --- Paso 2: Descargar Assets ---
        logging.info("▶ Paso 2: Descargando assets (silencioso)...")
        try:
            # Redirigimos logs internos de download_assets si es posible, o confiamos en que ya no son ruidosos
            download_assets.main() 
        except Exception as e:
            logging.error(f"Fallo descarga: {e}")
            continue

        # --- Paso 2.5: Extract Clips ---
        logging.info("▶ Paso 2.5: Extrayendo clips...")
        try:
            subprocess.run([sys.executable, str(SCRIPTS / "extract_video_clips_from_trailer.py")], 
                           check=True, cwd=ROOT, stdout=subprocess.DEVNULL)
        except: continue

        # --- Paso 3: Metadata ---
        logging.info("▶ Paso 3: Generando metadata...")
        build_youtube_metadata.main()

        # --- Paso 4: Build Short ---
        logging.info("▶ Paso 4: Generando VIDEO SHORT...")
        mp4_path = build_short.main() # build_short invoca la nueva narración

        video_id = None
        if mp4_path:
            # --- Paso 5: Subir ---
            logging.info(f"▶ Paso 5: Subiendo a YouTube ({Path(mp4_path).name})...")
            try:
                video_id = upload_youtube.main(mp4_path)
            except Exception as e: logging.error(f"Fallo subida: {e}")
        else:
            logging.error("🛑 Fallo al crear MP4.")

        # --- Paso 5.5: Marcar ---
        if video_id:
            if sel is None and last_sel: sel = last_sel
            try:
                movie_utils.mark_published(sel.get("tmdb_id"), sel.get("trailer_url"), sel.get("titulo"))
                video_published = True
                logging.info(f"✅ VIDEO PUBLICADO: https://studio.youtube.com/video/{video_id}/edit")
            except: pass
        else:
            logging.warning("⚠️ Intento fallido. Probando siguiente película...")

    # --- Final ---
    if video_published:
        cleanup_temp.cleanup_on_end()
        logging.info(f"🎉 Proceso completado en {(datetime.now() - start_time).seconds // 60} min.")
        
        # Resumen limpio
        if last_sel:
            logging.info("\n" + "="*30)
            logging.info(f"📼 {last_sel.get('titulo')}")
            logging.info(f"🔗 {last_sel.get('trailer_url')}")
            logging.info("="*30)
    else:
        logging.error("🚫 NO SE PUBLICÓ NADA.")

if __name__ == "__main__":
    main()