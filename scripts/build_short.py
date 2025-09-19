# scripts/build_short.py
import time
import unicodedata
import random  # Para música aleatoria
from pathlib import Path
import json, os, logging, re
from PIL import Image
import numpy as np
#from moviepy import VideoFileClip, concatenate_videoclips, ImageClip, CompositeVideoClip, AudioFileClip, concatenate_audioclips  # Imports actualizados para v2.2.1
from moviepy.audio.AudioClip import CompositeAudioClip, AudioClip  # Para mezcla y silencio
import moviepy.audio.fx as afx
import separate_narration  # Nuevo script para narración separada
import tempfile
import shutil
from slugify import slugify
from moviepy.editor import (VideoFileClip, ImageClip, AudioFileClip, AudioClip,
                            CompositeVideoClip, CompositeAudioClip,
                            concatenate_videoclips, concatenate_audioclips, afx)

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

def clip_from_img(path: Path, dur: float) -> ImageClip:
    """Crea un clip de video a partir de una imagen con duración dada."""
    try:
        img_clip = ImageClip(str(path), duration=dur)
        img_w, img_h = img_clip.size
        target_ratio = W / H
        current_ratio = img_w / img_h
        if current_ratio > target_ratio:
            # Landscape: Escalar por alto, crop lateral
            img_clip = img_clip.resize(height=H)  # Corrige a resize
            left_crop = (img_clip.w - W) // 2
            img_clip = img_clip.crop(x1=left_crop, x2=img_clip.w - left_crop)
        else:
            # Portrait: Escalar por ancho, crop vertical
            img_clip = img_clip.resize(width=W)  # Corrige a resize
            top_crop = (img_clip.h - H) // 2
            img_clip = img_clip.crop(y1=top_crop, y2=clip.h - top_crop)
        return img_clip
    except Exception as e:
        logging.error(f"Error al cargar imagen {path}: {e}")
        return None

def resize_to_9_16(clip):
    logging.debug(f"Redimensionando clip: tamaño original {clip.w}x{clip.h}, ratio {clip.w / clip.h}")
    target_ratio = 9 / 16
    current_ratio = clip.w / clip.h
   
    # Escalar al máximo sin distorsión y crop (tratamiento único para todos)
    if current_ratio > target_ratio: # Landscape: escalar por alto, crop lateral
        clip = clip.resize(height=H)
        left_crop = (clip.w - W) // 2
        logging.debug(f"Crop lateral: offset {left_crop}, nuevo ancho {W}")
        clip = clip.crop(x1=left_crop, x2=clip.w - left_crop)
    else: # Portrait: escalar por ancho, crop vertical
        clip = clip.resize(width=W)
        top_crop = (clip.h - H) // 2
        logging.debug(f"Crop vertical: offset {top_crop}, nuevo alto {H}")
        clip = clip.crop(y1=top_crop, y2=clip.h - top_crop)
   
    logging.debug(f"Tamaño final: {clip.w}x{clip.h}")
    return clip

