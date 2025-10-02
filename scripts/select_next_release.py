# scripts/select_next_release.py
import json
from pathlib import Path
from datetime import datetime, timedelta
import logging
import subprocess
import json as json_lib
import tempfile
import os
import requests
import unicodedata
import math
import sys

# Añadir la carpeta de scripts al path para imports
ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

# Módulos del proyecto
import yt_dlp # type: ignore

try:
    from datetime import UTC
except ImportError:
    from datetime import timezone as _tz
    UTC = _tz.utc

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
        'extractor_args': {'youtube': {'player_client': 'web,android'}},  # Cambio: Evita clientes que causen 403
        'forceipv4': True,  # Cambio: Fuerza IPv4 para estabilidad
        'format': 'bestvideo[height>=1080][vcodec^=avc1]+bestaudio/best',  # Cambio: Prefiere AVC para evitar premium
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

def find_best_hype_trailer(title: str, year: str, min_height=1080) -> str | None:
    search_queries = [
        f'"{title}" ({year}) trailer oficial',
        f'"{title}" official trailer ({year})',
        f'"{title}" film trailer {year}',
        f'"{title}" movie trailer {year}',
        f'"{title}" ({year}) trailer'
    ]

    valid_candidates = []
    unwanted_keywords = ['clip', 'escena', 'featurette', 'review', 'análisis', 'subtítulos', 'sub', 'subs']

    for query in search_queries:
        logging.info(f"Buscando en YouTube: '{query}'")
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'skip_download': True,
            'logger': SilentLogger(),
            'extract_flat': 'in_playlist',
            'playlistend': 5,
            'extractor_args': {'youtube': {'player_client': 'web,android'}},  # Cambio: Para mejor extracción
            'forceipv4': True,  # Cambio: Estabilidad
        }
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                search_results = ydl.extract_info(f"ytsearch5:{query}", download=False).get('entries', [])
                
                for entry in search_results:
                    if not entry: continue
                    
                    video_url = entry.get('url')
                    video_title = entry.get('title', '').lower()
                    view_count = entry.get('view_count', 0)

                    if any(keyword in video_title for keyword in unwanted_keywords):
                        logging.info(f"    ✗ Descartado por contener palabra clave no deseada: '{video_title}'")
                        continue

                    logging.info(f"  > Verificando calidad de: {video_url}")
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

    best_trailer = max(valid_candidates, key=lambda x: x['views'])
    logging.info(f"✅ Mejor tráiler encontrado y verificado para '{title}': {best_trailer['url']} ({best_trailer['views']} vistas)")
    return best_trailer['url']

def mark_published(tmdb_id: int, simulate=False):
    state = _load_state()
    if tmdb_id not in state.get("published_ids", []):
        state.setdefault("published_ids", []).append(tmdb_id)
        state["published_ids"] = sorted(set(state["published_ids"]))
        # Eliminamos el ID de la lista de "picked" si se publica correctamente
        if tmdb_id in state.get("picked_ids", []):
            state["picked_ids"].remove(tmdb_id)
        if not simulate:
            _save_state(state)
        logging.info(f"ID {tmdb_id} marcada publicada (y eliminada de picked){' (simulado)' if simulate else ''}.")
    else:
        logging.info(f"ID {tmdb_id} ya publicada.")

def mark_picked(tmdb_id: int, simulate=False):
    state = _load_state()
    if tmdb_id not in state.get("published_ids", []) and tmdb_id not in state.get("picked_ids", []):
        state.setdefault("picked_ids", []).append(tmdb_id)
        state["picked_ids"] = state["picked_ids"][-50:]  # Mantener la lista corta
        if not simulate:
            _save_state(state)
        logging.info(f"ID {tmdb_id} marcada como elegida (picked){' (simulado)' if simulate else ''}.")
    else:
        logging.info(f"ID {tmdb_id} ya está en picked o publicada.")


# --- LÓGICA DE TRENDING DESARROLLADA PREVIAMENTE ---
CONFIG_FILE = os.path.join(os.path.dirname(__file__), "..", "config", "tmdb_api_key.txt")
with open(CONFIG_FILE, "r") as f:
    TMDB_API_KEY = f.read().strip()
BASE = "https://api.themoviedb.org/3"
IMG = "https://image.tmdb.org/t/p"
POSTER_SIZE = "w500"
BACKDROP_SIZE = "w1280"

def api_get(path, params=None):
    p = {"api_key": TMDB_API_KEY}
    if params:
        p.update(params)
    r = requests.get(f"{BASE}{path}", params=p, timeout=20)
    r.raise_for_status()
    return r.json()

def _is_latin_text(text: str) -> bool:
    if not text:
        return False
    normalized = unicodedata.normalize('NFKD', text)
    return all(ord(c) < 128 or c in 'áéíóúüñÁÉÍÓÚÜÑ' for c in normalized if unicodedata.category(c) != 'Mn')

