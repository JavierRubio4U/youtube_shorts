# scripts/ai_narration.py
import json
import re
import subprocess
from pathlib import Path
import google.generativeai as genai
from slugify import slugify
from moviepy import (VideoFileClip, ImageClip, AudioFileClip, AudioClip,
                     CompositeVideoClip, ColorClip,
                     CompositeAudioClip, concatenate_videoclips, concatenate_audioclips)
import moviepy.audio.fx as afx
import warnings
warnings.filterwarnings("ignore", message=".*torch.load.*weights_only.*")
import logging
from elevenlabs.client import ElevenLabs
import requests
import sys
import tempfile

# --- Rutas y directorios ---
ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "output" / "state"
STATE.mkdir(parents=True, exist_ok=True)
CONFIG_DIR = ROOT / "config"

# --- Logging ---
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

# --- Constantes ---
GEMINI_MODEL = 'gemini-2.5-pro'

# --- Carga de la clave de API de Google ---
try:
    GOOGLE_CONFIG_FILE = CONFIG_DIR / "google_api_key.txt"
    with open(GOOGLE_CONFIG_FILE, "r") as f:
        GOOGLE_API_KEY = f.read().strip()
    if not GOOGLE_API_KEY:
        raise ValueError("La clave de API de Google está vacía.")
    genai.configure(api_key=GOOGLE_API_KEY)
except (FileNotFoundError, ValueError) as e:
    logging.error(f"Error crítico al cargar la clave de API de Google: {e}. Asegúrate de que 'google_api_key.txt' existe en '/config' y no está vacío.")
    sys.exit(1)

# --- Funciones de texto ---
def count_words(text: str) -> int:
    return len(re.findall(r'\b\w+\b', text))

# --- Funciones de IA ---
def _generate_narration_with_ai(sel: dict, model=GEMINI_MODEL, max_words=70, min_words=50, max_retries=3) -> str | None:
    attempt = 0
    generated_text = ""
    margin = 5
    lower_bound = min_words - margin
    upper_bound = max_words + margin
    while attempt < max_retries:
        attempt += 1
        logging.info(f"Generando narración (Intento {attempt}/{max_retries}) usando el modelo '{model}'...")
        initial_prompt = f"""
        Eres "El Sinóptico Gamberro", un crack resumiendo pelis para redes sociales. Tu voz es la de un colega contándote algo increíble en un bar.
        Tu misión es crear un guion corto y brutalmente divertido para la película '{sel.get("titulo")}' en castellano.
        **Las Reglas de Oro:**
        1.  **RITMO Y ENERGÍA**: ¡Esto es clave! Escribe con un ritmo rápido y dinámico. Usa frases cortas y directas. La puntuación es tu amiga para crear pausas.
        2.  **LÍMITE DE PALABRAS**: Entre {min_words} y {max_words} palabras.
        3.  **FORMATO**: SOLO el texto del guion.
        4.  **TONO**: 100% gamberro, coloquial, con ironía.
        5.  **PROHIBIDO**: Clichés como "una aventura épica", "un viaje inolvidable", etc.
        **Ejemplo del estilo que busco:** "A ver, que no te líen. El prota es un pringao, ¿vale? Pero un día... ¡PUM! Le cae un meteorito en el jardín. Y claro, ahora tiene superpoderes y la lía pardísima."
        **Sinopsis oficial para destrozar:** "{sel.get("sinopsis")}"
        """
        try:
            gemini_model = genai.GenerativeModel(model)
            response = gemini_model.generate_content(initial_prompt)
            generated_text = response.text.strip()
            word_count = count_words(generated_text)
            if lower_bound <= word_count <= upper_bound:
                logging.info(f"Narración generada con éxito ({word_count} palabras).")
                return generated_text
            else:
                logging.warning(f"Texto fuera de rango ({word_count} palabras). Reintentando...")
        except Exception as e:
            logging.error(f"Error al generar narración con Gemini: {e}")
            return None
    logging.error(f"No se logró el rango de palabras después de {max_retries} intentos.")
    return generated_text

def _get_tmp_voice_path(tm_id: str, slug: str, tmpdir: Path) -> Path:
    return tmpdir / f"{tmdb_id}_{slug}_narracion.wav"

def _get_elevenlabs_api_key() -> str | None:
    api_key_path = CONFIG_DIR / "elevenlabs_api_key.txt"
    try:
        if not api_key_path.exists(): return None
        with open(api_key_path, 'r', encoding='utf-8') as f:
            api_key = f.read().strip()
        return api_key if api_key else None
    except Exception:
        return None

