# scripts/extract_video_clips_from_trailer.py
import json
import os
import shutil
from pathlib import Path
import logging
import yt_dlp
import imagehash
from PIL import Image
from moviepy.editor import VideoFileClip
import unicodedata
import re
import time

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "output" / "state"
CLIPS_DIR = ROOT / "assets" / "video_clips"  # Carpeta final para clips validados
CLIPS_DIR.mkdir(parents=True, exist_ok=True)

SEL_FILE = STATE / "next_release.json"
CLIP_INTERVAL = 10  # Extraer clips cada 10 segundos
CLIP_DURATION = 6  # Duración de cada clip ajustada a 6 segundos
MAX_CLIPS = 4  # Máximo de 4 clips para 24 segundos
HASH_SIMILARITY_THRESHOLD = 5  # Umbral de distancia Hamming

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
        'format': 'best',
        'quiet': True
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])
    return next(f for f in os.listdir(output_dir) if f.startswith('trailer.'))

def extract_clips(video_path, interval=CLIP_INTERVAL, clip_dur=CLIP_DURATION, tmpdir=None):
    clip = VideoFileClip(video_path)
    duration = clip.duration
    clips = []
    paths = []
    for t in range(0, int(duration - clip_dur), interval):
        subclip = clip.subclip(t, t + clip_dur)
        if subclip.duration >= clip_dur - 0.1 and subclip.reader is not None:  # Validar integridad con margen
            tmp_path = os.path.join(tmpdir, f"clip_{t}.mp4") if tmpdir else os.path.join(os.path.dirname(video_path), f"clip_{t}.mp4")
            try:
                subclip.write_videofile(tmp_path, codec="libx264", audio=False, verbose=False)  # Deshabilitar audio
                logging.debug(f"Clip en t={t} generado sin audio en {tmp_path}")
                # Revalidar y recrear el clip desde el archivo
                if os.path.exists(tmp_path) and os.path.getsize(tmp_path) > 0:
                    new_clip = VideoFileClip(tmp_path)
                    if new_clip.duration > 0 and new_clip.reader is not None:
                        clips.append(new_clip)
                        paths.append(tmp_path)
                    else:
                        logging.warning(f"Clip en t={t} inválido después de recreación: duración o reader nulo.")
                        os.remove(tmp_path)
                else:
                    logging.warning(f"Clip en t={t} descartado: archivo no creado o vacío.")
            except Exception as e:
                logging.warning(f"Error al crear clip en t={t}: {e}")
            finally:
                if 'subclip' in locals():
                    subclip.close()  # Cerrar explícitamente el subclip original
        else:
            logging.warning(f"Clip en t={t} descartado por duración insuficiente o inválido.")
    clip.close()  # Cerrar el clip principal
    return clips, paths

def filter_similar_clips(clip_paths, threshold=HASH_SIMILARITY_THRESHOLD, tmpdir=None):
    unique_indices = []
    filtered_count = 0
    for i, path1 in enumerate(clip_paths):
        try:
            with VideoFileClip(path1) as vclip1:
                if vclip1.duration > 0:
                    is_similar = False
                    for j, path2 in enumerate(clip_paths):
                        if i != j and os.path.exists(path2):
                            try:
                                with VideoFileClip(path2) as vclip2:
                                    if vclip2.duration > 0:
                                        hash1 = imagehash.average_hash(Image.open(extract_frame_from_clip(path1, tmpdir)))
                                        hash2 = imagehash.average_hash(Image.open(extract_frame_from_clip(path2, tmpdir)))
                                        distance = hash1 - hash2
                                        logging.debug(f"Distancia Hamming entre clip {i} y clip {j}: {distance}")
                                        if distance <= threshold:
                                            is_similar = True
                                            filtered_count += 1
                                            if i < j:  # Mantener el clip de menor índice y descartar el de mayor índice
                                                logging.info(f"Clip {j} descartado por similitud con clip {i} (distancia Hamming: {distance})")
                                            else:
                                                logging.info(f"Clip {i} descartado por similitud con clip {j} (distancia Hamming: {distance})")
                                                is_similar = True  # Asegurar que se marque como descartado si i > j
                                            break
                            except Exception as e:
                                logging.warning(f"Error al comparar clips {i} y {j}: {e}")
                    if not is_similar:
                        unique_indices.append(i)
        except Exception as e:
            logging.warning(f"Error al procesar clip {i}: {e}")
    return unique_indices, filtered_count