def enrich_movie(mid):
    data = api_get(f"/movie/{mid}", {
        "language": "es-ES",
        "append_to_response": "images,videos,release_dates,watch/providers,credits,keywords",
        "include_image_language": "es,null,en",
    })

    titulo = data.get("title")
    if not _is_latin_text(titulo):
        return None

    posters = [f"{IMG}/{POSTER_SIZE}{p['file_path']}" for p in (data.get("images", {}) or {}).get("posters", [])[:5] if p.get("file_path")]
    backdrops = [f"{IMG}/{BACKDROP_SIZE}{b['file_path']}" for b in (data.get("images", {}) or {}).get("backdrops", [])[:8] if b.get("file_path")]

    trailer_url = None
    vids = (data.get("videos", {}) or {}).get("results", [])
    def pick_trailer(vlist, lang=None):
        for v in vlist:
            if v.get("site") == "YouTube" and v.get("type") == "Trailer" and (lang is None or v.get("iso_639_1") == lang):
                return f"https://www.youtube.com/watch?v={v['key']}"
        return None
    trailer_url = pick_trailer(vids, "es") or pick_trailer(vids)

    cert_es = None
    digital_date = None
    physical_date = None
    
    providers_es = (data.get("watch/providers", {}) or {}).get("results", {}).get("ES", {})
    if providers_es:
        release_dates = (data.get("release_dates", {}) or {}).get("results", [])
        rel_es = next((rel for rel in release_dates if rel.get("iso_3166_1") == "ES"), None)
        if rel_es:
            cert_es = next((x.get("certification") for x in rel_es.get("release_dates", []) if x.get("certification")), None)
            
            for rd in rel_es.get("release_dates", []):
                if rd.get("type") == 4 and not digital_date:
                    digital_date = rd.get("release_date").split('T')[0]
                elif rd.get("type") == 5 and not physical_date:
                    physical_date = rd.get("release_date").split('T')[0]
    
    flatrate = [p["provider_name"] for p in providers_es.get("flatrate", [])]
    platforms = flatrate if flatrate else ["TBD"]

    return {
        "id": data.get("id"), 
        "titulo": titulo, 
        "fecha_estreno": data.get("release_date"),
        "fecha_estreno_digital": digital_date,
        "fecha_estreno_video": physical_date,
        "generos": [g["name"] for g in data.get("genres", [])],
        "sinopsis": data.get("overview"),
        "poster_principal": posters[0] if posters else None,
        "posters": posters, 
        "backdrops": backdrops, 
        "trailer": trailer_url,
        "certificacion_ES": cert_es,
        "platforms": platforms,
        "popularity": data.get("popularity", 0.0),
        "vote_average": data.get("vote_average"),
        "vote_count": data.get("vote_count"),
        "keywords": [k["name"] for k in (data.get("keywords", {}) or {}).get("keywords", [])],
        "reparto_top": [c["name"] for c in (data.get("credits", {}) or {}).get("cast", [])[:5]],
        "hype": data.get("hype"),
    }

def get_trending_by_type(media_type: str, time_window: str, category: str):
    logging.info(f"Obteniendo top 20 trending semanal de {media_type} en {category}...")
    
    results = api_get(f"/trending/{media_type}/{time_window}", {"language": "es-ES"}).get("results", [])
    filtered_results = []
    
    for m in results:
        providers = api_get(f"/movie/{m['id']}/watch/providers", {"language": "es-ES"}).get("results", {}).get("ES", {})
        release_dates = api_get(f"/movie/{m['id']}/release_dates").get("results", [])
        rel_es = next((rel for rel in release_dates if rel.get("iso_3166_1") == "ES"), None)
        
        is_cinema = False
        if rel_es:
            release_types = [rd['type'] for rd in rel_es['release_dates']]
            if any(rt in release_types for rt in [2, 3]):
                is_cinema = True

        is_streaming = False
        if any(mt in providers.keys() for mt in ['flatrate', 'rent', 'buy']):
            is_streaming = True

        if (category == "Cine" and is_cinema) or (category == "Streaming" and is_streaming):
            filtered_results.append(m)
        
        if len(filtered_results) >= 20:
            break

    enriched_results = []
    for m in filtered_results[:20]:
        enriched = enrich_movie(m["id"])
        if enriched and enriched["poster_principal"]:
            enriched['category'] = category
            enriched_results.append(enriched)
    
    return enriched_results

# --- FUNCIÓN PRINCIPAL DE SELECCIÓN RE-IMPLEMENTADA ---

