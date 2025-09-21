# scripts/get_releases.py

import os
import math
import requests
from datetime import datetime, timedelta
import logging  # Agregado para logs de depuración
import re
import unicodedata

CONFIG_FILE = os.path.join(os.path.dirname(__file__), "..", "config", "tmdb_api_key.txt")
with open(CONFIG_FILE, "r") as f:
    TMDB_API_KEY = f.read().strip()

BASE = "https://api.themoviedb.org/3"
IMG = "https://image.tmdb.org/t/p"
POSTER_SIZE = "w500"
BACKDROP_SIZE = "w1280"

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')  # Configuración básica de logging

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
    two_months_ahead = today + timedelta(days=60)
    common_params = {
        "sort_by": "popularity.desc",
        "release_date.gte": today.isoformat(),
        "release_date.lte": two_months_ahead.isoformat(),
        "with_release_type": "3|2|1",  # Estrenos en cines, digitales o físicos
        "language": "es-ES",  # Agregado: Para títulos traducidos a español
        "with_original_language": "en|es|fr"  # Agregado: Solo idiomas inglés, español, francés para evitar otros como ruso
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
    # Búsqueda general (como antes)
    general_results = discover_movies({})

    # Búsquedas específicas para streaming en España (IDs ampliados con españolas: Netflix=8, HBO Max=384, Amazon Prime=119, Disney+=337, Movistar Plus+=149, Filmin=64, Atresplayer=62, Rakuten TV=220, SkyShowtime=1773)
    streaming_providers = ["8", "384", "119", "337", "149", "64", "62", "220", "1773"]  # IDs como strings para join
    streaming_results = discover_movies({
        "watch_region": "ES",
        "with_watch_providers": "|".join(streaming_providers)
    })

    # Combinar y eliminar duplicados por ID
    all_results = general_results + streaming_results
    unique_results = {m["id"]: m for m in all_results}.values()  # Dict para unique por ID

    enriched = []
    for m_id in [m["id"] for m in unique_results]:
        enriched_movie = enrich_movie(m_id)
        if enriched_movie:  # Solo agregar si no fue filtrado
            enriched.append(enriched_movie)

    enriched.sort(key=lambda x: x["popularity"], reverse=True)  # Ordenar por popularity (fueguito)
    return enriched

def enrich_movie(mid):
    # Trae los datos de la película en español e inglés
    data = api_get(f"/movie/{mid}", {
        "language": "es-ES",  # Pide la info principal en español
        "append_to_response": "images,videos,release_dates,watch/providers,credits,keywords",
        "include_image_language": "es,null,en",
    })

    titulo = data.get("title")
    if not _is_latin_text(titulo):
        logging.warning(f"Película {titulo} (ID: {mid}) tiene título no latino (ej. cirílico); descartando.")
        return None

    release_date = data.get("release_date")
    today = datetime.today().date()
    if release_date:
        try:
            release_dt = datetime.strptime(release_date, "%Y-%m-%d").date()
            if release_dt < today:
                logging.warning(f"Película {titulo} (ID: {mid}) tiene fecha pasada ({release_date}); descartando.")
                return None
        except ValueError:
            logging.warning(f"Fecha inválida para {titulo} (ID: {mid}): {release_date}; asumiendo válida.")

    # Si no hay sinopsis en español, la busca en inglés
    if not data.get("overview"):
        en_data = api_get(f"/movie/{mid}", {"language": "en-US"})
        data["overview"] = en_data.get("overview")

    # Posters y backdrops (varios)
    posters = []
    for p in (data.get("images", {}) or {}).get("posters", [])[:5]:
        if p.get("file_path"):
            posters.append(f"{IMG}/{POSTER_SIZE}{p['file_path']}")

    backdrops = []
    for b in (data.get("images", {}) or {}).get("backdrops", [])[:8]:
        if b.get("file_path"):
            backdrops.append(f"{IMG}/{BACKDROP_SIZE}{b['file_path']}")

    # Trailer ES (o fallback)
    trailer_url = None
    vids = (data.get("videos", {}) or {}).get("results", [])
    def pick_trailer(vlist, lang=None):
        for v in vlist:
            if v.get("site") == "YouTube" and v.get("type") == "Trailer":
                if lang is None or v.get("iso_639_1") == lang:
                    return f"https://www.youtube.com/watch?v={v['key']}"
        return None
    trailer_url = pick_trailer(vids, "es") or pick_trailer(vids)

    # Certificación y fecha ES
    cert_es = None
    rel_es = None
    release_types_es = []  # Nuevo: Capturar tipos de estreno en ES
    for rel in (data.get("release_dates", {}) or {}).get("results", []):
        if rel.get("iso_3166_1") == "ES":
            rel_es = rel.get("release_dates", [])
            if rel_es:
                cert_es = next((x.get("certification") for x in rel_es if x.get("certification")), None)
                release_types_es = [x.get("type") for x in rel_es if x.get("type")]  # Lista de tipos (puede haber múltiples)
            break

    # Providers ES
    providers = (data.get("watch/providers", {}) or {}).get("results", {}).get("ES", {})
    flatrate = [p["provider_name"] for p in providers.get("flatrate", [])]
    rent = [p["provider_name"] for p in providers.get("rent", [])]
    buy = [p["provider_name"] for p in providers.get("buy", [])]

    # Modificado: Determinar "platforms" priorizando más streaming españoles y mostrando nombres reales si no prioritarios
    platforms = []
    priority_streaming = ["Netflix", "HBO Max", "Amazon Prime Video", "Disney Plus", "Movistar Plus+", "Filmin", "Atresplayer", "Rakuten TV", "SkyShowtime", "Apple TV"]  # Ampliado con comunes en España
    for provider in flatrate:
        if any(stream in provider for stream in priority_streaming):
            platforms.append(provider)
    if not platforms and 3 in release_types_es:  # Type 3 = Theatrical
        platforms.append("Cine")
    elif flatrate:  # Modificado: Si hay flatrate pero no prioritarios, mostrarlos directamente
        platforms = flatrate  # Usa los nombres reales
    else:
        platforms.append("TBD")  # Fallback solo si no hay nada

    # Métricas de interés
    popularity = data.get("popularity", 0.0) or 0.0
    vote_count = data.get("vote_count", 0) or 0
    vote_average = data.get("vote_average", 0.0) or 0.0

    # “Hype” sencillo: popularidad + log(votes) + (vote_avg-5)*2
    hype = popularity + math.log(max(vote_count, 1), 10) * 10 + (vote_average - 5) * 2

    return {
        "id": data["id"],
        "titulo": titulo,
        "fecha_estreno": release_date,
        "generos": [g["name"] for g in data.get("genres", [])],
        "duracion_min": data.get("runtime"),
        "sinopsis": data.get("overview"),
        "poster_principal": posters[0] if posters else None,
        "posters": posters,
        "backdrops": backdrops,
        "trailer": trailer_url,
        "certificacion_ES": cert_es,
        "providers_ES": {"flatrate": flatrate, "rent": rent, "buy": buy},
        "platforms": platforms,  # Nuevo campo con plataformas determinadas
        "popularity": popularity,
        "vote_count": vote_count,
        "vote_average": vote_average,
        "hype": round(hype, 2),
        "keywords": [k["name"] for k in (data.get("keywords", {}) or {}).get("keywords", [])],
        "reparto_top": [c["name"] for c in (data.get("credits", {}) or {}).get("cast", [])[:5]],
    }

if __name__ == "__main__":
    movies = get_week_releases_enriched()
    print("🎬 Estrenos disponibles (ordenados por popularity):\n")
    if movies:
        for m in movies:
            print(f"- {m['titulo']} ({m['fecha_estreno']})  ⭐{m['vote_average']}  👍{m['vote_count']}  🔥{m['popularity']:.1f}  HYPE={m['hype']}")
            print(f"  Trailer: {m['trailer']}")
            print(f"  Poster:  {m['poster_principal']}")
            print(f"  Backdrops[{len(m['backdrops'])}]: {', '.join(m['backdrops'][:3])}...")
            print(f"  Cert_ES: {m['certificacion_ES']}  Providers ES: {m['providers_ES']}")
            print(f"  Platforms: {', '.join(m['platforms'])}")  # Nuevo: Mostrar platforms en el print de depuración
            print()
    else:
        print("No hay películas candidatas en el rango de búsqueda.")