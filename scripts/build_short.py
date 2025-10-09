# scripts/build_short.py
import time
import unicodedata
import random
from pathlib import Path
import json, os, logging, re
from PIL import Image
import numpy as np

import ai_narration  # CAMBIO: Importamos el script de narración con Gemini
import tempfile
import shutil
from slugify import slugify
# Imports de moviepy
from moviepy import (VideoFileClip, ImageClip, AudioFileClip, AudioClip,
                     CompositeVideoClip, ColorClip,
                     CompositeAudioClip, concatenate_videoclips, concatenate_audioclips)
import moviepy.audio.fx as afx

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "output" / "state"
SHORTS_DIR = ROOT / "output" / "shorts"
MANIFEST = STATE / "assets_manifest.json"
SEL_FILE = STATE / "next_release.json"

W, H = 2160, 3840  # Volvemos a resolución alta para mejor calidad (como en versión antigua)
SQUARE_SIZE = 2160
INTRO_DURATION = 4
CLIP_DURATION = 6
MAX_CLIPS_TO_USE = 4  # Renombrado de MAX_BACKDROPS para claridad

def clip_from_img(path: Path, dur: float) -> ImageClip:
    """Crea un clip de video a partir de una imagen con duración dada."""
    try:
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
    Recorta el centro del clip a un formato cuadrado y lo coloca en un fondo vertical 9:16.
    """
    logging.info(f"Dimensiones originales del clip: {clip.size[0]}x{clip.size[1]}")
    
    # v2 FIX: Usa .cropped() en lugar de .crop()
    square_clip_intermediate = clip.cropped(x_center=clip.w / 2, width=clip.h)
    square_clip = square_clip_intermediate.resized((SQUARE_SIZE, SQUARE_SIZE))

    logging.info(f"Dimensiones tras recorte y reescalado a cuadrado: {square_clip.size[0]}x{square_clip.size[1]}")
    
    background = ColorClip(size=(W, H), color=(0, 0, 0), duration=clip.duration)

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

    if not poster_path.exists():
        logging.error("No se encontró el archivo del póster principal.")
        return None
        
    if not video_clips_paths:
        logging.error("No hay clips de video disponibles.")
        return None

    # CAMBIO: Llamamos directamente a la función main de nuestro script de narración con Gemini.
    narracion, voice_path = ai_narration.main()
    if not voice_path or not os.path.exists(voice_path):
        logging.error("No se pudo obtener narración.")
        return None

    tmp_base = ROOT / 'temp'
    tmp_base.mkdir(exist_ok=True)
    tmp_dir = Path(tempfile.mkdtemp(dir=tmp_base, prefix=f"build_{tmdb_id}_"))

    opened_video_clips = []
    
    try:
        logging.info(f"Creando clip de introducción de {INTRO_DURATION}s con el póster...")
        intro_clip = clip_from_img(poster_path, INTRO_DURATION)
        if intro_clip is None:
            logging.error("Fallo al crear el clip de introducción.")
            return None

        logging.info(f"Procesando {len(video_clips_paths[:MAX_CLIPS_TO_USE])} clips de vídeo...")
        video_clips_resized = []
        for i, clip_path in enumerate(video_clips_paths[:MAX_CLIPS_TO_USE]):
            try:
                clip = VideoFileClip(str(clip_path))
                opened_video_clips.append(clip)
                
                # v2 FIX: Usa .subclipped() para consistencia
                end_time = min(CLIP_DURATION, clip.duration)
                sub_clip = clip.subclipped(0, end_time)
                
                logging.info(f"  - Clip {i+1}: Redimensionando a 9:16... (duración: {sub_clip.duration:.2f}s)")
                resized_clip = resize_to_9_16(sub_clip)
                video_clips_resized.append(resized_clip)
            except Exception as e:
                logging.warning(f"Fallo en clip {clip_path}: {e}")

        if not video_clips_resized:
            logging.error("No se pudieron procesar clips de video.")
            return None

        logging.info("Concatenando clips de vídeo para la secuencia final...")
        final_video = concatenate_videoclips([intro_clip] + video_clips_resized, method="compose")

        logging.info("Preparando pista de audio...")
        audio_clip = AudioFileClip(str(voice_path))
        final_audio = audio_clip

        music_dir = ROOT / "assets" / "music"
        if music_dir.exists() and list(music_dir.glob("*.mp3")):
            music_files = list(music_dir.glob("*.mp3"))
            if music_files:
                music_path = random.choice(music_files)
                try:
                    logging.info(f"Añadiendo música de fondo desde '{music_path.name}'...")
                    music_clip = AudioFileClip(str(music_path))
                    
                    # FIX: Usa concatenate_audioclips para loop (compatible con v1/v2, evita issues en .loop())
                    if music_clip.duration < final_video.duration:
                        repeats = int(final_video.duration / music_clip.duration) + 1
                        music_clip = concatenate_audioclips([music_clip] * repeats)
                    
                    # v2 FIX: Usa .subclipped()
                    music_clip = music_clip.subclipped(0, final_video.duration)
                    
                    # FIX: Usa .with_effects() como en versión antigua (más estable en v2 para chains)
                    audio_clip = audio_clip.with_effects([afx.AudioNormalize()])
                    music_clip = music_clip.with_effects([
                        afx.AudioNormalize(),
                        afx.AudioFadeIn(1.0),
                        afx.AudioFadeOut(1.0),
                        afx.MultiplyVolume(0.10)
                    ])
                    final_audio = CompositeAudioClip([audio_clip, music_clip])
                    
                    # v2 FIX: Usa .subclipped()
                    if final_audio.duration > final_video.duration:
                        final_audio = final_audio.subclipped(0, final_video.duration)
                    
                    logging.info(f"Música de fondo aleatoria añadida desde {music_path.name}.")
                except Exception as e:
                    logging.warning(f"No se pudo añadir música desde {music_path}: {e}")
        else:
            logging.info("No se encontraron archivos de música válidos. Solo narración.")
        
        # FIX: Manejo de silencio con AudioClip lambda (compatible)
        if final_audio.duration < final_video.duration:
            silence_duration = final_video.duration - final_audio.duration
            silence_clip = AudioClip(lambda t: [0, 0], duration=silence_duration, fps=final_audio.fps)
            final_audio = concatenate_audioclips([final_audio, silence_clip])
        
        final_clip = final_video.with_audio(final_audio)

        out_file = SHORTS_DIR / f"{tmdb_id}_{slug}_final.mp4"
        
        logging.info(f"Renderizando vídeo final en '{out_file.name}' (este paso puede tardar)...")
        final_clip.write_videofile(
            str(out_file),
            codec="libx264",
            fps=60,  # Volvemos a 60fps como en antigua para fluidez
            preset="fast",
            bitrate="50000k",  # Alto bitrate para calidad
            ffmpeg_params=["-crf", "18", "-movflags", "faststart"],
            logger=None
        )
        logging.info(f"✅ Short generado con éxito.")

        manifest_path = STATE / "assets_manifest.json"
        if manifest_path.exists():
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["mp4_path"] = str(out_file.relative_to(ROOT))  # Guarda path relativo
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
            logging.info(f"Path del MP4 guardado en manifiesto: {out_file}")

        return str(out_file)

    except Exception as e:
        logging.error(f"Error en build_short: {e}", exc_info=True)
        return None

    finally:
        # Cierre de clips de vídeo para liberar memoria
        for clip in opened_video_clips:
            try:
                clip.close()
            except Exception:
                pass  # Ignorar errores al cerrar
        # Limpieza de archivos temporales
        cleanup_temp_files(tmp_dir)

def cleanup_temp_files(tmpdir):
    time.sleep(2)
    # CAMBIO: Usamos shutil.rmtree para un borrado más robusto del directorio temporal
    try:
        shutil.rmtree(tmpdir)
        logging.info(f"Directorio temporal {tmpdir} eliminado con éxito.")
    except Exception as e:
        logging.warning(f"No se pudo eliminar el directorio temporal {tmpdir}: {e}")

if __name__ == "__main__":
    main()