def pick_next():
    state = _load_state()
    exclude = set(state.get("published_ids", []) + state.get("picked_ids", []))
    logging.info(f"IDs excluidas (published + picked): {exclude}")

    # 1. Obtener las listas de trending de cada categoría
    trending_cinema = get_trending_by_type("movie", "week", "Cine")
    trending_streaming = get_trending_by_type("movie", "week", "Streaming")
    
    # 2. Intercalar las listas de forma equitativa
    intercalated_trending = []
    seen_ids = set()
    
    max_len = max(len(trending_cinema), len(trending_streaming))
    for i in range(max_len):
        if i < len(trending_cinema):
            movie = trending_cinema[i]
            if movie["id"] not in seen_ids:
                intercalated_trending.append(movie)
                seen_ids.add(movie["id"])
        
        if i < len(trending_streaming):
            movie = trending_streaming[i]
            if movie["id"] not in seen_ids:
                intercalated_trending.append(movie)
                seen_ids.add(movie["id"])

    # 3. Priorizar películas con fecha de estreno digital
    digital_releases = []
    other_movies = []
    for movie in intercalated_trending:
        if movie.get('fecha_estreno_digital'):
            digital_releases.append(movie)
        else:
            other_movies.append(movie)
    
    prioritized_list = digital_releases + other_movies
    
    # 4. Filtrar por fecha de estreno (últimos 6 meses)
    hoy = datetime.now()
    hace_cuatro_meses = hoy - timedelta(days=184)
    
    filtered_trending_movies = []
    for movie in prioritized_list:
        effective_date_str = movie['fecha_estreno']
        if movie.get('category') == 'Streaming' and movie.get('fecha_estreno_digital'):
            effective_date_str = movie['fecha_estreno_digital']
        
        try:
            effective_date = datetime.strptime(effective_date_str, "%Y-%m-%d")
            if effective_date >= hace_cuatro_meses:
                filtered_trending_movies.append(movie)
        except (ValueError, TypeError):
            continue

    # === Nuevo código para el log de candidatos ===
    print("\n📝 Lista de candidatos Top 20:")
    print("----------------------------")
    for i, movie in enumerate(filtered_trending_movies[:20]):
        print(f"  {i+1}. {movie['titulo']} ({movie['fecha_estreno']})")
        print(f"     Popularidad: {movie['popularity']:.1f}")
        print(f"     Prioridad: Estreno Digital = {'Sí' if movie.get('fecha_estreno_digital') else 'No'}")
    print("----------------------------\n")
    # ===============================================

    # 5. Iterar sobre la lista filtrada para encontrar un candidato viable
    selected_movie = None
    final_trailer_url = None
    
    for movie in filtered_trending_movies:
        if movie["id"] in exclude:
            continue
            
        logging.info(f"\nProbando película: '{movie['titulo']}' [{movie['category']}] (Popularidad: {movie['popularity']:.1f})...")
        tmdb_trailer = movie.get("trailer")
        
        if tmdb_trailer:
            logging.info(f"  🔍 Verificando tráiler de TMDB: {tmdb_trailer}")
            if has_high_quality_format(tmdb_trailer, 1080):
                logging.info(f"    ✓ Tráiler de TMDB es viable.")
                selected_movie = movie
                final_trailer_url = tmdb_trailer
                break
        
        if not selected_movie:
            logging.info(f"   fallback -> Buscando en YouTube...")
            year = movie.get("fecha_estreno", "2025").split('-')[0]
            youtube_trailer = find_best_hype_trailer(movie["titulo"], year)
            if youtube_trailer:
                selected_movie = movie
                final_trailer_url = youtube_trailer
                break
        
        if not selected_movie:
            logging.warning(f"  ✗ No se encontró tráiler viable para '{movie['titulo']}'. Pasando al siguiente.")

    # 6. Guardar la selección
    if not selected_movie:
        print("🛑 No se encontraron películas con tráiler viable (>=1080p) entre los candidatos.")
        logging.error("Proceso detenido: no hay candidatos viables.")
        return None

    # --- LÓGICA AGREGADA: MARCAR COMO 'PICKED' ANTES DE GUARDAR EL NEXT FILE ---
    # Esto asegura que la película no se vuelva a seleccionar si el proceso falla en un paso posterior.
    mark_picked(selected_movie["id"])
    # -------------------------------------------------------------------------

    payload = {
        "tmdb_id": selected_movie["id"], "titulo": selected_movie["titulo"],
        "fecha_estreno": selected_movie["fecha_estreno"], "hype": selected_movie.get("hype"),
        "vote_average": selected_movie.get("vote_average"), "vote_count": selected_movie.get("vote_count"),
        "popularity": selected_movie["popularity"], "generos": selected_movie["generos"],
        "sinopsis": selected_movie["sinopsis"], "poster_principal": selected_movie["poster_principal"],
        "posters": selected_movie["posters"][:5], "backdrops": selected_movie["backdrops"][:8],
        "trailer_url": final_trailer_url, "providers_ES": selected_movie.get("providers_ES"),
        "certificacion_ES": selected_movie["certificacion_ES"], "reparto_top": selected_movie.get("reparto_top"),
        "keywords": selected_movie.get("keywords"), "platforms": selected_movie["platforms"],
        "seleccion_generada": datetime.now(UTC).isoformat().replace("+00:00", "Z")
    }

    NEXT_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n✅ Siguiente película seleccionada y guardada en:", NEXT_FILE)
    print(f"- {payload['titulo']} ({payload['fecha_estreno']}) Popularidad={payload['popularity']:.1f}")
    print("  Trailer:", payload["trailer_url"])
    return payload

if __name__ == "__main__":
    pick_next()