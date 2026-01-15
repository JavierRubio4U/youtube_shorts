# scripts/ai_narration.py
import json
import logging
from pathlib import Path
import google.generativeai as genai
import tempfile
import requests  # Necesario para llamar a ElevenLabs
from gemini_config import GEMINI_MODEL

# scripts/ai_narration.py
import json
import logging
from pathlib import Path
import google.generativeai as genai
import tempfile
import requests
from gemini_config import GEMINI_MODEL

# --- Configuración ---
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = ROOT / "output" / "state"
NARRATION_DIR = ROOT / "assets" / "narration"
NARRATION_DIR.mkdir(parents=True, exist_ok=True)
CONFIG_DIR = ROOT / "config"  # <--- AQUÍ SE DEFINE LA VARIABLE

# --- 🔥 PARCHE CORREGIDO: AHORA SÍ FUNCIONARÁ ---
# (Lo ponemos AQUÍ, justo después de definir CONFIG_DIR)
try:
    with open(CONFIG_DIR / "google_api_key.txt") as f:
        GOOGLE_API_KEY = f.read().strip()
    genai.configure(api_key=GOOGLE_API_KEY)
except Exception as e:
    logging.error(f"❌ Error al cargar google_api_key.txt en ai_narration: {e}")
# ------------------------------------------------

# Configuración ElevenLabs
ELEVEN_VOICE_ID = "2VUqK4PEdMj16L6xTN4J"  # La Andaluza
ELEVEN_MODEL_ID = "eleven_multilingual_v2" # El mejor para español natural

# --- GENERACIÓN DE GUION (GEMINI) ---
def _generate_narration_parts(sel: dict, model=GEMINI_MODEL, min_words=50, max_words=65) -> tuple[str, str] | None:
    
    # Datos
    title = sel.get("titulo")
    actor = sel.get("actors", ["el prota"])[0]
    actor_ref = sel.get("actor_reference", "")
    director = sel.get("director", "")
    curiosity = sel.get("movie_curiosity", "")
    synopsis = sel.get("sinopsis", "")
    
    hook_angle = sel.get("hook_angle", "CURIOSITY").upper() 
    
    logging.info(f"🧠 Escribiendo guion basado en estrategia: {hook_angle}")

    hook_instruction = ""
    if hook_angle == "ACTOR":
        ref = actor_ref if actor_ref else f"un dato loco sobre {actor}"
        hook_instruction = f"Empieza atacando a **{actor}** con este dato: '{ref}'. Tono ácido."
    elif hook_angle == "DIRECTOR":
        d_name = director if director else "el director"
        hook_instruction = f"Céntrate en **{d_name}**. Critica o alaba su estilo con mucha ironía."
    elif hook_angle == "PLOT":
        hook_instruction = f"Empieza directamente con lo más absurdo de la trama. ¡Que no se lo crean!"
    else: # CURIOSITY
        dato = curiosity if curiosity else "el rodaje fue un desastre"
        hook_instruction = f"¡Suelta la bomba! Empieza con este dato: **'{dato}'**. Tono de 'te cuento un secreto'."

    # --- PROMPT MEJORADO ---
    prompt = f"""
    Eres "La Sinóptica Gamberra". Crítica ácida. Voz: Español de España con acento andaluz, con carácter.
    
    OBJETIVO: Guion de {min_words}-{max_words} palabras.
    ESTRUCTURA OBLIGATORIA (Separada por "|"):
    
    PARTE 1: El Gancho ({hook_instruction}).
       - Máx 15 palabras.
       - Impacto inmediato.
    
    PARTE 2: El Cotilleo (Trama: "{synopsis}").
       - OBLIGATORIO: Empieza con una **muletilla de enlace** (Ej: "Resulta que...", "El caso es que...").
       - Cuenta el conflicto principal rápido.
       - **Termina con una PREGUNTA RETÓRICA o DESAFÍO** al espectador para que comenten (No es necesario hacerlo siempre si ves que te pasas de palabras en la narracion).
    
    OUTPUT: Texto Gancho | Texto Cotilleo
    """

    try:
        model_instance = genai.GenerativeModel(model)
        resp = model_instance.generate_content(prompt)
        text = resp.text.strip()
        
        if "|" in text:
            parts = text.split("|", 1)
            hook = parts[0].strip()
            body = parts[1].strip()
            logging.info(f"📝 Guion generado ({len(hook.split())+len(body.split())} words).")
            return hook, body
        else:
            return text, ""
    except Exception as e:
        logging.error(f"Fallo Gemini: {e}")
        return None, None

def _clean_text_for_eleven(text):
    """Limpia asteriscos de markdown que Gemini a veces pone."""
    return text.replace("*", "").replace('"', "").strip()

# --- SÍNTESIS ELEVENLABS ---
def _synthesize_elevenlabs(hook: str, body: str, tmdb_id: str) -> Path | None:
    try:
        api_key_path = CONFIG_DIR / "elevenlabs_api_key.txt"
        if not api_key_path.exists():
            logging.error("❌ Falta el archivo elevenlabs_api_key.txt en /config")
            return None
            
        api_key = api_key_path.read_text(encoding="utf-8").strip()

        # Limpieza simple
        safe_hook = _clean_text_for_eleven(hook)
        safe_body = _clean_text_for_eleven(body)
        
        # Unimos texto con una pausa natural (...) para que la IA respire
        full_text = f"{safe_hook} ... {safe_body}"

        url = f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVEN_VOICE_ID}"
        
        headers = {
            "xi-api-key": api_key,
            "Content-Type": "application/json"
        }

        payload = {
            "text": full_text,
            "model_id": ELEVEN_MODEL_ID,
            "voice_settings": {
                "stability": 0.5,       # Equilibrado para que tenga emoción
                "similarity_boost": 0.75,
                "style": 0.5,           # Un poco de exageración (si el modelo v2 lo permite)
                "use_speaker_boost": True
            }
        }

        logging.info("🎙️ Enviando texto a ElevenLabs...")
        response = requests.post(url, json=payload, headers=headers)

        if response.status_code == 200:
            final_wav = NARRATION_DIR / f"{tmdb_id}_narration.mp3" # Eleven devuelve MP3 por defecto
            with open(final_wav, "wb") as f:
                f.write(response.content)
            
            logging.info(f"✅ Audio generado con ElevenLabs: {final_wav}")
            return final_wav
        else:
            logging.error(f"❌ Error ElevenLabs ({response.status_code}): {response.text}")
            return None

    except Exception as e:
        logging.error(f"Error Crítico Audio: {e}")
        return None

def main():
    if not (STATE_DIR / "next_release.json").exists(): return None
    sel = json.loads((STATE_DIR / "next_release.json").read_text(encoding="utf-8"))
    
    # Ya no necesitamos directorio temporal para decodificar base64, 
    # pero mantenemos la estructura por si acaso.
    hook, body = _generate_narration_parts(sel)
    if not hook: return None
    
    # Llamamos a ElevenLabs
    voice_path = _synthesize_elevenlabs(hook, body, str(sel.get("tmdb_id")))
    
    if voice_path:
        return f"{hook} {body}", voice_path
            
    return None, None

if __name__ == "__main__":
    main()