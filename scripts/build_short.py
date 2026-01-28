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
from datetime import datetime

#logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "output" / "state"
SHORTS_DIR = ROOT / "output" / "shorts"
MANIFEST = STATE / "assets_manifest.json"
TMP_DIR = ROOT / "assets" / "tmp"
TMP_DIR.mkdir(parents=True, exist_ok=True)
SEL_FILE = TMP_DIR / "next_release.json"

# W, H = 1080, 1920  <- Ahora se calculan dinámicamente en main()
INTRO_DURATION = 4
CLIP_DURATION = 6
MAX_CLIPS_TO_USE = 4  # Renombrado de MAX_BACKDROPS para claridad
FPS_TARGET = 30 # Estándar para evitar cuelgues y asegurar compatibilidad

def clip_from_img(path: Path, dur: float, w: int, h: int, fps: float = FPS_TARGET) -> ImageClip:
    """Crea un clip de video a partir de una imagen con duración dada."""
    try:
        img_clip = ImageClip(str(path), duration=dur).with_fps(fps)
        img_w, img_h = img_clip.size
        target_ratio = w / h
        current_ratio = img_w / img_h
        if current_ratio > target_ratio:
            img_clip = img_clip.resized(height=h)
            left_crop = (img_clip.w - w) // 2
            img_clip = img_clip.cropped(x1=left_crop, x2=img_clip.w - left_crop)
        else:
            img_clip = img_clip.resized(width=w)
            top_crop = (img_clip.h - h) // 2
            img_clip = img_clip.cropped(y1=top_crop, y2=img_clip.h - top_crop)
        return img_clip
    except Exception as e:
        logging.error(f"Error al cargar imagen {path}: {e}")
        return None

