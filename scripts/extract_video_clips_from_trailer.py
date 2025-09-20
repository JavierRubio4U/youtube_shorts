# scripts/extract_video_clips_from_trailer.py
import json
import os
import shutil
from pathlib import Path
import logging
import yt_dlp
import unicodedata
import re
import time
import subprocess
from moviepy import VideoFileClip
from PIL import Image
import imagehash
import numpy as np

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "output" / "state"
CLIPS_DIR = ROOT / "assets" / "video_clips"
CLIPS_DIR.mkdir(parents=True, exist_ok=True)

SEL_FILE = STATE / "next_release.json"
CLIP_INTERVAL = 5
CLIP_DURATION = 6
MAX_CLIPS = 4
SKIP_INITIAL_CLIPS = 2
HASH_SIMILARITY_THRESHOLD = 5

def slugify(text: str, maxlen: int = 60) -> str:
    s = (text or "").lower().strip()
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"[\s_-]+", "-", s).strip("-")
    return (s or "title")[:maxlen]

# CAMBIO: La función ahora guarda el tráiler en la carpeta de assets
# Reemplaza esta función en scripts/extract_video_clips_from_trailer.py

def download_trailer(url, tmdb_id, slug):
    """Descarga el tráiler directamente a la carpeta assets/video_clips."""
    trailer_filename_template = f"{tmdb_id}_{slug}_trailer.%(ext)s"
    trailer_path_template = CLIPS_DIR / trailer_filename_template
    
    ydl_opts = {
        'outtmpl': str(trailer_path_template),
        # CAMBIO: Selector de formato mejorado para forzar alta calidad y fusión
        'format': 'bestvideo[height>=1080]+bestaudio/bestvideo+bestaudio/best',
        'merge_output_format': 'mp4',
        'no_playlist': True,
        'quiet': True,
        'no_warnings': True,
        'logger': logging.getLogger('yt_dlp_silent') # Logger silencioso
    }

    # Para evitar que yt-dlp muestre logs en la consola
    logging.getLogger('yt_dlp_silent').disabled = True
    
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])
    
    # Busca el archivo descargado
    downloaded_files = list(CLIPS_DIR.glob(f"{tmdb_id}_{slug}_trailer.*"))
    if not downloaded_files:
        raise ValueError("No se creó ningún archivo de tráiler.")
        
    trailer_path = downloaded_files[0]
    if not trailer_path.exists() or trailer_path.stat().st_size == 0:
        raise ValueError(f"Archivo de tráiler creado pero vacío o inválido: {trailer_path}")
    
    # Informar resolución y tamaño
    try:
        with VideoFileClip(str(trailer_path)) as clip:
            width, height = clip.size
            logging.info(f"Tráiler guardado en: {trailer_path.relative_to(ROOT)}")
            logging.info(f"✅ Resolución del tráiler descargado: {width}x{height}, Tamaño: {trailer_path.stat().st_size / (1024*1024):.2f} MB")
    except Exception as e:
        logging.warning(f"No se pudo leer la resolución del tráiler: {e}")

    return trailer_path