def main():
    if not SEL_FILE.exists() or not MANIFEST.exists():
        logging.error("Falta next_release.json o assets_manifest.json.")
        return None

    sel = json.loads(SEL_FILE.read_text(encoding="utf-8"))
    man = json.loads(MANIFEST.read_text(encoding="utf-8"))

    tmdb_id = sel.get("tmdb_id")
    title = sel.get("titulo")
    slug = slugify(title)  # Ajustado para llamar correctamente

    poster_path = ROOT / man.get("poster", "")
    video_clips = [ROOT / p for p in man.get("video_clips", []) if (ROOT / p).exists()]

    if not video_clips:
        logging.error("No hay clips de video disponibles.")
        return None

    # Llamar a narración separada para ahorrar recursos
    narracion, voice_path = separate_narration.main()
    if not voice_path or not os.path.exists(voice_path):
        logging.error("No se pudo obtener narración.")
        return None

    # Crear tmpdir de forma segura y centralizada
    tmp_base = ROOT / 'temp'
    tmp_base.mkdir(exist_ok=True)
    tmp_dir = Path(tempfile.mkdtemp(dir=tmp_base, prefix=f"build_{tmdb_id}_"))

    try:
        # Intro con póster
        intro_clip = clip_from_img(poster_path, INTRO_DURATION)
        if intro_clip is None:
            logging.error("Fallo en intro clip.")
            return None

        # Clips de video (redimensionados)
        video_clips_resized = []
        is_high_res = man.get("trailer_resolution", "").startswith("alta") or "2160" in man.get("trailer_resolution", "")
        for clip_path in video_clips[:MAX_BACKDROPS]:
            try:
                clip = VideoFileClip(str(clip_path)).subclip(0, CLIP_DURATION)
                resized_clip = resize_to_9_16(clip)
                video_clips_resized.append(resized_clip)
                clip.close()
            except Exception as e:
                logging.warning(f"Fallo en clip {clip_path}: {e}")

        if not video_clips_resized:
            logging.error("No se pudieron procesar clips de video.")
            return None

        # Concatenar video
        final_video = concatenate_videoclips([intro_clip] + video_clips_resized, method="compose")

        # Audio: Narración + música opcional
        audio_clip = AudioFileClip(str(voice_path))
        final_audio = audio_clip

        # Música de fondo aleatoria (opcional)
        music_dir = ROOT / "assets" / "music"
        if music_dir.exists() and list(music_dir.glob("*.mp3")):
            music_files = list(music_dir.glob("*.mp3"))
            if music_files:
                music_path = random.choice(music_files)
                logging.debug(f"Archivo de música seleccionado: {music_path.name}")
                try:
                    music_clip = AudioFileClip(str(music_path))
                    logging.debug(f"Música cargada: duración original {music_clip.duration}s")
                    
                    if music_clip.duration < final_video.duration:
                        repeats = int(final_video.duration / music_clip.duration) + 1
                        music_clip = concatenate_audioclips([music_clip] * repeats).subclip(0, final_video.duration)
                    else:
                        music_clip = music_clip.subclip(0, final_video.duration)  
                    logging.debug(f"Música ajustada a {final_video.duration}s")
                    
                    # Normalize audio and music
                    audio_clip = audio_clip.fx(afx.audio_normalize)
                    music_clip = music_clip.fx(afx.audio_normalize).fx(afx.audio_fadein, 1.0).fx(afx.audio_fadeout, 1.0).fx(afx.volumex, 0.10)
                    final_audio = CompositeAudioClip([audio_clip, music_clip])
                    logging.debug(f"Mezcla de audio completada: duración {final_audio.duration}s")
                    
                    if final_audio.duration > final_video.duration:
                        final_audio = final_audio.subclip(0, final_video.duration)  
                        logging.info(f"Audio final recortado a {final_video.duration}s para fit exacto.")
                    
                    logging.info(f"Música de fondo aleatoria añadida desde {music_path.name}.")
                except Exception as e:
                    logging.warning(f"No se pudo añadir música desde {music_path}: {e}")
        else:
            logging.info("No se encontraron archivos de música válidos. Solo narración.")
        
        if final_audio.duration < final_video.duration:
            silence_duration = final_video.duration - final_audio.duration
            silence_clip = AudioClip(lambda t: 0, duration=silence_duration, fps=final_audio.fps)
            final_audio = concatenate_audioclips([final_audio, silence_clip])
            logging.debug(f"Audio extendido con silencio a {final_video.duration}s")
        
        final_clip = final_video.set_audio(final_audio)
        logging.debug(f"Audio final aplicado al video: duración {final_clip.duration}s")

        out_file = SHORTS_DIR / f"{tmdb_id}_{slug}_final.mp4"
        final_clip.write_videofile(
            str(out_file), fps=60,
            codec="libx264", preset="veryslow",
            bitrate="20000k",
            ffmpeg_params=["-crf", "18"]
        )
        logging.info(f"🎬 Short generado en: {out_file}")

        # Cerrar clips para liberar memoria
        intro_clip.close()
        for rc in video_clips_resized:
            rc.close()
        final_clip.close()
        final_audio.close()

        return str(out_file)

    except Exception as e:
        logging.error(f"Error en build_short: {e}")
        return None

    finally:
        # Cleanup temporal al final, después de liberar memorias
        cleanup_temp_files(tmp_dir)

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

if __name__ == "__main__":
    main()