def _synthesize_elevenlabs_with_pauses(text: str, tmpdir: Path, tmdb_id: str, slug: str, video_duration: float | None = None) -> Path | None:
    try:
        SPEED_FACTOR = 1.10
        VOICE_ID = "2VUqK4PEdMj16L6xTN4J"
        API_KEY = _get_elevenlabs_api_key()
        if not API_KEY:
            logging.error("No se pudo obtener la clave de ElevenLabs.")
            return None

        client = ElevenLabs(api_key=API_KEY)
        
        audio_stream = client.text_to_speech.convert(
            voice_id=VOICE_ID,
            text=text,
            model_id="eleven_multilingual_v2",
            voice_settings={ "stability": 0.2, "style": 0.9, "use_speaker_boost": True }
        )
        
        temp_mp3_path = tmpdir / f"_temp_voice_{tmdb_id}_{slug}.mp3"
        with open(temp_mp3_path, 'wb') as f:
            for chunk in audio_stream: f.write(chunk)
        
        if not temp_mp3_path.exists(): raise FileNotFoundError("ElevenLabs no generó el archivo.")

        logging.info(f"Acelerando el audio un {int((SPEED_FACTOR - 1) * 100)}% con FFmpeg...")
        temp_accelerated_mp3_path = tmpdir / f"_temp_accelerated_{tmdb_id}_{slug}.mp3"
        ffmpeg_command = [
            "ffmpeg", "-y", "-i", str(temp_mp3_path),
            "-af", f"atempo={SPEED_FACTOR}",
            str(temp_accelerated_mp3_path)
        ]
        subprocess.run(ffmpeg_command, check=True, capture_output=True)
        
        temp_wav_path = tmpdir / f"_temp_voice_{tmdb_id}_{slug}.wav"
        subprocess.run(["ffmpeg", "-y", "-i", str(temp_accelerated_mp3_path), str(temp_wav_path)], check=True, capture_output=True)

        voice_clip = AudioFileClip(str(temp_wav_path))
        initial_silence_clip = AudioClip(lambda t: 0, duration=1.0)
        final_adjusted_clip = concatenate_audioclips([initial_silence_clip, voice_clip])
        
        target_total_duration = 28.0
        if final_adjusted_clip.duration < target_total_duration:
            extra_silence = target_total_duration - final_adjusted_clip.duration
            silence_at_end = AudioClip(lambda t: 0, duration=extra_silence)
            final_adjusted_clip = concatenate_audioclips([final_adjusted_clip, silence_at_end])

        final_wav_path_final = _get_tmp_voice_path(tmdb_id, slug, tmpdir)
        final_adjusted_clip.write_audiofile(str(final_wav_path_final), logger=None)
        
        temp_mp3_path.unlink()
        temp_accelerated_mp3_path.unlink()
        temp_wav_path.unlink()
        
        logging.info(f"Voz generada y acelerada con duración ajustada: {final_adjusted_clip.duration:.2f}s.")
        return final_wav_path_final
        
    except Exception as e:
        logging.error(f"Error en la síntesis con ElevenLabs/FFmpeg: {e}")
        return None

def generate_narration(sel: dict, tmdb_id: str, slug: str, tmpdir: Path, video_duration: float | None = None) -> tuple[str | None, Path | None]:
    logging.info("🔎 Generando narración con IA (Gemini)...")
    narracion = _generate_narration_with_ai(sel, model=GEMINI_MODEL)
    
    if narracion:
        logging.info(f"Narración generada completa: {narracion}")
        voice_path = _synthesize_elevenlabs_with_pauses(narracion, tmpdir, tmdb_id, slug, video_duration=video_duration)
    else:
        logging.warning("No se generó narración. Creando un archivo de audio vacío.")
        voice_path = tmpdir / f"silent_{tmdb_id}_{slug}.wav"
        empty_audio = AudioClip(lambda t: 0, duration=1, fps=44100)
        empty_audio.write_audiofile(str(voice_path), logger=None)
        narracion = None
    
    if voice_path and voice_path.exists():
        return narracion, voice_path
    
    return None, None

def main():
    SEL_FILE = STATE / "next_release.json"
    if not SEL_FILE.exists():
        raise SystemExit("Falta next_release.json.")

    sel = json.loads(SEL_FILE.read_text(encoding="utf-8"))
    tmdb_id = str(sel.get("tmdb_id", "unknown"))
    title = sel.get("titulo") or ""
    slug = slugify(title)
    
    with tempfile.TemporaryDirectory(prefix=f"narration_{tmdb_id}_") as tmpdir_str:
        tmpdir = Path(tmpdir_str)
        narracion, voice_path = generate_narration(sel, tmdb_id, slug, tmpdir)
        if voice_path:
            logging.info(f"Proceso de narración finalizado. Audio en: {voice_path}")
        else:
            logging.error("El proceso de narración falló.")

if __name__ == "__main__":
    main()