# CAMBIO: Parámetros de ffmpeg ajustados para alta calidad
def extract_clips(trailer_path, tmpdir, num_clips=MAX_CLIPS, clip_dur=CLIP_DURATION, interval=CLIP_INTERVAL, skip_initial=SKIP_INITIAL_CLIPS * CLIP_DURATION):
    """Extrae clips del tráiler usando FFmpeg con alta calidad."""
    clip_paths = []
    for i in range(num_clips):
        start_time = skip_initial + i * interval
        out_path = tmpdir / f"clip_{i+1}.mp4"
        cmd = [
            'ffmpeg', '-y', 
            '-ss', str(start_time),   # Busca el tiempo de inicio (rápido)
            '-i', str(trailer_path),  # Archivo de entrada
            '-t', str(clip_dur),      # Duración del clip
            '-c:v', 'libx264',        # Códec de video H.264
            '-preset', 'slow',        # Prioriza la calidad sobre la velocidad de codificación
            '-crf', '18',             # Factor de calidad (18 es considerado visualmente sin pérdidas)
            '-an', str(out_path)      # Sin pista de audio
        ]
        try:
            # Usamos DEVNULL para no llenar la consola con la salida de ffmpeg
            subprocess.check_call(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if out_path.exists() and out_path.stat().st_size > 50000: # Chequeo de tamaño mínimo
                clip_paths.append(out_path)
            else:
                logging.warning(f"Clip {i+1} no generado o es demasiado pequeño.")
        except Exception as e:
            logging.error(f"Fallo al extraer clip {i+1} con FFmpeg: {e}")
    return clip_paths

def select_best_clips(clip_paths):
    """Selecciona los mejores clips basados en diversidad visual usando imagehash."""
    if not clip_paths:
        return []
    
    hashes = []
    selected_clips = []
    
    for path in clip_paths:
        try:
            with VideoFileClip(str(path)) as clip:
                frame = clip.get_frame(1) # Usar el segundo 1 para evitar transiciones negras
                img = Image.fromarray(frame)
                h = imagehash.average_hash(img)
                
                is_diverse = True
                for existing_hash in hashes:
                    if h - existing_hash < HASH_SIMILARITY_THRESHOLD:
                        is_diverse = False
                        break
                
                if is_diverse:
                    hashes.append(h)
                    selected_clips.append(path)
        except Exception as e:
            logging.warning(f"No se pudo procesar el clip para selección: {path.name}, error: {e}")

    logging.info(f"Clips seleccionados por diversidad: {len(selected_clips)} de {len(clip_paths)}")
    # Si se descartan demasiados, rellenar con los primeros que se tengan
    if len(selected_clips) < min(MAX_CLIPS, len(clip_paths)):
        logging.info("Pocos clips diversos, rellenando con los primeros extraídos.")
        needed = min(MAX_CLIPS, len(clip_paths)) - len(selected_clips)
        for p in clip_paths:
            if p not in selected_clips and needed > 0:
                selected_clips.append(p)
                needed -= 1

    return selected_clips[:MAX_CLIPS]

def save_clips(best_paths, tmdb_id, slug):
    """Guarda los clips seleccionados en la carpeta final."""
    saved_paths = []
    for i, path in enumerate(best_paths):
        dest_path = CLIPS_DIR / f"{tmdb_id}_{slug}_clip_{i+1}.mp4"
        shutil.move(str(path), str(dest_path))
        saved_paths.append(str(dest_path.relative_to(ROOT)))
    return saved_paths

def cleanup_temp_files(tmpdir):
    """Limpia el directorio temporal."""
    shutil.rmtree(tmpdir, ignore_errors=True)
    logging.info(f"Limpieza de clips temporales en {tmpdir} completada.")

def main():
    if not SEL_FILE.exists():
        logging.warning("Falta next_release.json. Omitiendo.")
        return

    sel = json.loads(SEL_FILE.read_text(encoding="utf-8"))
    tmdb_id = sel.get("tmdb_id", "unknown")
    slug = slugify(sel.get("titulo") or "title")
    trailer_url = sel.get("trailer_url")

    if not trailer_url:
        logging.warning("No hay tráiler disponible. Omitiendo extracción de clips.")
        return

    temp_root = ROOT / "temp"
    temp_root.mkdir(parents=True, exist_ok=True)
    tmpdir = temp_root / f"tmp_{tmdb_id}_clips" # Directorio temporal solo para clips
    if tmpdir.exists():
        shutil.rmtree(tmpdir, ignore_errors=True)
    tmpdir.mkdir(parents=True, exist_ok=True)

    try:
        logging.info(f"Descargando tráiler desde {trailer_url}...")
        # CAMBIO: La descarga ahora devuelve la ruta final y persistente del tráiler
        trailer_path = download_trailer(trailer_url, tmdb_id, slug)
        
        if not trailer_path or not trailer_path.exists():
            logging.error(f"Archivo de tráiler no encontrado después de la descarga.")
            return

        clip_paths_temp = extract_clips(trailer_path, tmpdir)
        logging.info(f"Clips extraídos temporalmente: {len(clip_paths_temp)}")

        best_paths = select_best_clips(clip_paths_temp)

        saved_paths = save_clips(best_paths, tmdb_id, slug)
        logging.info(f"Clips finales guardados: {saved_paths}")

        manifest_path = STATE / "assets_manifest.json"
        if manifest_path.exists():
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["video_clips"] = [p for p in saved_paths if Path(ROOT / p).exists()]
            # Guardamos también la ruta del tráiler en el manifiesto
            manifest["trailer_file"] = str(trailer_path.relative_to(ROOT))
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
            logging.info(f"Manifiesto actualizado con clips de alta calidad y ruta del tráiler: {manifest_path}")
        else:
            logging.warning("Manifiesto no encontrado. No se actualizaron los clips.")
    except Exception as e:
        logging.error(f"Error inesperado en el proceso de extracción: {e}")
    finally:
        # Limpiamos solo el directorio temporal de los clips, el tráiler ya está en su sitio
        cleanup_temp_files(tmpdir)

if __name__ == "__main__":
    main()