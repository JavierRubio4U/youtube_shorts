# scripts/build_youtube_metadata.py
import json
import re
from pathlib import Path
from datetime import datetime
try:
    from datetime import UTC            # Py 3.11+
except ImportError:                     # Py 3.8–3.10
    from datetime import timezone as _tz
    UTC = _tz.utc

import ollama
from langdetect import detect, DetectorFactory

# Para resultados consistentes
DetectorFactory.seed = 0

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "output" / "state"
SEL_FILE = STATE / "next_release.json"
MANIFEST = STATE / "assets_manifest.json"
YT_META = STATE / "youtube_metadata.json"

def _is_latin_text(text: str) -> bool:
    """Devuelve True si el texto contiene solo caracteres latinos."""
    if not text:
        return False
    # Modificación: solo comprueba si *todos* los caracteres son latinos
    return all('a' <= c.lower() <= 'z' or c.isdigit() or c in 'áéíóúüñÁÉÍÓÚÜÑ\s\:\-\!\?\.,\'"' for c in text)

def _translate_with_ai(text: str, model='mistral') -> str | None:
    """Traduce un texto usando un modelo local de Ollama."""
    try:
        prompt = f"""Traduce el siguiente texto al español de forma natural, sin añadir ninguna explicación adicional:
        {text}
        """
        response = ollama.chat(model=model, messages=[
            {'role': 'user', 'content': prompt}
        ])
        
        # Elimina cualquier texto de traducción automática
        translated_text = response['message']['content'].strip()
        translated_text = re.sub(r'\(.*?\)', '', translated_text)
        return translated_text.strip()
    except Exception as e:
        print(f"❌ Error al traducir el título con Ollama: {e}")
        return None

def _shorten(text: str, max_len: int) -> str:
    if not text:
        return ""
    text = text.strip()
    # Asegura que el título nunca supere la longitud máxima de YouTube (100)
    if len(text) > max_len:
        text = text[:max_len].rstrip()
        # Vuelve atrás hasta el último espacio para no cortar una palabra
        last_space = text.rfind(' ')
        if last_space != -1:
            text = text[:last_space]
        text = text.rstrip('.,-') + '...'
    return text

def _make_title(titulo: str, fecha: str) -> str:
    # NUEVO: Título compacto y claro para Shorts
    base = f"{titulo} — estreno en España {fecha}".strip()
    return _shorten(base, 90)

def _make_tags(generos, reparto_top, max_cast=3):
    """
    Tags = géneros + 2–3 actores principales.
    Sin genéricos repetitivos tipo 'Estrenos', 'Cine', 'Películas', 'Trailer'.
    """
    tags = []
    # Géneros (tal cual, con espacios permitidos en tags de YouTube)
    for g in (generos or []):
        g = g.strip()
        if g and g not in tags:
            tags.append(g)

    # Actores principales
    for name in (reparto_top or [])[:max_cast]:
        name = name.strip()
        if name and name not in tags:
            tags.append(name)

    # (Opcional) cota suave por si algún día se desmadra
    # YouTube limita ~500 chars totales; aquí recortamos si se supera:
    total = 0
    kept = []
    for t in tags:
        if total + len(t) + 1 > 480:  # margen
            break
        kept.append(t)
        total += len(t) + 1
    return kept

def _is_made_for_kids(cert: str | None, genres: list[str]) -> bool:
    """Decide si un vídeo es apto para niños basándose en la certificación."""
    cert = (cert or "").upper()
    if not cert:
        # Si no hay certificación, revisa géneros de forma conservadora
        genres_lower = {g.lower() for g in genres}
        if {"animación", "familiar", "ciencia ficción", "fantasía"} & genres_lower:
            return True
        return False
    
    # Se considera apto para niños si es "APTA PARA TODOS LOS PÚBLICOS"
    # o una clasificación similar que signifique sin restricción de edad.
    return cert.startswith("APTA") or cert in ("G", "E", "T")


def main():
    if not SEL_FILE.exists() or not MANIFEST.exists():
        raise SystemExit("Falta next_release.json o assets_manifest.json. Ejecuta primero el pipeline hasta descargar assets.")

    sel = json.loads(SEL_FILE.read_text(encoding="utf-8"))
    man = json.loads(MANIFEST.read_text(encoding="utf-8"))

    titulo = sel.get("titulo") or "Estreno"
    
    # NUEVO: traducir el título solo si no tiene caracteres latinos
    # y el idioma no es español
    try:
        if not _is_latin_text(titulo):
            print(f"🌐 Traduciendo título '{titulo}' a español...")
            translated_title = _translate_with_ai(titulo)
            if translated_title:
                titulo = translated_title
                sel["titulo"] = titulo
                print("✅ Título traducido:", titulo)
    except Exception as e:
        print(f"⚠ Fallo en la detección o traducción del título: {e}")

    fecha_es = sel.get("fecha_estreno") or ""
    generos = sel.get("generos") or []
    reparto = sel.get("reparto_top") or []
    hype = sel.get("hype")
    vote_avg = sel.get("vote_average")
    vote_count = sel.get("vote_count")
    sinopsis = sel.get("sinopsis") or ""
    trailer = man.get("trailer_url") or sel.get("trailer_url")
    certificacion = sel.get("certificacion_ES")

    title = _make_title(titulo, fecha_es)
    tags = _make_tags(generos, reparto, max_cast=3)
    made_for_kids = _is_made_for_kids(certificacion, generos)


    # Descripción rica pero concisa (puedes ajustar longitudes si quieres)
    lines = []
    lines.append(f"{titulo} — estreno en España: {fecha_es}".strip())
    if generos:
        lines.append("Género: " + ", ".join(generos))
    if reparto:
        lines.append("Reparto: " + ", ".join(reparto[:5]))
    # Métricas informativas (opcionales)
    metrics = []
    if hype is not None:        metrics.append(f"Hype: {hype}")
    if vote_avg is not None:    metrics.append(f"TMDb: {vote_avg} ({vote_count or 0} votos)")
    if metrics:
        lines.append(" | ".join(metrics))
    if sinopsis:
        lines.append("")
        lines.append("Sinopsis:")
        lines.append(_shorten(sinopsis, 600))
    if trailer:
        lines.append("")
        lines.append(f"Tráiler oficial: {trailer}")

    lines.append("")
    lines.append("Créditos de datos e imágenes: The Movie Database (TMDb)")
    lines.append("Voz sintética: Coqui TTS (modelo xtts_v2)") # NUEVO: Crédito para la voz
    

    description = "\n".join(lines)

    payload = {
        "tmdb_id": sel["tmdb_id"],
        "title": title,
        "description": description,
        "tags": tags,
        "default_visibility": "public",   # cambia a "unlisted" si prefieres revisar primero
        "shorts": True,
        "made_for_kids": made_for_kids,   # NUEVO: apto para niños
        "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z")
    }

    YT_META.parent.mkdir(parents=True, exist_ok=True)
    YT_META.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print("✅ YouTube metadata generado en:", YT_META)

if __name__ == "__main__":
    main()