def extract_frame_from_clip(clip_path, tmpdir=None):
    clip = VideoFileClip(clip_path)
    try:
        frame = clip.get_frame(0)  # Primer frame como representativo
        if frame is not None and frame.size > 0:
            img = Image.fromarray(frame)
            tmp_path = os.path.join(tmpdir, f"frame_from_clip_{os.path.basename(clip_path)}.jpg") if tmpdir else os.path.join(os.path.dirname(clip_path), f"frame_from_clip_{os.path.basename(clip_path)}.jpg")
            img.save(tmp_path)
        else:
            logging.error(f"Frame inválido para {clip_path}")
            tmp_path = None
    except Exception as e:
        logging.error(f"Error al extraer frame de {clip_path}: {e}")
        tmp_path = None
    finally:
        clip.close()  # Cerrar explícitamente el clip
    return tmp_path

def save_clips(clips, tmdb_id, slug, tmpdir=None):
    saved_paths = []
    for i, clip in enumerate(clips):
        if clip is not None and clip.duration >= CLIP_DURATION - 0.1 and clip.reader is not None:  # Validar integridad
            filename = f"{tmdb_id}_{slug}_bd_v{i+1:02d}.mp4"
            path = CLIPS_DIR / filename
            try:
                clip.write_videofile(str(path), codec="libx264", audio=False, verbose=False)  # Deshabilitar audio
                saved_paths.append(str(path.relative_to(ROOT)))
            except Exception as e:
                logging.error(f"Error al guardar clip {filename}: {e}")
        else:
            logging.warning(f"Clip {i} omitido por ser inválido o nulo: {clip}")
    return saved_paths

def cleanup_temp_files(tmpdir):
    """Elimina archivos temporales generados durante el proceso con retraso para liberar bloqueos."""
    time.sleep(2)  # Retraso para liberar bloqueos
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
        return  # Sale del script sin fallar

    temp_root = ROOT / "temp"
    temp_root.mkdir(parents=True, exist_ok=True)
    tmpdir = temp_root / f"tmp_{tmdb_id}"
    # Limpieza inicial de la carpeta temporal si existe
    if tmpdir.exists():
        shutil.rmtree(tmpdir, ignore_errors=True)
        logging.info(f"Carpeta temporal {tmpdir} eliminada antes de la ejecución.")
    tmpdir.mkdir(parents=True, exist_ok=True)

    try:
        logging.info(f"Descargando tráiler desde {trailer_url}...")
        try:
            trailer_file = download_trailer(trailer_url, tmpdir)
            trailer_path = os.path.join(tmpdir, trailer_file)
            # No necesitamos cerrar trailer_path explícitamente aquí, ya que download_trailer lo maneja
        except Exception as e:
            logging.error(f"Fallo al descargar el tráiler: {e}")
            return

        logging.info("Extrayendo clips...")
        try:
            clips, clip_paths = extract_clips(trailer_path, tmpdir=tmpdir)
            logging.info(f"Clips extraídos: {len(clips)}")
        except Exception as e:
            logging.error(f"Fallo al extraer clips: {e}")
            return

        logging.info("Filtrando clips similares...")
        unique_indices, filtered_count = filter_similar_clips(clip_paths, tmpdir=tmpdir)
        unique_clips = [clips[i] for i in unique_indices if i < len(clips)]  # Evitar índices inválidos

        logging.info("Seleccionando los mejores clips...")
        best_clips = unique_clips[:MAX_CLIPS]  # Toma los primeros únicos tras filtrar

        logging.info("Guardando clips...")
        try:
            saved_paths = save_clips(best_clips, tmdb_id, slug, tmpdir=tmpdir)
            logging.info(f"Clips extraídos y guardados: {saved_paths}")

            # Actualizar manifiesto con las rutas de los clips
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