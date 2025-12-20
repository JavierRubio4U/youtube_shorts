# scripts/ai_narration.py
import json
import re
import subprocess
from pathlib import Path
import google.generativeai as genai
from google.generativeai.types import GenerationConfig, HarmCategory, HarmBlockThreshold
from slugify import slugify
from moviepy import AudioFileClip, AudioClip, concatenate_audioclips
from moviepy.audio.AudioClip import AudioArrayClip
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

ROOT = Path(__file__).resolve().parents[1]
ASSETS_DIR = ROOT / "assets"
NARRATION_DIR = ASSETS_DIR / "narration"
NARRATION_DIR.mkdir(parents=True, exist_ok=True)
STATE_DIR = ROOT / "output" / "state" # Aseguramos que existe
STATE_DIR.mkdir(parents=True, exist_ok=True)
HISTORY_FILE = STATE_DIR / "hype_history.json"

# --- NUEVO: ARCHIVO DE ROTACIÓN ---
ROTATION_FILE = STATE_DIR / "narration_rotation.txt"

# --- Funciones de Rotación ---
def _load_rotation() -> int:
    """Carga el índice de rotación (0-3) para la Sinóptica."""
    try:
        if ROTATION_FILE.exists():
            # Devuelve el valor actual, asegurando que esté entre 0 y 3
            return int(ROTATION_FILE.read_text().strip()) % 4
    except:
        pass
    return 0

def _save_rotation(next_rotation: int):
    """Guarda el siguiente índice de rotación (0-3)."""
    try:
        # Guarda el siguiente valor, asegurando que esté entre 0 y 3
        ROTATION_FILE.write_text(str(next_rotation % 4), encoding="utf-8")
    except:
        pass


# --- Funciones (Hype, count_words) Siguen igual ---
def count_words(text: str) -> int:
    return len(re.findall(r'\b\w+\b', text))

def calculate_hype_metrics(sel: dict, save_to_history: bool = True) -> dict:
    """
    Calcula el hype y da una DIRECCIÓN CREATIVA ABIERTA (sin frases fijas).
    """
    try:
        views = sel.get("views", 0) or sel.get("view_count", 0)
        pub_date_raw = sel.get("upload_date") or sel.get("publish_date") or ""
        movie_title = sel.get("titulo", "Desconocido")
        
        if not pub_date_raw or not views:
            return {
                "score": 0, 
                "category": "BAJO", 
                "instruction": "Actitud: Pasotismo. Invéntate cualquier excusa absurda para no verla."
            }

        # Normalización de fecha
        if len(pub_date_raw) == 8 and pub_date_raw.isdigit():
            pub_date = datetime.datetime.strptime(pub_date_raw, "%Y%m%d")
        elif 'T' in pub_date_raw:
            pub_date = datetime.datetime.strptime(pub_date_raw.split('T')[0], "%Y-%m-%d")
        elif '-' in pub_date_raw:
            pub_date = datetime.datetime.strptime(pub_date_raw, "%Y-%m-%d")
        else:
            pub_date = datetime.datetime.now() - datetime.timedelta(days=1)

        # Cálculo
        now = datetime.datetime.now()
        hours_diff = (now - pub_date).total_seconds() / 3600
        if hours_diff < 1: hours_diff = 1
        velocity = views / hours_diff 

        # --- Categorías con INSTRUCCIONES ABIERTAS ---
        # Umbrales ajustados: <1000 (Bajo), <2500 (Medio), >2500 (Alto)
        
        if velocity < 1000:
            category = "BAJO"
            # CAMBIO: Sin frases fijas. Libertad para inventar excusas.
            instruction = "Actitud: Desinterés y Pereza Máxima. El tráiler no lo ve nadie. Invéntate una excusa surrealista y diferente cada vez para no ir al cine (ej: tienes que peinar a tu iguana, se te ha olvidado andar, etc.) y dile al espectador que vaya él solo."
            
        elif velocity < 2500:
            category = "MEDIO"
            # CAMBIO: Libertad para proponer "tratos" diferentes.
            instruction = "Actitud: Interesada y Pícara. La peli pinta bien. Proponle al espectador ir al cine juntos, PERO ponle una condición de 'cara dura' distinta cada vez (que te lleve a caballito, que pague la cena de lujo, que te abanique durante la peli...)."
            
        else:
            category = "ALTO"
            # CAMBIO: Libertad para exagerar.
            instruction = "Actitud: Hype Desmedido y Locura. Es un exitazo. Ponte eufórica. Usa una comparación exagerada andaluza para decir que hay que verla OBLIGATORIAMENTE en pantalla grande o se acaba el mundo."

        # --- 💾 GUARDADO DE HISTORIAL ---
        if save_to_history:
            history_entry = {
                "movie": movie_title,
                "execution_date": now.strftime("%Y-%m-%d %H:%M:%S"),
                "upload_date": pub_date.strftime("%Y-%m-%d"),
                "hours_since_upload": round(hours_diff, 2),
                "views": views,
                "velocity_views_per_hour": round(velocity, 2),
                "hype_category": category
            }
            # Carga segura
            history_data = []
            if HISTORY_FILE.exists():
                try: history_data = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
                except: pass
            
            history_data.append(history_entry)
            HISTORY_FILE.write_text(json.dumps(history_data, indent=4, ensure_ascii=False), encoding="utf-8")
            logging.info(f"📊 Hype: {velocity:.0f} v/h | Cat: {category} | Histórico guardado.")
        else:
            logging.info(f"📊 Hype (Prompt): {velocity:.0f} v/h | Cat: {category}")

        return {"score": round(velocity, 2), "category": category, "instruction": instruction}

    except Exception as e:
        logging.error(f"Error calculando hype: {e}")
        return {"score": 0, "category": "ERROR", "instruction": "Invita a verla sin mojarte mucho."}


