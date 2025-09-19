# scripts/select_next_release.py
import json
from pathlib import Path
from datetime import datetime
try:
    from datetime import UTC
except ImportError:
    from datetime import timezone as _tz
    UTC = _tz.utc

from get_releases import get_week_releases_enriched

# Importar yt_dlp para chequear formatos y search
import yt_dlp
import logging
import subprocess
import json as json_lib
import tempfile
import os

ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = ROOT / "output" / "state"
STATE_DIR.mkdir(parents=True, exist_ok=True)

PUBLISHED_FILE = STATE_DIR / "published.json"
NEXT_FILE = STATE_DIR / "next_release.json"

import re
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

class SilentLogger:
    """Logger silencioso para suprimir output de yt-dlp (tablas y warnings)."""
    def debug(self, msg): pass
    def info(self, msg): pass
    def warning(self, msg): pass
    def error(self, msg): logging.error(msg)

def _load_state():
    if PUBLISHED_FILE.exists():
        return json.loads(PUBLISHED_FILE.read_text(encoding="utf-8"))
    return {"published_ids": [], "picked_ids": []}

def _save_state(state):
    PUBLISHED_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    logging.info(f"Estado guardado en {PUBLISHED_FILE} con published_ids: {state.get('published_ids')}")

def reset_picked(tmdb_id: int):
    """Borra una ID específica de picked_ids para re-escogerla."""
    state = _load_state()
    if tmdb_id in state.get("picked_ids", []):
        state["picked_ids"].remove(tmdb_id)
        _save_state(state)
        logging.info(f"✅ ID {tmdb_id} borrada de picked_ids.")
    else:
        logging.info(f"ID {tmdb_id} no estaba en picked_ids.")

def has_high_quality_format(trailer_url: str, min_height=1080) -> bool:
    """Chequea metadata sin descargar (rápido, sin logs ni tablas)."""
    if not trailer_url:
        return False
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'simulate': True,
        'logger': SilentLogger(),
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(trailer_url, download=False)
            formats = info.get('formats', [])
            heights = [f.get('height', 0) for f in formats if f.get('vcodec') and f.get('height')]
            max_height = max(heights) if heights else 0
            return max_height >= min_height
    except Exception:
        return False  # Silencioso

def verify_trailer_quality(trailer_url: str, min_height=1080, verify_by_download=False) -> bool:
    """Verifica calidad: primero metadata, luego descarga y ffprobe si se pide (silencioso)."""
    if not has_high_quality_format(trailer_url, min_height):
        return False
    if not verify_by_download:
        return True

    with tempfile.NamedTemporaryFile(delete=False, suffix='.mp4') as tmp:
        temp_path = tmp.name
    try:
        # Chequeo pre-ffprobe para evitar warnings
        ydl_opts = {
            'format': f'bestvideo[height>={min_height}]+bestaudio/best',
            'outtmpl': temp_path,
            'quiet': True,
            'no_warnings': True,
            'logger': SilentLogger(),
            'merge_output_format': 'mp4',
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([trailer_url])
        
        # Valida archivo antes de ffprobe
        if not os.path.exists(temp_path) or os.path.getsize(temp_path) == 0:
            logging.warning(f"Archivo temp inválido en verificación: {temp_path}. Asumiendo inválido.")
            return False
        
        cmd = ['ffprobe', '-v', 'quiet', '-print_format', 'json', '-show_streams', temp_path]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode == 0:
            data = json_lib.loads(result.stdout)
            for stream in data['streams']:
                if stream.get('codec_type') == 'video':
                    actual_height = int(stream.get('height', 0))
                    return actual_height >= min_height
        logging.warning(f"ffprobe falló en verificación: {temp_path}. Asumiendo inválido.")
        return False
    except Exception:
        logging.warning(f"Excepción en verificación de tráiler: {trailer_url}. Asumiendo inválido.")
        return False
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)

def find_best_hype_trailer(title: str, verify_by_download=False, min_height=1080) -> str | None:
    """Busca en YouTube el tráiler oficial con más views y máxima calidad (>= min_height preferido)."""
    search_queries = [
        f"tráiler oficial {title}",
        f"official trailer {title}"
    ]  # Prueba en ES y EN para más resultados (da igual idioma)

    best_url = None
    best_views = 0
    best_height = 0

    for query in search_queries:
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'extract_flat': True,  # No descargar, solo info
            'skip_download': True,
            'logger': SilentLogger(),
            'playlistend': 20,  # Limitar a top 20
            'match_filter': lambda info: 'trailer' in (info.get('title', '') or '').lower(),
        }
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                search_results = ydl.extract_info(f"ytsearch20:{query}", download=False)['entries']
                for entry in search_results:
                    if not entry:
                        continue
                    url = entry.get('url')
                    views = entry.get('view_count', 0)
                    height = entry.get('height', 0) or 0
                    if height < min_height:
                        continue
                    if verify_by_download:
                        if not verify_trailer_quality(url, min_height, verify_by_download):
                            continue
                    best_url = url
                    best_views = views
                    best_height = height
                    logging.info(f"Encontrado mejor candidato en YouTube: {url} (views: {views}, height: {height}p)")
                    # No break: check all for potentially better

        except Exception as e:
            logging.warning(f"Error en búsqueda YouTube para '{query}': {e}")

    if best_url:
        logging.info(f"✅ Mejor tráiler YouTube encontrado: {best_url} (views: {best_views}, height: {best_height}p)")
        return best_url
    else:
        logging.warning(f"No tráiler viable (>= {min_height}p) encontrado en YouTube para '{title}'.")
        return None

