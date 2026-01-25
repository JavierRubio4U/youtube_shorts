# scripts/ai_narration.py
import json
import logging
from pathlib import Path
from google import genai
import tempfile
import requests
import subprocess
from gemini_config import GEMINI_MODEL

# --- Configuración ---
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = ROOT / "output" / "state"
TMP_DIR = ROOT / "assets" / "tmp"
TMP_DIR.mkdir(parents=True, exist_ok=True)
NARRATION_DIR = ROOT / "assets" / "narration"
NARRATION_DIR.mkdir(parents=True, exist_ok=True)
CONFIG_DIR = ROOT / "config"

# --- API Key Loading Helper ---
def get_google_api_key():
    try:
        with open(CONFIG_DIR / "google_api_key.txt") as f:
            return f.read().strip()
    except Exception as e:
        logging.error(f"❌ Error al cargar google_api_key.txt: {e}")
        return None

# Configuración ElevenLabs
ELEVEN_VOICE_ID = "2VUqK4PEdMj16L6xTN4J"
ELEVEN_MODEL_ID = "eleven_multilingual_v2"

# --- GENERACIÓN DE GUION (GEMINI) ---
def _generate_narration_parts(sel: dict, model=GEMINI_MODEL, min_words=55, max_words=65) -> tuple[str, str] | None:
    
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
    Eres "La Sinóptica Gamberra". Crítica ácida pero informativa. Voz: Español de España con acento andaluz, con carácter.
    
    OBJETIVO: Guion de {min_words}-{max_words} palabras que cuente DE QUÉ VA la peli mientras sueltas verdades incómodas.
    
    ESTRUCTURA OBLIGATORIA (Separada por "|"):
    
    PARTE 1: El Gancho ({hook_instruction}).
       - Máx 18 palabras. 
       - Sé directo. Si mencionas a alguien (director, actor, etc.), aclara brevemente quién es o qué ha hecho (ej: "Gunn, el jefazo de DC" o "Milly Alcock, la de La Casa del Dragón") para que hasta mi abuela lo entienda. Evita que parezca un código secreto para cinéfilos.
    
    PARTE 2: El Meollo (Trama: "{synopsis}").
       - Conecta el gancho con el resto de la historia de forma fluida y natural.
       - Da detalles de la trama. Que el espectador sepa QUÉ va a ver realmente.
       - Termina con una CONCLUSIÓN ÁCIDA: Una frase final con mucha ironía o humor sobre la peli. Usa el sarcasmo en lugar del ataque directo. Evita estructuras repetitivas.
       - NO hagas preguntas al espectador.
    
    OUTPUT: Texto Gancho | Texto Meollo
    """

    try:
        api_key = get_google_api_key()
        if not api_key: return None, None

        client = genai.Client(api_key=api_key)
        resp = client.models.generate_content(model=model, contents=prompt)
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
            temp_wav = NARRATION_DIR / f"{tmdb_id}_raw.mp3"
            final_wav = NARRATION_DIR / f"{tmdb_id}_narration.mp3"
            
            with open(temp_wav, "wb") as f:
                f.write(response.content)
            
            # ACELERACIÓN 1.1x con FFmpeg (mantiene el tono original)
            try:
                logging.info("🚀 Acelerando narración a 1.1x...")
                cmd = [
                    'ffmpeg', '-y', '-i', str(temp_wav),
                    '-filter:a', "atempo=1.1",
                    '-vn', str(final_wav)
                ]
                subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                if temp_wav.exists(): temp_wav.unlink() # Borramos el original lento
                logging.info(f"✅ Audio generado y acelerado: {final_wav}")
            except Exception as e:
                logging.warning(f"⚠️ Falló aceleración FFmpeg, usando audio original: {e}")
                temp_wav.rename(final_wav)

            return final_wav
        else:
            logging.error(f"❌ Error ElevenLabs ({response.status_code}): {response.text}")
            return None

    except Exception as e:
        logging.error(f"Error Crítico Audio: {e}")
        return None

def main():
    if not (TMP_DIR / "next_release.json").exists(): return None
    sel = json.loads((TMP_DIR / "next_release.json").read_text(encoding="utf-8"))
    
    # Ya no necesitamos directorio temporal para decodificar base64, 
    # pero mantenemos la estructura por si acaso.
    hook, body = _generate_narration_parts(sel)
    if not hook: return None
    
    # Llamamos a ElevenLabs
    voice_path = _synthesize_elevenlabs(hook, body, str(sel.get("tmdb_id")))
    
    if voice_path:
        return f"{hook} {body}", voice_path
            
    return None, None
            
    return None, None

if __name__ == "__main__":
    main()