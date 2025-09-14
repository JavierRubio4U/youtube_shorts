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
        'format': 'bestvideo[height>=2160]+bestaudio/bestvideo+bestaudio',
        'merge_output_format': 'mp4',
        'no_playlist': True,
        'quiet': True
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])
    return next(f for f in os.listdir(output_dir) if f.startswith('trailer.'))

def extract_clips(video_path, interval=CLIP_INTERVAL, clip_dur=CLIP_DURATION, tmpdir=None):
    paths = []
    duration_cmd = ['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'default=noprint_wrappers=1:nokey=1', str(video_path)]
    duration = float(subprocess.check_output(duration_cmd).decode().strip())
    
    start_time = 0
    for t in range(int(start_time), int(duration - clip_dur), interval):
        out_clip = tmpdir / f"clip_{t}.mp4" if tmpdir else Path(os.path.dirname(video_path)) / f"clip_{t}.mp4"
        cmd = [
            'ffmpeg', '-y', '-ss', str(t), '-i', str(video_path),
            '-t', str(clip_dur), '-c:v', 'copy', '-c:a', 'copy',
            str(out_clip)
        ]
        subprocess.run(cmd, check=True, capture_output=True)
        if out_clip.exists() and out_clip.stat().st_size > 0:
            paths.append(out_clip)
    
    if len(paths) > SKIP_INITIAL_CLIPS:
        paths = paths[SKIP_INITIAL_CLIPS:]
        logging.info(f"Quitados los primeros {SKIP_INITIAL_CLIPS} clips para evitar intros/anuncios.")
    
    logging.info(f"{len(paths)} clips extraídos sin pérdida.")
    return paths

def is_static_or_solid_color(video_path):
    """
    Comprueba si el clip es mayormente estático o tiene un color sólido.
    Retorna True si es un logo o texto estático.
    """
    try:
        cmd_motion = ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "frame=pkt_pts_time,side_data_list", "-of", "csv=p=0", "-i", str(video_path)]
        output = subprocess.check_output(cmd_motion).decode().strip()
        motion_scores = [float(line.split(' ')[1]) for line in output.split('\n') if 'lavfi.scene_score' in line]
        if motion_scores and sum(motion_scores) / len(motion_scores) < 0.1:
            logging.info(f"Clip {video_path.name} descartado por ser estático (score: {sum(motion_scores) / len(motion_scores):.2f}).")
            return True
        return False
    except Exception as e:
        logging.warning(f"Error al analizar el movimiento del clip: {e}")
        return False

def select_best_clips(clip_paths, max_clips=MAX_CLIPS):
    valid_clips = []
    
    for path in clip_paths:
        if is_static_or_solid_color(path):
            continue
        valid_clips.append(path)

    if len(valid_clips) >= max_clips:
        step = len(valid_clips) // max_clips
        selected = [valid_clips[i * step] for i in range(max_clips)]
        logging.info(f"{len(selected)} clips seleccionados de los {len(valid_clips)} válidos.")
        return selected
    
    logging.warning(f"No hay suficientes clips válidos ({len(valid_clips)}). Devolviendo todos los válidos.")
    return valid_clips

def save_clips(clip_paths, tmdb_id, slug):
    saved_paths = []
    for i, path in enumerate(clip_paths):
        filename = f"{tmdb_id}_{slug}_bd_v{i+1:02d}.mp4"
        out_path = CLIPS_DIR / filename
        cmd = [
            'ffmpeg', '-y', '-i', str(path),
            '-c:v', 'copy', '-an',
            str(out_path)
        ]
        subprocess.run(cmd, check=True, capture_output=True)
        if out_path.exists():
            saved_paths.append(str(out_path.relative_to(ROOT)))
        else:
            logging.error(f"Error al guardar clip {filename}")
    return saved_paths

def cleanup_temp_files(tmpdir):
    time.sleep(2)
    for file in os.listdir(tmpdir):
        file_path = os.path.join(tmpdir, file)
        if os.path.isfile(file_path):
            try:
                os.remove(file_path)
                logging.debug(f"Archivo temporal {file} eliminado.")
            except PermissionError as e:
                logging.warning(f"No se pudo eliminar {file} por bloqueo: {e}")
    logging.info(f"Archivos temporales en {tmpdir} eliminados (o intentados).")

def main():
    if not SEL_FILE.exists():
        raise SystemExit("Falta next_release.json.")

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
        try:
            trailer_file = download_trailer(trailer_url, tmpdir)
            trailer_path = os.path.join(tmpdir, trailer_file)
        except Exception as e:
            logging.error(f"Fallo al descargar el tráiler: {e}")
            return

        logging.info("Extrayendo clips...")
        try:
            clip_paths = extract_clips(trailer_path, tmpdir=tmpdir)
            logging.info(f"Clips extraídos: {len(clip_paths)}")
        except Exception as e:
            logging.error(f"Fallo al extraer clips: {e}")
            return

        logging.info("Seleccionando los mejores clips...")
        best_paths = select_best_clips(clip_paths)

        logging.info("Guardando clips...")
        try:
            saved_paths = save_clips(best_paths, tmdb_id, slug)
            logging.info(f"Clips extraídos y guardados: {saved_paths}")

            manifest_path = STATE / "assets_manifest.json"
            if manifest_path.exists():
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                manifest["video_clips"] = [p for p in saved_paths if Path(ROOT / p).exists()]
                manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
                logging.info(f"Manifiesto actualizado con clips: {manifest_path}")
            else:
                logging.warning("Manifiesto no encontrado. No se actualizaron los clips.")
        except Exception as e:
            logging.error(f"Fallo al guardar clips: {e}")
    except Exception as e:
        logging.error(f"Error inesperado en el proceso: {e}")
                                                    
if __name__ == "__main__":
    main()