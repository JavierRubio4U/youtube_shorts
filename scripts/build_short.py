# scripts/build_short.py
import time
import unicodedata
import tempfile 
import imagehash
from pathlib import Path
import sys, json, os, logging, re
from moviepy.editor import VideoFileClip, concatenate_videoclips, ImageClip, CompositeVideoClip, AudioFileClip
from PIL import Image, ImageDraw, ImageFont
import numpy as np
from overlay import make_overlay_image  # Asumiendo que está en overlay.py
from ai_narration import generate_narration  # Asumiendo que está en ai_narration.py

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "output" / "state"
SHORTS_DIR = ROOT / "output" / "shorts"
MANIFEST = ROOT / "assets" / "assets_manifest.json"
SEL_FILE = STATE / "next_release.json"

W, H = 1080, 1920  # Ancho y alto para formato vertical 9:16
INTRO_DURATION = 4  # Duración de la intro con la imagen
CLIP_DURATION = 6  # Duración de los clips de video (consistente con Paso 2.5)
MAX_BACKDROPS = 8  # Máximo de backdrops o clips
HASH_SIMILARITY_THRESHOLD = 5  # Umbral de distancia Hamming

def slugify(text: str, maxlen: int = 60) -> str:
    """Convierte texto en un slug seguro para nombres de archivo."""
    s = (text or "").lower().strip()
    s = ''.join(c for c in unicodedata.normalize('NFD', s) if unicodedata.category(c) != 'Mn')
    s = re.sub(r'[^\w\s-]', '', s)
    s = re.sub(r'[\s_-]+', '-', s).strip('-')
    return (s or "title")[:maxlen]

def extract_frame_from_path(path: Path, tmpdir: Path) -> str | None:
    """Extrae un frame representativo de un video o devuelve la ruta de una imagen."""
    if path.suffix.lower() in ['.jpg', '.jpeg', '.png']:
        return str(path)
    else:
        try:
            clip = VideoFileClip(str(path))
            frame = clip.get_frame(0)
            img = Image.fromarray(frame)
            tmp_path = os.path.join(tmpdir, f"frame_from_{path.name}.jpg")  # Usar tmpdir
            img.save(tmp_path)
            clip.close()
            return tmp_path
        except Exception as e:
            logging.error(f"Error al extraer frame de {path}: {e}")
            return None

def is_similar_image(img1_path: Path, img2_path: Path, threshold: int) -> tuple[bool, int]:
    """Compara dos imágenes usando imagehash y devuelve si son similares y la distancia."""
    try:
        hash1 = imagehash.average_hash(Image.open(img1_path))
        hash2 = imagehash.average_hash(Image.open(img2_path))
        distance = hash1 - hash2
        logging.debug(f"Distancia Hamming entre {img1_path.name} y {img2_path.name}: {distance}")
        return distance <= threshold, distance
    except Exception as e:
        logging.error(f"Error al comparar {img1_path} y {img2_path}: {e}")
        return False, 0

def clip_from_img(path: Path, dur: float) -> ImageClip:
    """Crea un clip de video a partir de una imagen con duración dada."""
    try:
        img_clip = ImageClip(str(path)).set_duration(dur)
        # Letterboxing para preservar proporción
        img_w, img_h = img_clip.size
        target_ratio = W / H
        current_ratio = img_w / img_h
        if current_ratio > target_ratio:  # Imagen más ancha, añadir barras arriba/abajo
            new_h = int(img_w / target_ratio)
            y_offset = (new_h - img_h) // 2
            img_clip = img_clip.margin(top=y_offset, bottom=y_offset, color=(0, 0, 0))
        else:  # Imagen más alta, añadir barras a los lados
            new_w = int(img_h * target_ratio)
            x_offset = (new_w - img_w) // 2
            img_clip = img_clip.margin(left=x_offset, right=x_offset, color=(0, 0, 0))
        return img_clip.resize((W, H))
    except Exception as e:
        logging.error(f"Error al cargar imagen {path}: {e}")
        return None

