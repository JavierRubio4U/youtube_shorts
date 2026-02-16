# scripts/ai_narration.py
import json
import logging
from pathlib import Path
from google import genai
import tempfile
import requests
import subprocess
import os
import sys
import random
import time
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
    
    if not synopsis or len(synopsis.strip()) < 10:
        logging.error("❌ No hay sinopsis suficiente para generar un guion real. Abortando narración.")
        return None
    
    logging.info(f"DEBUG - Sinopsis recibida en ai_narration: {synopsis[:100]}...")
    
    hook_angle = sel.get("hook_angle", "CURIOSITY").upper() 
    
    logging.info(f"🧠 Escribiendo guion basado en estrategia: {hook_angle}")

    hook_instruction = ""
    if hook_angle == "ACTOR":
        ref = actor_ref if actor_ref else f"un dato loco sobre {actor}"
        hook_instruction = f"Empieza con un comentario divertido sobre **{actor}** usando este dato: '{ref}'. Tono de colega que sabe demasiado."
    elif hook_angle == "DIRECTOR":
        d_name = director if director else "el director"
        hook_instruction = f"Céntrate en **{d_name}**. Comenta su estilo con mucha guasa y anécdotas locas."
    elif hook_angle == "PLOT":
        hook_instruction = f"Empieza soltando lo más loco o flipante de la trama. ¡Que floten!"
    else: # CURIOSITY
        dato = curiosity if curiosity else "el rodaje fue un desastre"
        hook_instruction = f"¡Suelta el salseo! Empieza con este dato: **'{dato}'**. Tono de 'no te vas a creer lo que pasó'."

    # --- PROMPT MEJORADO ---
    prompt = f"""
    Eres "La Sinóptica Gamberra". Humor canalla, salseo y mucha chispa. Voz: Español de España con acento andaluz, con carácter de barrio.
    
    **ESTILO PROHIBIDO:** No seas un poeta. No uses palabras cultas, frases largas o lenguaje que parezca de Cervantes. NADA de "he aquí", "asimismo", "obra cinematográfica" o "relato épico". 
    **ESTILO REQUERIDO:** Sé callejero, usa jerga moderna, sé directo y muy gracioso. Habla como si estuvieras contando la movida a tus colegas en un bar entre risas.

    OBJETIVO: Guion de {min_words}-{max_words} palabras que cuente DE QUÉ VA la peli con un tono divertido, exagerado y muy entretenido.
    
    ESTRUCTURA OBLIGATORIA (Separada por "|"):
    
    PARTE 1: El Gancho ({hook_instruction}).
       - Máx 18 palabras. 
       - **REGLA DE ORO SOBRE NOMBRES:** Máximo UN nombre propio en todo el guion (contando PARTE 1 y 2). Si no es una estrella mundial (tipo Brad Pitt), explica brevemente quién es (ej: "Gunn, el jefazo de DC"). Si hay varios actores, elige solo al más importante o ninguno.
       - **PROHIBIDO:** No uses títulos de otras películas como metáforas o adjetivos (ej: NADA de "un Taken de mercadillo" o "un Rambo de barrio"). Si quieres comparar, describe la acción (ej: "un padre repartiendo leña").
       - Evita anglicismos que el lector de voz (TTS) pueda pronunciar mal.
    
    PARTE 2: El Meollo (Trama: "{synopsis}").
       - Conecta el gancho con el resto de la historia de forma fluida y natural.
       - Da detalles de la trama con **MUCHO HUMOR, guasa y exageración**. Imagina que se lo cuentas a un colega que está medio sordo y se ríe de todo.
       - No enumeres personajes ni villanos secundarios. Céntrate en la movida principal de forma gamberra.
       - Todo debe entenderse a la primera, sin necesidad de ser un experto en cine o conocer referencias oscuras.
       - Termina con un REMATE DIVERTIDO: Una frase final con mucha guasa, un chiste o una observación ingeniosa que deje con ganas de más.
       - NO hagas preguntas al espectador.
    
    OUTPUT: Texto Gancho | Texto Meollo
    """

    try:
        api_key = get_google_api_key()
        if not api_key: return None, None

        logging.info(f"DEBUG - Prompt Final Narración:\n{prompt}")

        client = genai.Client(api_key=api_key)
        
        # Retry logic for Gemini call
        max_retries = 3
        resp = None
        for attempt in range(max_retries):
            try:
                resp = client.models.generate_content(model=model, contents=prompt)
                break
            except Exception as e:
                error_str = str(e)
                if "503" in error_str or "Deadline" in error_str or "429" in error_str:
                    if attempt < max_retries - 1:
                        logging.warning(f"⚠️ Error temporal de Gemini en Narración ({e}). Reintentando... ({attempt+1}/{max_retries})")
                        time.sleep(5)
                        continue
                raise e

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