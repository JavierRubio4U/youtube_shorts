# scripts/ai_narration.py
import json
import re
import subprocess
from pathlib import Path
import logging
import logging
import ollama

from moviepy.editor import AudioFileClip

import ollama
from langdetect import detect, DetectorFactory

import warnings
warnings.filterwarnings("ignore", message=".*torch.load.*weights_only.*")

# Para resultados consistentes
DetectorFactory.seed = 0

# --- Rutas y dirs ---
ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "output" / "state"
STATE.mkdir(parents=True, exist_ok=True)

# --- Logging ---
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

# --- Funciones de texto ---
def _normalize_text(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())

def _sentences(s: str):
    return [seg.strip() for seg in re.split(r"(?<=[\.\!\?…])\s+", s) if seg.strip()]

def _trim_to_words(text: str, max_words: int) -> str:
    words = text.split()
    if len(words) <= max_words:
        return text
    t = " ".join(words[:max_words])
    return t.rstrip(",;:") + "…"

# --- Funciones de IA ---
def _generate_narration_with_ai(sel: dict, model='mistral', max_words=60, min_words=50) -> str | None:
    """
    Genera una sinopsis con Ollama, con un intento de auto-corrección si excede la longitud.
    """
    initial_prompt = f"""
    Genera una sinopsis detallada y atractiva de entre {min_words} y {max_words} palabras en español para la película '{sel.get("titulo")}'.
    Es crucial que la sinopsis finalice con una oración completa y natural.
    NO excedas las {max_words} palabras.
    El título '{sel.get("titulo")}' es un nombre propio y NO debe traducirse.
    La sinopsis debe ser un párrafo cohesivo y no debe listar metadata como reparto o géneros.
    Usa la siguiente información para inspirarte:

    Título: {sel.get("titulo")}
    Sinopsis original: {sel.get("sinopsis")}
    Géneros: {', '.join(sel.get("generos"))}
    """
    
    try:
        # --- Primer Intento ---
        logging.info("Generando sinopsis (Intento 1)...")
        response = ollama.chat(model=model, messages=[{'role': 'user', 'content': initial_prompt}])
        generated_text = response['message']['content'].strip()
        word_count = len(generated_text.split())

        # Si el primer intento es demasiado largo, lo corregimos
        if word_count > max_words:
            logging.warning(
                f"Intento 1 generó {word_count} palabras (máximo {max_words}). Pidiendo a la IA que lo resuma."
            )
            
            # --- Segundo Intento (Auto-Corrección) ---
            refinement_prompt = f"""
            El siguiente texto es demasiado largo. Resume este texto para que tenga menos de {max_words} palabras.
            El resultado DEBE ser una frase completa y sonar natural. No lo cortes.
            Simplemente devuelve el texto corregido, sin añadir introducciones como 'Aquí tienes el resumen:'.

            Texto a corregir:
            "{generated_text}"
            """
            response = ollama.chat(model=model, messages=[{'role': 'user', 'content': refinement_prompt}])
            generated_text = response['message']['content'].strip()
            word_count = len(generated_text.split())

        # Verificación final
        if word_count > max_words or word_count < min_words:
            logging.warning(f"La narración final tiene {word_count} palabras, fuera del rango deseado ({min_words}-{max_words}).")
        else:
            logging.info(f"Narración generada con éxito ({word_count} palabras).")
            
        return generated_text

    except Exception as e:
        logging.error(f"Error al generar narración con Ollama: {e}")
        return None
    
