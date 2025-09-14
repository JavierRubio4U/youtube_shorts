# scripts/build_short.py
import time
import unicodedata
import random  # Para música aleatoria
from pathlib import Path
import json, os, logging, re
from PIL import Image
import numpy as np
#from overlay import make_overlay_image
from ai_narration import generate_narration

from moviepy import VideoFileClip, concatenate_videoclips, ImageClip, CompositeVideoClip, AudioFileClip, concatenate_audioclips
from moviepy.audio.AudioClip import CompositeAudioClip, AudioClip  # Para mezcla y silencio
import moviepy.audio.fx as afx

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')  # Cambia a DEBUG si necesitas más detalles

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "output" / "state"
SHORTS_DIR = ROOT / "output" / "shorts"
MANIFEST = STATE / "assets_manifest.json"
SEL_FILE = STATE / "next_release.json"

W, H = 1080, 1920  # Ancho y alto para formato vertical 9:16
INTRO_DURATION = 4  # Duración de la intro con la imagen
CLIP_DURATION = 6  # Duración de los clips de video
MAX_BACKDROPS = 4  # Máximo de clips (ajustado a 4 para consistencia con trailer)
HASH_SIMILARITY_THRESHOLD = 5  # Umbral de distancia Hamming

def slugify(text: str, maxlen: int = 60) -> str:
    """Convierte texto en un slug seguro para nombres de archivo."""
    s = (text or "").lower().strip()
    s = ''.join(c for c in unicodedata.normalize('NFD', s) if unicodedata.category(c) != 'Mn')
    s = re.sub(r'[^\w\s-]', '', s)
    s = re.sub(r'[\s_-]+', '-', s).strip('-')
    return (s or "title")[:maxlen]

def clip_from_img(path: Path, dur: float) -> ImageClip:
    """Crea un clip de video a partir de una imagen con duración dada."""
    try:
        img_clip = ImageClip(str(path)).with_duration(dur)
        img_w, img_h = img_clip.size
        target_ratio = W / H
        current_ratio = img_w / img_h
        if current_ratio > target_ratio:
            new_h = int(img_w / target_ratio)
            y_offset = (new_h - img_h) // 2
            img_clip = img_clip.margined(top=y_offset, bottom=y_offset, color=(0, 0, 0))
        else:
            new_w = int(img_h * target_ratio)
            x_offset = (new_w - img_w) // 2
            img_clip = img_clip.margined(left=x_offset, right=x_offset, color=(0, 0, 0))
        return img_clip.resized((W, H))
    except Exception as e:
        logging.error(f"Error al cargar imagen {path}: {e}")
        return None

def resize_to_9_16(clip):
    logging.debug(f"Redimensionando clip: tamaño original {clip.w}x{clip.h}, ratio {clip.w / clip.h}")
    target_ratio = 9 / 16
    current_ratio = clip.w / clip.h
    
    # Escalar al máximo sin distorsión y crop
    if current_ratio > target_ratio:  # Landscape: escalar por alto, crop lateral
        clip = clip.resized(height=H)
        left_crop = (clip.w - W) // 2
        logging.debug(f"Crop lateral: offset {left_crop}, nuevo ancho {W}")
        clip = clip.cropped(x1=left_crop, x2=clip.w - left_crop)
    else:  # Portrait: escalar por ancho, crop vertical
        clip = clip.resized(width=W)
        top_crop = (clip.h - H) // 2
        logging.debug(f"Crop vertical: offset {top_crop}, nuevo alto {H}")
        clip = clip.cropped(y1=top_crop, y2=clip.h - top_crop)
    
    logging.debug(f"Tamaño final: {clip.w}x{clip.h}")
    return clip

