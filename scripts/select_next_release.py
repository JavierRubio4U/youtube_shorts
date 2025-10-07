# scripts/select_next_release.py
import json
from pathlib import Path
from datetime import datetime, timedelta, timezone
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
    if not PUBLISHED_FILE.exists():
        return {"published_ids": [], "picked_ids": []}
    
    state = json.loads(PUBLISHED_FILE.read_text(encoding="utf-8"))
    now = datetime.now(timezone.utc)
    
    # <<< CAMBIO: Lógica de caducidad y migración para published_ids (60 días) >>>
    if state.get("published_ids"):
        two_months_ago = now - timedelta(days=60)
        
        filtered_published = []
        for pub in state["published_ids"]:
            # Manejar formato legacy (solo ID)
            if not isinstance(pub, dict):
                filtered_published.append({
                    "id": pub, 
                    "timestamp": now.isoformat() + "Z",
                    "trailer_url": None # No sabemos el tráiler antiguo
                })
                logging.info(f"Convertido published_id legacy: {pub} a formato con timestamp.")
                continue

            try:
                pub_time = datetime.fromisoformat(pub["timestamp"].replace("Z", "+00:00"))
                if pub_time >= two_months_ago:
                    filtered_published.append(pub)
                else:
                    logging.info(f"Removido published_id viejo (caducado): {pub['id']} (de {pub['timestamp']})")
            except (ValueError, KeyError):
                # Mantener con timestamp actual si es inválido
                pub["timestamp"] = now.isoformat() + "Z"
                filtered_published.append(pub)
        
        state["published_ids"] = filtered_published
    # >>> FIN DEL CAMBIO <<<

    # Lógica existente para picked_ids (7 días), ahora se ejecuta después
    if state.get("picked_ids"):
        seven_days_ago = now - timedelta(days=7)
        
        filtered_picked = []
        for pick in state["picked_ids"]:
            if isinstance(pick, dict) and "timestamp" in pick and "id" in pick:
                try:
                    pick_time = datetime.fromisoformat(pick["timestamp"].replace("Z", "+00:00"))
                    if pick_time >= seven_days_ago:
                        filtered_picked.append(pick)
                    else:
                        logging.info(f"Removido picked_id viejo: {pick['id']} (de {pick['timestamp']})")
                except (ValueError, KeyError):
                    pick["timestamp"] = now.isoformat() + "Z"
                    filtered_picked.append(pick)
            else:
                legacy_id = pick if isinstance(pick, (int, str)) else pick.get("id", pick)
                filtered_picked.append({"id": legacy_id, "timestamp": now.isoformat() + "Z"})
                logging.info(f"Convertido picked_id legacy: {legacy_id} a formato con timestamp.")
        
        state["picked_ids"] = filtered_picked

    # Guardar el estado si ha habido alguna limpieza en cualquiera de las listas
    if len(state.get("published_ids", [])) != len(state.get("published_ids", [])) or \
       len(state.get("picked_ids", [])) != len(state.get("picked_ids", [])):
        _save_state(state)
        logging.info("Estado de IDs (published/picked) limpiado y actualizado.")
        
    return state


def _save_state(state):
    PUBLISHED_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    logging.info(f"Estado guardado en {PUBLISHED_FILE}")

def has_high_quality_format(trailer_url: str, min_height=1080) -> bool:
    if not trailer_url:
        return False
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'simulate': True,
        'logger': SilentLogger(),
        'extractor_args': {'youtube': {'player_client': 'web,android'}},
        'forceipv4': True,
        'format': 'bestvideo[height>=1080]+bestaudio/best',
        'no_check_certificate': True,
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
            'quiet': True, 'no_warnings': True, 'skip_download': True,
            'logger': SilentLogger(), 'extract_flat': 'in_playlist',
            'playlistend': 5, 'extractor_args': {'youtube': {'player_client': 'web,android'}},
            'forceipv4': True, 'no_check_certificate': True,
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
                        logging.info(f"    ✗ Descartado por palabra clave no deseada: '{video_title}'")
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

