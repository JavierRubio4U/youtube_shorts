# scripts/movie_utils.py
import logging
import json
import requests
from pathlib import Path
from datetime import datetime, timezone, timedelta
from bs4 import BeautifulSoup

from google import genai
from gemini_config import GEMINI_MODEL
import time

# --- Configuración de Paths (global para utils) ---
ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = ROOT / "config"
STATE_DIR = ROOT / "output" / "state"
PUBLISHED_FILE = STATE_DIR / "published.json"

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
            except (ValueError, KeyError):
                continue
        state["published_ids"] = filtered_published
    return state

def _save_state(state):
    try:
        PUBLISHED_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        logging.error(f"Error al guardar estado: {e}")

def mark_published(tmdb_id: int, trailer_url: str, title: str):
    state = _load_state()
    timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    new_entry = {
        "id": tmdb_id,
        "title": title,
        "timestamp": timestamp,
        "trailer_url": trailer_url
    }
    if not any(pub.get("id") == tmdb_id for pub in state["published_ids"]):
        state["published_ids"].append(new_entry)
        _save_state(state)
        logging.info(f"✅ Película marcada como publicada: {title} (ID: {tmdb_id})")
    else:
        logging.warning(f"Ya publicada: {title} (ID: {tmdb_id})")

def is_published(tmdb_id: int) -> bool:
    state = _load_state()
    return any(pub.get("id") == tmdb_id for pub in state["published_ids"])

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

def enrich_movie_basic(tmdb_id: int, movie_name: str, year: int, trailer_url: str = None):
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
            return None

        sinopsis = data.get("overview", "")
        cast_data = data.get("credits", {}).get("cast", [])
        top_actors = [actor["name"] for actor in cast_data[:3]]

        posters = [f"{IMG_BASE_URL}/{POSTER_SIZE}{p['file_path']}" for p in data.get("images", {}).get("posters", [])]
        poster_principal = posters[0] if posters else None
        if not poster_principal:
            return None

        generos = [g["name"] for g in data.get("genres", [])]
        
        fecha_estreno = data.get("release_date", "").split("T")[0]
        all_release_results = data.get("release_dates", {}).get("results", [])
        for rel in all_release_results:
             if rel.get("iso_3166_1") == "ES":
                 for rd in rel.get("release_dates", []):
                     if rd.get("type") == 3 and rd.get("release_date"):
                         fecha_estreno = rd["release_date"].split("T")[0]
                         break

        all_providers = data.get("watch/providers", {}).get("results", {})
        es_providers = all_providers.get("ES", {})
        us_providers = all_providers.get("US", {})
        
        es_streaming = [p["provider_name"] for p in es_providers.get("flatrate", [])]
        us_streaming = [p["provider_name"] for p in us_providers.get("flatrate", [])]
        
        final_streaming_list = es_streaming if es_streaming else [f"{p} (US)" for p in us_streaming]
        platforms = {"streaming": final_streaming_list}

        enriched_data = {
            "tmdb_id": tmdb_id, # Usamos tmdb_id
            "titulo": data["title"],
            "fecha_estreno": fecha_estreno,
            "generos": generos,
            "sinopsis": sinopsis,
            "actors": top_actors,
            "poster_principal": poster_principal,
            "has_poster": bool(poster_principal),
            "platforms": platforms,
            "has_streaming": bool(final_streaming_list)
        }
        if trailer_url:
            enriched_data["trailer_url"] = trailer_url
        return enriched_data
    except Exception as e:
        logging.error(f"Error enrich básico '{movie_name}': {e}")
        return None
    
def get_synopsis_chain(title: str, year: int, tmdb_id: str) -> str:
    """FALLBACK simple."""
    try:
        movie_data = api_get(f"/movie/{tmdb_id}", {"language": "en-US"})
        if not movie_data: return ""
        
        prompt = f"Escribe una sinopsis corta (50 palabras) y gamberra en español para la película '{title}'. Trama: {movie_data.get('overview', '')}"
        
        config = load_config()
        client = genai.Client(api_key=config["GEMINI_API_KEY"])
        response = client.models.generate_content(model=GEMINI_MODEL, contents=prompt)
        return response.text.strip() if response.text else ""
    except Exception:
        return ""

# --- DEEP RESEARCH AGENT (STANDARD API) ---
def get_deep_research_data(title: str, year: int, main_actor: str, tmdb_id: str) -> dict | None:
    """
    Obtiene salseo y DECIDE cuál es el mejor ángulo de venta (Gancho) usando la API Estándar.
    """
    logging.info(f"🧠 Deep Research: Analizando estrategia editorial para '{title}'...")
    
    config = load_config()
    if not config: return None
    
    try:
        client = genai.Client(api_key=config["GEMINI_API_KEY"])

        research_prompt = f"""
        Investiga la película '{title}' ({year}). 
        Tu objetivo es decidir CÓMO venderla en un video corto de humor ácido/salseo.
        
        1. **Curiosidad/Salseo:** Algo impactante (presupuesto, peleas, anécdotas, marketing loco).
        2. **Actor Principal:** ({main_actor}) ¿Tiene trapos sucios, es una leyenda o un meme?
        3. **Director:** ¿Es famoso o tiene un estilo raro?
        
        DECISIÓN FINAL: ¿Cuál es el gancho más fuerte para empezar el vídeo?
        Elige UNO: 
        - 'ACTOR' (Si el actor es lo más interesante).
        - 'DIRECTOR' (Si es alguien de culto).
        - 'CURIOSITY' (Si el dato de producción es lo más fuerte).
        - 'PLOT' (Si la trama es tan absurda que se vende sola).
        
        IMPORTANTE: Responde SÓLO con un JSON válido.
        Formato JSON:
        {{
            "synopsis": "Sinopsis gamberra de la trama (máx 2 líneas)",
            "actor_reference": "Dato corto ácido sobre el actor",
            "director": "Nombre y estilo",
            "movie_curiosity": "El dato impactante/salseo",
            "hook_angle": "ACTOR" | "DIRECTOR" | "CURIOSITY" | "PLOT",
            "platform": "Cine o plataforma streaming (estimada)"
        }}
        """
        
        # Llamada directa sin agentes beta
        response = client.models.generate_content(model=GEMINI_MODEL, contents=research_prompt)
        text = response.text.strip()
        
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0].strip()
        elif "```" in text:
            text = text.split("```")[1].split("```")[0].strip()
            
        final_json = json.loads(text)
        return final_json

    except Exception as e:
        logging.error(f"Error Deep Research (Standard): {e}")
        # Fallback de seguridad
        return {
            "synopsis": "",
            "actor_reference": "",
            "director": "",
            "movie_curiosity": "Se dice que es la película del año",
            "hook_angle": "PLOT",
            "platform": "Cine"
        }