def main():
    if not SEL_FILE.exists():
        logging.warning("next_release.json no encontrado. El proceso se detiene.")
        return

    sel = json.loads(SEL_FILE.read_text(encoding="utf-8"))

    video_clips = []
    tmdb_id = sel.get("tmdb_id", "unknown")
    title = sel.get("titulo") or sel.get("title") or ""
    fecha = sel.get("fecha_estreno") or ""
    slug = slugify(title)

    # Crear directorio temporal
    temp_root = ROOT / "temp"
    temp_root.mkdir(parents=True, exist_ok=True)
    tmpdir = temp_root / f"tmp_{tmdb_id}"
    tmpdir.mkdir(parents=True, exist_ok=True)

    # Cargar assets desde manifiesto
    if MANIFEST.exists():
        man = json.loads(MANIFEST.read_text(encoding="utf-8"))
        poster_path = ROOT / man.get("poster", "")
        video_clips = [ROOT / p for p in man.get("video_clips", []) if (ROOT / p).exists()]
    else:
        logging.warning("assets_manifest.json no encontrado. Buscando póster como fallback.")
        poster_path = ROOT / "assets" / "posters" / f"{tmdb_id}_poster.jpg"
        if not poster_path.exists():
            logging.error(f"Póster no encontrado para tmdb_id {tmdb_id}")
            poster_path = None

    if not video_clips and poster_path and poster_path.exists():
        logging.warning("No hay clips de video. Usando póster como fallback.")
        video_clips = [poster_path] * MAX_BACKDROPS

    bd_paths = video_clips[:MAX_BACKDROPS]

    # Crear clips
    clips = []
    for path in bd_paths:
        if path.suffix.lower() in ['.jpg', '.jpeg', '.png']:
            img_clip = clip_from_img(path, CLIP_DURATION)
            if img_clip:
                img_clip = resize_to_9_16(img_clip)
                clips.append(img_clip)
        else:
            try:
                video_clip = VideoFileClip(str(path)).with_duration(CLIP_DURATION)
                logging.debug(f"Cargando clip {path.name}: tamaño original {video_clip.w}x{video_clip.h}, ratio {video_clip.w / video_clip.h}")
                video_clip = resize_to_9_16(video_clip)
                clips.append(video_clip)
            except Exception as e:
                logging.error(f"Error al cargar clip de video {path}: {e}")

    # Intro con póster (aplicar crop)
    if poster_path and poster_path.is_file():
        logging.debug(f"Póster válido encontrado: {poster_path}")
        intro_clip = ImageClip(str(poster_path)).with_duration(INTRO_DURATION)
        if intro_clip:
            intro_clip = resize_to_9_16(intro_clip)
            clips.insert(0, intro_clip)
        else:
            logging.error(f"Fallo al crear intro_clip desde {poster_path}")
            intro_clip = None
    else:
        logging.warning(f"Póster no válido o no encontrado: {poster_path}. Usando fallback.")
        intro_clip = None

    # Fallback: Usa el primer backdrop/clip si no hay póster
    if intro_clip is None and bd_paths:
        first_bd = bd_paths[0]
        if first_bd.suffix.lower() in ['.jpg', '.jpeg', '.png']:
            intro_clip = clip_from_img(first_bd, INTRO_DURATION)
        else:
            intro_clip = VideoFileClip(str(first_bd)).get_frame(0)
            intro_clip = ImageClip(intro_clip).with_duration(INTRO_DURATION)
        if intro_clip:
            intro_clip = resize_to_9_16(intro_clip)
            clips.insert(0, intro_clip)
        else:
            intro_clip = ImageClip(np.zeros((H, W, 3), dtype=np.uint8)).with_duration(INTRO_DURATION)
            clips.insert(0, intro_clip)

    # Concatenar todos los clips para obtener la duración real
    final_clip = concatenate_videoclips(clips, method="compose")
    final_clip = resize_to_9_16(final_clip)

    # Generar narración y audio, pasando la duración real del video
    narracion, voice_path = generate_narration(sel, tmdb_id, slug, tmpdir=tmpdir, video_duration=final_clip.duration)

    # Añadir narración y música como audio
    if voice_path and os.path.exists(voice_path):
        try:
            audio_clip = AudioFileClip(str(voice_path))
            logging.debug(f"Audio de narración cargado: duración {audio_clip.duration}s")
            
            # Recortar narración si ya es más larga (antes de mezcla)
            if audio_clip.duration > final_clip.duration:
                audio_clip = audio_clip.subclipped(0, final_clip.duration) 
                logging.debug(f"Narración recortada a {final_clip.duration}s antes de mezcla.")
            
            # Añadir música de fondo aleatoria
            audio_dir = ROOT / "assets" / "music"
            final_audio = audio_clip
            if audio_dir.exists() and any(audio_dir.iterdir()):
                music_files = [f for f in audio_dir.iterdir() if f.suffix.lower() in ['.mp3', '.wav']]
                if music_files:
                    music_path = random.choice(music_files)
                    logging.debug(f"Archivo de música seleccionado: {music_path.name}")
                    try:
                        music_clip = AudioFileClip(str(music_path))
                        logging.debug(f"Música cargada: duración original {music_clip.duration}s")
                        
                        if music_clip.duration < final_clip.duration:
                            repeats = int(final_clip.duration / music_clip.duration) + 1
                            music_clip = concatenate_audioclips([music_clip] * repeats).subclipped(0, final_clip.duration)  # Fixed: .subclip
                        else:
                            music_clip = music_clip.subclipped(0, final_clip.duration)  
                        logging.debug(f"Música ajustada a {final_clip.duration}s")
                        
                        # Fixed: Use .fx with afx instead of .with_effects
                        music_clip = music_clip.with_effects([afx.AudioFadeIn(1.0), afx.AudioFadeOut(1.0), afx.MultiplyVolume(0.10)])
                        final_audio = CompositeAudioClip([audio_clip, music_clip])
                        logging.debug(f"Mezcla de audio completada: duración {final_audio.duration}s")
                        
                        if final_audio.duration > final_clip.duration:
                            final_audio = final_audio.subclipped(0, final_clip.duration)  
                            logging.info(f"Audio final recortado a {final_clip.duration}s para fit exacto.")
                        
                        logging.info(f"Música de fondo aleatoria añadida desde {music_path.name}.")
                    except Exception as e:
                        logging.warning(f"No se pudo añadir música desde {music_path}: {e}")
                else:
                    logging.info("No se encontraron archivos de música válidos. Solo narración.")
            else:
                logging.info("Carpeta de music no encontrada o vacía. Solo narración.")
            
            if final_audio.duration < final_clip.duration:
                silence_duration = final_clip.duration - final_audio.duration
                silence_clip = AudioClip(lambda t: 0, duration=silence_duration, fps=final_audio.fps)
                final_audio = concatenate_audioclips([final_audio, silence_clip])
                logging.debug(f"Audio extendido con silencio a {final_clip.duration}s")
            
            final_clip = final_clip.with_audio(final_audio)
            logging.debug(f"Audio final aplicado al video: duración {final_clip.duration}s")
        except Exception as e:
            logging.error(f"Error al cargar audio desde {voice_path}: {e}")

    out_file = SHORTS_DIR / f"{tmdb_id}_{slug}_final.mp4"
    final_clip.write_videofile(
        str(out_file), fps=60,
        codec="libx264", preset="veryslow",
        bitrate="20000k",
        ffmpeg_params=["-crf", "18"]
    )
    logging.info(f"🎬 Short generado en: {out_file}")

    cleanup_temp_files(tmpdir)

    return str(out_file)

def cleanup_temp_files(tmpdir):
    """Limpia archivos temporales específicos del directorio temporal."""
    time.sleep(2)
    for file in os.listdir(tmpdir):
        file_path = os.path.join(tmpdir, file)
        if os.path.isfile(file_path):
            try:
                os.remove(file_path)
                logging.debug(f"Archivo temporal {file} eliminado de {tmpdir}.")
            except PermissionError as e:
                logging.warning(f"No se pudo eliminar {file} por bloqueo en {tmpdir}: {e}")
    logging.info(f"Archivos temporales en {tmpdir} eliminados (o intentados).")