def generate_narration(sel: dict, tmdb_id: str, slug: str, tmpdir=None) -> tuple[str | None, Path | None]:
    """
    Genera la narración con IA (usando auto-corrección) y sintetiza el audio.
    """
    logging.info("🔎 Generando sinopsis con IA local...")
    # La función de generación ahora se encarga de la longitud.
    narracion = _generate_narration_with_ai(sel)
    
    # Ya no necesitamos el log de "recortada" porque no se recorta.
    logging.info(f"Narración generada: {narracion[:100]}...") if narracion else logging.warning("No se generó narración.")

    voice_path = None
    if narracion:
        # ¡¡¡ LA LÍNEA DE _trim_to_words HA SIDO ELIMINADA !!!
        
        voice_path = (tmpdir / f"{tmdb_id}_{slug}_narracion.wav") if tmpdir else (STATE / f"{tmdb_id}_{slug}_narracion.wav")
        if not voice_path.exists():
            voice_path = _synthesize_xtts_with_pauses(narracion, voice_path, tmpdir) or _synthesize_tts_coqui(narracion, voice_path)
        
        if voice_path:
            # Este log sigue siendo útil para saber la duración final del audio
            audio_duration = AudioFileClip(str(voice_path)).duration
            logging.info(f"Audio de voz generado con duración de {audio_duration:.2f} segundos.")
        else:
            logging.warning("No se pudo generar el audio de voz. El vídeo no tendrá narración.")

    return narracion, voice_path

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

def _synthesize_tts_coqui(text: str, out_wav: Path) -> Path | None:
    try:
        from TTS.api import TTS
    except ImportError:
        logging.error("Coqui TTS no está instalado. No se generará audio de voz.")
        return None
    try:
        cleaned_text = _clean_for_tts(text)
        if not cleaned_text:
            return None
        tts = TTS(model_name="tts_models/es/css10/vits", progress_bar=False, gpu=False)
        tts.tts_to_file(text=cleaned_text, file_path=str(out_wav), language="es")
        if out_wav.exists() and out_wav.stat().st_size > 0:
            return out_wav
    except Exception as e:
        logging.error(f"Error en la síntesis de voz con VITS: {e}")
    return None

def _synthesize_xtts_with_pauses(text: str, out_wav: Path, tmpdir=None) -> Path | None:
    try:
        from TTS.api import TTS
    except ImportError:
        logging.error("Coqui TTS no está instalado. No se generará audio de voz.")
        return None

    cleaned_text = _clean_for_tts(text)
    if not cleaned_text:
        return None
    
    sents = _sentences(cleaned_text) or [cleaned_text]
    
    try:
        tts = TTS(model_name="tts_models/multilingual/multi-dataset/xtts_v2", progress_bar=False, gpu=False)
        
        tmp_parts = []
        for i, s in enumerate(sents, 1):
            part_path = (tmpdir / f"_xtts_part_{i}.wav") if tmpdir else (STATE / f"_xtts_part_{i}.wav")
            tts.tts_to_file(text=s, file_path=str(part_path), language="es", speaker="Alma María")
            if not part_path.exists(): raise FileNotFoundError
            tmp_parts.append(part_path)
        
        silence_path = (tmpdir / "_xtts_silence.wav") if tmpdir else (STATE / "_xtts_silence.wav")
        subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono", "-t", "0.35", "-q:a", "9", str(silence_path)], check=True, capture_output=True)
        
        seq = []
        for i, p in enumerate(tmp_parts):
            seq.append(p)
            if i < len(tmp_parts) - 1:
                seq.append(silence_path)
        
        if _concat_wav_ffmpeg(seq, out_wav):
            for p in set(tmp_parts + [silence_path]):
                p.unlink()
            return out_wav
    except Exception as e:
        logging.error(f"Error en la síntesis XTTS: {e}")
    return None

# (Opcional: si usas main() standalone, lo puedes dejar; si no, elimínalo)
def main():
    SEL_FILE = STATE / "next_release.json"
    if not SEL_FILE.exists():
        raise SystemExit("Falta next_release.json.")

    sel = json.loads(SEL_FILE.read_text(encoding="utf-8"))
    tmdb_id = sel.get("tmdb_id", "unknown")
    title = sel.get("titulo") or sel.get("title") or ""
    slug = slugify(title)  # Asumiendo que slugify está definido en otro lado

    narracion, voice_path = generate_narration(sel, tmdb_id, slug)
    return narracion, voice_path

if __name__ == "__main__":
    main()