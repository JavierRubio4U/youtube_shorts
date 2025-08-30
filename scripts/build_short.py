# scripts/build_short.py
import json, re, unicodedata, subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from moviepy.editor import (
    ImageClip, concatenate_videoclips, AudioFileClip, CompositeAudioClip
)
import moviepy.audio.fx.all as afx

# --- Shim Pillow 10+: MoviePy 1.0.3 usa Image.ANTIALIAS (eliminado en Pillow>=10) ---
try:
    if not hasattr(Image, "ANTIALIAS"):
        Image.ANTIALIAS = Image.Resampling.LANCZOS
except Exception:
    pass


# --- Rutas y dirs ---
ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "output" / "state"
STATE.mkdir(parents=True, exist_ok=True)
SHORTS_DIR = ROOT / "output" / "shorts"
SHORTS_DIR.mkdir(parents=True, exist_ok=True)

MUSIC_DIR = (ROOT / "assets" / "music")

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

def _generate_synopsis_with_ai(sel: dict) -> str | None:
        """Genera una sinopsis con una IA local (Ollama)."""
        try:
            prompt = f"""Escribe una sinopsis de unas 60-70 palabras para una película. Usa los siguientes datos para inspirarte:

    Título: {sel.get("titulo")}
    Géneros: {', '.join(sel.get("generos"))}
    Reparto: {', '.join(sel.get("reparto_top"))}
    Palabras clave: {', '.join(sel.get("keywords"))}

    La sinopsis debe ser atractiva, concisa y en español.
    """
            response = ollama.chat(model='mistral', messages=[
                {'role': 'user', 'content': prompt}
            ])
            synopsis = response['message']['content']
            return synopsis
        except Exception as e:
            print(f"❌ Error al generar sinopsis con Ollama: {e}")
            return None


# ------------------ UTILIDADES TEXTO ------------------
def slugify(text: str, maxlen: int = 60) -> str:
    s = (text or "").lower().strip()
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"[\s_-]+", "-", s).strip("-")
    return (s or "title")[:maxlen]

