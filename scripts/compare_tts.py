"""
Compara ElevenLabs vs Voxtral TTS (Mistral) usando el texto del próximo vídeo.

Genera dos archivos de audio en assets/narration/comparison/:
  - comparison_elevenlabs.mp3
  - comparison_voxtral.mp3
  - comparison_texto.txt  (el texto enviado a ambos)

Uso:
  python scripts/compare_tts.py

Requiere:
  - config/elevenlabs_api_key.txt  (ya existe)
  - config/mistral_api_key.txt     (nueva, obtener en console.mistral.ai)
  - pip install mistralai
"""

import json
import logging
import subprocess
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = ROOT / "config"
TMP_DIR = ROOT / "assets" / "tmp"
COMPARISON_DIR = ROOT / "assets" / "narration" / "comparison"
COMPARISON_DIR.mkdir(parents=True, exist_ok=True)

# Importamos la lógica de narración existente
sys.path.insert(0, str(Path(__file__).parent))
from ai_narration import _generate_narration_parts, _clean_text_for_eleven, ELEVEN_VOICE_ID, ELEVEN_MODEL_ID

import requests


def _load_key(filename: str) -> str | None:
    path = CONFIG_DIR / filename
    if not path.exists():
        logging.error(f"❌ Falta {filename} en /config")
        return None
    key = path.read_text(encoding="utf-8").strip()
    return key if key else None


def generate_elevenlabs(text: str) -> Path | None:
    api_key = _load_key("elevenlabs_api_key.txt")
    if not api_key:
        return None

    out_path = COMPARISON_DIR / "comparison_elevenlabs.mp3"
    tmp_path = COMPARISON_DIR / "comparison_elevenlabs_raw.mp3"

    url = f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVEN_VOICE_ID}"
    headers = {"xi-api-key": api_key, "Content-Type": "application/json"}
    payload = {
        "text": text,
        "model_id": ELEVEN_MODEL_ID,
        "voice_settings": {
            "stability": 0.5,
            "similarity_boost": 0.75,
            "style": 0.5,
            "use_speaker_boost": True,
        },
    }

    logging.info("🎙️ Generando audio con ElevenLabs...")
    r = requests.post(url, json=payload, headers=headers)
    if r.status_code != 200:
        logging.error(f"❌ ElevenLabs error {r.status_code}: {r.text}")
        return None

    tmp_path.write_bytes(r.content)

    # Misma aceleración 1.1x que usa el pipeline real
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(tmp_path), "-filter:a", "atempo=1.1", "-vn", str(out_path)],
            check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        tmp_path.unlink()
    except Exception:
        tmp_path.rename(out_path)

    logging.info(f"✅ ElevenLabs → {out_path}")
    return out_path


def generate_voxtral(text: str) -> Path | None:
    api_key = _load_key("mistral_api_key.txt")
    if not api_key:
        logging.info("💡 Obtén tu key en https://console.mistral.ai → API Keys")
        return None

    try:
        from mistralai.client import Mistral
    except ImportError:
        logging.error("❌ Falta el paquete mistralai. Ejecuta: pip install mistralai")
        return None

    out_path = COMPARISON_DIR / "comparison_voxtral.mp3"

    logging.info("🎙️ Generando audio con Voxtral TTS (Mistral)...")
    try:
        client = Mistral(api_key=api_key)
        response = client.audio.speech.complete(
            model="voxtral-mini-tts-2603",
            input=text,
            response_format="mp3",
        )
        out_path.write_bytes(response.content)
        logging.info(f"✅ Voxtral → {out_path}")
        return out_path
    except Exception as e:
        logging.error(f"❌ Voxtral error: {e}")
        return None


def main():
    next_release = TMP_DIR / "next_release.json"
    if not next_release.exists():
        logging.error("❌ No existe assets/tmp/next_release.json — ejecuta primero find.py")
        return

    sel = json.loads(next_release.read_text(encoding="utf-8"))
    logging.info(f"🎬 Película: {sel.get('titulo')}")

    hook, body = _generate_narration_parts(sel)
    if not hook:
        logging.error("❌ No se pudo generar el guion con Gemini")
        return

    safe_hook = _clean_text_for_eleven(hook)
    safe_body = _clean_text_for_eleven(body)
    full_text = f"{safe_hook} ... {safe_body}"

    # Guardamos el texto para referencia
    texto_path = COMPARISON_DIR / "comparison_texto.txt"
    texto_path.write_text(full_text, encoding="utf-8")
    logging.info(f"\n📝 Texto generado:\n{full_text}\n")

    eleven_path = generate_elevenlabs(full_text)
    voxtral_path = generate_voxtral(full_text)

    print("\n" + "="*50)
    print("COMPARACIÓN GENERADA")
    print("="*50)
    print(f"Texto:      {texto_path}")
    if eleven_path:
        print(f"ElevenLabs: {eleven_path}")
    if voxtral_path:
        print(f"Voxtral:    {voxtral_path}")
    print("="*50)


if __name__ == "__main__":
    main()