def filter_similar_images(image_paths: list[Path], poster_path: Path | None = None, threshold: int = HASH_SIMILARITY_THRESHOLD, tmpdir: Path = None) -> list[Path]:
    """Filtra imágenes o clips similares basándose en imagehash, comparando todos con todos y con el póster."""
    if not image_paths:
        return []
    
    unique_images = []
    filtered_count = 0
    for i, path1 in enumerate(image_paths):
        is_similar = False
        for j, path2 in enumerate(image_paths):
            if i != j:  # Evitar comparar una imagen consigo misma
                # Extraer un frame representativo del clip si es video
                img1_path = Path(extract_frame_from_path(path1, tmpdir)) if extract_frame_from_path(path1, tmpdir) else None
                img2_path = Path(extract_frame_from_path(path2, tmpdir)) if extract_frame_from_path(path2, tmpdir) else None
                if img1_path and img2_path and img1_path.exists() and img2_path.exists():
                    is_sim, distance = is_similar_image(img1_path, img2_path, threshold)
                    if is_sim:
                        is_similar = True
                        filtered_count += 1
                        logging.info(f"Clip/Imagen {Path(path1).name} descartado por similitud con {Path(path2).name} (distancia Hamming: {distance})")
                        break
        # Comparar con el póster si existe y es un archivo
        if poster_path and not is_similar and poster_path.is_file():
            img1_path = Path(extract_frame_from_path(path1, tmpdir)) if extract_frame_from_path(path1, tmpdir) else None
            if img1_path and img1_path.exists() and poster_path.exists():
                try:
                    is_sim, distance = is_similar_image(img1_path, poster_path, threshold)
                    if is_sim:
                        is_similar = True
                        filtered_count += 1
                        logging.info(f"Clip/Imagen {Path(path1).name} descartado por similitud con el póster {Path(poster_path).name} (distancia Hamming: {distance})")
                except PermissionError as e:
                    logging.warning(f"Permiso denegado al comparar {img1_path} y {poster_path}: {e}")
        if not is_similar:
            unique_images.append(path1)
    
    logging.info(f"Backdrops seleccionados: {len(unique_images[:max(4, MAX_BACKDROPS)])}")
    if not unique_images and poster_path and poster_path.is_file() and poster_path.exists():
        logging.warning("No hay backdrops válidos; usando póster como fallback.")
        return [poster_path]
    return unique_images[:max(4, MAX_BACKDROPS)]  # Asegura al menos 4 si hay suficientes