def _generate_narration_with_ai(sel: dict, model=GEMINI_MODEL, max_words=60, min_words=45, max_retries=5) -> str | None:
    logging.info(f"Usando modelo Gemini: {model}")
    
    # --- NUEVO: LÓGICA DE ROTACIÓN ---
    current_rotation = _load_rotation()
    _save_rotation(current_rotation + 1) # Guarda la siguiente rotación
    logging.info(f"🔄 Rotación de Tema: {current_rotation}")

    # Extracción de datos
    cast_list = sel.get("cast") or sel.get("actors") or []
    main_actor = cast_list[0] if isinstance(cast_list, list) and len(cast_list) > 0 else "el protagonista"
    # Tomamos el primer género
    genres = sel.get("generos", ["un género cualquiera"])
    genre_name = genres[0] if genres else "un género cualquiera"
    # La plataforma viene de 'ia_platform_from_title' o el dato enriquecido
    platform_data = sel.get("ia_platform_from_title") or sel.get("platforms", {}).get("streaming", ["Cine"])[0] 
    release_date = sel.get("fecha_estreno", "Pronto")

    # Definición de la instrucción para el paso 1
    if current_rotation == 0:
        # 0. Foco en el Actor
        if main_actor != "el protagonista":
            step1_instruction = f"""1. **El Protagonista:** Menciona a **{main_actor}**. 
            Usa tu base de datos de cine para hacer una referencia ácida o gamberra a su pasado (algún papel icónico, algún escándalo, o si siempre hace lo mismo).
            ¡Sé creativa! No uses siempre las mismas fórmulas."""
        else:
            step1_instruction = "1. **El Protagonista:** No sabemos el nombre. Búrlate de que han cogido a uno de la calle o que es un 'héroe marca blanca'. Improvisa el insulto cariñoso."

    elif current_rotation == 1:
        # 1. Foco en el Género
        step1_instruction = f"""1. **El Género:** Menciona que es una película de **{genre_name}**. 
        Búrlate de forma sarcástica de los clichés de ese género (ej: si es acción: siempre hay explosiones; si es drama: todos lloran). 
        ¡Sé exagerada!"""
        
    elif current_rotation == 2:
        # 2. Foco en la Plataforma
        platform_name = platform_data if platform_data != "Cine" else "el cine"
        platform_instruction = "la comodidad de tu casa con palomitas de microondas" if platform_data != "Cine" else "una butaca incómoda"
        step1_instruction = f"""1. **La Plataforma:** Menciona que se estrena en **{platform_name}**. 
        Haz un comentario gracioso sobre lo que te vas a poner/hacer para verla allí (ej: si es Netflix: "me pongo el pijama roto"; si es Cine: "me llevo el tupper"). 
        ¡Sé pícara!"""

    elif current_rotation == 3:
        # 3. Foco en la Fecha de Estreno
        step1_instruction = f"""1. **La Fecha:** Menciona que la peli llega el **{release_date}**. 
        Haz una broma sobre el tiempo que falta (o no falta) para el estreno, comparándolo con algo absurdo que tienes que hacer (ej: "tengo que tejer un tapiz" o "me da tiempo a aprender chino").
        ¡Sé surrealista!"""
    
    logging.info(f"Actor principal identificado: {main_actor}")

    # Hype (sin duplicar historial)
    hype_data = calculate_hype_metrics(sel, save_to_history=False)

    # --- Prompt General ---
    initial_prompt = f"""
    Eres "La Sinóptica Gamberra", una crítica de cine andaluza, sarcástica y con mucha calle.
    
    TU OBJETIVO: Guion de **{min_words} a {max_words} PALABRAS**.
    
    ESTRUCTURA (Improvisa el contenido, respeta el orden):
    {step1_instruction}
    2. **El Cotilleo (Trama):** Cuenta el problema de la peli como si fuera un chisme de vecinas. Haz que suene a lío gordo.
    3. **El Veredicto:** {hype_data['instruction']}
    
    ESTILO:
    - Muy andaluz, muy coloquial.
    - **VARIEDAD:** No empieces siempre igual. Sorpréndeme.
    - Frases cortas y con ritmo.

    DATOS:
    - Título: {sel.get("titulo")}
    - Sinopsis: "{sel.get("sinopsis")}"

    OUTPUT: Solo texto del guion.
    """
    
    attempts = []
    for attempt in range(max_retries):
        try:
            model_instance = genai.GenerativeModel(model)
            response = model_instance.generate_content(
                initial_prompt,
                generation_config=GenerationConfig(max_output_tokens=2048, temperature=0.9, top_p=0.95), # Temp alta para creatividad
                safety_settings={HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_NONE}
            )
            
            generated_text = ""
            if response.candidates and response.candidates[0].content.parts:
                generated_text = re.sub(r'\s+', ' ', response.candidates[0].content.parts[0].text).strip()
            
            if not generated_text: raise ValueError("Texto vacío.")
            
            word_count = count_words(generated_text)
            attempts.append((generated_text, word_count))
            
            if min_words <= word_count <= max_words:
                logging.info(f"Narración generada con éxito ({word_count} palabras).")
                return generated_text
        except Exception as e:
            logging.warning(f"Reintento {attempt+1}: {e}")
    
    if attempts:
        return min(attempts, key=lambda x: abs(x[1] - (min_words + max_words) / 2))[0]
    return None

