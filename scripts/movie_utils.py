# scripts/movie_utils.py
import logging
import json
import requests
from pathlib import Path
from datetime import datetime, timezone, timedelta
from operator import itemgetter
from bs4 import BeautifulSoup

# --- Configuración de Paths (global para utils) ---
ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = ROOT / "config"
STATE_DIR = ROOT / "output" / "state"
PUBLISHED_FILE = STATE_DIR / "published.json"

# --- Configuración del Logging (hereda del caller) ---
# No basicConfig aquí, usa el del script principal

# --- Configuración de APIs y Constantes ---
def load_config():
    """Carga keys de config. Retorna dict o None si falla."""
    try:
        with open(CONFIG_DIR / "tmdb_api_key.txt") as f:
            tmdb_key = f.read().strip()
        with open(CONFIG_DIR / "google_api_key.txt") as f:
            gemini_key = f.read().strip()
        return {"TMDB_API_KEY": tmdb_key, "GEMINI_API_KEY": gemini_key}
    except FileNotFoundError as e:
        logging.error(f"ERROR: No se encuentra archivo: {e}. Verifica config/")
        return None

TMDB_BASE_URL = "https://api.themoviedb.org/3"
IMG_BASE_URL = "https://image.tmdb.org/t/p"
POSTER_SIZE = "w500"
BACKDROP_SIZE = "w1280"

# --- FUNCIONES DE GESTIÓN DE ESTADO ---
def _load_state():
    if not PUBLISHED_FILE.exists():
        return {"published_ids": []}
    try:
        content = PUBLISHED_FILE.read_text(encoding="utf-8")
        if not content:
            return {"published_ids": []}
        state = json.loads(content)
    except json.JSONDecodeError:
        logging.error(f"Error al decodificar {PUBLISHED_FILE}, se tratará como vacío.")
        return {"published_ids": []}

    now = datetime.now(timezone.utc)
    if state.get("published_ids"):
        one_month_ago = now - timedelta(days=30)
        filtered_published = []
        for pub in state.get("published_ids", []):
            if not isinstance(pub, dict):
                filtered_published.append({
                    "id": pub,
                    "timestamp": now.isoformat() + "Z",
                    "trailer_url": None,
                    "title": "N/A (Legacy)"
                })
                continue
            try:
                if "title" not in pub:
                    pub["title"] = "N/A (Legacy)"
                pub_time = datetime.fromisoformat(pub["timestamp"].replace("Z", "+00:00"))
                if pub_time >= one_month_ago:
                    filtered_published.append(pub)
                else:
                    logging.info(f"Limpiando entrada antigua: {pub.get('title', 'N/A')} ({pub.get('id', 'N/A')})")
            except (ValueError, KeyError) as e:
                logging.warning(f"Entrada inválida en published_ids: {pub}, error: {e}. Omitiendo.")
                continue
        state["published_ids"] = filtered_published
    return state

def _save_state(state):
    try:
        PUBLISHED_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        logging.info(f"Estado guardado en {PUBLISHED_FILE}")
    except Exception as e:
        logging.error(f"Error al guardar estado: {e}")

def mark_published(tmdb_id: int, trailer_url: str, title: str):
    """Marca una película como publicada en el estado."""
    state = _load_state()
    timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    new_entry = {
        "id": tmdb_id,
        "title": title,
        "timestamp": timestamp,
        "trailer_url": trailer_url
    }
    if new_entry not in state["published_ids"]:
        state["published_ids"].append(new_entry)
        _save_state(state)
        logging.info(f"✅ Película marcada como publicada: {title} (ID: {tmdb_id})")
    else:
        logging.warning(f"Ya publicada: {title} (ID: {tmdb_id})")

def is_published(tmdb_id: int) -> bool:
    """Verifica si un TMDB ID ya fue publicado."""
    state = _load_state()
    return any(pub.get("id") == tmdb_id for pub in state["published_ids"])

# --- API HELPERS ---
def api_get(path, params=None):
    config = load_config()
    if not config:
        return None
    p = {"api_key": config["TMDB_API_KEY"]}
    if params:
        p.update(params)
    r = requests.get(f"{TMDB_BASE_URL}{path}", params=p, timeout=20)
    r.raise_for_status()
    return r.json()

