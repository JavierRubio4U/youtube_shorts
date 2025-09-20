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

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

class SilentLogger:
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

def has_high_quality_format(trailer_url: str, min_height=1080) -> bool:
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
            if not formats: return False
            heights = [f.get('height', 0) for f in formats if f.get('vcodec') != 'none' and f.get('height')]
            max_height = max(heights) if heights else 0
            logging.info(f"Máxima resolución encontrada para {trailer_url}: {max_height}p")
            return max_height >= min_height
    except Exception:
        return False

# --- FUNCIÓN DE BÚSQUEDA MEJORADA ---
def find_best_hype_trailer(title: str, year: str, min_height=1080) -> str | None:
    """
    Busca en YouTube el tráiler más relevante, valida su calidad real y devuelve el mejor.
    """
    # Consultas de búsqueda más variadas y efectivas
    search_queries = [
        f'"{title}" ({year}) trailer oficial',
        f'"{title}" official trailer',
        f'"{title}" trailer'
    ]

    valid_candidates = []

    for query in search_queries:
        logging.info(f"Buscando en YouTube: '{query}'")
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'skip_download': True,
            'logger': SilentLogger(),
            'extract_flat': 'in_playlist', # Extraer info básica de resultados
            'playlistend': 5, # Analizar los primeros 5 resultados de cada búsqueda
        }
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                search_results = ydl.extract_info(f"ytsearch5:{query}", download=False).get('entries', [])
                
                for entry in search_results:
                    if not entry: continue
                    
                    video_url = entry.get('url')
                    video_title = entry.get('title', '').lower()
                    view_count = entry.get('view_count', 0)

                    # Filtro básico para evitar clips y análisis
                    if any(keyword in video_title for keyword in ['clip', 'escena', 'featurette', 'review', 'análisis']):
                        continue

                    logging.info(f"  > Verificando calidad de: {video_url}")
                    # Verificación de calidad real antes de considerarlo candidato
                    if has_high_quality_format(video_url, min_height):
                        logging.info(f"    ✓ Calidad OK. Vistas: {view_count}")
                        valid_candidates.append({'url': video_url, 'views': view_count})
                    else:
                        logging.info(f"    ✗ Calidad insuficiente.")

        except Exception as e:
            logging.warning(f"Error en búsqueda YouTube para '{query}': {e}")
    
    if not valid_candidates:
        logging.warning(f"No se encontraron tráilers de alta calidad en YouTube para '{title}'.")
        return None

    # De todos los candidatos válidos, elegir el que tenga más vistas
    best_trailer = max(valid_candidates, key=lambda x: x['views'])
    logging.info(f"✅ Mejor tráiler encontrado y verificado para '{title}': {best_trailer['url']} ({best_trailer['views']} vistas)")
    return best_trailer['url']

def pick_next():
    state = _load_state()
    exclude = set(state.get("published_ids", []) + state.get("picked_ids", []))
    logging.info(f"IDs excluidas (published + picked): {exclude}")

    movies = get_week_releases_enriched()
    logging.info(f"Candidatos totales: {len(movies)}")

    # Ordenar por hype y filtrar excluidos
    candidates = [m for m in movies if m["id"] not in exclude]
    logging.info(f"Top candidatos por hype: {len(candidates)}")
    for i, m in enumerate(candidates[:6]):
        platforms_str = ', '.join(m.get('platforms', [])) or 'No especificado'
        logging.info(f"📋 Candidato {i+1}: {m['titulo']} (ID: {m['id']}, hype: {m['hype']}, platforms: {platforms_str})")

    selected_movie = None
    final_trailer_url = None

    for movie in candidates:
        logging.info(f"\nProbando película: '{movie['titulo']}' (hype: {movie['hype']})...")
        tmdb_trailer = movie.get("trailer")
        
        # 1. Probar el tráiler de TMDB si existe
        if tmdb_trailer:
            logging.info(f"  🔍 Verificando tráiler de TMDB: {tmdb_trailer}")
            if has_high_quality_format(tmdb_trailer, 1080):
                logging.info(f"    ✓ Tráiler de TMDB es viable.")
                selected_movie = movie
                final_trailer_url = tmdb_trailer
                break # Película encontrada, salimos del bucle
        
        # 2. Si no hay tráiler en TMDB o no es viable, buscar en YouTube
        if not selected_movie:
            logging.info(f"   fallback -> Buscando en YouTube...")
            year = movie.get("fecha_estreno", "2025").split('-')[0]
            youtube_trailer = find_best_hype_trailer(movie["titulo"], year)
            if youtube_trailer:
                selected_movie = movie
                final_trailer_url = youtube_trailer
                break # Película encontrada, salimos del bucle
        
        # Si llegamos aquí, no se encontró tráiler viable para esta película
        logging.warning(f"  ✗ No se encontró tráiler viable para '{movie['titulo']}'. Pasando al siguiente.")

    if not selected_movie:
        print("🛑 No se encontraron películas con tráiler viable (>=1080p) entre los candidatos.")
        logging.error("Proceso detenido: no hay candidatos viables.")
        return None

    payload = {
        "tmdb_id": selected_movie["id"], "titulo": selected_movie["titulo"],
        "fecha_estreno": selected_movie["fecha_estreno"], "hype": selected_movie["hype"],
        "vote_average": selected_movie["vote_average"], "vote_count": selected_movie["vote_count"],
        "popularity": selected_movie["popularity"], "generos": selected_movie["generos"],
        "sinopsis": selected_movie["sinopsis"], "poster_principal": selected_movie["poster_principal"],
        "posters": selected_movie["posters"][:5], "backdrops": selected_movie["backdrops"][:8],
        "trailer_url": final_trailer_url, "providers_ES": selected_movie["providers_ES"],
        "certificacion_ES": selected_movie["certificacion_ES"], "reparto_top": selected_movie["reparto_top"],
        "keywords": selected_movie["keywords"], "platforms": selected_movie["platforms"],
        "seleccion_generada": datetime.now(UTC).isoformat().replace("+00:00", "Z")
    }

    NEXT_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n✅ Siguiente película seleccionada y guardada en:", NEXT_FILE)
    print(f"- {payload['titulo']} ({payload['fecha_estreno']}) HYPE={payload['hype']}")
    print("  Trailer:", payload["trailer_url"])
    return payload

def mark_published(tmdb_id: int, simulate=False):
    # (El resto del archivo no necesita cambios)
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
    pick_next()