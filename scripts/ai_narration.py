# scripts/ai_narration.py
import json
import re
import subprocess
from pathlib import Path
import google.generativeai as genai
# <<< CAMBIO 1: Importación correcta para FinishReason >>>
from google.generativeai.types import GenerationConfig, HarmCategory, HarmBlockThreshold
from slugify import slugify
from moviepy import AudioFileClip, AudioClip, concatenate_audioclips
from moviepy.audio.AudioClip import AudioArrayClip  # Import correcto para AudioArrayClip
import warnings
warnings.filterwarnings("ignore", message=".*torch.load.*weights_only.*")
import logging
from elevenlabs.client import ElevenLabs
import requests
import sys
import tempfile
import numpy as np
import shutil

# --- Logging y Constantes ---
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
GEMINI_MODEL = 'gemini-2.5-pro'  # Revertido: Disponible en tu entorno

# --- Funciones ---
def count_words(text: str) -> int:
    return len(re.findall(r'\b\w+\b', text))

def _generate_narration_with_ai(sel: dict, model=GEMINI_MODEL, max_words=80, min_words=65, max_retries=5) -> str | None:
    logging.info(f"Usando modelo Gemini: {model}")
    initial_prompt = f"""
    Eres "El Sinóptico Gamberro", el terror de los departamentos de marketing. 
    Tu superpoder es contar de qué va una película como si se la estuvieras resumiendo a un colega en un bar, con cero paciencia para tonterías.
     Tu misión, si la aceptas (y más te vale), es crear una sinopsis brutalmente honesta y divertida para la película '{sel.get("titulo")}'.
    **REGLA MÁS IMPORTANTE E INQUEBRANTABLE:** El guion DEBE tener **ENTRE {min_words} Y {max_words} PALABRAS**. Es un requisito técnico obligatorio. Cuenta las palabras.
    **Otras Reglas:**
    1.  **RITMO Y ENERGÍA**: Frases cortas y directas, como para un Short de YouTube.
    2.  **FORMATO**: Devuelve SOLO el texto de la sinopsis. Sin saludos, sin explicaciones, sin "Aquí tienes..."
    3.  **TONO**: 100% gamberro, coloquial, con ironía.  Pasa del lenguaje cursi de tráiler. Sé el amigo que dice "tienes que ver esta mierda" y te convence.
    4.  **PROHIBIDO**: Clichés como "una aventura épica", "un viaje inolvidable" o "personajes que te robarán el corazón".
    **Ejemplo de estilo:** "A ver, que no te líen. El prota es un pringao, ¿vale? Pero un día... ¡PUM! Le cae un meteorito. Ahora tiene superpoderes y la lía pardísima."
    **Aquí tienes la sinopsis oficial (la versión aburrida para que te inspires y la destroces)** "{sel.get("sinopsis")}"
    """
    
    # Log aprox tokens en prompt (rough estimate)
    prompt_tokens = len(initial_prompt.split()) * 1.3  # Aprox
    logging.info(f"Longitud prompt aprox: {prompt_tokens} tokens")
    
    attempts = []
    for attempt in range(max_retries):
        try:
            model_instance = genai.GenerativeModel(model)
            # Config con params para evitar loops/reps
            response = model_instance.generate_content(
                initial_prompt,
                generation_config=GenerationConfig(
                    max_output_tokens=2048,  # Aumentado
                    temperature=0.1,  # Bajo para consistencia
                    top_p=0.8,
                    top_k=40,
                    stop_sequences=["\n\n", "###"]  # Fuerza fin
                ),
                safety_settings={
                    HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_ONLY_HIGH,
                    HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE,
                    HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE,
                    HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE,
                }
            )
            
            # Logs detallados
            logging.info(f"Response type: {type(response)}")
            logging.info(f"Has candidates: {hasattr(response, 'candidates') and bool(response.candidates)}")
            
            generated_text = ""
            if response.candidates and len(response.candidates) > 0:
                candidate = response.candidates[0]
                finish_reason = candidate.finish_reason.name if hasattr(candidate, 'finish_reason') else 'UNKNOWN'
                logging.info(f"Finish reason: {finish_reason}")
                
                if candidate.content and candidate.content.parts:
                    num_parts = len(candidate.content.parts)
                    logging.info(f"Número de parts: {num_parts}")
                    raw_text = candidate.content.parts[0].text if candidate.content.parts[0].text else ""
                    # Limpieza: Remover reps de espacios y strip
                    generated_text = re.sub(r'\s+', ' ', raw_text).strip()
                    logging.info(f"Texto raw (primeros 100 chars): {raw_text[:100]}...")
                    if generated_text:
                        logging.info(f"Texto limpio: {generated_text}")
                    else:
                        logging.warning("Texto limpio vacío (posible loop de espacios)")
                else:
                    logging.warning(f"Sin parts en content (finish_reason: {finish_reason})")
                
                # Log usage si disponible
                if hasattr(response, 'usage_metadata'):
                    logging.info(f"Prompt tokens: {response.usage_metadata.prompt_token_count}, Output tokens: {response.usage_metadata.candidates_token_count}")
                
                # Log safety ratings
                if hasattr(candidate, 'safety_ratings') and candidate.safety_ratings:
                    ratings_str = ', '.join([f"{r.category.name}: {r.probability.name}" for r in candidate.safety_ratings])
                    logging.info(f"Safety ratings: {ratings_str}")
                
                if finish_reason == 'SAFETY':
                    ratings_str = ', '.join([f"{r.category.name}: {r.probability.name}" for r in candidate.safety_ratings]) if candidate.safety_ratings else "No ratings"
                    raise ValueError(f"Bloqueado por SAFETY (ratings: {ratings_str})")
                elif finish_reason == 'MAX_TOKENS':
                    logging.warning(f"Generación truncada por MAX_TOKENS.")
                    if not generated_text.strip():
                        logging.warning("Pero sin texto sustancial (suspect: loop de tokens repetidos).")
            else:
                raise ValueError("No hay candidates en la respuesta.")
            
            if not generated_text.strip():
                raise ValueError("Texto generado vacío después de limpieza.")
            
            word_count = count_words(generated_text)
            attempts.append((generated_text, word_count))
            
            if min_words <= word_count <= max_words:
                logging.info(f"Narración generada con éxito ({word_count} palabras).")
                return generated_text
            else:
                logging.warning(f"Intento {attempt + 1}: Texto tiene {word_count} palabras (fuera de {min_words}-{max_words}). Reintentando...")
        except Exception as e:
            logging.warning(f"Error en intento {attempt + 1}: {e}. Reintentando...")
    
    # Fallback: Si todos fallan, prueba prompt simplificado
    if not attempts:
        logging.warning("Todos intentos fallaron. Probando prompt simplificado.")
        sinopsis = sel.get("sinopsis", "")
        truncated_sinopsis = sinopsis[:500] + "..." if sinopsis else ""
        simple_prompt = f"Genera un guion gamberro, coloquial e irónico de {min_words}-{max_words} palabras para la película '{sel.get('titulo', '')}': {truncated_sinopsis}"
        try:
            model_instance = genai.GenerativeModel(model)
            response = model_instance.generate_content(simple_prompt, generation_config=GenerationConfig(max_output_tokens=512, temperature=0.1))
            if response.text:
                generated_text = response.text.strip()
                word_count = count_words(generated_text)
                if min_words <= word_count <= max_words + 10:  # Tolerancia
                    logging.info(f"Narración fallback OK ({word_count} palabras).")
                    return generated_text
        except Exception as e:
            logging.error(f"Fallback falló: {e}")
    
    if attempts:
        best_attempt = min(attempts, key=lambda x: abs(x[1] - (min_words + max_words) / 2))
        logging.info(f"Usando narración más cercana: {best_attempt[1]} palabras.")
        return best_attempt[0]
    
    logging.error(f"Falló generar narración después de {max_retries} intentos.")
    return None

