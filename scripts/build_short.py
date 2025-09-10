# scripts/build_short.py
import json, re, unicodedata, subprocess
import random
from pathlib import Path
import logging

from PIL import Image, ImageDraw, ImageFont
from moviepy.editor import (
    ImageClip, concatenate_videoclips, AudioFileClip, CompositeAudioClip
)
import moviepy.audio.fx.all as afx
import math

# --- Compatibilidad Pillow >=10 con MoviePy 1.0.3 ---
from PIL import Image
if not hasattr(Image, "ANTIALIAS"):
    Image.ANTIALIAS = Image.Resampling.LANCZOS

# --- Rutas y dirs ---
ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "output" / "state"
STATE.mkdir(parents=True, exist_ok=True)
SHORTS_DIR = ROOT / "output" / "shorts"
SHORTS_DIR.mkdir(parents=True, exist_ok=True)

MUSIC_DIR = (ROOT / "assets" / "music")
FONTS_DIR = (ROOT / "assets" / "fonts")

# Parámetros generales
MUSIC_VOL = 0.28
FADE_IN  = 0.6
FADE_OUT = 0.8

SEL_FILE = STATE / "next_release.json"
MANIFEST = STATE / "assets_manifest.json"

W, H = 1080, 1920

# Duraciones
INTRO_DUR = 4.0
TARGET_SECONDS = 28.0
MAX_BACKDROPS = 9

# Bandas negras
TOP_MARGIN = 420
BOTTOM_MARGIN = 420

# Posiciones relativas
TITLE_POS = 0.40
DATE_POS  = 0.40
LINE_SPACING = 10

HOOK_MAX_CHARS = 90


import ollama
from langdetect import detect, DetectorFactory

# Para resultados consistentes
DetectorFactory.seed = 0

def _translate_with_ai(text: str, model='mistral') -> str | None:
    """Traduce un texto usando un modelo local de Ollama."""
    try:
        prompt = f"""Traduce el siguiente texto al español de forma natural, sin añadir ninguna explicación adicional:
        {text}
        """
        response = ollama.chat(model=model, messages=[
            {'role': 'user', 'content': prompt}
        ])
        
        # Elimina cualquier texto de traducción automática
        translated_text = response['message']['content'].strip()
        translated_text = re.sub(r'\(.*?\)', '', translated_text)
        return translated_text.strip()
    except Exception as e:
        print(f"❌ Error al traducir el título con Ollama: {e}")
        return None

def _generate_narracion_with_ai(sel: dict, model='mistral') -> str | None:
    """Genera una sinopsis larga y limpia con Ollama."""
    try:
        prompt = f"""
        Genera una sinopsis detallada y atractiva de 70-80 palabras en español para la siguiente película.
        La sinopsis debe ser un párrafo cohesivo y no debe listar el título, los géneros, el reparto ni ninguna otra metadata.
        Utiliza la siguiente información para inspirarte:
        
        Título: {sel.get("titulo")}
        Sinopsis original: {sel.get("sinopsis")}
        Géneros: {', '.join(sel.get("generos"))}
        Palabras clave: {', '.join(sel.get("keywords"))}
        """
        response = ollama.chat(model=model, messages=[
            {'role': 'user', 'content': prompt}
        ])
        narracion = response['message']['content']
        # Limpieza adicional para eliminar posibles metadatos
        narracion = re.sub(r'(Título|Géneros|Reparto|Sinopsis original):.*$', '', narracion, flags=re.MULTILINE).strip()
        narracion = re.sub(r'[^\w\s\¿\¡\?\!\,\.\-\:\;«»"]', '', narracion)
        return _normalize_text(narracion)

    except Exception as e:
        print(f"❌ Error al generar sinopsis con Ollama: {e}")
        return None

def _generate_hook_with_ai(sel: dict, model='mistral') -> str | None:
    """Genera un gancho corto con Ollama."""
    try:
        prompt = f"""
        Basándote en el título y la sinopsis de la película, crea un gancho corto y llamativo para un short de YouTube.
        El gancho debe ser una única frase impactante de no más de 15 palabras.
        No incluyas el título de la película.
        
        Información de la película:
        - Título: {sel.get('titulo')}
        - Sinopsis: {sel.get('sinopsis')}
        - Géneros: {', '.join(sel.get('generos'))}
        """
        response = ollama.chat(model=model, messages=[
            {'role': 'user', 'content': prompt}
        ])
        hook = response['message']['content'].strip()
        
        # Limpiamos el texto de posibles iconos o metadatos
        hook = re.sub(r'[^\w\s\¿\¡\?\!\,\.\-\:\;«»"]', '', hook)
        hook = re.sub(r'\s+', ' ', hook).strip()

        return hook

    except Exception as e:
        print(f"❌ Error al generar gancho con Ollama: {e}")
        return f"¡No te la puedes perder!"


