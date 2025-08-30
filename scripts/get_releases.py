import os
import math
import requests
from datetime import datetime, timedelta

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

def discover_es_week():
    today = datetime.today().date()
    week_ahead = today + timedelta(days=30)
    return api_get("/discover/movie", {
        "language": "es-ES",
        "region": "ES",
        "sort_by": "release_date.asc",
        "release_date.gte": today.isoformat(),
        "release_date.lte": week_ahead.isoformat(),
        "with_release_type": "3|2|1"  # theatrical/digital/limited
    }).get("results", [])

def enrich_movie(mid):
    # Trae todo en una sola llamada con append_to_response
    data = api_get(f"/movie/{mid}", {
        "language": "es-ES",
        "append_to_response": "images,videos,release_dates,watch/providers,credits,keywords",
        # Parámetros adicionales útiles para sub-recursos:
        "include_image_language": "es,null,en",
    })

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
    for rel in (data.get("release_dates", {}) or {}).get("results", []):
        if rel.get("iso_3166_1") == "ES":
            rel_es = rel.get("release_dates", [])
            if rel_es:
                # coge la última con certification si existe
                cert_es = next((x.get("certification") for x in rel_es if x.get("certification")), None)
            break

    # Providers ES
    providers = (data.get("watch/providers", {}) or {}).get("results", {}).get("ES", {})
    flatrate = [p["provider_name"] for p in providers.get("flatrate", [])]
    rent = [p["provider_name"] for p in providers.get("rent", [])]
    buy = [p["provider_name"] for p in providers.get("buy", [])]

    # Métricas de interés
    popularity = data.get("popularity", 0.0) or 0.0
    vote_count = data.get("vote_count", 0) or 0
    vote_average = data.get("vote_average", 0.0) or 0.0

    # “Hype” sencillo: popularidad + log(votes) + (vote_avg-5)*2
    hype = popularity + math.log(max(vote_count, 1), 10) * 10 + (vote_average - 5) * 2

    return {
        "id": data["id"],
        "titulo": data.get("title"),
        "fecha_estreno": data.get("release_date"),
        "generos": [g["name"] for g in data.get("genres", [])],
        "duracion_min": data.get("runtime"),
        "sinopsis": data.get("overview"),
        "poster_principal": posters[0] if posters else None,
        "posters": posters,
        "backdrops": backdrops,
        "trailer": trailer_url,
        "certificacion_ES": cert_es,
        "providers_ES": {"flatrate": flatrate, "rent": rent, "buy": buy},
        "popularity": popularity,
        "vote_count": vote_count,
        "vote_average": vote_average,
        "hype": round(hype, 2),
        "keywords": [k["name"] for k in (data.get("keywords", {}) or {}).get("keywords", [])],
        "reparto_top": [c["name"] for c in (data.get("credits", {}) or {}).get("cast", [])[:5]],
    }

def get_week_releases_enriched():
    base = discover_es_week()
    enriched = [enrich_movie(m["id"]) for m in base]
    # Ordena por hype descendente (las “más esperadas” arriba)
    enriched.sort(key=lambda x: x["hype"], reverse=True)
    return enriched

movies = get_week_releases_enriched()
print("🎬 Películas candidatas disponibles:")
if movies:
    for m in movies:
        print(f"- {m['titulo']} (Hype: {m['hype']})")
else:
    print("- No hay películas candidatas en el rango de búsqueda.")

if __name__ == "__main__":
    movies = get_week_releases_enriched()
    print("🎬 Estrenos en España (ordenados por HYPE):\n")
    for m in movies:
        print(f"- {m['titulo']} ({m['fecha_estreno']})  ⭐{m['vote_average']}  👍{m['vote_count']}  🔥{m['popularity']:.1f}  HYPE={m['hype']}")
        print(f"  Trailer: {m['trailer']}")
        print(f"  Poster:  {m['poster_principal']}")
        print(f"  Backdrops[{len(m['backdrops'])}]: {', '.join(m['backdrops'][:3])}...")
        print(f"  Cert_ES: {m['certificacion_ES']}  Providers ES: {m['providers_ES']}")
        print()