def _get_tmp_voice_path(tmdb_id: str, slug: str, tmpdir: Path) -> Path:
    return tmpdir / f"{tmdb_id}_{slug}_narracion.wav"

def _get_elevenlabs_api_key(CONFIG_DIR: Path) -> str | None:
    api_key_path = CONFIG_DIR / "elevenlabs_api_key.txt"
    try:
        if not api_key_path.exists(): return None
        with open(api_key_path, 'r', encoding='utf-8') as f:
            api_key = f.read().strip()
        return api_key if api_key else None
    except Exception:
        return None

def _synthesize_elevenlabs_with_pauses(text: str, tmpdir: Path, tmdb_id: str, slug: str, CONFIG_DIR: Path) -> Path | None:
    try:
        api_key = _get_elevenlabs_api_key(CONFIG_DIR)
        if not api_key:
            raise ValueError("No se encontró clave API de ElevenLabs")
        
        client = ElevenLabs(api_key=api_key)
        
        # Insertar pausas simples con SSML (para frases cortas)
        ssml_text = text.replace('.', '.<break time="0.5s"/>').replace('!', '!<break time="0.3s"/>')
        ssml_text = f'<speak>{ssml_text}</speak>'
        
        # Generar audio con ElevenLabs (método correcto)
        SPEED_FACTOR = 1.15
        VOICE_ID = "2VUqK4PEdMj16L6xTN4J"  # Voz andaluza expresiva
        audio_stream = client.text_to_speech.convert(
            voice_id=VOICE_ID,
            text=ssml_text,
            model_id="eleven_multilingual_v2",  # Soporta SSML y español
            voice_settings={
                "stability": 0.2,
                "style": 0.9,
                "use_speaker_boost": True
            }
        )

        # Guardar el stream en temp MP3
        temp_path = tmpdir / f"{tmdb_id}_{slug}_temp.mp3"
        with open(temp_path, "wb") as f:
            for chunk in audio_stream:
                f.write(chunk)

        # Verificar que se generó el archivo
        if not temp_path.exists() or temp_path.stat().st_size == 0:
            raise IOError("ElevenLabs no generó el archivo de audio o está vacío.")
        
        logging.info(f"Audio generado con ElevenLabs (tamaño: {temp_path.stat().st_size} bytes)")

        # Post-procesar con FFmpeg para acelerar (corregido: usa temp_path)
        final_wav_path = _get_tmp_voice_path(tmdb_id, slug, tmpdir)
        VOLUME_FACTOR = 1.5  # Ajusta aquí: 1.0 = normal, 1.5 = +50%, 2.0 = doble
        ffmpeg_cmd = [
            'ffmpeg', '-y', '-i', str(temp_path),  # <-- Corregido: temp_path
            '-filter:a', f'atempo={SPEED_FACTOR},volume={VOLUME_FACTOR}',
            '-ar', '44100', '-ac', '2', str(final_wav_path)
        ]
        subprocess.run(ffmpeg_cmd, check=True, capture_output=True, text=True)
        
        # Limpiar temp (corregido: usa temp_path)
        temp_path.unlink()
        
        # Log duración
        test_clip = AudioFileClip(str(final_wav_path))
        logging.info(f"Audio sintetizado con pausas: {final_wav_path} (duración: {test_clip.duration:.2f}s)")
        test_clip.close()
        
        return final_wav_path
    except subprocess.CalledProcessError as e:
        logging.error(f"FFmpeg falló al acelerar el audio. Asegúrate de que FFmpeg está instalado y en el PATH: {e.stderr}")
        if 'temp_path' in locals() and temp_path.exists():
            temp_path.unlink()
        return None
    except Exception as e:
        logging.error(f"Error en la síntesis con ElevenLabs/FFmpeg: {e}", exc_info=True)
        return None
    
