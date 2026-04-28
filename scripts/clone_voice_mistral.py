"""
Genera 3 versiones de audio para comparar expresividad:

  A) voxtral_v1_referencia_normal.mp3   — ref. ElevenLabs estilo normal (ya existe)
  B) voxtral_v2_referencia_expresiva.mp3 — ref. ElevenLabs style=1.0 (más exagerada)
  C) voxtral_v3_stage_directions.mp3    — ref. expresiva + stage directions en el texto

También genera la referencia expresiva de ElevenLabs por si quieres escucharla:
  elevenlabs_expresiva.mp3

Uso:
  python scripts/clone_voice_mistral.py
"""

import base64
import json
import logging
import sys
import requests
from pathlib import Path

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = ROOT / "config"
NARRATION_DIR = ROOT / "assets" / "narration"
COMPARISON_DIR = ROOT / "assets" / "narration" / "comparison"
TMP_DIR = ROOT / "assets" / "tmp"
COMPARISON_DIR.mkdir(parents=True, exist_ok=True)

REFERENCE_NORMAL   = NARRATION_DIR / "1381071_narration.mp3"
REFERENCE_EXPRESIVA = COMPARISON_DIR / "elevenlabs_expresiva.mp3"

ELEVEN_VOICE_ID = "2VUqK4PEdMj16L6xTN4J"
ELEVEN_MODEL_ID = "eleven_multilingual_v2"

sys.path.insert(0, str(Path(__file__).parent))
from ai_narration import _generate_narration_parts, _clean_text_for_eleven


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_mistral_client():
    try:
        from mistralai.client import Mistral
    except ImportError:
        logging.error("❌ Falta mistralai. Ejecuta: pip install mistralai")
        sys.exit(1)
    key_path = CONFIG_DIR / "mistral_api_key.txt"
    if not key_path.exists():
        logging.error("❌ Falta config/mistral_api_key.txt")
        sys.exit(1)
    return Mistral(api_key=key_path.read_text(encoding="utf-8").strip())


def get_text() -> str:
    comparison_texto = COMPARISON_DIR / "comparison_texto.txt"
    if comparison_texto.exists():
        logging.info("📄 Usando texto de comparison_texto.txt")
        return comparison_texto.read_text(encoding="utf-8").strip()

    next_release = TMP_DIR / "next_release.json"
    if next_release.exists():
        logging.info("🧠 Generando texto con Gemini...")
        sel = json.loads(next_release.read_text(encoding="utf-8"))
        hook, body = _generate_narration_parts(sel)
        if hook:
            text = f"{_clean_text_for_eleven(hook)} ... {_clean_text_for_eleven(body)}"
            comparison_texto.write_text(text, encoding="utf-8")
            return text

    logging.warning("⚠️ Usando texto de prueba fijo")
    return "Sobrevives a una guerra mundial y para rematar un lagarto gigante te destroza el barrio. Pasan dos anitos, estas tan pancho haciendote la cena y pumba! El bicho vuelve con mas mala leche a terminar el curro. Son unos pobres desgraciados intentando no ser la tapa de un reptil tamano familiar. Fijo que el seguro de hogar no cubre tremendo bocado."


def elevenlabs_generate(text: str, style: float, out_path: Path) -> Path | None:
    key_path = CONFIG_DIR / "elevenlabs_api_key.txt"
    if not key_path.exists():
        logging.error("❌ Falta config/elevenlabs_api_key.txt")
        return None
    api_key = key_path.read_text(encoding="utf-8").strip()

    payload = {
        "text": text,
        "model_id": ELEVEN_MODEL_ID,
        "voice_settings": {
            "stability": 0.3,          # más bajo = más variación/expresividad
            "similarity_boost": 0.75,
            "style": style,
            "use_speaker_boost": True,
        },
    }
    r = requests.post(
        f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVEN_VOICE_ID}",
        json=payload,
        headers={"xi-api-key": api_key, "Content-Type": "application/json"},
    )
    if r.status_code != 200:
        logging.error(f"❌ ElevenLabs error {r.status_code}: {r.text}")
        return None
    out_path.write_bytes(r.content)
    logging.info(f"✅ ElevenLabs (style={style}) → {out_path.name}")
    return out_path


def voxtral_generate(client, text: str, ref_audio_b64: str, out_path: Path) -> Path:
    response = client.audio.speech.complete(
        model="voxtral-mini-tts-2603",
        input=text,
        ref_audio=ref_audio_b64,
        response_format="mp3",
    )
    out_path.write_bytes(base64.b64decode(response.audio_data))
    logging.info(f"✅ Voxtral → {out_path.name}")
    return out_path


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    client = get_mistral_client()
    text = get_text()

    # --- Stage directions version del texto ---
    text_con_direcciones = (
        "(habla con mucha emocion, exageracion comica y acento andaluz de barrio, "
        "como si le contaras algo increible a tus colegas en un bar) "
        + text
    )

    print(f"\nTexto base:\n{text}\n")

    # --- PASO 1: Referencia expresiva con ElevenLabs style=1.0 ---
    logging.info("━" * 50)
    logging.info("PASO 1 — Generando referencia expresiva con ElevenLabs (style=1.0)...")
    ref_expresiva = elevenlabs_generate(text, style=1.0, out_path=REFERENCE_EXPRESIVA)

    # --- PASO 2: Voxtral v2 con referencia expresiva ---
    logging.info("━" * 50)
    logging.info("PASO 2 — Voxtral con referencia expresiva...")
    if ref_expresiva and ref_expresiva.exists():
        ref_exp_b64 = base64.b64encode(ref_expresiva.read_bytes()).decode()
        voxtral_generate(client, text, ref_exp_b64, COMPARISON_DIR / "voxtral_v2_referencia_expresiva.mp3")
    else:
        logging.warning("⚠️ Saltando v2 porque falló ElevenLabs")

    # --- PASO 3: Voxtral v3 con referencia expresiva + stage directions ---
    logging.info("━" * 50)
    logging.info("PASO 3 — Voxtral con referencia expresiva + stage directions...")
    if ref_expresiva and ref_expresiva.exists():
        voxtral_generate(client, text_con_direcciones, ref_exp_b64, COMPARISON_DIR / "voxtral_v3_stage_directions.mp3")
    else:
        logging.warning("⚠️ Saltando v3 porque falló ElevenLabs")

    print("\n" + "="*55)
    print("COMPARATIVA COMPLETA — escúchalos en orden:")
    print("="*55)
    print(f"  [REF]  {COMPARISON_DIR / 'comparison_elevenlabs.mp3'}")
    print(f"  [REF+] {REFERENCE_EXPRESIVA.name}  ← ElevenLabs style=1.0")
    print(f"  [V1]   voxtral_v1_referencia_normal.mp3    ← ref normal")
    print(f"  [V2]   voxtral_v2_referencia_expresiva.mp3 ← ref style=1.0")
    print(f"  [V3]   voxtral_v3_stage_directions.mp3     ← ref+1.0 + stage dirs")
    print("="*55)


if __name__ == "__main__":
    main()