def load_fonts():
    try:
        title_font = ImageFont.truetype("arial.ttf", 58)
        small_font = ImageFont.truetype("arial.ttf", 40)
    except:
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

    d.rectangle([0, 0, W, TOP_MARGIN], fill=(0,0,0,220))
    d.rectangle([0, H - BOTTOM_MARGIN, W, H], fill=(0,0,0,220))

    t_lines, t_h = wrap_lines(d, title or "", title_f, int(W*0.94), max_lines=2, line_spacing=LINE_SPACING)
    y_title_center = int(TOP_MARGIN * TITLE_POS)
    y = y_title_center - t_h // 2
    for ln in t_lines:
        lw = d.textlength(ln, font=title_f)
        d.text(((W - lw)//2, y), ln, fill=(255,255,255,255), font=title_f)
        _, _, _, bh = d.textbbox((0,0), ln, font=title_f)
        y += bh + LINE_SPACING

    f_txt = f"Estreno en España: {fecha}" if fecha else ""
    f_txt = wrap_fit(d, f_txt, small_f, int(W*0.94))
    fw = d.textlength(f_txt, font=small_f)
    y_date = H - BOTTOM_MARGIN + int(BOTTOM_MARGIN * DATE_POS) - small_f.size // 2

    if hook:
        max_w = int(W * 0.94)
        lines, block_h = wrap_lines(d, hook, small_f, max_w, max_lines=2, line_spacing=LINE_SPACING)
        y_top = y_date - block_h - LINE_SPACING
        y = y_top
        for i, ln in enumerate(lines):
            lw = d.textlength(ln, font=small_f)
            d.text(((W - lw)//2, y), ln, fill=(255,255,255,255), font=small_f)
            _, _, _, bh = d.textbbox((0,0), ln, font=small_f)
            y += bh + (LINE_SPACING if i < len(lines)-1 else 0)

    d.text(((W - fw)//2, y_date), f_txt, fill=(255,255,255,255), font=small_f)
    return ov


# ------------------ CLIPS ------------------
def clip_from_img(path: Path, dur: float) -> ImageClip:
    return ImageClip(str(path)).set_duration(dur).resize((W, H))

def pick_music():
    if not MUSIC_DIR.exists():
        return None
    files = sorted(MUSIC_DIR.glob("*.mp3"))
    return files[0] if files else None


# ------------------ NARRACIÓN ------------------
def smart_hook(sel: dict) -> str:
    sinopsis = (sel.get("sinopsis") or "").strip()
    generos = [g.lower() for g in (sel.get("generos") or [])]

    base = _first_clause(sinopsis)
    if len(base) < 20:
        if any(g in generos for g in ["terror", "misterio", "suspense", "thriller"]):
            base = "Un misterio que te mantendrá en vilo"
        elif any(g in generos for g in ["acción", "aventura"]):
            base = "Acción y adrenalina sin descanso"
        elif any(g in generos for g in ["comedia"]):
            base = "Risas, enredos y mucha chispa"
        elif any(g in generos for g in ["romance", "romántica"]):
            base = "Un romance que enciende el corazón"
        elif any(g in generos for g in ["animación", "familiar"]):
            base = "Una aventura familiar llena de magia"
        elif any(g in generos for g in ["documental"]):
            base = "Una historia real que sorprende"
        else:
            base = "La historia que todos comentan"

    base = re.sub(r'["“”]+', "", base).strip(" .,!¡¿?;:")
    return _truncate_ellipsis(base, HOOK_MAX_CHARS)

# Modifica la firma de la función para aceptar 'sinopsis' como argumento
def _narracion_from_sinopsis(sinopsis: str, target_words: int = 70) -> str | None:
    sinopsis = _normalize_text(sinopsis)
    if not sinopsis:
        return None
    sents = _sentences(sinopsis) or [sinopsis]
    out, count = [], 0
    for sent in sents:
        w = len(sent.split())
        if count + w <= target_words or count < max(20, int(target_words * 0.6)):
            out.append(sent)
            count += w
        else:
            break
    body = " ".join(out) if out else sinopsis
    return _trim_to_words(body, target_words)

def _narracion_from_generos(sel: dict, target_words: int = 70) -> str:
    generos = [g.lower() for g in (sel.get("generos") or sel.get("genres") or [])]
    if any(g in generos for g in ["terror", "misterio", "suspense", "thriller"]):
        base = "Un misterio que te atrapará desde el primer minuto. Secretos, giros y una verdad que nadie ve venir."
    elif any(g in generos for g in ["acción", "aventura"]):
        base = "Una aventura a toda mecha: persecuciones, decisiones al límite y un destino que no da tregua."
    elif any(g in generos for g in ["comedia"]):
        base = "Risas y enredos con un toque de ternura. Nadie sale ileso… de pasarlo bien."
    elif any(g in generos for g in ["romance", "romántica"]):
        base = "Un romance que prende poco a poco y estalla cuando parece imposible."
    elif any(g in generos for g in ["animación", "familiar"]):
        base = "Magia, amistad y una lección que se queda contigo después de los créditos."
    elif any(g in generos for g in ["documental"]):
        base = "Una historia real que sorprende por lo que cuenta y por cómo lo cuenta."
    else:
        base = "La historia de la que todos hablarán: personajes potentes, emociones a flor de piel y un final que deja huella."
    return _trim_to_words(base, target_words)

# --- Tu función _generate_synopsis_with_ai está aquí ---
# ...

def get_narracion(sel: dict) -> str | None:
    # Primero intenta con la sinopsis de TMDb
    sinopsis = _normalize_text(sel.get("sinopsis") or sel.get("overview") or "")

    # Si no hay sinopsis, la genera con IA local
    if not sinopsis:
        print("🔎 Sinopsis no encontrada en TMDb. Generando con IA local...")
        sinopsis = _generate_synopsis_with_ai(sel)

    # Ahora, si tenemos una sinopsis (ya sea de TMDb o generada por IA),
    # se la pasamos a la función que la recorta y la usa.
    if sinopsis:
        return _narracion_from_sinopsis(sinopsis, target_words=60)
        
    return None


# ------------------ TTS ------------------
def _clean_for_tts(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r"http[s]?://\S+", "", text)
    text = re.sub(r"\s+", " ", text).replace("—", "-").replace("–", "-")
    text = re.sub(r"[^A-Za-zÁÉÍÓÚÜÑáéíóúüñ0-9 ,\.\-\!\?\:\;\'\"]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()[:900]

def _retime_wav_ffmpeg(in_wav: Path, out_wav: Path, atempo: float = 0.92) -> bool:
    try:
        cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
               "-i", str(in_wav),
               "-filter:a", f"atempo={atempo}",
               str(out_wav)]
        res = subprocess.run(cmd, capture_output=True, text=True)
        return res.returncode == 0 and out_wav.exists() and out_wav.stat().st_size > 0
    except Exception as e:
        print("⚠ _retime_wav_ffmpeg error:", e)
        return False

def _concat_wav_ffmpeg(inputs: list[Path], out_wav: Path) -> bool:
    if not inputs: return False
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
    for s in inputs: cmd += ["-i", str(s)]
    n = len(inputs)
    filt = "".join(f"[{i}:a]" for i in range(n)) + f"concat=n={n}:v=0:a=1[outa]"
    cmd += ["-filter_complex", filt, "-map", "[outa]", str(out_wav)]
    res = subprocess.run(cmd, capture_output=True, text=True)
    return res.returncode == 0 and out_wav.exists() and out_wav.stat().st_size > 0

def _synthesize_tts_coqui(text: str, out_wav: Path,
                          model_name: str = "tts_models/multilingual/multi-dataset/xtts_v2",
                          language: str = "es",
                          speaker: str | None = None) -> bool:
    try:
        from TTS.api import TTS
    except Exception as e:
        print(f"ℹ Coqui TTS no instalado: {e}")
        return False
    try:
        clean = _clean_for_tts(text)
        if not clean: return False
        tts = TTS(model_name=model_name, progress_bar=False, gpu=False)
        if "xtts_v2" in model_name:
            tts.tts_to_file(text=clean, file_path=str(out_wav), language=language, speaker=speaker)
        else:
            tts.tts_to_file(text=clean, file_path=str(out_wav))
        return out_wav.exists() and out_wav.stat().st_size > 0
    except Exception as e:
        print(f"⚠ Error Coqui TTS: {e}")
        return False

def _compose_tts_text(narracion: str, sel: dict) -> str:
    base = (narracion or "").strip()
    cleaned = _clean_for_tts(base)
    sents = _sentences(cleaned)
    if not sents:
        return smart_hook(sel) or "Próximamente en cines."
    if len(sents) >= 2:
        return " ".join(sents)
    extra = smart_hook(sel)
    extra = re.sub(r"\s+", " ", extra).strip(" .") + "."
    return (sents[0].strip(" .") + ". " + extra).strip()

def _synthesize_xtts_with_pauses(text: str, out_wav: Path,
                                 model_name: str = "tts_models/multilingual/multi-dataset/xtts_v2",
                                 language: str = "es",
                                 speaker: str | None = "Alma María",
                                 pause_s: float = 0.35) -> bool:
    try:
        from TTS.api import TTS
    except Exception as e:
        print(f"ℹ Coqui TTS no instalado: {e}")
        return False

    cleaned = _clean_for_tts(text)
    sents = _sentences(cleaned) or [cleaned]
    tmp_parts = []
    try:
        tts = TTS(model_name=model_name, progress_bar=False, gpu=False)
        for i, s in enumerate(sents, 1):
            part = STATE / f"_xtts_part_{i}.wav"
            tts.tts_to_file(text=s, file_path=str(part), language=language, speaker=speaker)
            if not part.exists() or part.stat().st_size == 0:
                return False
            tmp_parts.append(part)
    except Exception as e:
        print("⚠ Error XTTS por frases:", e)
        return False

    sil = STATE / "_xtts_silence.wav"
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
           "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono", "-t", f"{pause_s}",
           str(sil)]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0 or not sil.exists():
        return False

    seq = []
    for i, p in enumerate(tmp_parts):
        seq.append(p)
        if i < len(tmp_parts) - 1:
            seq.append(sil)

    ok = _concat_wav_ffmpeg(seq, out_wav)
    for p in set(tmp_parts + [sil]):
        try: p.unlink()
        except: pass
    return ok


def _mix_audio_with_voice(video_clip, voice_audio_path: Path, music_path: Path | None,
                          music_vol: float = 0.15, fade_in: float = 0.6, fade_out: float = 0.8):
    dur_v = video_clip.duration
    voice = AudioFileClip(str(voice_audio_path))
    v_end = max(0.0, min(dur_v, float(voice.duration)) - 1e-3)
    voice = voice.subclip(0, v_end)
    tracks = [voice]

    if music_path and music_path.exists():
        m = AudioFileClip(str(music_path))
        if m.duration < dur_v:
            m = afx.audio_loop(m, duration=dur_v)
        else:
            m = m.subclip(0, dur_v)
        m = m.audio_fadein(fade_in).audio_fadeout(fade_out).volumex(music_vol)
        tracks.append(m)

    final_audio = CompositeAudioClip(tracks).subclip(0, dur_v)
    return video_clip.set_audio(final_audio)


# ------------------ MAIN ------------------
def main():
    if not SEL_FILE.exists() or not MANIFEST.exists():
        raise SystemExit("Falta next_release.json o assets_manifest.json.")

    sel = json.loads(SEL_FILE.read_text(encoding="utf-8"))
    man = json.loads(MANIFEST.read_text(encoding="utf-8"))

    tmdb_id = sel["tmdb_id"]
    title   = sel.get("titulo") or sel.get("title") or ""
    fecha   = sel.get("fecha_estreno") or ""
    hook    = smart_hook(sel)

    slug = slugify(title)

    narracion = get_narracion(sel)
    if narracion:
        print("📜 Narración usada en este vídeo:\n")
        print(narracion)
        narr_path = SHORTS_DIR / f"{tmdb_id}_{slug}_narracion.txt"
        narr_path.write_text(narracion, encoding="utf-8")
    else:
        print("ℹ No se pudo generar narración; continuamos solo con overlay.")

    voice_wav = STATE / f"{tmdb_id}_{slug}_narracion.wav"
    have_voice = False
    if narracion:
        tts_text = _compose_tts_text(narracion, sel)
        have_voice = _synthesize_xtts_with_pauses(tts_text, voice_wav,
                                                  model_name="tts_models/multilingual/multi-dataset/xtts_v2",
                                                  language="es", speaker="Alma María", pause_s=0.35)
        if have_voice and _retime_wav_ffmpeg(voice_wav, STATE / f"{tmdb_id}_{slug}_slow.wav", atempo=0.92):
            voice_wav = STATE / f"{tmdb_id}_{slug}_slow.wav"
        if not have_voice:
            print("↪ XTTS falló, probando VITS (ES)…")
            have_voice = _synthesize_tts_coqui(tts_text, voice_wav,
                                               model_name="tts_models/es/css10/vits",
                                               language="es", speaker=None)

    poster    = man.get("poster_vertical") or man.get("poster")
    backdrops = (man.get("backdrops_vertical") or man.get("backdrops") or [])
    if not poster and not backdrops:
        raise SystemExit("No hay ni poster ni backdrops en el manifest.")
    if poster and not backdrops:
        backdrops = [poster] * min(5, MAX_BACKDROPS)

    intro = clip_from_img(ROOT / poster, INTRO_DUR) if poster else None
    ov_path = STATE / f"overlay_static_{tmdb_id}.png"
    make_overlay(title, fecha, hook).save(ov_path, "PNG")

    def compose(base_img: Path) -> Path:
        base = Image.open(base_img).convert("RGB")
        comp = Image.alpha_composite(base.convert("RGBA"),
                                     Image.open(ov_path).convert("RGBA")).convert("RGB")
        out = STATE / f"bd_{tmdb_id}_{base_img.stem}_ov.jpg"
        comp.save(out, "JPEG", quality=92)
        return out

    bd_paths = [compose(ROOT / b) for b in backdrops]

    n = len(bd_paths)
    per_bd = max(2.0, (TARGET_SECONDS - (INTRO_DUR if intro else 0.0)) / n)
    total = (INTRO_DUR if intro else 0.0) + per_bd * n
    adjust = TARGET_SECONDS - total

    clips = []
    if intro: clips.append(intro)
    for i, p in enumerate(bd_paths, 1):
        dur = per_bd + (adjust if i == n else 0.0)
        clips.append(clip_from_img(p, dur))

    video = concatenate_videoclips(clips, method="compose")
    out_path = SHORTS_DIR / f"{tmdb_id}_{slug}.mp4"

    music_path = pick_music()
    if have_voice:
        video = _mix_audio_with_voice(video, voice_wav, music_path,
                                      music_vol=0.15, fade_in=FADE_IN, fade_out=FADE_OUT)
    elif music_path and music_path.exists():
        music = AudioFileClip(str(music_path))
        if music.duration < video.duration:
            music = afx.audio_loop(music, duration=video.duration)
        else:
            music = music.subclip(0, video.duration)
        music = music.audio_fadein(FADE_IN).audio_fadeout(FADE_OUT).volumex(MUSIC_VOL)
        video = video.set_audio(music)

    video.write_videofile(str(out_path),
                          fps=30, codec="libx264", audio_codec="aac",
                          threads=4, preset="medium", bitrate="4000k",
                          ffmpeg_params=["-movflags", "+faststart"])
    print("✅ Short generado:", out_path)
    print(f"   Duración total: {video.duration:.2f}s (objetivo {TARGET_SECONDS:.2f}s)")
    return str(out_path)


if __name__ == "__main__":
    main()
