# scripts/build_short.py
import time
import unicodedata
import random  # Para música aleatoria
from pathlib import Path
import json, os, logging, re
from PIL import Image
import numpy as np

import separate_narration  # Nuevo script para narración separada
import tempfile
import shutil
from slugify import slugify
# Imports de moviepy
from moviepy import (VideoFileClip, ImageClip, AudioFileClip, AudioClip,
                     CompositeVideoClip, ColorClip, # ColorClip es necesario para el fondo
                     CompositeAudioClip, concatenate_videoclips, concatenate_audioclips)
import moviepy.audio.fx as afx

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "output" / "state"
SHORTS_DIR = ROOT / "output" / "shorts"
MANIFEST = STATE / "assets_manifest.json"
SEL_FILE = STATE / "next_release.json"

W, H = 2160, 3840  # CAMBIO: 4K vertical (2160x3840)
SQUARE_SIZE = 2160 # CAMBIO: El cuadrado debe ser 2160x2160
INTRO_DURATION = 4  # Duración de la intro con la imagen
CLIP_DURATION = 6  # Duración de los clips de video
MAX_BACKDROPS = 4  # Máximo de clips

def clip_from_img(path: Path, dur: float) -> ImageClip:
    """Crea un clip de video a partir de una imagen con duración dada."""
    try:
        # Esta función mantiene el recorte que llena la pantalla para el póster inicial
        img_clip = ImageClip(str(path), duration=dur)
        img_w, img_h = img_clip.size
        target_ratio = W / H
        current_ratio = img_w / img_h
        if current_ratio > target_ratio:
            img_clip = img_clip.resized(height=H)
            left_crop = (img_clip.w - W) // 2
            img_clip = img_clip.cropped(x1=left_crop, x2=img_clip.w - left_crop)
        else:
            img_clip = img_clip.resized(width=W)
            top_crop = (img_clip.h - H) // 2
            img_clip = img_clip.cropped(y1=top_crop, y2=img_clip.h - top_crop)
        return img_clip
    except Exception as e:
        logging.error(f"Error al cargar imagen {path}: {e}")
        return None


def resize_to_9_16(clip: VideoFileClip) -> VideoFileClip:
    """
    Recorta el centro del clip a un formato cuadrado (1080x1080) y lo coloca
    en un fondo vertical 9:16 con bandas negras.
    """
    
    logging.info(f"Dimensiones originales del clip: {clip.size[0]}x{clip.size[1]}")

# Paso 1: Recortar el centro del clip original a una proporción cuadrada.
    # El ancho será el menor entre el ancho del clip y la altura del clip,
    # reescalado para asegurar que la porción central es la que usamos.
    
    # Tomamos el centro del clip de origen con dimensiones SQUARE_SIZE x SQUARE_SIZE
    # Si el clip original es 1920x1080 y SQUARE_SIZE=1440:
    # 1. Recortamos horizontalmente, dejando un clip de (1440/1080)*1920 x 1080 (aprox)
    # 2. Reescalamos el recorte resultante a 1440x1440
    
    # ----------------------------------------------------------------------------------
    # CAMBIO: Recorte y Reescalado forzado a SQUARE_SIZE x SQUARE_SIZE
    # ----------------------------------------------------------------------------------
    
    # Recortar horizontalmente (si es 16:9), manteniendo la altura de 1080.
    # Esto resulta en un clip de 1920x1080 -> 1080x1080
    square_clip_intermediate = clip.cropped(x_center=clip.w / 2, width=clip.h)
    
    # Forzar el reescalado a la dimensión final del cuadrado (1440x1440)
    square_clip = square_clip_intermediate.resized((SQUARE_SIZE, SQUARE_SIZE))

    logging.info(f"Dimensiones tras recorte y reescalado forzado a cuadrado: {square_clip.size[0]}x{square_clip.size[1]}")
    
    # Paso 2: Crear el fondo negro vertical final.
    background = ColorClip(size=(W, H), color=(0, 0, 0), duration=clip.duration)

    # Paso 3: Colocar el clip cuadrado (que ya es 1080x1080) en el centro del fondo.
    # Esto creará las bandas negras superior e inferior automáticamente.
    final_clip = CompositeVideoClip([
        background,
        square_clip.with_position("center")
    ])

    return final_clip

