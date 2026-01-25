import sys
import io
import logging

# --- FIX: FORZAR UTF-8 EN WINDOWS ---
if sys.stdout.encoding != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
if sys.stderr.encoding != 'utf-8':
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

import json
import os
import shutil
from pathlib import Path
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

TMP_DIR = ROOT / "assets" / "tmp"
TMP_DIR.mkdir(parents=True, exist_ok=True)
SEL_FILE = TMP_DIR / "next_release.json"
CLIP_INTERVAL = 5
CLIP_DURATION = 6
MAX_CLIPS = 4
SKIP_INITIAL_CLIPS = 3 # Saltamos los primeros 18 segundos aprox (logos/intro)
SKIP_FINAL_CLIPS = 2  # Saltamos los últimos 12 segundos aprox (créditos/fechas)

HASH_SIMILARITY_THRESHOLD = 5

def slugify(text: str, maxlen: int = 60) -> str:
    s = (text or "").lower().strip()
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"[\s_-]+", "-", s).strip("-")
    return (s or "title")[:maxlen]

def get_video_fps(video_path):
    """Obtiene los FPS de un video usando ffprobe (más rápido y estable que MoviePy para trailers 4K)."""
    try:
        cmd = [
            'ffprobe', '-v', 'error', 
            '-select_streams', 'v:0', 
            '-show_entries', 'stream=r_frame_rate', 
            '-of', 'default=noprint_wrappers=1:nokey=1', 
            str(video_path)
        ]
        output = subprocess.check_output(cmd).decode('utf-8').strip()
        if '/' in output:
            num, den = map(int, output.split('/'))
            return num / den
        return float(output)
    except Exception as e:
        logging.warning(f"No se pudo detectar FPS con ffprobe: {e}")
        return 30.0

def download_trailer(url, tmdb_id, slug):
    """Descarga el tráiler directamente a la carpeta assets/video_clips."""
    trailer_filename_template = f"{tmdb_id}_{slug}_trailer.%(ext)s"
    trailer_path_template = CLIPS_DIR / trailer_filename_template
    
    ydl_opts = {
        'outtmpl': str(trailer_path_template),
        # CAMBIO: Exigir un mínimo de 1080p para la descarga de video.
        # Si no hay 1080p, yt-dlp devolverá un error, lo cual es lo que queremos.
        'format': 'bestvideo[height>=1080]+bestaudio/best',  
        'format_sort': ['res', 'vcodec:vp9'], # Ordena por resolución descendente y prefiere VP9
        'prefer_free_formats': True,  # Evita formatos premium restringidos
        'merge_output_format': 'mp4',
        'no_playlist': True,
        'quiet': True,
        'verbose': False,
        'no_warnings': True,
        'extractor_args': {'youtube': {'player_client': 'web,android'}},
        'forceipv4': True,
        'retries': 3,
        'log_level': 'error',
    }
    

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            print("Extrayendo información del tráiler...")
            info = ydl.extract_info(url, download=False)  # Extrae info primero
            print("Formato seleccionado:", info.get('format_id', 'No encontrado'))  # Log: ID de formato
            print("Iniciando descarga...")
            ydl.download([url])
    except yt_dlp.utils.DownloadError as de:
        logging.error(f"Error de descarga específico: {de}")
        raise
    except Exception as e:
        logging.error(f"Fallo general en descarga: {e}")
        import traceback
        print("Traza del error:")
        traceback.print_exc()
        raise
    
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