# Buscar tráiler oficial con más hype que cumpla calidad (pref >=1080p)
def pick_next(verify_by_download=False):
    state = _load_state()
    exclude = set(state.get("published_ids", []) + state.get("picked_ids", []))
    logging.info(f"IDs excluidas (published + picked): {exclude}")

    movies = get_week_releases_enriched()
    logging.info(f"Candidatos totales: {len(movies)}")

    top_6 = [m for m in movies if m["id"] not in exclude][:6]
    logging.info(f"Top 6 candidatos por hype: {len(top_6)}")
    for i, m in enumerate(top_6):
        platforms_str = ', '.join(m.get('platforms', [])) or 'No especificado'
        logging.info(f"📋 Candidato {i+1}: {m['titulo']} (ID: {m['id']}, hype: {m['hype']}, platforms: {platforms_str})")

    candidate = None
    trailer_url = None
    for m in top_6:  # Prioriza highest hype first
        logging.info(f"Probando {m['titulo']} (hype: {m['hype']})...")
        tmdb_trailer = m.get("trailer")
        quality_ok = False
        if tmdb_trailer:
            logging.info(f"🔍 Probando TMDB: {tmdb_trailer}")
            if has_high_quality_format(tmdb_trailer, 1080):
                quality_ok = verify_trailer_quality(tmdb_trailer, 1080, verify_by_download)
            if quality_ok:
                candidate = m
                trailer_url = tmdb_trailer
                logging.info(f"✅ TMDB viable (>=1080p) para {m['titulo']}.")
                break

        # Fallback a YouTube si TMDB no viable
        youtube_trailer = find_best_hype_trailer(m["titulo"], verify_by_download)
        if youtube_trailer and has_high_quality_format(youtube_trailer, 1080):
            quality_ok = verify_trailer_quality(youtube_trailer, 1080, verify_by_download)
            if quality_ok:
                candidate = m
                trailer_url = youtube_trailer
                logging.info(f"✅ YouTube viable (>=1080p) para {m['titulo']}.")
                break

    if not candidate:
        print("🛑 No películas con tráiler viable (>=1080p) en top 6.")
        logging.warning("Ningún top 6 viable.")
        return None

    payload = {
        "tmdb_id": candidate["id"],
        "titulo": candidate["titulo"],
        "fecha_estreno": candidate["fecha_estreno"],
        "hype": candidate["hype"],
        "vote_average": candidate["vote_average"],
        "vote_count": candidate["vote_count"],
        "popularity": candidate["popularity"],
        "generos": candidate["generos"],
        "sinopsis": candidate["sinopsis"],
        "poster_principal": candidate["poster_principal"],
        "posters": candidate["posters"][:5],
        "backdrops": candidate["backdrops"][:8],
        "trailer_url": trailer_url,
        "providers_ES": candidate["providers_ES"],
        "certificacion_ES": candidate["certificacion_ES"],
        "reparto_top": candidate["reparto_top"],
        "keywords": candidate["keywords"],
        "platforms": candidate["platforms"],  # Nuevo: Incluir platforms en el payload
        "seleccion_generada": datetime.now(UTC).isoformat().replace("+00:00", "Z")
    }

    NEXT_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    logging.info(f"Siguiente guardada: {NEXT_FILE} (ID: {candidate['id']})")
    logging.info("ℹ️ No marcado como published (se hace en script de subida a YouTube).")

    print("✅ Siguiente guardada en:", NEXT_FILE)
    print(f"- {payload['titulo']} ({payload['fecha_estreno']}) HYPE={payload['hype']}")
    print("  Trailer:", payload["trailer_url"])
    return payload

def mark_published(tmdb_id: int, simulate=False):
    state = _load_state()
    if tmdb_id not in state.get("published_ids", []):
        state.setdefault("published_ids", []).append(tmdb_id)
        state["published_ids"] = sorted(set(state["published_ids"]))
        if tmdb_id not in state.get("picked_ids", []):
            state.setdefault("picked_ids", []).append(tmdb_id)
            state["picked_ids"] = state["picked_ids"][-50:]
        if not simulate:
            _save_state(state)
        logging.info(f"ID {tmdb_id} marcada publicada (y en picked){' (simulado)' if simulate else ''}.")
    else:
        logging.info(f"ID {tmdb_id} ya publicada.")

if __name__ == "__main__":
    pick_next(verify_by_download=False)  # Set to True for stricter verification (slower)