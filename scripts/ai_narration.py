# scripts/ai_narration.py
import json
import re
import subprocess
from pathlib import Path
import google.generativeai as genai  # CAMBIO: Importamos la librería de Google
from slugify import slugify
from moviepy import AudioFileClip, concatenate_audioclips, AudioClip
import moviepy.audio.fx as afx
import warnings
warnings.filterwarnings("ignore", message=".*torch.load.*weights_only.*")
import logging
import moviepy.video.fx as vfx
from elevenlabs.client import ElevenLabs
from elevenlabs import save
import requests
import tempfile
import sys

# --- Rutas y directorios ---
ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "output" / "state"
STATE.mkdir(parents=True, exist_ok=True)
CONFIG_DIR = ROOT / "config"

# --- Logging ---
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

# --- Constantes ---
# CAMBIO: Usamos el modelo de Gemini Pro
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
    """Contador de palabras preciso."""
    return len(re.findall(r'\b\w+\b', text))

# --- Funciones de IA ---
def _generate_narration_with_ai(sel: dict, model=GEMINI_MODEL, max_words=70, min_words=50, max_retries=3) -> str | None:
    """
    Genera una narración con Gemini, usando el prompt de estilo gamberro y humorístico.
    """
    attempt = 0
    generated_text = ""
    
    # Mantenemos los márgenes de la versión de sinopsis para flexibilidad
    margin = 5
    lower_bound = min_words - margin
    upper_bound = max_words + margin

    while attempt < max_retries:
        attempt += 1
        logging.info(f"Generando narración (Intento {attempt}/{max_retries}) usando el modelo '{model}'...")

        # <<< CAMBIO: Prompt principal con el tono gamberro >>>
        initial_prompt = f"""
        Eres "El Sinóptico Gamberro", el terror de los departamentos de marketing. Tu superpoder es contar de qué va una película como si se la estuvieras resumiendo a un colega en un bar, con cero paciencia para tonterías.

        Tu misión, si la aceptas (y más te vale), es crear una sinopsis brutalmente honesta y divertida para la película '{sel.get("titulo")}' en castellano.

        **Las Reglas de Oro (o te vas a la calle):**
        1.  **LA MÁS IMPORTANTE**: Clava el texto **idealmente entre {min_words} y {max_words} palabras**. No me hagas sacar la calculadora, sé profesional.
        2.  **FORMATO**: Devuelve SOLO el texto de la sinopsis. Sin saludos, sin explicaciones, sin "Aquí tienes...". Si escribes algo más, el script explota.
        3.  **TONO**: 100% gamberro, coloquial y con humor negro o ironía. Pasa del lenguaje cursi de tráiler. Sé el amigo que dice "tienes que ver esta mierda" y te convence.
        4.  **PROHIBIDO**: Nada de clichés como "una aventura épica", "un viaje inolvidable" o "personajes que te robarán el corazón". Si veo una de esas frases, te bajo el sueldo.

        **Aquí tienes la sinopsis oficial (la versión aburrida para que te inspires y la destroces):** "{sel.get("sinopsis")}"
        """
        
        try:
            gemini_model = genai.GenerativeModel(model)
            response = gemini_model.generate_content(initial_prompt)
            generated_text = response.text.strip()
            word_count = count_words(generated_text)
            
            # Usamos los límites con margen para la comprobación
            if lower_bound <= word_count <= upper_bound:
                logging.info(f"Narración generada con éxito ({word_count} palabras).")
                return generated_text
            
            # <<< CAMBIO: Prompts de corrección con el mismo tono >>>
            if word_count < lower_bound:
                logging.warning(f"Texto demasiado corto ({word_count} palabras). Pidiendo expansión...")
                correction_prompt = f"Te has quedado corto, colega. Este texto: \"{generated_text}\" necesita más chicha. Estíralo para que tenga **entre {min_words} y {max_words} palabras**, pero sin perder la mala leche. Solo el texto final."
                response = gemini_model.generate_content(correction_prompt)
                generated_text = response.text.strip()

            elif word_count > upper_bound:
                logging.warning(f"Texto demasiado largo ({word_count} palabras). Pidiendo resumen...")
                correction_prompt = f"Te has pasado de largo, máquina. Este texto: \"{generated_text}\" es muy largo. Métele tijera y déjalo **entre {min_words} y {max_words} palabras**, manteniendo el tono. Solo el texto final."
                response = gemini_model.generate_content(correction_prompt)
                generated_text = response.text.strip()
            
            word_count = count_words(generated_text)

            if lower_bound <= word_count <= upper_bound:
                logging.info(f"Narración corregida con éxito ({word_count} palabras).")
                return generated_text
            else:
                logging.warning(f"Aún fuera del rango ({word_count} palabras) tras la corrección. Reintentando...")

        except Exception as e:
            logging.error(f"Error al generar narración con Gemini: {e}")
            return None
    
    logging.error(f"No se logró el rango de palabras después de {max_retries} intentos. Devolviendo último resultado.")
    return generated_text

