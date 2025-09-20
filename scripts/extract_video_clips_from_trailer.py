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
# Correcto
from moviepy import VideoFileClip
from PIL import Image
import imagehash
import numpy as np

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "output" / "state"
CLIPS_DIR = ROOT / "assets" / "video_clips"  # Carpeta final para clips validados
CLIPS_DIR.mkdir(parents=True, exist_ok=True)

SEL_FILE = STATE / "next_release.json"
CLIP_INTERVAL = 5
CLIP_DURATION = 6
MAX_CLIPS = 4
SKIP_INITIAL_CLIPS = 2
HASH_SIMILARITY_THRESHOLD = 5  # Umbral para diversidad

def slugify(text: str, maxlen: int = 60) -> str:
    """Convierte texto en un slug seguro para nombres de archivo."""
    s = (text or "").lower().strip()
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"[\s_-]+", "-", s).strip("-")
    return (s or "title")[:maxlen]

def download_trailer(url, output_dir):
    ydl_opts = {
        'outtmpl': os.path.join(output_dir, 'trailer.%(ext)s'),
        'format': 'best[height>=2160]/best[height>=1080]/best[height>=720]/best',  # Prioriza 4K > 1080p
        'merge_output_format': 'mp4',
        'no_playlist': True,
        'quiet': True
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])
    
    # Chequea si se creó un archivo válido
    files = [f for f in os.listdir(output_dir) if f.startswith('trailer.')]
    if not files:
        raise ValueError("No se creó ningún archivo de tráiler. Posible fallo en el formato o acceso al video.")
    trailer_file = files[0]  # Toma el primero
    trailer_full = os.path.join(output_dir, trailer_file)
    if not os.path.exists(trailer_full) or os.path.getsize(trailer_full) == 0:
        raise ValueValueError(f"Archivo de tráiler creado pero vacío o inválido: {trailer_full}")
    
    logging.info(f"Tráiler descargado exitosamente: {trailer_file} (tamaño: {os.path.getsize(trailer_full)} bytes)")
    return trailer_file

def extract_clips(trailer_path, tmpdir, num_clips=MAX_CLIPS, clip_dur=CLIP_DURATION, interval=CLIP_INTERVAL, skip_initial=SKIP_INITIAL_CLIPS * CLIP_DURATION):
    """Extrae clips del tráiler usando FFmpeg para eficiencia."""
    clip_paths = []
    for i in range(num_clips):
        start_time = skip_initial + i * interval
        out_path = tmpdir / f"clip_{i+1}.mp4"
        cmd = [
            'ffmpeg', '-y', '-ss', str(start_time), '-i', str(trailer_path),
            '-t', str(clip_dur), '-c:v', 'libx264', '-preset', 'fast',
            '-crf', '23', '-an', str(out_path)  # Sin audio para simplicidad
        ]
        try:
            subprocess.check_call(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if out_path.exists() and out_path.stat().st_size > 0:
                clip_paths.append(out_path)
            else:
                logging.warning(f"Clip {i+1} no generado o vacío.")
        except Exception as e:
            logging.error(f"Fallo al extraer clip {i+1}: {e}")
    return clip_paths

def select_best_clips(clip_paths):
    """Selecciona los mejores clips basados en diversidad visual usando imagehash."""
    if not clip_paths:
        return []
    
    hashes = []
    for path in clip_paths:
        clip = VideoFileClip(str(path))
        frame = clip.get_frame(0)  # Primer frame como representante
        clip.close()
        img = Image.fromarray(frame)
        hashes.append(imagehash.average_hash(img))
    
    selected = [clip_paths[0]]
    for i in range(1, len(clip_paths)):
        similar = False
        for h in hashes[:len(selected)]:
            if hashes[i] - h < HASH_SIMILARITY_THRESHOLD:
                similar = True
                break
        if not similar:
            selected.append(clip_paths[i])
    
    logging.info(f"Clips seleccionados por diversidad: {len(selected)}")
    return selected

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
    logging.info(f"Limpieza de {tmpdir} completada.")

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
    tmpdir = temp_root / f"tmp_{tmdb_id}"
    if tmpdir.exists():
        shutil.rmtree(tmpdir, ignore_errors=True)
        logging.info(f"Carpeta temporal {tmpdir} eliminada antes de la ejecución.")
    tmpdir.mkdir(parents=True, exist_ok=True)

    try:
        logging.info(f"Descargando tráiler desde {trailer_url}...")
        trailer_file = download_trailer(trailer_url, tmpdir)
        trailer_path = os.path.join(tmpdir, trailer_file)

        if not os.path.exists(trailer_path):
            logging.error(f"Archivo de tráiler no encontrado después de download: {trailer_path}")
            return

        tamaño_mb = os.path.getsize(trailer_path) / (1024 * 1024)
        logging.info(f"Tráiler descargado: {tamaño_mb:.1f} MB en alta resolución (máx. via yt-dlp). Procediendo.")
        logging.info("✅ Tráiler confirmado sin chequeo de resolución (modo robusto).")

        clip_paths = extract_clips(trailer_path, tmpdir)
        logging.info(f"Clips extraídos: {len(clip_paths)}")

        best_paths = select_best_clips(clip_paths)

        saved_paths = save_clips(best_paths, tmdb_id, slug)
        logging.info(f"Clips extraídos y guardados: {saved_paths}")

        manifest_path = STATE / "assets_manifest.json"
        if manifest_path.exists():
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["video_clips"] = [p for p in saved_paths if Path(ROOT / p).exists()]
            manifest["trailer_resolution"] = "alta (máx. via yt-dlp)"  # Placeholder
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
            logging.info(f"Manifiesto actualizado con clips y resolución: {manifest_path}")
        else:
            logging.warning("Manifiesto no encontrado. No se actualizaron los clips.")
    except Exception as e:
        logging.error(f"Error inesperado en el proceso: {e}")
    finally:
        cleanup_temp_files(tmpdir)

if __name__ == "__main__":
    main()