# ------------------ UTILIDADES TEXTO ------------------
def slugify(text: str, maxlen: int = 60) -> str:
    s = (text or "").lower().strip()
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"[\s_-]+", "-", s).strip("-")
    return (s or "title")[:maxlen]

def load_fonts():
    # NUEVO: intenta cargar una fuente personalizada
    try:
        title_font_path = FONTS_DIR / "BebasNeue-Bold.ttf"
        if title_font_path.exists():
            title_font = ImageFont.truetype(str(title_font_path), 58)
        else:
            print("⚠ Fuente BebasNeue-Bold.ttf no encontrada. Usando fuente por defecto.")
            title_font = ImageFont.load_default()
        
        small_font_path = FONTS_DIR / "Arial.ttf" # o cualquier otra que prefieras
        if small_font_path.exists():
            small_font = ImageFont.truetype(str(small_font_path), 40)
        else:
            small_font = ImageFont.load_default()
            
    except Exception as e:
        print(f"⚠ Error cargando fuentes: {e}. Usando fuentes por defecto.")
        title_font = ImageFont.load_default()
        small_font = ImageFont.load_default()
        
    return title_font, small_font

def wrap_fit(draw, text, font, max_width):
    if not text: return ""
    txt = text.strip()
    while draw.textlength(txt, font=font) > max_width and len(txt) > 4:
        txt = txt[:-1]
    if draw.textlength(txt, font=font) > max_width:
        txt = txt[: max(0, len(txt)-3)] + "..."
    return txt

def wrap_lines(draw, text, font, max_width, max_lines=2, line_spacing=10):
    if not text:
        return [], 0
    words = text.strip().split()
    lines, cur = [], ""
    for w in words:
        trial = (cur + " " + w).strip()
        if draw.textlength(trial, font=font) <= max_width:
            cur = trial
        else:
            if cur: lines.append(cur)
            cur = w
        if len(lines) == max_lines:
            break
    if len(lines) < max_lines and cur:
        lines.append(cur)
    leftover = len(words) > sum(len(l.split()) for l in lines)
    if leftover and lines:
        last = lines[-1]
        while draw.textlength(last + "…", font=font) > max_width and len(last) > 1:
            last = last[:-1]
        lines[-1] = last + "…"
    h = 0
    for i, ln in enumerate(lines):
        _, _, _, bh = draw.textbbox((0,0), ln, font=font)
        h += bh
        if i < len(lines)-1:
            h += line_spacing
    return lines, h

def _first_clause(text: str) -> str:
    if not text:
        return ""
    s = re.sub(r"\s+", " ", text).strip()
    for sep in [". ", "…", "!", "¡", "?", "¿", ";", ":"]:
        if sep in s:
            s = s.split(sep)[0]
            break
    return s