def main():
    if not SEL_FILE.exists():
        logging.warning("next_release.json no encontrado. Usando valores predeterminados para prueba.")
        sel = {"tmdb_id": "936108", "titulo": "Pitufos", "fecha_estreno": "1984-09-12"}
    else:
        sel = json.loads(SEL_FILE.read_text(encoding="utf-8"))

    video_clips = []
    backdrops = []  # Inicializar backdrops
    if not MANIFEST.exists():
        logging.warning("assets_manifest.json no encontrado. Usando clips preexistentes en assets/video_clips.")
        clips_dir = ROOT / "assets" / "video_clips"
        video_clips = [clips_dir / p for p in os.listdir(clips_dir) if p.endswith((".mp4", ".avi"))]
    else:
        man = json.loads(MANIFEST.read_text(encoding="utf-8"))
        video_clips = [ROOT / p for p in man.get("video_clips", []) if (ROOT / p).exists()]
        backdrops = [ROOT / b for b in man.get("backdrops", []) if (ROOT / b).exists()]

    tmdb_id = sel.get("tmdb_id", "unknown")
    title = sel.get("titulo") or sel.get("title") or ""
    fecha = sel.get("fecha_estreno") or ""
    slug = slugify(title)

    # Crear directorio temporal
    temp_root = ROOT / "temp"
    temp_root.mkdir(parents=True, exist_ok=True)
    tmpdir = temp_root / f"tmp_{tmdb_id}"
    tmpdir.mkdir(parents=True, exist_ok=True)

    # Generar narración y audio en el directorio temporal
    narracion, voice_path = generate_narration(sel, tmdb_id, slug, tmpdir=tmpdir)

    # Cargar assets
    if MANIFEST.exists():
        poster_path = ROOT / man.get("poster_path", "")
        if not poster_path.exists():
            logging.warning(f"Ruta del manifiesto inválida: {poster_path}, buscando en assets/posters")
            poster_path = ROOT / "assets" / "posters" / f"{tmdb_id}_poster.jpg"
    else:
        poster_path = ROOT / "assets" / "posters" / f"{tmdb_id}_poster.jpg"  # Buscar por tmdb_id
        if not poster_path.exists():
            logging.warning(f"Póster no encontrado en {poster_path}, buscando fallback en assets/posters")
            for file in os.listdir(ROOT / "assets" / "posters"):
                if file.startswith(f"{tmdb_id}") and file.endswith((".jpg", ".jpeg", ".png")):
                    poster_path = ROOT / "assets" / "posters" / file
                    logging.info(f"Póster encontrado como fallback: {poster_path}")
                    break
            if not poster_path.exists():
                logging.error(f"Ningún póster encontrado para tmdb_id {tmdb_id} en assets/posters")
                poster_path = None
    video_clips = video_clips if video_clips else [ROOT / p for p in os.listdir(ROOT / "assets" / "video_clips") if p.endswith((".mp4", ".avi"))]
    backdrops = backdrops if backdrops else []

    # Combinar clips y backdrops, priorizando clips de video
    bd_paths = video_clips + backdrops if video_clips else backdrops
    if not bd_paths:
        logging.warning("No se encontraron backdrops ni clips de video. Usando póster como fallback.")
        bd_paths = [poster_path] if poster_path.exists() else []

    # Filtrar imágenes o clips similares
    bd_paths = filter_similar_images(bd_paths, poster_path, HASH_SIMILARITY_THRESHOLD, tmpdir)

    # Crear clips de video o imágenes
    clips = []
    for path in bd_paths:
        if path.suffix.lower() in ['.jpg', '.jpeg', '.png']:
            img_clip = clip_from_img(path, CLIP_DURATION)
            if img_clip:
                clips.append(img_clip)
        else:
            try:
                video_clip = VideoFileClip(str(path)).set_duration(CLIP_DURATION)
                clips.append(video_clip)
            except Exception as e:
                logging.error(f"Error al cargar clip de video {path}: {e}")
            else:
                video_clip.close()  # Cerrar explícitamente el VideoFileClip

    # Crear intro con la imagen sin recortar, mantener tamaño original
    if poster_path and poster_path.is_file():
        logging.debug(f"Póster válido encontrado: {poster_path}")
        img = Image.open(poster_path).convert("RGB")
        w, h = img.size
        intro_clip = ImageClip(str(poster_path)).set_duration(INTRO_DURATION)
        if intro_clip:
            logging.debug(f"Intro_clip creado con tamaño original {w}x{h} y duración: {INTRO_DURATION} segundos")
            clips.insert(0, intro_clip)
        else:
            logging.error(f"Fallo al crear intro_clip desde {poster_path}")
    else:
        logging.error(f"Póster no válido o no encontrado: {poster_path}, usando clip vacío 1080x1920")
        intro_clip = ImageClip(np.zeros((1920, 1080, 3), dtype=np.uint8), duration=INTRO_DURATION)
        clips.insert(0, intro_clip)

    # Eliminar overlay (sin letras)
    final_clips = clips  # Usar clips directamente sin overlay

    # Concatenar todos los clips
    final_clip = concatenate_videoclips(final_clips, method="compose")

    # Ajustar tamaño para agrandar clips a 1080x1920
    def resize_to_9_16(clip):
        logging.debug(f"Redimensionando clip: tamaño original {clip.w}x{clip.h}, ratio {clip.w / clip.h}")
        target_ratio = 9 / 16
        current_ratio = clip.w / clip.h
        
        # Escalar al 90% del alto (dejando margen superior)
        target_height = 1728  # 90% de 1920
        if current_ratio > target_ratio:  # Imagen más ancha
            scale = target_height / clip.h
            new_w = int(clip.w * scale)
            clip = clip.resize((new_w, target_height))
            # Añadir barras negras a los lados si no llega a 1080
            if new_w < 1080:
                x_offset = (1080 - new_w) // 2
                clip = clip.margin(left=x_offset, right=x_offset, color=(0, 0, 0))
        else:  # Imagen más alta o cuadrada
            scale = target_height / clip.h
            new_w = int(clip.w * scale)
            clip = clip.resize((new_w, target_height))
            # Añadir barras negras a los lados si no llega a 1080
            if new_w < 1080:
                x_offset = (1080 - new_w) // 2
                clip = clip.margin(left=x_offset, right=x_offset, color=(0, 0, 0))
        
        # Añadir margen superior fijo y completar con barras negras abajo
        y_margin = (1920 - target_height) // 2  # Centrar verticalmente
        clip = clip.margin(top=y_margin, bottom=y_margin, color=(0, 0, 0))
        
        final_size = clip.size
        logging.debug(f"Tamaño final después de ajustar a 1080x1920 con margen: {final_size[0]}x{final_size[1]}")
        return clip.resize((1080, 1920))  # Forzar tamaño final exacto

    final_clip = resize_to_9_16(final_clip)

    # Añadir narración como audio
    if voice_path and os.path.exists(voice_path):
        try:
            audio_clip = AudioFileClip(str(voice_path))  # Convertir a str
            if audio_clip.duration >= final_clip.duration:
                audio_clip = audio_clip.subclip(0, final_clip.duration)
            else:
                audio_clip = concatenate_audioclips([audio_clip] * (int(final_clip.duration / audio_clip.duration) + 1)).subclip(0, final_clip.duration)
            final_clip = final_clip.set_audio(audio_clip)
        except Exception as e:
            logging.error(f"Error al cargar audio desde {voice_path}: {e}")

    out_file = SHORTS_DIR / f"{tmdb_id}_{slug}_final.mp4"
    final_clip.write_videofile(str(out_file), fps=30, codec="libx264", audio_codec="aac")
    logging.info(f"🎬 Short generado en: {out_file}")
    # cleanup_temp_files(tmpdir)  # Comentar limpieza temporal
    return str(out_file)  # Devolver la ruta del video

def cleanup_temp_files(tmpdir):
    """Limpia archivos temporales específicos del directorio temporal."""
    time.sleep(2)  # Retraso para liberar bloqueos
    for file in os.listdir(tmpdir):
        file_path = os.path.join(tmpdir, file)
        if os.path.isfile(file_path):
            try:
                os.remove(file_path)
                logging.debug(f"Archivo temporal {file} eliminado de {tmpdir}.")
            except PermissionError as e:
                logging.warning(f"No se pudo eliminar {file} por bloqueo en {tmpdir}: {e}")
    logging.info(f"Archivos temporales en {tmpdir} eliminados (o intentados).")