def resize_to_9_16(clip: VideoFileClip, target_w: int, target_h: int, square_size: int, fps: float = 30) -> VideoFileClip:
    """
    Recorta el centro del clip a un formato cuadrado y lo coloca en un fondo vertical 9:16.
    """
    logging.info(f"Dimensiones originales del clip: {clip.w}x{clip.h}")
    
    # Aseguramos que el ancho del recorte no sea mayor que el ancho real del vídeo
    crop_width = min(clip.w, clip.h)
    
    # Forzamos que las dimensiones sean pares para evitar problemas de códec
    if crop_width % 2 != 0:
        crop_width -= 1

    # Recorte central cuadrado
    square_clip = clip.cropped(x_center=clip.w / 2, y_center=clip.h / 2, width=crop_width, height=crop_width)
    
    # Redimensionado al tamaño cuadrado proporcional al destino
    square_clip = square_clip.resized(width=square_size)

    logging.info(f"Dimensiones tras recorte y reescalado a cuadrado: {square_clip.w}x{square_clip.h}")
    
    background = ColorClip(size=(target_w, target_h), color=(0, 0, 0), duration=clip.duration).with_fps(fps)

    final_clip = CompositeVideoClip([
        background,
        square_clip.with_position("center")
    ]).with_duration(clip.duration).with_fps(fps)

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
    # Usamos un estándar de 30 FPS para evitar problemas de sincronización y cuelgues
    trailer_fps = FPS_TARGET 
    
    # Dinamismo: Intentamos usar la resolución original del tráiler pero en vertical
    # Si el tráiler es 4K (3840x2160), el short será 2160x3840.
    orig_w = man.get("trailer_w", 1920)
    orig_h = man.get("trailer_h", 1080)
    
    # El ancho del Short es la altura del tráiler original (para hacerlo vertical)
    target_w = min(orig_w, orig_h) 
    # El alto del Short mantiene la proporción 9:16 basada en ese ancho
    target_h = int(target_w * 16 / 9)
    
    # Aseguramos que sean pares
    if target_w % 2 != 0: target_w -= 1
    if target_h % 2 != 0: target_h -= 1
    
    square_size = target_w # El cuadrado central ocupa todo el ancho

    if not poster_path.exists():
        logging.error("No se encontró el archivo del póster principal.")
        return None
        
    if not video_clips_paths:
        logging.error("No hay clips de video disponibles.")
        return None

    # Información mostrada detalladamente en ai_narration.py (PRE-RENDER)

    # CAMBIO: Llamamos directamente a la función main de nuestro script de narración con Gemini.
    narracion, voice_path = ai_narration.main()
    if not voice_path or not os.path.exists(voice_path):
        logging.error("No se pudo obtener narración.")
        return None

    # Guardar el guión en el JSON para poder mostrarlo después
    if narracion:
        sel['guion_generado'] = narracion
        SEL_FILE.write_text(json.dumps(sel, ensure_ascii=False, indent=2), encoding="utf-8")

    tmp_base = ROOT / 'temp'
    tmp_base.mkdir(exist_ok=True)
    tmp_dir = Path(tempfile.mkdtemp(dir=tmp_base, prefix=f"build_{tmdb_id}_"))

    opened_video_clips = []
    
    try:
        logging.info(f"Creando clip de introducción de {INTRO_DURATION}s con el póster...")
        intro_clip = clip_from_img(poster_path, INTRO_DURATION, target_w, target_h, fps=trailer_fps)
        if intro_clip is None:
            logging.error("Fallo al crear el clip de introducción.")
            return None

        logging.info(f"Procesando {len(video_clips_paths[:MAX_CLIPS_TO_USE])} clips de vídeo...")
        video_clips_resized = []
        for i, clip_path in enumerate(video_clips_paths[:MAX_CLIPS_TO_USE]):
            try:
                clip = VideoFileClip(str(clip_path)).with_fps(trailer_fps)
                opened_video_clips.append(clip)
                
                # VOLVEMOS A DURACIÓN FIJA
                end_time = min(CLIP_DURATION, clip.duration)
                sub_clip = clip.subclipped(0, end_time)
                
                logging.info(f"  - Clip {i+1}: Redimensionando a 9:16... (duración: {sub_clip.duration:.2f}s)")
                resized_clip = resize_to_9_16(sub_clip, target_w, target_h, square_size, fps=trailer_fps)
                video_clips_resized.append(resized_clip)
            except Exception as e:
                logging.warning(f"Fallo en clip {clip_path}: {e}")

        if not video_clips_resized:
            logging.error("No se pudieron procesar clips de video.")
            return None

        logging.info("Concatenando clips de vídeo para la secuencia final...")
        # CAMBIO: Usamos method="chain" para evitar frames negros entre clips
        final_video = concatenate_videoclips([intro_clip] + video_clips_resized, method="chain")

        logging.info("Preparando pista de audio...")
        raw_voice = AudioFileClip(str(voice_path))
        
        silence_padding = AudioClip(lambda t: [0, 0], duration=1.0, fps=44100)
        audio_clip = concatenate_audioclips([silence_padding, raw_voice])
        
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
                        afx.MultiplyVolume(0.07)
                    ])
                    final_audio = CompositeAudioClip([audio_clip, music_clip])
                    
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

        # --- PREVIEW GENERATION REMOVED ---
        logging.info("ℹ️ Vista previa rápida omitida.")
        
        # Define una ruta para el audio temporal dentro de tmp_dir
        temp_audio_path = tmp_dir / "temp_audio_mix.mp3" 

        # Ajuste de bitrate dinámico: ~8Mbps para 1080p, ~35Mbps para 4K
        bitrate_calc = "35000k" if target_w >= 2000 else "12000k"

        logging.info(f"Renderizando vídeo final ({target_w}x{target_h}) en '{out_file.name}'...")
        final_clip.write_videofile(
            str(out_file),
            codec="libx264",
            fps=trailer_fps,
            preset="medium",
            bitrate=bitrate_calc,
            ffmpeg_params=["-crf", "18", "-pix_fmt", "yuv420p", "-movflags", "faststart"],
            temp_audiofile=str(temp_audio_path), 
            remove_temp=True 
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