# <<< CAMBIO: mark_published ahora requiere la URL del tráiler para guardarla en el estado >>>
def mark_published(tmdb_id: int, trailer_url: str, simulate=False):
    state = _load_state()
    now_ts = datetime.now(timezone.utc).isoformat() + "Z"
    
    # Crear la nueva entrada
    new_entry = {"id": tmdb_id, "timestamp": now_ts, "trailer_url": trailer_url}

    # Eliminar cualquier entrada previa con el mismo ID para evitar duplicados
    state["published_ids"] = [p for p in state.get("published_ids", []) if p.get("id") != tmdb_id]
    
    # Añadir la nueva entrada
    state.setdefault("published_ids", []).append(new_entry)
    
    # Remover de picked si existe
    state["picked_ids"] = [p for p in state.get("picked_ids", []) if p.get("id") != tmdb_id]
    
    if not simulate:
        _save_state(state)
    logging.info(f"ID {tmdb_id} marcada publicada con tráiler {trailer_url}{' (simulado)' if simulate else ''}.")
# >>> FIN DEL CAMBIO <<<

def mark_picked(tmdb_id: int, simulate=False):
    state = _load_state()
    picked_ids = {p["id"] for p in state.get("picked_ids", [])}
    published_ids = {p["id"] for p in state.get("published_ids", [])}

    if tmdb_id not in picked_ids and tmdb_id not in published_ids:
        now = datetime.now(timezone.utc)
        new_pick = {"id": tmdb_id, "timestamp": now.isoformat() + "Z"}
        state.setdefault("picked_ids", []).append(new_pick)
        state["picked_ids"] = state["picked_ids"][-50:]
        if not simulate:
            _save_state(state)
        logging.info(f"ID {tmdb_id} marcada como elegida (picked) en {now.isoformat()[:19]}Z{' (simulado)' if simulate else ''}.")
    else:
        logging.info(f"ID {tmdb_id} ya está en picked o publicada.")

# --- LÓGICA DE TRENDING (SIN CAMBIOS) ---
CONFIG_FILE = os.path.join(os.path.dirname(__file__), "..", "config", "tmdb_api_key.txt")
with open(CONFIG_FILE, "r") as f:
    TMDB_API_KEY = f.read().strip()
BASE = "https://api.themoviedb.org/3"
IMG = "https://image.tmdb.org/t/p"
POSTER_SIZE = "w500"
BACKDROP_SIZE = "w1280"
def api_get(path, params=None):
    p = {"api_key": TMDB_API_KEY}
    if params: p.update(params)
    r = requests.get(f"{BASE}{path}", params=p, timeout=20)
    r.raise_for_status()
    return r.json()
def _is_latin_text(text: str) -> bool:
    if not text: return False
    normalized = unicodedata.normalize('NFKD', text)
    return all(ord(c) < 128 or c in 'áéíóúüñÁÉÍÓÚÜÑ' for c in normalized if unicodedata.category(c) != 'Mn')