# --- WEB FALLBACK ---
def get_synopsis_from_web(title: str, year: int) -> str:
    """Si no hay sinopsis en TMDB, busca en web."""
    try:
        query = f"sinopsis {title} película {year}"
        search_params = {"q": query, "num": 3}
        r = requests.get("https://www.google.com/search", params=search_params, timeout=10)
        soup = BeautifulSoup(r.text, 'html.parser')
        snippet = soup.find("div", class_="BNeawe s3v9rd AP7Wnd")
        snippet_text = snippet.text if snippet else ""
        if len(snippet_text) > 200:
            snippet_text = snippet_text[:197] + "..."
        logging.info(f"Sinopsis de web para '{title}': {snippet_text[:100]}...")
        return snippet_text if snippet_text else ""
    except Exception as e:
        logging.warning(f"Error buscando sinopsis para '{title}': {e}")
        return ""
    
def enrich_movie_basic(tmdb_id: int, movie_name: str, year: int, trailer_url: str = None):
    """Enrich básico: TMDB sin web fallback (rápido para ranking)."""
    try:
        data = api_get(
            f"/movie/{tmdb_id}",
            {
                "language": "es-ES",
                "append_to_response": "images,credits,release_dates,watch/providers",
                "include_image_language": "es,null,en"
            }
        )
        if not data or not data.get("title"):
            logging.warning(f"Enrich básico falló: Sin título para ID {tmdb_id}")
            return None

        sinopsis = data.get("overview", "")  # Solo TMDB, no web

        posters = [f"{IMG_BASE_URL}/{POSTER_SIZE}{p['file_path']}" for p in data.get("images", {}).get("posters", [])]
        poster_principal = posters[0] if posters else None
        if not poster_principal:
            logging.warning(f"Sin póster básico para '{data.get('title')}' (ID: {tmdb_id})")
            return None

        # Mínimo para rank (géneros, fecha opcionales)
        generos = [g["name"] for g in data.get("genres", [])]
        fecha_estreno = data.get("release_date", "")

        enriched_data = {
            "id": tmdb_id,
            "titulo": data["title"],
            "fecha_estreno": fecha_estreno,
            "generos": generos,
            "sinopsis": sinopsis,  # Puede ser vacía
            "poster_principal": poster_principal,
            "has_poster": bool(poster_principal)
        }
        if trailer_url:
            enriched_data["trailer_url"] = trailer_url
        return enriched_data
    except Exception as e:
        logging.error(f"Error enrich básico '{movie_name}' (ID: {tmdb_id}): {e}")
        return None

