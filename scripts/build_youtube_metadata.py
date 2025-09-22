# scripts/build_youtube_metadata.py
import json
import re
from pathlib import Path
from datetime import datetime
try:
    from datetime import UTC
except ImportError:
    from datetime import timezone as _tz
    UTC = _tz.utc

import ollama
from langdetect import detect, DetectorFactory
import logging

# Configuración básica opcional (para que logging funcione sin errores y muestre mensajes)
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

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
    return all('a' <= c.lower() <= 'z' or c.isdigit() or c in 'áéíóúüñÁÉÍÓÚÜÑ\s\:\-\!\?\.,\'"' for c in text)

def _translate_with_ai(text: str, title: str, model='mistral') -> str | None:
    """Traduce un texto usando un modelo local de Ollama."""
    try:
        if detect(title) == 'es' or re.search(r'\d', title):
            logging.info(f"Título '{title}' ya es adecuado; manteniendo original.")
            return title
        
        prompt = f"""Si el siguiente título es un nombre propio de franquicia o película popular, no lo traduzcas y mantén el original. De lo contrario, tradúcelo al español de forma natural, sin añadir ninguna explicación adicional. Título: {text}"""
        response = ollama.chat(model=model, messages=[
            {'role': 'user', 'content': prompt}
        ])
        translated_text = response['message']['content'].strip()
        translated_text = re.sub(r'\s*\([^)]*\)|\n.*', '', translated_text).strip()
        return translated_text
    except Exception as e:
        logging.error(f"❌ Error al traducir el título con Ollama: {e}")
        return None

def _shorten(text: str, max_len: int) -> str:
    if not text:
        return ""
    text = text.strip()
    if len(text) > max_len:
        text = text[:max_len].rstrip()
        last_space = text.rfind(' ')
        if last_space != -1:
            text = text[:last_space]
        text = text.rstrip('.,-') + '...'
    return text

def _format_date(release_date: str) -> str:
    if not release_date:
        return ""
    try:
        dt = datetime.strptime(release_date, "%Y-%m-%d")
        return dt.strftime("%d/%m/%y")
    except ValueError:
        return release_date

def _make_title(titulo: str, fecha: str, plataformas: list[str]) -> str:
    formatted_date = _format_date(fecha)
    # Si hay plataformas, las unimos en una cadena
    plataforma_str = ", ".join(plataformas)
    if plataforma_str:
        base = f"{titulo} — {plataforma_str} {formatted_date}".strip()
    else:
        # Si no hay plataforma, mantenemos el formato original
        base = f"{titulo} — {formatted_date}".strip()
    return _shorten(base, 90)

def _make_tags(generos, reparto_top, max_cast=3):
    tags = ["pelicula", "cine","shorts"]
    for g in (generos or []):
        g = g.strip()
        if g and g.lower() not in [t.lower() for t in tags]:
            tags.append(g)
    for name in (reparto_top or [])[:max_cast]:
        name = name.strip()
        if name and name.lower() not in [t.lower() for t in tags]:
            tags.append(name)
    total = 0
    kept = []
    for t in tags:
        if total + len(t) + 1 > 480:
            break
        kept.append(t)
        total += len(t) + 1
    return kept

def _is_made_for_kids(cert: str | None, genres: list[str]) -> bool:
    cert = (cert or "").upper()
    if not cert:
        genres_lower = {g.lower() for g in genres}
        if {"animación", "familiar", "ciencia ficción", "fantasía"} & genres_lower:
            return True
        return False
    return cert.startswith("APTA") or cert in ("G", "E", "T")

def main():
    if not SEL_FILE.exists() or not MANIFEST.exists():
        raise SystemExit("Falta next_release.json o assets_manifest.json.")

    sel = json.loads(SEL_FILE.read_text(encoding="utf-8"))
    man = json.loads(MANIFEST.read_text(encoding="utf-8"))

    titulo = sel.get("titulo") or "Estreno"
    
    try:
        if not _is_latin_text(titulo) or detect(titulo) != "es":
            logging.info(f"🌐 Traduciendo título '{titulo}' a español...")
            translated_title = _translate_with_ai(titulo, titulo)
            if translated_title and translated_title.strip() and translated_title != titulo:
                titulo = translated_title
                sel["titulo"] = titulo
                logging.info(f"✅ Título traducido: {titulo}")
            else:
                logging.warning(f"⚠ Traducción no válida o no necesaria, manteniendo título original: {titulo}")
        else:
            logging.info(f"✅ Título ya en español: {titulo}")
    except Exception as e:
        logging.warning(f"⚠ Fallo en la detección o traducción del título: {e}, manteniendo título original: {titulo}")

    fecha_es = sel.get("fecha_estreno") or ""
    generos = sel.get("generos") or []
    reparto = sel.get("reparto_top") or []
    hype = sel.get("hype")
    vote_avg = sel.get("vote_average")
    vote_count = sel.get("vote_count")
    sinopsis = sel.get("sinopsis") or ""
    trailer = man.get("trailer_url") or sel.get("trailer_url")
    certificacion = sel.get("certificacion_ES")
    platforms = sel.get('platforms')

    title = _make_title(titulo, fecha_es, platforms)
    
    tags = _make_tags(generos, reparto, max_cast=3)
    made_for_kids = _is_made_for_kids(certificacion, generos)

    lines = []
    lines.append(title)
    if generos:
        lines.append("Género: " + ", ".join(generos))
    if reparto:
        lines.append("Reparto: " + ", ".join(reparto[:5]))
    if sinopsis:
        lines.append("")
        lines.append("Sinopsis:")
        lines.append(_shorten(sinopsis, 600))
    if trailer:
        lines.append("")
        lines.append(f"Tráiler oficial: {trailer}")

    lines.append("")
    lines.append("Créditos de datos y poster: The Movie Database (TMDb)")

    description = "\n".join(lines)
    
    payload = {
        "tmdb_id": sel["tmdb_id"],
        "title": title,  # El título ya no tiene #shorts
        "description": description,
        "tags": tags,
        "default_visibility": "public",
        "shorts": True,
        "made_for_kids": made_for_kids,
        "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z")
    }

    YT_META.parent.mkdir(parents=True, exist_ok=True)
    YT_META.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    logging.info(f"✅ YouTube metadata generado en: {YT_META}")

if __name__ == "__main__":
    main()