def enrich_movie(mid):
    data = api_get(f"/movie/{mid}", {"language": "es-ES", "append_to_response": "images,videos,release_dates,watch/providers,credits,keywords", "include_image_language": "es,null,en"})
    titulo = data.get("title")
    if not _is_latin_text(titulo): return None
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
                if rd.get("type") == 4 and not digital_date: digital_date = rd.get("release_date").split('T')[0]
                elif rd.get("type") == 5 and not physical_date: physical_date = rd.get("release_date").split('T')[0]
    platforms = []
    flatrate = [p["provider_name"] for p in providers_es.get("flatrate", [])]
    if flatrate: platforms = flatrate
    else:
        is_cinema_release = False
        release_dates = (data.get("release_dates", {}) or {}).get("results", [])
        rel_es = next((rel for rel in release_dates if rel.get("iso_3166_1") == "ES"), None)
        if rel_es:
            release_types = {rd.get("type") for rd in rel_es.get("release_dates", [])}
            if 2 in release_types or 3 in release_types: is_cinema_release = True
        if is_cinema_release: platforms = ["Cine"]
        else: platforms = ["TBD"]
    return {"id": data.get("id"), "titulo": titulo, "fecha_estreno": data.get("release_date"), "fecha_estreno_digital": digital_date, "fecha_estreno_video": physical_date, "generos": [g["name"] for g in data.get("genres", [])], "sinopsis": data.get("overview"), "poster_principal": posters[0] if posters else None, "posters": posters, "backdrops": backdrops, "trailer": trailer_url, "certificacion_ES": cert_es, "platforms": platforms, "popularity": data.get("popularity", 0.0), "vote_average": data.get("vote_average"), "vote_count": data.get("vote_count"), "keywords": [k["name"] for k in (data.get("keywords", {}) or {}).get("keywords", [])], "reparto_top": [c["name"] for c in (data.get("credits", {}) or {}).get("cast", [])[:5]], "hype": data.get("hype")}
def get_trending_by_type(media_type: str, time_window: str, category: str):
    logging.info(f"Obteniendo top trending semanal de {media_type} en {category} (páginas 1-3)...")
    all_results = []
    for page in range(1, 4):
        try:
            results = api_get(f"/trending/{media_type}/{time_window}", {"language": "es-ES", "page": page}).get("results", [])
            all_results.extend(results)
        except Exception as e:
            logging.warning(f"Fallo al obtener la página {page} de trending: {e}"); break
    filtered_results = []
    for m in all_results:
        providers = api_get(f"/movie/{m['id']}/watch/providers", {"language": "es-ES"}).get("results", {}).get("ES", {})
        release_dates = api_get(f"/movie/{m['id']}/release_dates").get("results", [])
        rel_es = next((rel for rel in release_dates if rel.get("iso_3166_1") == "ES"), None)
        is_cinema = False
        if rel_es:
            release_types = [rd['type'] for rd in rel_es['release_dates']]
            if any(rt in release_types for rt in [2, 3]): is_cinema = True
        is_streaming = False
        if any(mt in providers.keys() for mt in ['flatrate', 'rent', 'buy']): is_streaming = True
        if (category == "Cine" and is_cinema) or (category == "Streaming" and is_streaming):
            filtered_results.append(m)
    enriched_results = []
    for m in filtered_results[:40]: 
        enriched = enrich_movie(m["id"])
        if enriched and enriched["poster_principal"]:
            enriched['category'] = category
            enriched_results.append(enriched)
    return enriched_results

# --- FUNCIÓN PRINCIPAL DE SELECCIÓN RE-IMPLEMENTADA ---

