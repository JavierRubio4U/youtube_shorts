# scripts/video_clips.py
import json, re, subprocess
import random
import logging
import math
import numpy as np
from pathlib import Path
from moviepy.editor import VideoFileClip, ImageClip, CompositeVideoClip, concatenate_videoclips, AudioFileClip
import moviepy.audio.fx.all as afx
from PIL import Image, ImageDraw, ImageFont

# --- Rutas y dirs ---
ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "output" / "state"
SHORTS_DIR = ROOT / "output" / "shorts"
TRAILER_DIR = ROOT / "assets" / "trailers"
MANIFEST = STATE / "assets_manifest.json"

# --- Parámetros globales ---
W, H = 1080, 1920
INTRO_DUR = 4.0
TARGET_SECONDS = 28.0
MUSIC_VOL = 0.28
FADE_IN, FADE_OUT = 0.6, 0.8


def _get_trailer_file(trailer_url: str, tmdb_id: str) -> Path | None:
    if not trailer_url:
        return None
    
    file_path = TRAILER_DIR / f"trailer_{tmdb_id}.mp4"
    if file_path.exists():
        logging.info(f"✅ Tráiler ya descargado: {file_path}")
        return file_path

    try:
        logging.info(f"⬇️ Descargando tráiler de YouTube: {trailer_url}")
        cmd = [
            "yt-dlp",
            "-f", "mp4",
            "-o", str(file_path),
            trailer_url
        ]
        subprocess.run(cmd, check=True, capture_output=True)
        if file_path.exists():
            logging.info(f"✅ Tráiler descargado con éxito: {file_path}")
            return file_path
    except Exception as e:
        logging.error(f"❌ Error al descargar el tráiler: {e}")
        return None

def _mix_audio_with_voice(video_clip, voice_audio_path: Path, music_path: Path | None,
                          music_vol: float = 0.15, fade_in: float = 0.6, fade_out: float = 0.8):
    dur_v = video_clip.duration
    tracks = []
    if voice_audio_path and voice_audio_path.exists():
        voice = AudioFileClip(str(voice_audio_path))
        tracks.append(voice)

    if music_path and music_path.exists():
        m = AudioFileClip(str(music_path))
        if m.duration < dur_v:
            m = afx.audio_loop(m, duration=dur_v)
        else:
            m = m.subclip(0, dur_v)
        m = m.audio_fadein(fade_in).audio_fadeout(fade_out).volumex(music_vol)
        tracks.append(m)

    if tracks:
        final_audio = CompositeAudioClip(tracks).subclip(0, dur_v)
        return video_clip.set_audio(final_audio)
    return video_clip
        
def create_video_with_clips(sel, ov_img: Image.Image, narracion_texto: str, voice_path: Path, title: str):
    man = json.loads(MANIFEST.read_text(encoding="utf-8"))
    tmdb_id = sel.get("tmdb_id", "unknown")
    slug = re.sub(r'[^a-zA-Z0-9]+', '-', title).strip('-')

    trailer_url = man.get("trailer_url")
    if not trailer_url:
        logging.warning("No se encontró URL de tráiler. No se pueden generar clips de vídeo.")
        return None
    
    trailer_path = _get_trailer_file(trailer_url, tmdb_id)
    if not trailer_path:
        return None
    
    try:
        full_trailer_clip = VideoFileClip(str(trailer_path))
    except Exception as e:
        logging.error(f"❌ Error al cargar el tráiler con MoviePy: {e}")
        return None
        
    audio_dur = AudioFileClip(str(voice_path)).duration if voice_path and voice_path.exists() else 0
    video_dur = audio_dur + INTRO_DUR
    video_dur = min(video_dur, TARGET_SECONDS)
    
    clip_dur = 2.0
    num_clips = math.floor((video_dur - INTRO_DUR) / clip_dur)
    
    if num_clips <= 0:
        logging.warning("Duración de narración muy corta para generar clips. Usando backdrops estáticos.")
        return None

    clips = []
    if full_trailer_clip.duration > clip_dur * num_clips:
        trailer_cuts = random.sample(range(0, int(full_trailer_clip.duration - clip_dur)), num_clips)
        for start_time in sorted(trailer_cuts):
            clip = full_trailer_clip.subclip(start_time, start_time + clip_dur)
            clips.append(clip.resize((W,H)))
    else:
        logging.warning("Tráiler demasiado corto para generar suficientes clips.")
        return None

    if clips:
        final_video_clip = concatenate_videoclips(clips)
        overlay_clip = ImageClip(np.array(ov_img)).set_duration(final_video_clip.duration)
        final_video_clip = CompositeVideoClip([final_video_clip, overlay_clip])
        return final_video_clip
    
    return None