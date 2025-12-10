# scripts/ai_narration.py
import json
import re
import subprocess
from pathlib import Path
import google.generativeai as genai
from google.generativeai.types import GenerationConfig, HarmCategory, HarmBlockThreshold
from slugify import slugify
from moviepy import AudioFileClip, AudioClip, concatenate_audioclips
from moviepy.audio.AudioClip import AudioArrayClip  # Import correcto para AudioArrayClip
import warnings
warnings.filterwarnings("ignore", message=".*torch.load.*weights_only.*")
import logging
from elevenlabs.client import ElevenLabs
import tempfile
import numpy as np
import shutil
from gemini_config import GEMINI_MODEL
import datetime


# --- Logging y Constantes ---
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
# GEMINI_MODEL = 'gemini-2.5-pro'  # Revertido: Disponible en tu entorno

# <<< CAMBIO: Definimos la ruta de assets/narration >>>
ROOT = Path(__file__).resolve().parents[1]
ASSETS_DIR = ROOT / "assets"
NARRATION_DIR = ASSETS_DIR / "narration"
NARRATION_DIR.mkdir(parents=True, exist_ok=True) # Nos aseguramos de que exista

# --- Funciones ---
def count_words(text: str) -> int:
    return len(re.findall(r'\b\w+\b', text))



def _generate_narration_with_ai(sel: dict, model=GEMINI_MODEL, max_words=60, min_words=45, max_retries=5) -> str | None:
    logging.info(f"Usando modelo Gemini: {model}")
    current_year = datetime.datetime.now().year

    # --- NUEVO: Extracción segura del Actor Principal ---
    # Intentamos sacar el primer actor de la lista 'cast' o 'actors'
    cast_list = sel.get("cast") or sel.get("actors") or []
    
    if isinstance(cast_list, list) and len(cast_list) > 0:
        main_actor = cast_list[0] # Cogemos el primero (el prota)
    elif isinstance(cast_list, str):
        main_actor = cast_list # Por si viene como string
    else:
        main_actor = "el protagonista" # Fallback si no hay datos
        
    logging.info(f"Actor principal identificado: {main_actor}")
    
    # --- Prompt Optimizado: Protagonista + Longitud Asegurada ---
    initial_prompt = f"""
    Eres "La Sinóptica Gamberra", una crítica de cine andaluza, sarcástica y sin filtros.
    
    TU OBJETIVO:
    Crear un guion de narración de **{min_words} a {max_words} PALABRAS** (OBLIGATORIO, NO LO HAGAS CORTO).
    
    ESTRUCTURA OBLIGATORIA (Sigue estos pasos para rellenar tiempo):
    1. **El Gancho:** Empieza con una frase o expresion andaluza potente.
    2. **El/La Protagonista:** Tienes que hablar del personaje principal.
       * IMPORTANTE: Menciona al actor **{main_actor}** si eso ayuda al chiste (ej: "Ahí tienes al {main_actor} poniendo caritas").
       * Dime qué desgracia o lío tiene encima.
    3. **El Nudo:** Cómo intenta arreglarlo (y si la lía más).
    4. **El Cierre:** Un comentario final irónico invitando a verla.
    
    ESTILO DE NARRACIÓN:
    - **Céntrate en el personaje:** Usa expresiones como "el pobre desgraciao", "la tía esta", "el nota".
    - **Usa el nombre del actor ({main_actor})** para dar familiaridad, como si fuera tu vecino.
    - Naturalidad ante todo: Habla como si le contaras un cotilleo a un colega.
    - Acento Andaluz Escrito pero legible.
    - Humor Negro/Adulto: Se permite ser picante e irónica.
    - **Usa frases cortas, PERO usa varias.**
    
    CAJA DE HERRAMIENTAS DE ACTUACIÓN (Usa estas técnicas SOLO si la frase lo pide):
    - **El Tartamudeo (-):** Úsalo para indignación o duda real. (Ej: "Es que es- es pa matarlo").
    - **Alargamiento Vocal:** Alarga vocales (máx 3 letras) para sarcasmo puro. (Ej: "Una idea bueeenísima").
    - **Puntos Suspensivos (...):** Para dejar caer una ironía o crear suspense.
    - **Mayúsculas Selectivas:** Pon EN MAYÚSCULAS solo 1 palabra clave para dar un grito o golpe de voz.
    - **Doble Salto de Línea:** Úsalo siempre para separar ideas y que la voz respire.

    DATOS DE LA PELÍCULA:
    - Título: {sel.get("titulo")}
    - Año: {sel.get("año", current_year)}
    - Actor Principal: {main_actor}
    - Sinopsis base: "{sel.get("sinopsis")}"

    OUTPUT: Solo el texto del guion. Asegúrate de llegar al mínimo de {min_words} palabras describiendo bien las penas del protagonista.
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
                    temperature=0.7,  # Bajo para consistencia
                    top_p=0.8,
                    top_k=40,
                    stop_sequences=["###"]  # Fuerza fin
                ),
                safety_settings={
                    HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_NONE,
                    HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_ONLY_HIGH,
                    HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_ONLY_HIGH, # Tu cambio
                    HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_ONLY_HIGH,
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
        
        text_to_send = text
        
        # Generar audio con ElevenLabs (método correcto)
        SPEED_FACTOR = 1.00
        VOICE_ID = "2VUqK4PEdMj16L6xTN4J"  # Voz andaluza expresiva
        audio_stream = client.text_to_speech.convert(
            voice_id=VOICE_ID,
            text=text_to_send,
            model_id="eleven_multilingual_v2",  # Soporta SSML y español
            voice_settings={
                "stability": 0.45,      # <-- CAMBIO: 0.40 es el Sweet Spot para expresividad controlada
                "style": 0.60,          # <-- CAMBIO: 0.70 para acentuar la actuación andaluza
                "similarity_boost": 0.75,
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
        VOLUME_FACTOR = 1.0  # Ajusta aquí: 1.0 = normal, 1.5 = +50%, 2.0 = doble
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
    
    # --- CAMBIO: Si no hay narración, lanzamos error y abortamos ---
    if not narracion:
        logging.error("🛑 CRÍTICO: Gemini no generó el guion (posible bloqueo de seguridad).")
        logging.error("🛑 Abortando para no crear un vídeo mudo.")
        # Esto detendrá el flujo actual y saltará al 'except' de publish.py (si lo pusiste)
        # o detendrá el script completamente.
        raise ValueError("Abortado: Fallo en generación de guion.")
    # ---------------------------------------------------------------
    
    logging.info(f"Narración generada completa: {narracion}")
    
    # Si llegamos aquí, ES SEGURO que hay texto
    voice_path = _synthesize_elevenlabs_with_pauses(narracion, tmpdir, tmdb_id, slug, CONFIG_DIR)
    
    if voice_path and voice_path.exists():
        return narracion, voice_path
    
    # Si falló ElevenLabs pero había texto:
    logging.error("🛑 Falló la síntesis de voz en ElevenLabs.")
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
            final_voice_path = NARRATION_DIR  / voice_path_temp.name
            shutil.copy2(voice_path_temp, final_voice_path)
            logging.info(f"Proceso de narración finalizado. Audio copiado a: {final_voice_path}")
            return narracion, final_voice_path
        else:
            logging.error("El proceso de narración falló.")
            return None, None

if __name__ == "__main__":
    main()