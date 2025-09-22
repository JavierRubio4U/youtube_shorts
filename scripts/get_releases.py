# scripts/get_releases.py

import os
import math
import requests
from datetime import datetime, timedelta
import logging
import re
import unicodedata

CONFIG_FILE = os.path.join(os.path.dirname(__file__), "..", "config", "tmdb_api_key.txt")
with open(CONFIG_FILE, "r") as f:
    TMDB_API_KEY = f.read().strip()

BASE = "https://api.themoviedb.org/3"
IMG = "https://image.tmdb.org/t/p"
POSTER_SIZE = "w500"
BACKDROP_SIZE = "w1280"

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

def api_get(path, params=None):
    p = {"api_key": TMDB_API_KEY}
    if params:
        p.update(params)
    r = requests.get(f"{BASE}{path}", params=p, timeout=20)
    r.raise_for_status()
    return r.json()

def _is_latin_text(text: str) -> bool:
    """Devuelve True si el texto contiene solo caracteres latinos (incluyendo acentos españoles)."""
    if not text:
        return False
    normalized = unicodedata.normalize('NFKD', text)
    return all(ord(c) < 128 or c in 'áéíóúüñÁÉÍÓÚÜÑ' for c in normalized if unicodedata.category(c) != 'Mn')

def discover_movies(params, max_pages=5):
    """Función helper para discover con parámetros comunes, fetching múltiples páginas."""
    today = datetime.today().date()
    # Modificación: Fecha de inicio para la búsqueda (hace 90 días)
    three_months_ago = today - timedelta(days=90)
    # Fecha de finalización (2 meses en el futuro)
    two_months_ahead = today + timedelta(days=60)
    common_params = {
        "sort_by": "popularity.desc",
        "release_date.gte": three_months_ago.isoformat(),
        "release_date.lte": two_months_ahead.isoformat(),
        "with_release_type": "3|2|1",
        "language": "es-ES",
        "with_original_language": "en|es|fr"
    }
    common_params.update(params)
    logging.info(f"Búsqueda con params: {common_params}")

    all_results = []
    for page in range(1, max_pages + 1):
        common_params["page"] = page
        results = api_get("/discover/movie", common_params).get("results", [])
        if not results:
            break
        all_results.extend(results)
    return all_results

def get_week_releases_enriched():
    today = datetime.today().date()
    start_date = today - timedelta(days=90)

    # Búsqueda general, ahora filtrada por la región de visionado "ES"
    general_results = discover_movies({
        "watch_region": "ES" # Añadido este filtro
    })

    streaming_providers = ["8", "384", "119", "337", "149", "64", "62", "220", "1773"]
    streaming_results = discover_movies({
        "watch_region": "ES",
        "with_watch_providers": "|".join(streaming_providers)
    })

    all_results =  streaming_results + general_results
    unique_results = {m["id"]: m for m in all_results}.values()

    enriched = []
    for m_id in [m["id"] for m in unique_results]:
        # Pasar la fecha de inicio del rango a enrich_movie
        enriched_movie = enrich_movie(m_id, start_date)
        if enriched_movie:
            enriched.append(enriched_movie)

    enriched.sort(key=lambda x: x["popularity"], reverse=True)
    return enriched

