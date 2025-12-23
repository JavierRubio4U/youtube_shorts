# scripts/ai_narration.py
import json
import re
import subprocess
from pathlib import Path
import google.generativeai as genai
import logging
import tempfile
import requests
import base64
from gemini_config import GEMINI_MODEL

# --- Configuración ---
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = ROOT / "output" / "state"
NARRATION_DIR = ROOT / "assets" / "narration"
NARRATION_DIR.mkdir(parents=True, exist_ok=True)
CONFIG_DIR = ROOT / "config"

# --- GENERACIÓN INTELIGENTE ---
def _generate_narration_parts(sel: dict, model=GEMINI_MODEL, min_words=45, max_words=60) -> tuple[str, str] | None:
    
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

    # --- PROMPT MEJORADO (CON ENTRADILLA) ---
    prompt = f"""
    Eres "La Sinóptica Gamberra". Crítica ácida. Voz: Español Neutro.
    
    OBJETIVO: Guion de {min_words}-{max_words} palabras.
    ESTRUCTURA OBLIGATORIA (Separada por "|"):
    
    PARTE 1: El Gancho ({hook_instruction}).
       - Máx 15 palabras.
       - Impacto inmediato.
    
    PARTE 2: El Cotilleo (Trama: "{synopsis}").
       - OBLIGATORIO: Empieza con una **muletilla de enlace natural** (Ej: "Resulta que...", "Total, que...", "El caso es que...", "Pues imagínate...").
       - Cuenta el conflicto principal rápido y con "mala leche".
       - NO te despidas.
    
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

def _clean_text_for_xml(text):
    return text.replace("&", "y").replace("<", "").replace(">", "").replace('"', "")

def _synthesize_google_ssml(hook: str, body: str, tmpdir: Path, tmdb_id: str) -> Path | None:
    try:
        api_key_path = CONFIG_DIR / "google_api_key.txt"
        if not api_key_path.exists(): return None
        api_key = api_key_path.read_text(encoding="utf-8").strip()

        safe_hook = _clean_text_for_xml(hook)
        safe_body = _clean_text_for_xml(body)
        
        # --- SSML MODIFICADO (TONO CONFIDENCIAL) ---
        # 1. break 200ms: Silencio seguridad.
        # 2. Gancho: Normal.
        # 3. break 700ms: Pausa dramática.
        # 4. Cuerpo: pitch="-1.5st" (Voz un poco más grave/íntima).
        ssml_text = f"""
        <speak>
            <break time="200ms"/>
            <p>
                <s>{safe_hook}</s>
                <break time="700ms"/>
                <prosody pitch="-1.5st">
                    <s>{safe_body}</s>
                </prosody>
            </p>
        </speak>
        """

        url = f"https://texttospeech.googleapis.com/v1/text:synthesize?key={api_key}"
        payload = {
            "input": {"ssml": ssml_text},
            "voice": {"languageCode": "es-ES", "name": "es-ES-Neural2-D"},
            "audioConfig": {"audioEncoding": "MP3", "speakingRate": 1.0, "pitch": 0.0}
        }

        response = requests.post(url, json=payload)
        if response.status_code == 200:
            audio_content = response.json().get("audioContent")
            raw_mp3 = tmpdir / f"{tmdb_id}_raw.mp3"
            with open(raw_mp3, "wb") as f: f.write(base64.b64decode(audio_content))
            
            final_wav = NARRATION_DIR / f"{tmdb_id}_narration.wav"
            subprocess.run(['ffmpeg', '-y', '-i', str(raw_mp3), '-ac', '2', '-ar', '44100', str(final_wav)], 
                           check=True, capture_output=True)
            return final_wav
        return None
    except Exception as e:
        logging.error(f"Error Audio: {e}")
        return None

def main():
    if not (STATE_DIR / "next_release.json").exists(): return None
    sel = json.loads((STATE_DIR / "next_release.json").read_text(encoding="utf-8"))
    
    with tempfile.TemporaryDirectory() as tmpdir_str:
        tmpdir = Path(tmpdir_str)
        hook, body = _generate_narration_parts(sel)
        if not hook: return None
        
        voice_path = _synthesize_google_ssml(hook, body, tmpdir, str(sel.get("tmdb_id")))
        if voice_path:
            logging.info(f"✅ Audio generado con Estrategia {sel.get('hook_angle', 'AUTO')}")
            return f"{hook} {body}", voice_path
    return None, None

if __name__ == "__main__":
    main()