def extract_clips(trailer_path, tmpdir, num_clips=15, clip_dur=CLIP_DURATION, interval=CLIP_INTERVAL):
    """Extrae clips del tráiler usando FFmpeg con alta calidad, evitando iniciales y finales."""
    clip_paths = []
    try:
        with VideoFileClip(str(trailer_path)) as trailer_clip:
            duration = trailer_clip.duration
            skip_initial = SKIP_INITIAL_CLIPS * clip_dur
            skip_final = SKIP_FINAL_CLIPS * clip_dur
            effective_duration = duration - skip_initial - skip_final
            if effective_duration <= 0:
                logging.error("Duración efectiva del tráiler demasiado corta después de skips.")
                return []
            
            # Ajustar intervalo para cubrir el centro con más clips
            adjusted_interval = max(1, effective_duration / (num_clips - 1)) if num_clips > 1 else 0
            
            # Detectar FPS originales para no forzar en la extracción
            orig_fps = trailer_clip.fps or 24
            
            for i in range(num_clips):
                start_time = skip_initial + i * adjusted_interval
                if start_time + clip_dur > duration - skip_final:
                    break
                out_path = tmpdir / f"clip_{i+1}.mp4"
                # Forzamos 30 FPS desde la extracción para máxima estabilidad en MoviePy
                cmd = [
                    'ffmpeg', '-y',
                    '-ss', str(start_time),
                    '-i', str(trailer_path),
                    '-t', str(clip_dur),
                    '-c:v', 'libx264',
                    '-r', '30', # Estandarizamos a 30 FPS
                    '-preset', 'ultrafast',
                    '-crf', '20',
                    '-an',
                    '-pix_fmt', 'yuv420p',
                    str(out_path)
                ]
                try:
                    subprocess.check_call(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    if out_path.exists() and out_path.stat().st_size > 50000: # Chequeo de tamaño mínimo
                        clip_paths.append(out_path)
                    else:
                        logging.warning(f"Clip {i+1} no generado o es demasiado pequeño.")
                except Exception as e:
                    logging.error(f"Fallo al extraer clip {i+1} con FFmpeg: {e}")
    except Exception as e:
        logging.error(f"Error al obtener duración del tráiler: {e}")
    return clip_paths

def select_best_clips(clip_paths):
    """Selecciona los mejores clips basados en diversidad visual y evita inicios en negro."""
    if not clip_paths:
        return []
    
    hashes = []
    selected_clips = []
    
    # Contadores para el log informativo
    discarded_black = 0
    discarded_diversity = 0
    candidates_pool = [] # Clips que pasan el filtro de negros pero no el de diversidad
    
    for path in clip_paths:
        try:
            with VideoFileClip(str(path)) as clip:
                # 1. COMPROBACIÓN EXHAUSTIVA DE NEGROS/LOGOS
                check_points = [0.1, 0.5, 1.2]
                is_bad = False
                avg_b = 0
                
                for p in check_points:
                    frame = clip.get_frame(p)
                    avg_b = np.mean(frame)
                    if avg_b < 25 or np.std(frame) < 5:
                        is_bad = True
                        break
                
                if is_bad:
                    discarded_black += 1
                    logging.info(f"   [x] Clip {path.name} descartado (negro/logo, brillo: {avg_b:.1f}).")
                    continue

                # 2. COMPROBACIÓN DE DIVERSIDAD (EXISTENTE)
                frame_for_hash = clip.get_frame(2.0)
                img = Image.fromarray(frame_for_hash)
                h = imagehash.average_hash(img)
                
                is_diverse = True
                for existing_hash in hashes:
                    if h - existing_hash < HASH_SIMILARITY_THRESHOLD:
                        is_diverse = False
                        break
                
                if is_diverse:
                    hashes.append(h)
                    selected_clips.append(path)
                else:
                    discarded_diversity += 1
                    candidates_pool.append(path)
                    logging.info(f"   [-] Clip {path.name} con poca diversidad.")
        except Exception as e:
            logging.warning(f"No se pudo procesar el clip para selección: {path.name}, error: {e}")

    logging.info(f"📊 RESUMEN FILTRADO: {len(selected_clips)} ideales, {discarded_black} negros/logos, {discarded_diversity} similares.")

    # --- JERARQUÍA DE RESCATE ---
    # 1. Si faltan, rellenar con los similares (pero que tienen luz)
    if len(selected_clips) < MAX_CLIPS and candidates_pool:
        needed = MAX_CLIPS - len(selected_clips)
        logging.info(f"⚠️ Faltan clips. Rescatando {min(needed, len(candidates_pool))} similares con luz...")
        for p in candidates_pool:
            if len(selected_clips) < MAX_CLIPS:
                selected_clips.append(p)

    # 2. Si aún faltan (tráiler muy oscuro), rescatar incluso los negros
    if len(selected_clips) < MAX_CLIPS:
        needed = MAX_CLIPS - len(selected_clips)
        logging.info(f"🚨 ¡CRÍTICO! Faltan clips. Rescatando {needed} incluso si son negros/logos...")
        for p in clip_paths:
            if p not in selected_clips and len(selected_clips) < MAX_CLIPS:
                selected_clips.append(p)

    return selected_clips[:MAX_CLIPS]

def save_clips(best_paths, tmdb_id, slug):
    """Guarda los clips seleccionados en la carpeta final."""
    saved_paths = []
    for i, path in enumerate(best_paths):
        dest_path = CLIPS_DIR / f"{tmdb_id}_{slug}_clip_{i+1}.mp4"
        shutil.move(str(path), str(dest_path))
        saved_paths.append(str(dest_path.relative_to(ROOT)))
    return saved_paths


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

        # Detectar FPS original de forma robusta
        orig_fps = get_video_fps(trailer_path)
        logging.info(f"🎞️ FPS detectados en el tráiler (ffprobe): {orig_fps:.2f}")

        # Guardar FPS en next_release para el histórico
        sel['trailer_fps'] = orig_fps
        SEL_FILE.write_text(json.dumps(sel, ensure_ascii=False, indent=2), encoding="utf-8")

        clip_paths_temp = extract_clips(trailer_path, tmpdir)
        logging.info(f"Clips extraídos temporalmente: {len(clip_paths_temp)}")

        best_paths = select_best_clips(clip_paths_temp)

        saved_paths = save_clips(best_paths, tmdb_id, slug)
        logging.info(f"Clips finales guardados: {saved_paths}")

        manifest_path = STATE / "assets_manifest.json"
        if manifest_path.exists():
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["video_clips"] = [p for p in saved_paths if Path(ROOT / p).exists()]
            # Guardamos también la ruta del tráiler y los FPS en el manifiesto
            manifest["trailer_file"] = str(trailer_path.relative_to(ROOT))
            manifest["trailer_fps"] = orig_fps
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
            logging.info(f"Manifiesto actualizado con clips, ruta y FPS ({orig_fps}): {manifest_path}")
        else:
            logging.warning("Manifiesto no encontrado. No se actualizaron los clips.")
    except Exception as e:
        logging.error(f"Error inesperado en el proceso de extracción: {e}")

if __name__ == "__main__":
    main()