def enrich_movie(mid, start_date):
    data = api_get(f"/movie/{mid}", {
        "language": "es-ES",
        "append_to_response": "images,videos,release_dates,watch/providers,credits,keywords",
        "include_image_language": "es,null,en",
    })

    titulo = data.get("title")
    if not _is_latin_text(titulo):
        logging.warning(f"Película {titulo} (ID: {mid}) tiene título no latino (ej. cirílico); descartando.")
        return None

    release_date = data.get("release_date")
    # Este filtro ahora solo descarta las películas con fecha de estreno anterior al rango de búsqueda
    if release_date:
        try:
            release_dt = datetime.strptime(release_date, "%Y-%m-%d").date()
            if release_dt < start_date:
                logging.warning(f"Película {titulo} (ID: {mid}) tiene fecha pasada y fuera de rango ({release_date}); descartando.")
                return None
        except ValueError:
            logging.warning(f"Fecha inválida para {titulo} (ID: {mid}): {release_date}; asumiendo válida.")
    
    # Si no hay sinopsis en español, la busca en inglés
    if not data.get("overview"):
        en_data = api_get(f"/movie/{mid}", {"language": "en-US"})
        data["overview"] = en_data.get("overview")

    posters = []
    for p in (data.get("images", {}) or {}).get("posters", [])[:5]:
        if p.get("file_path"):
            posters.append(f"{IMG}/{POSTER_SIZE}{p['file_path']}")

    backdrops = []
    for b in (data.get("images", {}) or {}).get("backdrops", [])[:8]:
        if b.get("file_path"):
            backdrops.append(f"{IMG}/{BACKDROP_SIZE}{b['file_path']}")

    trailer_url = None
    vids = (data.get("videos", {}) or {}).get("results", [])
    def pick_trailer(vlist, lang=None):
        for v in vlist:
            if v.get("site") == "YouTube" and v.get("type") == "Trailer":
                if lang is None or v.get("iso_639_1") == lang:
                    return f"https://www.youtube.com/watch?v={v['key']}"
        return None
    trailer_url = pick_trailer(vids, "es") or pick_trailer(vids)

    cert_es = None
    rel_es = None
    release_types_es = []
    for rel in (data.get("release_dates", {}) or {}).get("results", []):
        if rel.get("iso_3166_1") == "ES":
            rel_es = rel.get("release_dates", [])
            if rel_es:
                cert_es = next((x.get("certification") for x in rel_es if x.get("certification")), None)
                release_types_es = [x.get("type") for x in rel_es if x.get("type")]
            break

    providers = (data.get("watch/providers", {}) or {}).get("results", {}).get("ES", {})
    flatrate = [p["provider_name"] for p in providers.get("flatrate", [])]
    rent = [p["provider_name"] for p in providers.get("rent", [])]
    buy = [p["provider_name"] for p in providers.get("buy", [])]

    platforms = []
    priority_streaming = ["Netflix", "HBO Max", "Amazon Prime Video", "Disney Plus", "Movistar Plus+", "Filmin", "Atresplayer", "Rakuten TV", "SkyShowtime", "Apple TV"]
    for provider in flatrate:
        if any(stream in provider for stream in priority_streaming):
            platforms.append(provider)
    if not platforms and 3 in release_types_es:
        platforms.append("Cine")
    elif flatrate:
        platforms = flatrate
    else:
        platforms.append("TBD")

    popularity = data.get("popularity", 0.0) or 0.0
    vote_count = data.get("vote_count", 0) or 0
    vote_average = data.get("vote_average", 0.0) or 0.0

    hype = popularity + math.log(max(vote_count, 1), 10) * 10 + (vote_average - 5) * 2

    return {
        "id": data["id"], "titulo": titulo, "fecha_estreno": release_date,
        "generos": [g["name"] for g in data.get("genres", [])], "duracion_min": data.get("runtime"),
        "sinopsis": data.get("overview"), "poster_principal": posters[0] if posters else None,
        "posters": posters, "backdrops": backdrops, "trailer": trailer_url,
        "certificacion_ES": cert_es, "providers_ES": {"flatrate": flatrate, "rent": rent, "buy": buy},
        "platforms": platforms, "popularity": popularity, "vote_count": vote_count,
        "vote_average": vote_average, "hype": round(hype, 2),
        "keywords": [k["name"] for k in (data.get("keywords", {}) or {}).get("keywords", [])],
        "reparto_top": [c["name"] for c in (data.get("credits", {}) or {}).get("cast", [])[:5]],
    }

if __name__ == "__main__":
    movies = get_week_releases_enriched()
    print("🎬 Estrenos disponibles (ordenados por popularity):\n")
    if movies:
        for m in movies[:6]:
            print(f"- {m['titulo']} ({m['fecha_estreno']})  ⭐{m['vote_average']}  👍{m['vote_count']}  🔥{m['popularity']:.1f}  HYPE={m['hype']}")
            print(f"  Trailer: {m['trailer']}")
            print(f"  Poster:  {m['poster_principal']}")
            print(f"  Cert_ES: {m['certificacion_ES']}  Platforms: {', '.join(m['platforms'])}")
            print()
    else:
        print("No hay películas candidatas en el rango de búsqueda.")