def main():
    if not SEL_FILE.exists() or not MANIFEST.exists():
        logging.error("Falta next_release.json o assets_manifest.json.")
        return None

    sel = json.loads(SEL_FILE.read_text(encoding="utf-8"))
    man = json.loads(MANIFEST.read_text(encoding="utf-8"))

    tmdb_id = sel.get("tmdb_id")
    title = sel.get("titulo")
    slug = slugify(title)

    poster_path = ROOT / man.get("poster", "")
    video_clips_paths = [ROOT / p for p in man.get("video_clips", []) if (ROOT / p).exists()]

    if not video_clips_paths:
        logging.error("No hay clips de video disponibles.")
        return None

    narracion, voice_path = separate_narration.main()
    if not voice_path or not os.path.exists(voice_path):
        logging.error("No se pudo obtener narración.")
        return None

    tmp_base = ROOT / 'temp'
    tmp_base.mkdir(exist_ok=True)
    tmp_dir = Path(tempfile.mkdtemp(dir=tmp_base, prefix=f"build_{tmdb_id}_"))

    opened_video_clips = []
    
    try:
        intro_clip = clip_from_img(poster_path, INTRO_DURATION)
        if intro_clip is None:
            logging.error("Fallo en intro clip.")
            return None

        video_clips_resized = []
        for clip_path in video_clips_paths[:MAX_BACKDROPS]:
            try:
                clip = VideoFileClip(str(clip_path))
                opened_video_clips.append(clip)
                
                sub_clip = clip.subclipped(0, CLIP_DURATION)
                # Esta llamada ahora usa la nueva lógica de recorte cuadrado
                resized_clip = resize_to_9_16(sub_clip)
                video_clips_resized.append(resized_clip)
            except Exception as e:
                logging.warning(f"Fallo en clip {clip_path}: {e}")

        if not video_clips_resized:
            logging.error("No se pudieron procesar clips de video.")
            return None

        final_video = concatenate_videoclips([intro_clip] + video_clips_resized, method="compose")

        audio_clip = AudioFileClip(str(voice_path))
        final_audio = audio_clip

        music_dir = ROOT / "assets" / "music"
        if music_dir.exists() and list(music_dir.glob("*.mp3")):
            music_files = list(music_dir.glob("*.mp3"))
            if music_files:
                music_path = random.choice(music_files)
                try:
                    music_clip = AudioFileClip(str(music_path))
                    
                    if music_clip.duration < final_video.duration:
                        repeats = int(final_video.duration / music_clip.duration) + 1
                        music_clip = concatenate_audioclips([music_clip] * repeats)
                    
                    music_clip = music_clip.subclipped(0, final_video.duration)
                    
                    audio_clip = audio_clip.with_effects([afx.AudioNormalize()])
                    music_clip = music_clip.with_effects([
                        afx.AudioNormalize(),
                        afx.AudioFadeIn(1.0),
                        afx.AudioFadeOut(1.0),
                        afx.MultiplyVolume(0.10)
                    ])
                    final_audio = CompositeAudioClip([audio_clip, music_clip])
                    
                    if final_audio.duration > final_video.duration:
                        final_audio = final_audio.subclipped(0, final_video.duration)
                    
                    logging.info(f"Música de fondo aleatoria añadida desde {music_path.name}.")
                except Exception as e:
                    logging.warning(f"No se pudo añadir música desde {music_path}: {e}")
        else:
            logging.info("No se encontraron archivos de música válidos. Solo narración.")
        
        if final_audio.duration < final_video.duration:
            silence_duration = final_video.duration - final_audio.duration
            silence_clip = AudioClip(lambda t: 0, duration=silence_duration, fps=final_audio.fps)
            final_audio = concatenate_audioclips([final_audio, silence_clip])
        
        final_clip = final_video.with_audio(final_audio)

        out_file = SHORTS_DIR / f"{tmdb_id}_{slug}_final.mp4"
        final_clip.write_videofile(
            str(out_file),
            codec="libx264",
            fps=60, 
            preset="slow", # Mejor calidad de compresión, aunque tarda más
            bitrate="50000k", # 50 Mbps para forzar la clasificación 4K
            ffmpeg_params=["-crf", "18", "-movflags", "faststart"] 
        )
        logging.info(f"🎬 Short generado en: {out_file}")

        intro_clip.close()
        final_clip.close()
        final_audio.close()
        
        for clip in opened_video_clips:
            clip.close()

        return str(out_file)

    except Exception as e:
        logging.error(f"Error en build_short: {e}", exc_info=True)
        for clip in opened_video_clips:
            clip.close()
        return None

    finally:
        cleanup_temp_files(tmp_dir)

def cleanup_temp_files(tmpdir):
    time.sleep(2)
    for file in os.listdir(tmpdir):
        file_path = os.path.join(tmpdir, file)
        if os.path.isfile(file_path):
            try:
                os.remove(file_path)
            except PermissionError as e:
                logging.warning(f"No se pudo eliminar {file} por bloqueo en {tmpdir}: {e}")
    logging.info(f"Archivos temporales en {tmpdir} eliminados (o intentados).")

if __name__ == "__main__":
    main()