def generate_narration(sel: dict, tmdb_id: str, slug: str, tmpdir: Path, CONFIG_DIR: Path) -> tuple[str | None, Path | None]:
    logging.info("🔎 Generando narración con IA (Gemini)...")
    try:
        GOOGLE_CONFIG_FILE = CONFIG_DIR / "google_api_key.txt"
        with open(GOOGLE_CONFIG_FILE, "r") as f:
            GOOGLE_API_KEY = f.read().strip()
        genai.configure(api_key=GOOGLE_API_KEY)
    except Exception:
        logging.error("Fallo al cargar la clave API de Gemini en generate_narration.")
        return None, None
        
    narracion = _generate_narration_with_ai(sel, model=GEMINI_MODEL)
    
    voice_path = None
    if narracion:
        logging.info(f"Narración generada completa: {narracion}")
        voice_path = _synthesize_elevenlabs_with_pauses(narracion, tmpdir, tmdb_id, slug, CONFIG_DIR)
    else:
        logging.warning("No se generó narración. Creando un archivo de audio vacío.")
        voice_path = tmpdir / f"silent_{tmdb_id}_{slug}.wav"

        duration = 28.0
        try:
            # Intenta AudioArrayClip primero (ahora importado correctamente)
            sample_rate = 44100
            num_samples = int(duration * sample_rate)
            silence_stereo = np.zeros((num_samples, 2))  # (samples, channels)
            empty_audio = AudioArrayClip(silence_stereo, fps=sample_rate)
            empty_audio.write_audiofile(str(voice_path), logger=None)
            logging.info(f"Audio silencioso con AudioArrayClip: {duration}s")
        except Exception as e:
            logging.warning(f"AudioArrayClip falló ({e}). Fallback a lambda simple.")
            # Fallback a tu lambda [0, 0]
            empty_audio = AudioClip(lambda t: [0, 0], duration=duration, fps=44100)
            empty_audio.write_audiofile(str(voice_path), logger=None)

        # Verificación post-write
        try:
            test_clip = AudioFileClip(str(voice_path))
            actual_duration = test_clip.duration
            test_clip.close()
            if abs(actual_duration - duration) > 0.1:
                logging.error(f"Duración audio inválida: {actual_duration}s (esperado {duration}s)")
            else:
                logging.info(f"Audio verificado OK: {actual_duration:.2f}s")
        except Exception as e:
            logging.error(f"Error verificando audio: {e}")

        narracion = " "  # Espacio para no romper build_short
    
    if voice_path and voice_path.exists():
        return narracion, voice_path
    
    return None, None

def main() -> tuple[str | None, Path | None] | None:
    ROOT = Path(__file__).resolve().parents[1]
    STATE = ROOT / "output" / "state"
    CONFIG_DIR = ROOT / "config"

    SEL_FILE = STATE / "next_release.json"
    if not SEL_FILE.exists():
        logging.error("Falta next_release.json.")
        return None

    sel = json.loads(SEL_FILE.read_text(encoding="utf-8"))
    tmdb_id = str(sel.get("tmdb_id", "unknown"))
    title = sel.get("titulo") or ""
    slug = slugify(title)
    
    with tempfile.TemporaryDirectory(prefix=f"narration_{tmdb_id}_") as tmpdir_str:
        tmpdir = Path(tmpdir_str)
        narracion, voice_path_temp = generate_narration(sel, tmdb_id, slug, tmpdir, CONFIG_DIR)
        
        if voice_path_temp:
            final_voice_path = STATE / voice_path_temp.name
            shutil.copy2(voice_path_temp, final_voice_path)
            logging.info(f"Proceso de narración finalizado. Audio copiado a: {final_voice_path}")
            return narracion, final_voice_path
        else:
            logging.error("El proceso de narración falló.")
            return None, None

if __name__ == "__main__":
    main()