# --- El resto de las funciones (_get_tmp_voice_path, _get_elevenlabs_api_key, _synthesize_elevenlabs_with_pauses, generate_narration, main) siguen igual.
# Ya que la lógica de audio no cambia.

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
        if not api_key: raise ValueError("No API Key")
        
        client = ElevenLabs(api_key=api_key)
        
        # Audio Stream
        audio_stream = client.text_to_speech.convert(
            voice_id="2VUqK4PEdMj16L6xTN4J",
            text=text,
            model_id="eleven_multilingual_v2",
            voice_settings={"stability": 0.45, "style": 0.65, "similarity_boost": 0.75, "use_speaker_boost": True}
        )

        temp_path = tmpdir / f"{tmdb_id}_{slug}_temp.mp3"
        with open(temp_path, "wb") as f:
            for chunk in audio_stream: f.write(chunk)

        if not temp_path.exists() or temp_path.stat().st_size == 0: raise IOError("ElevenLabs failed")
        
        final_wav_path = _get_tmp_voice_path(tmdb_id, slug, tmpdir)
        
        # FFmpeg: Speed 1.1x
        subprocess.run([
            'ffmpeg', '-y', '-i', str(temp_path),
            '-filter:a', 'atempo=1.10,volume=1.0',
            '-ar', '44100', '-ac', '2', str(final_wav_path)
        ], check=True, capture_output=True, text=True)
        temp_path.unlink()
        
        return final_wav_path
    except Exception as e:
        logging.error(f"Error Audio: {e}")
        return None
    
def generate_narration(sel: dict, tmdb_id: str, slug: str, tmpdir: Path, CONFIG_DIR: Path) -> tuple[str | None, Path | None]:
    logging.info("🔎 Generando narración con IA (Gemini)...")
    try:
        GOOGLE_CONFIG_FILE = CONFIG_DIR / "google_api_key.txt"
        with open(GOOGLE_CONFIG_FILE, "r") as f:
            GOOGLE_API_KEY = f.read().strip()
        genai.configure(api_key=GOOGLE_API_KEY)
    except Exception:
        return None, None
        
    narracion = _generate_narration_with_ai(sel, model=GEMINI_MODEL)
    if not narracion: raise ValueError("Fallo guion")
    
    logging.info(f"Narración: {narracion}")
    voice_path = _synthesize_elevenlabs_with_pauses(narracion, tmpdir, tmdb_id, slug, CONFIG_DIR)
    
    if voice_path and voice_path.exists(): return narracion, voice_path
    return None, None

def main() -> tuple[str | None, Path | None] | None:
    ROOT = Path(__file__).resolve().parents[1]
    SEL_FILE = ROOT / "output" / "state" / "next_release.json"
    CONFIG_DIR = ROOT / "config"

    if not SEL_FILE.exists(): return None
    sel = json.loads(SEL_FILE.read_text(encoding="utf-8"))
    
    # Save Hype Metrics
    hype_metrics = calculate_hype_metrics(sel, save_to_history=True)
    sel["hype_score"] = hype_metrics["score"]
    sel["hype_category"] = hype_metrics["category"]
    SEL_FILE.write_text(json.dumps(sel, indent=4, ensure_ascii=False), encoding="utf-8")

    tmdb_id = str(sel.get("tmdb_id", "unknown"))
    title = sel.get("titulo") or ""
    slug = slugify(title)
    
    with tempfile.TemporaryDirectory(prefix=f"narration_{tmdb_id}_") as tmpdir_str:
        tmpdir = Path(tmpdir_str)
        narracion, voice_path_temp = generate_narration(sel, tmdb_id, slug, tmpdir, CONFIG_DIR)
        
        if voice_path_temp:
            final_voice_path = NARRATION_DIR  / voice_path_temp.name
            shutil.copy2(voice_path_temp, final_voice_path)
            logging.info(f"Audio listo: {final_voice_path}")
            return narracion, final_voice_path
        else:
            return None, None

if __name__ == "__main__":
    main()