# (El resto de funciones de ElevenLabs y audio permanecen igual, no necesitan cambios)
def _get_tmp_voice_path(tmdb_id: str, slug: str, tmpdir: Path) -> Path:
    return tmpdir / f"{tmdb_id}_{slug}_narracion.wav"

def _get_elevenlabs_api_key() -> str | None:
    api_key_path = CONFIG_DIR / "elevenlabs_api_key.txt"
    try:
        if not api_key_path.exists():
            logging.error(f"Archivo de clave API no encontrado: {api_key_path}")
            return None
        with open(api_key_path, 'r', encoding='utf-8') as f:
            api_key = f.read().strip()
            if not api_key:
                logging.error("La clave API está vacía en el archivo.")
                return None
            return api_key
    except Exception as e:
        logging.error(f"Error al leer la clave API desde {api_key_path}: {e}")
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
        audio_clip = AudioFileClip(str(voice_path))
        
        if video_duration is not None and audio_clip.duration < video_duration:
            silence_duration = video_duration - audio_clip.duration
            silence_clip = AudioClip(lambda t: 0, duration=silence_duration)
            final_audio = concatenate_audioclips([audio_clip, silence_clip])
            final_audio_path = tmpdir / f"final_{tmdb_id}_{slug}.wav"
            final_audio.write_audiofile(str(final_audio_path), logger=None)
            logging.info(f"Audio final con silencio ajustado a {video_duration:.2f} segundos.")
            voice_path.unlink(missing_ok=True)
            return narracion, final_audio_path
        
        logging.info(f"Audio de voz generado con duración de {audio_clip.duration:.2f} segundos.")
        return narracion, voice_path
    
    return None, None

def _synthesize_elevenlabs_with_pauses(text: str, tmpdir: Path, tmdb_id: str, slug: str, video_duration: float | None = None) -> Path | None:
    try:
        # <<< CAMBIO 1: Nuevo ID de la voz Andaluza >>>
        VOICE_ID = "2VUqK4PEdMj16L6xTN4J"
        API_KEY = _get_elevenlabs_api_key()
        if not API_KEY:
            logging.error("No se pudo obtener la clave de ElevenLabs. Se detiene la síntesis.")
            return None

        client = ElevenLabs(api_key=API_KEY)
        user_subscription_data = client.user.subscription.get()
        character_limit = user_subscription_data.character_limit or float('inf')
        character_count = user_subscription_data.character_count or 0

        if character_count > (0.9 * character_limit):
            logging.warning(f"¡Cuidado! Te estás quedando sin cuota. Usados: {character_count}/{character_limit}")

        # <<< CAMBIO 2: Añadimos los ajustes de voz para controlar la entonación >>>
        # - stability: 0.0 (más variable) a 1.0 (más monótono). Usamos 0.3 para más expresividad.
        # - style: 0.0 (normal) a 1.0 (muy exagerado). Usamos 0.75 para un estilo muy marcado.
        # - similarity_boost: No lo tocamos, lo dejamos por defecto.
        audio_stream = client.text_to_speech.convert(
            voice_id=VOICE_ID,
            text=text,
            model_id="eleven_multilingual_v2",
            voice_settings={
                "stability": 0.3,
                "style": 0.75,
                "use_speaker_boost": True
            }
        )
        
        temp_voice_path = tmpdir / f"_temp_voice_{tmdb_id}_{slug}.mp3"
        with open(temp_voice_path, 'wb') as f:
            for chunk in audio_stream: f.write(chunk)
        
        if not temp_voice_path.exists(): raise FileNotFoundError

        temp_wav_path = tmpdir / f"_temp_voice_{tmdb_id}_{slug}.wav"
        subprocess.run(["ffmpeg", "-y", "-i", str(temp_voice_path), str(temp_wav_path)], check=True, capture_output=True)
        temp_voice_path.unlink(missing_ok=True)

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
        
        temp_wav_path.unlink(missing_ok=True)
        
        logging.info(f"Voz generada con duración ajustada: {final_adjusted_clip.duration:.2f}s.")
        return final_wav_path_final
        
    except requests.exceptions.HTTPError as http_err:
        if http_err.response.status_code == 401 and "quota_exceeded" in http_err.response.text:
            logging.error("¡ERROR! Cuota de ElevenLabs agotada. Por favor, recarga tu cuenta.")
            sys.exit(1)
        else:
            logging.error(f"Error HTTP desconocido: {http_err}")
        return None
    except Exception as e:
        logging.error(f"Error en la síntesis con ElevenLabs: {e}")
        return None

def main():
    SEL_FILE = STATE / "next_release.json"
    if not SEL_FILE.exists():
        raise SystemExit("Falta next_release.json.")

    sel = json.loads(SEL_FILE.read_text(encoding="utf-8"))
    tmdb_id = sel.get("tmdb_id", "unknown")
    title = sel.get("titulo") or sel.get("title") or ""
    slug = slugify(title)
    narracion, voice_path = generate_narration(sel, tmdb_id, slug, tmpdir=Path(tempfile.mkdtemp(prefix=f"narration_{tmdb_id}_")))
    return narracion, voice_path

if __name__ == "__main__":
    main()