# --- ENRICH ---
def enrich_movie(tmdb_id: int, movie_name: str, year: int, trailer_url: str = None):
    """Obtiene todos los detalles de una película desde TMDB, incluyendo edad y plataformas. Si no sinopsis, busca en web."""
    try:
        data = api_get(
            f"/movie/{tmdb_id}",
            {
                "language": "es-ES",
                "append_to_response": "images,credits,release_dates,watch/providers",
                "include_image_language": "es,null,en"
            }
        )
        if not data or not data.get("title"):
            logging.warning(f"Enriquecimiento falló: Sin título para ID {tmdb_id}")
            return None

        # Sinopsis: Si no hay, busca en web
        sinopsis = data.get("overview", "")
        if not sinopsis:
            logging.info(f"Sin overview en TMDB para '{data.get('title')}'. Buscando en web...")
            sinopsis = get_synopsis_from_web(data.get("title"), year)
            if not sinopsis:
                logging.warning(f"Sinopsis no encontrada para '{data.get('title')}' (ni TMDB ni web) – pero OK, no bloquea.")
                sinopsis = ""  # ✅ Vacío OK, no return None

        # Pósters y backdrops
        posters = [f"{IMG_BASE_URL}/{POSTER_SIZE}{p['file_path']}" for p in data.get("images", {}).get("posters", [])]
        backdrops = [f"{IMG_BASE_URL}/{BACKDROP_SIZE}{b['file_path']}" for b in data.get("images", {}).get("backdrops", [])]
        poster_principal = posters[0] if posters else None
        if not poster_principal:
            logging.warning(f"Sin póster para '{data.get('title')}' (ID: {tmdb_id})")
            return None  # ✅ Solo esto bloquea

        # Géneros
        generos = [g["name"] for g in data.get("genres", [])]

        # Fecha estreno (ES si disponible)
        fecha_estreno = data.get("release_date", "")
        for rel in data.get("release_dates", {}).get("results", []):
            if rel["iso_3166_1"] == "ES":
                for rd in rel["release_dates"]:
                    if rd["release_date"]:
                        fecha_estreno = rd["release_date"]
                        break

        # Certificación edad (ES si disponible)
        certificacion_ES = "Pendiente"
        for rel in data.get("release_dates", {}).get("results", []):
            if rel["iso_3166_1"] == "ES":
                for rd in rel["release_dates"]:
                    if rd.get("certification"):
                        certificacion_ES = rd["certification"]
                        break

        # Reparto top (top 3 cast)
        reparto = sorted(
            data.get("credits", {}).get("cast", []),
            key=itemgetter("order")
        )[:3]
        reparto_top = [f"{c['name']} ({c['character']})" for c in reparto if c.get("character")]

        # Plataformas (streaming/buy/rent en ES)
        providers = data.get("watch/providers", {}).get("results", {}).get("ES", {})
        platforms = {
            "streaming": [p["provider_name"] for p in providers.get("flatrate", [])],
            "buy": [p["provider_name"] for p in providers.get("buy", [])],
            "rent": [p["provider_name"] for p in providers.get("rent", [])]
        }

        # --- AÑADIDO: Log para verificar plataformas ---
        has_streaming = bool(platforms["streaming"])
        has_purchase = bool(platforms["buy"] or platforms["rent"])
        if has_streaming or has_purchase:
            logging.info(f"✓ Plataformas encontradas para '{data['title']}'.")
        else:
            logging.info(f"ℹ️ Sin plataformas de streaming/compra para '{data['title']}'. Se usará 'Cine'.")
        # --- FIN AÑADIDO ---

        enriched_data = {
            "id": tmdb_id,
            "titulo": data["title"],
            "fecha_estreno": fecha_estreno,
            "generos": generos,
            "sinopsis": sinopsis,  # Puede ser "", no bloquea
            "poster_principal": poster_principal,
            "posters": posters,
            "backdrops": backdrops,
            "reparto_top": reparto_top,
            "platforms": platforms,
            "certificacion_ES": certificacion_ES,
            "popularity": data.get("popularity", 0),
            "has_poster": bool(poster_principal)
        }

        # Agregar trailer_url si se proporciona
        if trailer_url:
            enriched_data["trailer_url"] = trailer_url

        return enriched_data
    except Exception as e:
        logging.error(f"Error enriqueciendo '{movie_name}' (ID: {tmdb_id}): {e}")
        return None
    
def get_synopsis_chain(title: str, year: int) -> str:
    """Chain multi-site para sinopsis oficial: Filmaffinity > Sensacine > IMDb. Retorna primera hit >50 chars."""
    sites = [
        f"sinopsis {title} {year} site:filmaffinity.com",
        f"sinopsis {title} {year} site:sensacine.com",
        f"plot summary {title} {year} site:imdb.com"
    ]
    for site_query in sites:
        try:
            params = {"q": site_query, "num": 3}
            r = requests.get("https://www.google.com/search", params=params, timeout=10)
            soup = BeautifulSoup(r.text, 'html.parser')
            snippet = soup.find("div", class_="BNeawe s3v9rd AP7Wnd")
            text = snippet.text.strip() if snippet else ""
            if len(text) > 50:  # Válido si >50 chars
                logging.info(f"Hit en {site_query.split(' site:')[1]}: {text[:100]}...")
                return text[:200]  # Corta a 200 para base
        except Exception as e:
            logging.warning(f"Falla en {site_query}: {e}")
    logging.warning(f"No sinopsis en chain para '{title} {year}'.")
    return ""  # Vacío si todo falla