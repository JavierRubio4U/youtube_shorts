# scripts/ai_narration.py
import json
import re
import subprocess
from pathlib import Path
import ollama
from slugify import slugify
# LÍNEA CORRECTA
from moviepy import AudioFileClip, concatenate_audioclips, AudioClip
import moviepy.audio.fx as afx
from langdetect import detect, DetectorFactory
import warnings
warnings.filterwarnings("ignore", message=".*torch.load.*weights_only.*")
import logging
import moviepy.video.fx as vfx
from elevenlabs.client import ElevenLabs
from elevenlabs import save
import requests
import tempfile

# Para resultados consistentes
DetectorFactory.seed = 0

# --- Rutas y dirs ---
ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "output" / "state"
STATE.mkdir(parents=True, exist_ok=True)
CONFIG_DIR = ROOT / "config" # Nueva ruta para el directorio de configuración

# --- Logging ---
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')


# --- Funciones de texto ---
def _sentences(s: str):
    return [seg.strip() for seg in re.split(r"(?<=[\.\!\?…])\s+", s) if seg.strip()]

def count_words(text: str) -> int:
    """Conteador de palabras más preciso."""
    return len(re.findall(r'\b\w+\b', text))

# --- Funciones de IA ---
# Reemplaza esta función en scripts/ai_narration.py

def _generate_narration_with_ai(sel: dict, model='jobautomation/OpenEuroLLM-Spanish', max_words=60, min_words=45, max_retries=3) -> str | None:
    """
    Genera una sinopsis con Ollama, con reintentos y un margen de flexibilidad.
    """
    attempt = 0
    generated_text = ""
    word_count = 0
    
    # CAMBIO: Definimos un margen de tolerancia para las palabras
    margin = 4

    while attempt < max_retries:
        attempt += 1
        logging.info(f"Generando sinopsis (Intento {attempt})...")

        # El prompt inicial no cambia
        initial_prompt = f"""
        Genera una sinopsis detallada, atractiva y descriptiva de aproximadamente {min_words}-{max_words} palabras en español de España (castellano).
        Debe ser un párrafo cohesivo, emocionante, enfocado en el conflicto principal, personajes y atmósfera, sin spoilers.
        Es crucial que finalice con una oración completa y natural.
        El título '{sel.get("titulo")}' es un nombre propio y NO debe traducirse.
        No listes metadata como reparto o géneros.
        Ejemplo: 'En esta épica aventura, un joven héroe descubre un antiguo secreto en un mundo lleno de peligros, donde debe unir fuerzas con aliados inesperados para enfrentar a un villano poderoso que amenaza con destruir todo lo que ama, en una batalla que pondrá a prueba su coraje y determinación.'
        Usa la siguiente información para inspirarte:

        Título: {sel.get("titulo")}
        Sinopsis original: {sel.get("sinopsis")}
        Géneros: {', '.join(sel.get("generos"))}
        """
        
        try:
            response = ollama.chat(model=model, messages=[{'role': 'user', 'content': initial_prompt}])
            generated_text = response['message']['content'].strip()
            word_count = count_words(generated_text)

            # CAMBIO: La condición de éxito ahora incluye el margen
            if min_words <= word_count <= (max_words + margin):
                logging.info(f"Narración generada con éxito ({word_count} palabras, dentro del margen de {max_words + margin}).")
                return generated_text
            
            # Si demasiado corta, expandir (esta lógica se mantiene)
            if word_count < min_words:
                logging.warning(f"La sinopsis tiene {word_count} palabras (mínimo {min_words}). Expandiendo.")
                # ... (el resto del bloque de expansión se mantiene igual)
                expansion_prompt = f"""
                El siguiente texto es demasiado corto. Expándelo agregando detalles descriptivos sobre el conflicto, personajes o atmósfera para alcanzar al menos {min_words} palabras.
                El resultado DEBE ser un párrafo cohesivo y sonar natural.
                Simplemente devuelve el texto corregido.

                Texto a corregir:
                "{generated_text}"
                """
                response = ollama.chat(model=model, messages=[{'role': 'user', 'content': expansion_prompt}])
                generated_text = response['message']['content'].strip()
                word_count = count_words(generated_text)

            # Si demasiado larga (fuera del margen), resumir (esta lógica se mantiene)
            elif word_count > (max_words + margin):
                logging.warning(f"La sinopsis tiene {word_count} palabras (máximo con margen: {max_words + margin}). Resumiendo.")
                refinement_prompt = f"""
                El siguiente texto es demasiado largo. Resúmelo manteniendo detalles clave para que tenga menos de {max_words + margin} palabras.
                El resultado DEBE ser un párrafo cohesivo y sonar natural.
                Simplemente devuelve el texto corregido.

                Texto a corregir:
                "{generated_text}"
                """
                response = ollama.chat(model=model, messages=[{'role': 'user', 'content': refinement_prompt}])
                generated_text = response['message']['content'].strip()
                word_count = count_words(generated_text)

            # Verificación final del intento con el margen incluido
            if min_words <= word_count <= (max_words + margin):
                logging.info(f"Narración corregida con éxito ({word_count} palabras, dentro del margen de {max_words + margin}).")
                return generated_text
            else:
                logging.warning(f"Aún fuera del rango ({word_count} palabras) tras la corrección. Reintentando...")

        except Exception as e:
            logging.error(f"Error al generar narración con Ollama: {e}")
            return None
    
    logging.warning(f"No se logró el rango después de {max_retries} intentos. Usando la última versión generada ({word_count} palabras).")
    return generated_text

def _get_tmp_voice_path(tmdb_id: str, slug: str, tmpdir: Path) -> Path:
    """Retorna la ruta temporal para el archivo de voz."""
    return tmpdir / f"{tmdb_id}_{slug}_narracion.wav"