def _truncate_ellipsis(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    t = text[:max_chars-1].rstrip()
    sp = t.rfind(" ")
    if sp > max_chars * 0.6:
        t = t[:sp]
    return t + "…"

def _normalize_text(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())

def _sentences(s: str):
    return [seg.strip() for seg in re.split(r"(?<=[\.\!\?…])\s+", s) if seg.strip()]

def _trim_to_words(text: str, max_words: int) -> str:
    words = text.split()
    if len(words) <= max_words:
        return text
    t = " ".join(words[:max_words])
    return t.rstrip(",;:") + "…"


# ------------------ OVERLAY ------------------
def make_overlay(title, fecha, hook=None):
    title_f, small_f = load_fonts()
    ov = Image.new("RGBA", (W, H), (0,0,0,0))
    d = ImageDraw.Draw(ov)

    # Dibuja los fondos semi-transparentes
    d.rectangle([0, 0, W, TOP_MARGIN], fill=(0,0,0,220))
    d.rectangle([0, H-BOTTOM_MARGIN, W, H], fill=(0,0,0,220))

    # Función para dibujar texto con contorno
    def draw_outlined_text(pos, text, font, fill_color, outline_color=(0, 0, 0, 255), outline_width=2):
        x, y = pos
        # Dibuja el contorno
        for dx, dy in [(-outline_width, 0), (outline_width, 0), (0, -outline_width), (0, outline_width)]:
            d.text((x + dx, y + dy), text, font=font, fill=outline_color)
        # Dibuja el texto principal
        d.text(pos, text, font=font, fill=fill_color)

    # Texto del hook
    hook_lines, hook_h = wrap_lines(d, hook or "", small_f, int(W*0.94))
    y_hook = H - BOTTOM_MARGIN + int(BOTTOM_MARGIN*0.5) - hook_h//2
    for ln in hook_lines:
        lw = d.textlength(ln, font=small_f)
        draw_outlined_text(((W-lw)//2, y_hook), ln, small_f, (255,255,255,255))
        _, _, _, bh = d.textbbox((0,0), ln, font=small_f)
        y_hook += bh + LINE_SPACING

    # Título
    t_lines, t_h = wrap_lines(d, title or "", title_f, int(W*0.94))
    y_title_center = int(TOP_MARGIN * TITLE_POS)
    y = y_title_center - t_h//2
    for ln in t_lines:
        lw = d.textlength(ln, font=title_f)
        draw_outlined_text(((W-lw)//2, y), ln, title_f, (255,255,0,255))
        _, _, _, bh = d.textbbox((0,0), ln, font=title_f)
        y += bh + LINE_SPACING

    # Fecha
    f_txt = f"Estreno en España: {fecha}" if fecha else ""
    f_txt = wrap_fit(d, f_txt, small_f, int(W*0.94))
    fw = d.textlength(f_txt, font=small_f)
    y_date = H - BOTTOM_MARGIN + int(BOTTOM_MARGIN*0.85) - small_f.size//2
    draw_outlined_text(((W-fw)//2, y_date), f_txt, small_f, (255,255,0,255)) # Cambio aquí

    return ov


# ------------------ CLIPS y AUDIO ------------------
def clip_from_img(path: Path, dur: float) -> ImageClip:
    return ImageClip(str(path)).set_duration(dur).resize((W,H))

def pick_music():
    if not MUSIC_DIR.exists(): return None
    files = sorted(MUSIC_DIR.glob("*.mp3"))
    return random.choice(files) if files else None

def _clean_for_tts(text: str) -> str:
    if not text: return ""
    text = re.sub(r"http[s]?://\S+", "", text)
    text = re.sub(r"\s+", " ", text).replace("—","-").replace("–","-")
    text = re.sub(r"[^A-Za-zÁÉÍÓÚÜÑáéíóúüñ0-9 ,\.\-\!\?\:\;\'\"]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()[:900]

def _retime_wav_ffmpeg(in_wav: Path, out_wav: Path, atempo: float = 0.92) -> bool:
    try:
        cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
               "-i", str(in_wav), "-filter:a", f"atempo={atempo}", str(out_wav)]
        res = subprocess.run(cmd, check=True, capture_output=True)
        return res.returncode == 0 and out_wav.exists() and out_wav.stat().st_size > 0
    except Exception as e:
        logging.error(f"Error en _retime_wav_ffmpeg: {e}")
        return False

def _concat_wav_ffmpeg(inputs: list[Path], out_wav: Path) -> bool:
    if not inputs: return False
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
    for s in inputs: cmd += ["-i", str(s)]
    n = len(inputs)
    filt = "".join(f"[{i}:a]" for i in range(n)) + f"concat=n={n}:v=0:a=1[outa]"
    cmd += ["-filter_complex", filt, "-map", "[outa]", str(out_wav)]
    res = subprocess.run(cmd, check=True, capture_output=True)
    return res.returncode == 0 and out_wav.exists() and out_wav.stat().st_size > 0

def _synthesize_tts_coqui(text: str, out_wav: Path) -> Path | None:
    try:
        from TTS.api import TTS
    except ImportError:
        logging.error("Coqui TTS no está instalado. No se generará audio de voz.")
        return None
    try:
        cleaned_text = _clean_for_tts(text)
        if not cleaned_text:
            return None
        tts = TTS(model_name="tts_models/es/css10/vits", progress_bar=False, gpu=False)
        tts.tts_to_file(text=cleaned_text, file_path=str(out_wav), language="es")
        if out_wav.exists() and out_wav.stat().st_size > 0:
            return out_wav
    except Exception as e:
        logging.error(f"Error en la síntesis de voz con VITS: {e}")
    return None


def _synthesize_xtts_with_pauses(text: str, out_wav: Path) -> Path | None:
    try:
        from TTS.api import TTS
    except ImportError:
        logging.error("Coqui TTS no está instalado. No se generará audio de voz.")
        return None

    cleaned_text = _clean_for_tts(text)
    if not cleaned_text:
        return None
    
    sents = _sentences(cleaned_text) or [cleaned_text]
    
    try:
        tts = TTS(model_name="tts_models/multilingual/multi-dataset/xtts_v2", progress_bar=False, gpu=False)
        
        tmp_parts = []
        for i, s in enumerate(sents, 1):
            part_path = STATE / f"_xtts_part_{i}.wav"
            tts.tts_to_file(text=s, file_path=str(part_path), language="es", speaker="Alma María")
            if not part_path.exists(): raise FileNotFoundError
            tmp_parts.append(part_path)
        
        silence_path = STATE / "_xtts_silence.wav"
        subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono", "-t", "0.35", "-q:a", "9", str(silence_path)], check=True, capture_output=True)
        
        seq = []
        for i, p in enumerate(tmp_parts):
            seq.append(p)
            if i < len(tmp_parts) - 1:
                seq.append(silence_path)
        
        if _concat_wav_ffmpeg(seq, out_wav):
            for p in set(tmp_parts + [silence_path]):
                p.unlink()
            return out_wav
        
    except Exception as e:
        logging.error(f"Error en la síntesis XTTS: {e}")
    
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

# --- MAIN ---
def main():
    SEL_FILE = STATE / "next_release.json"
    MANIFEST = STATE / "assets_manifest.json"
    if not SEL_FILE.exists() or not MANIFEST.exists():
        raise SystemExit("Falta next_release.json o assets_manifest.json.")

    sel = json.loads(SEL_FILE.read_text(encoding="utf-8"))
    man = json.loads(MANIFEST.read_text(encoding="utf-8"))

    tmdb_id = sel.get("tmdb_id", "unknown")
    title = sel.get("titulo") or sel.get("title") or ""
    fecha = sel.get("fecha_estreno") or ""
    slug = slugify(title)

    # Paso 1: Generar narración y hook
    logging.info("Generando sinopsis y hook con IA...")
    narracion_generada = _generate_narracion_with_ai(sel)
    sel["sinopsis_generada"] = narracion_generada # Guardamos para el hook
    hook = _generate_hook_with_ai(sel)
    
    narracion = _trim_to_words(narracion_generada, max_words=80) if narracion_generada else None

    
    if not narracion:
        logging.warning("No se pudo generar una narración de IA. Usando un gancho genérico.")
        hook = f"«{title}»\n¡No te la puedes perder!"

    logging.info(f"Hook generado: {hook}")
    logging.info(f"Narración generada: {narracion[:100]}...") if narracion else logging.info("No se generó narración.")

    # Paso 2: Generar audio de voz si es posible
    voice_path = None
    if narracion:
        voice_path = STATE / f"{tmdb_id}_{slug}_narracion.wav"
        if not voice_path.exists():
            voice_path = _synthesize_xtts_with_pauses(narracion, voice_path) or _synthesize_tts_coqui(narracion, voice_path)
        
        if voice_path:
            logging.info(f"Audio de voz generado con duración de {AudioFileClip(str(voice_path)).duration:.2f} segundos.")
        else:
            logging.warning("No se pudo generar el audio de voz. El vídeo no tendrá narración.")

    # Paso 3: Generar el overlay (título, gancho, etc.)
    ov_path = STATE / f"overlay_static_{tmdb_id}.png"
    make_overlay(title, fecha, hook).save(ov_path, "PNG")

    # Paso 4: Preparar clips de vídeo
    poster = man.get("poster_vertical") or man.get("poster")
    backdrops = man.get("backdrops_vertical") or man.get("backdrops") or []
    
    # Asegura que haya backdrops si solo hay póster
    if poster and not backdrops:
        backdrops = [poster]*min(5, MAX_BACKDROPS)
    
    bd_paths = [ROOT / b for b in backdrops if (ROOT / b).exists()]
    if not bd_paths:
        logging.warning("No hay backdrops válidos. Usando póster como fallback.")
        bd_paths = [ROOT / poster] if poster and (ROOT / poster).exists() else []

    intro = clip_from_img(ROOT / poster, INTRO_DUR) if poster and (ROOT / poster).exists() else None
    
    # Paso 5: Calcular la duración del vídeo y generar clips
    audio_dur = AudioFileClip(str(voice_path)).duration if voice_path else 0
    video_dur = audio_dur + (INTRO_DUR if intro else 0)
    video_dur = min(video_dur, TARGET_SECONDS)
    
    n_bd = len(bd_paths)
    per_bd = (video_dur - (INTRO_DUR if intro else 0)) / max(1, n_bd)
    clips = [clip_from_img(p, per_bd) for p in bd_paths]
    if intro: clips.insert(0, intro)
    
    final_clip = concatenate_videoclips(clips, method="compose")

    # Paso 6: Mezclar el audio y exportar
    music_file = pick_music()
    final_clip = _mix_audio_with_voice(final_clip, voice_path, music_file, music_vol=MUSIC_VOL,
                                     fade_in=FADE_IN, fade_out=FADE_OUT)

    out_file = SHORTS_DIR / f"{tmdb_id}_{slug}_final.mp4"
    final_clip.write_videofile(str(out_file), fps=30, codec="libx264", audio_codec="aac")
    logging.info(f"🎬 Short generado en: {out_file}")
    return str(out_file)

if __name__ == "__main__":
    main()