def pick_next():
    state = _load_state()
    
    # <<< CAMBIO: Se crean estructuras de datos para la nueva lógica de exclusión >>>
    # Un set para los IDs 'picked' (exclusión total)
    picked_ids_set = {p["id"] for p in state.get("picked_ids", [])}
    # Un diccionario para los IDs 'published' para poder consultar el tráiler publicado
    published_map = {p["id"]: p.get("trailer_url") for p in state.get("published_ids", [])}
    logging.info(f"IDs en 'picked' (exclusión temporal): {picked_ids_set}")
    logging.info(f"IDs en 'published' (exclusión por tráiler): {list(published_map.keys())}")
    # >>> FIN DEL CAMBIO <<<

    # 1. Obtener y 2. Intercalar listas
    trending_cinema = get_trending_by_type("movie", "week", "Cine")
    trending_streaming = get_trending_by_type("movie", "week", "Streaming")
    intercalated_trending = []
    seen_ids = set()
    max_len = max(len(trending_cinema), len(trending_streaming))
    for i in range(max_len):
        if i < len(trending_cinema):
            movie = trending_cinema[i]
            if movie["id"] not in seen_ids: intercalated_trending.append(movie); seen_ids.add(movie["id"])
        if i < len(trending_streaming):
            movie = trending_streaming[i]
            if movie["id"] not in seen_ids: intercalated_trending.append(movie); seen_ids.add(movie["id"])

    # 3. Priorizar y 4. Filtrar
    digital_releases = [m for m in intercalated_trending if m.get('fecha_estreno_digital')]
    other_movies = [m for m in intercalated_trending if not m.get('fecha_estreno_digital')]
    prioritized_list = digital_releases + other_movies
    hoy = datetime.now()
    hace_seis_meses = hoy - timedelta(days=184)
    filtered_trending_movies = []
    for movie in prioritized_list:
        effective_date_str = movie['fecha_estreno']
        if movie.get('category') == 'Streaming' and movie.get('fecha_estreno_digital'):
            effective_date_str = movie['fecha_estreno_digital']
        try:
            effective_date = datetime.strptime(effective_date_str, "%Y-%m-%d")
            if effective_date >= hace_seis_meses:
                filtered_trending_movies.append(movie)
        except (ValueError, TypeError): continue

    print(f"\n📝 Lista de candidatos (Total: {len(filtered_trending_movies)}):")
    print("----------------------------")
    for i, movie in enumerate(filtered_trending_movies):
        print(f"  {i+1}. {movie['titulo']} ({movie['fecha_estreno']}) - Pop: {movie['popularity']:.1f} - Digital: {'Sí' if movie.get('fecha_estreno_digital') else 'No'}")
    print("----------------------------\n")

    selected_movie = None
    final_trailer_url = None
    
    # 5. Iterar sobre la lista filtrada para encontrar un candidato viable
    for movie in filtered_trending_movies:
        tmdb_id = movie["id"]
        
        # <<< CAMBIO: Nueva lógica de validación >>>
        # Descarte 1: Si está en la lista 'picked' recientemente, se salta siempre.
        if tmdb_id in picked_ids_set:
            logging.info(f"Saltando '{movie['titulo']}' porque está en la lista 'picked'.")
            continue

        logging.info(f"\nProbando película: '{movie['titulo']}' [{movie['category']}] (Pop: {movie['popularity']:.1f})...")
        
        # Buscar un tráiler viable
        current_trailer = None
        tmdb_trailer = movie.get("trailer")
        if tmdb_trailer and has_high_quality_format(tmdb_trailer, 1080):
            logging.info(f"  ✓ Tráiler de TMDB es viable: {tmdb_trailer}")
            current_trailer = tmdb_trailer
        else:
            logging.info("   -> Tráiler de TMDB no viable o no existe. Buscando en YouTube...")
            year = movie.get("fecha_estreno", "2025").split('-')[0]
            youtube_trailer = find_best_hype_trailer(movie["titulo"], year)
            if youtube_trailer:
                current_trailer = youtube_trailer
        
        # Si no se encontró ningún tráiler, pasar al siguiente candidato
        if not current_trailer:
            logging.warning(f"  ✗ No se encontró tráiler viable para '{movie['titulo']}'.")
            continue

        # Descarte 2: Comprobar si ya fue publicada CON EL MISMO tráiler
        published_trailer = published_map.get(tmdb_id)
        if published_trailer and published_trailer == current_trailer:
            logging.warning(f"  ✗ Película ya publicada con el mismo tráiler. Saltando.")
            continue
        
        if published_trailer and published_trailer != current_trailer:
            logging.info(f"  🎉 ¡CANDIDATA VÁLIDA! Ya fue publicada, pero se encontró un TRÁILER NUEVO.")

        # Si llegamos aquí, es un candidato válido
        selected_movie = movie
        final_trailer_url = current_trailer
        break
        # >>> FIN DEL CAMBIO <<<

    # 6. Guardar la selección
    if not selected_movie:
        print("🛑 No se encontraron películas con tráiler viable (>=1080p) entre los candidatos.")
        logging.error("Proceso detenido: no hay candidatos viables.")
        return None

    mark_picked(selected_movie["id"])

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