def _get_elevenlabs_api_key() -> str | None:
    """Lee la clave API desde el archivo de texto en el directorio 'config'."""
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
    """
    Genera la narración con IA (usando auto-corrección) y sintetiza el audio.
    """
    logging.info("🔎 Generando sinopsis con IA local...")
    narracion = _generate_narration_with_ai(sel)
    
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
        # Reemplaza esta clave con la tuya personal
        VOICE_ID = "yiWEefwu5z3DQCM79clN" # ID para la voz de Rachel
        
        # Obtener la clave API desde el archivo
        API_KEY = _get_elevenlabs_api_key()
        if not API_KEY:
            logging.error("No se pudo obtener la clave de ElevenLabs. Se detiene la síntesis.")
            return None

        client = ElevenLabs(api_key=API_KEY)
        
        # Obtener datos de suscripción
        user_subscription_data = client.user.subscription.get()

        # Manejo seguro de limits
        character_limit = user_subscription_data.character_limit
        if character_limit is None:
            logging.warning("Character limit is None; assuming unlimited plan.")
            character_limit = float('inf')
        elif isinstance(character_limit, str):
            logging.warning(f"Character limit is string '{character_limit}'; assuming unlimited.")
            character_limit = float('inf')
        else:
            character_limit = int(character_limit)  # Asegura int

        character_count = user_subscription_data.character_count or 0  # Default 0 si None

        # Quota check solo si finito
        MAX_CHARACTERS = 0.9 * character_limit
        if character_limit != float('inf') and character_count > MAX_CHARACTERS:
            logging.warning(f"¡Cuidado! Te estás quedando sin cuota. Usados: {character_count}/{character_limit}")

# Resto del código (generación audio)...
        # Generar audio con ElevenLabs
        audio_stream = client.text_to_speech.convert(
            voice_id=VOICE_ID,
            text=text,
            model_id="eleven_multilingual_v2",
        )
        
        # Guardar el stream de bytes como archivo temporal MP3
        temp_voice_path = tmpdir / f"_temp_voice_{tmdb_id}_{slug}.mp3"
        with open(temp_voice_path, 'wb') as f:
            for chunk in audio_stream:
                f.write(chunk)
        
        if not temp_voice_path.exists(): 
            raise FileNotFoundError

        # Convertir a WAV para compatibilidad con MoviePy
        temp_wav_path = tmpdir / f"_temp_voice_{tmdb_id}_{slug}.wav"
        subprocess.run(["ffmpeg", "-y", "-i", str(temp_voice_path), str(temp_wav_path)], check=True, capture_output=True)
        temp_voice_path.unlink(missing_ok=True)

        # Cargar la voz temporal en un AudioFileClip
        voice_clip = AudioFileClip(str(temp_wav_path))
        
        # Añadir el silencio inicial de 1 segundo
        initial_silence_clip = AudioClip(lambda t: 0, duration=1.0)
        final_adjusted_clip = concatenate_audioclips([initial_silence_clip, voice_clip])
        
        # Ajustar a la duración total deseada (26 segundos) si es necesario
        target_total_duration = 28.0
        if final_adjusted_clip.duration < target_total_duration:
            extra_silence = target_total_duration - final_adjusted_clip.duration
            silence_at_end = AudioClip(lambda t: 0, duration=extra_silence)
            final_adjusted_clip = concatenate_audioclips([final_adjusted_clip, silence_at_end])

        # Guardar la voz ajustada como final
        final_wav_path_final = _get_tmp_voice_path(tmdb_id, slug, tmpdir)
        final_adjusted_clip.write_audiofile(str(final_wav_path_final), logger=None)
        
        # Limpieza
        temp_wav_path.unlink(missing_ok=True)
        
        logging.info(f"Voz generada con duración ajustada: {final_adjusted_clip.duration:.2f}s (incluyendo 1s inicial).")
        return final_wav_path_final
        
    except requests.exceptions.HTTPError as http_err:
        if http_err.response.status_code == 401:
            logging.error("Error de autenticación. Clave de API inválida.")
        elif http_err.response.status_code == 400 and "quota_exceeded" in http_err.response.text:
            logging.error("Error 400: Cuota excedida. No se puede completar la solicitud.")
        elif http_err.response.status_code == 403:
            logging.error("Error 403: Acceso denegado. Es posible que el plan actual no soporte esta función.")
        else:
            logging.error(f"Error HTTP desconocido al verificar la suscripción: {http_err}")
        return None
    except Exception as e:
        logging.error(f"Error en la síntesis con ElevenLabs: {e}")
        return None
        
# --- Funciones de audio ---
def _clean_for_tts(text: str) -> str:
    if not text: return ""
    text = re.sub(r"http[s]?://\S+", "", text)
    text = re.sub(r"\s+", " ", text).replace("—","-").replace("–","-")
    text = re.sub(r"[^A-Za-zÁÉÍÓÚÜÑáéíóúüñ0-9 ,\.\-\!\?\:\;\'\"]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()[:900]


def _concat_wav_ffmpeg(inputs: list[Path], out_wav: Path) -> bool:
    if not inputs: return False
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
    for s in inputs: cmd += ["-i", str(s)]
    n = len(inputs)
    filt = "".join(f"[{i}:a]" for i in range(n)) + f"concat=n={n}:v=0:a=1[outa]"
    cmd += ["-filter_complex", filt, "-map", "[outa]", str(out_wav)]
    res = subprocess.run(cmd, check=True, capture_output=True)
    return res.returncode == 0 and out_wav.exists() and out_wav.stat().st_size > 0



# (Opcional: si usas main() standalone, lo puedes dejar; si no, elimínalo)
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