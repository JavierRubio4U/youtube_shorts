# scripts/movie_utils.py
import logging
import json
import requests
from pathlib import Path
from datetime import datetime, timezone, timedelta
from operator import itemgetter
from bs4 import BeautifulSoup
import google.generativeai as genai
from gemini_config import GEMINI_MODEL
from google.generativeai.types import HarmCategory, HarmBlockThreshold
import time # <-- NUEVO: Para el polling asíncrono

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

# --- FUNCIONES DE GESTIÓN DE ESTADO (SIN CAMBIOS) ---
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
    if not any(pub.get("id") == tmdb_id for pub in state["published_ids"]):
        state["published_ids"].append(new_entry)
        _save_state(state)
        logging.info(f"✅ Película marcada como publicada: {title} (ID: {tmdb_id})")
    else:
        logging.warning(f"Ya publicada: {title} (ID: {tmdb_id})")

def is_published(tmdb_id: int) -> bool:
    """Verifica si un TMDB ID ya fue publicado."""
    state = _load_state()
    return any(pub.get("id") == tmdb_id for pub in state["published_ids"])

# --- API HELPERS (SIN CAMBIOS) ---
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

# --- WEB FALLBACK (SIN CAMBIOS) ---
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
    """Enrich básico: TMDB sin web fallback (rápido para ranking). (SIN CAMBIOS)"""
    # ... (El cuerpo de la función enrich_movie_basic sigue igual) ...
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

        sinopsis = data.get("overview", "")

        # --- EXTRACCIÓN DE ACTORES (NUEVO) ---
        cast_data = data.get("credits", {}).get("cast", [])
        # Cogemos los 3 primeros nombres para el prompt de Gemini
        top_actors = [actor["name"] for actor in cast_data[:3]]
        # -------------------------------------

        posters = [f"{IMG_BASE_URL}/{POSTER_SIZE}{p['file_path']}" for p in data.get("images", {}).get("posters", [])]
        poster_principal = posters[0] if posters else None
        if not poster_principal:
            logging.warning(f"Sin póster básico para '{data.get('title')}' (ID: {tmdb_id})")
            return None

        generos = [g["name"] for g in data.get("genres", [])]
        
        # Lógica de Fechas
        fecha_estreno = data.get("release_date", "").split("T")[0]
        country_priority = ["ES", "US"]
        type_priority = [3, 2, 1]
        all_release_results = data.get("release_dates", {}).get("results", [])
        found_priority_date = False

        for country_code in country_priority:
            country_releases = []
            for rel in all_release_results:
                if rel.get("iso_3166_1") == country_code:
                    country_releases = rel.get("release_dates", [])
                    break
            
            if country_releases:
                for type_code in type_priority:
                    for rd in country_releases:
                        if rd.get("type") == type_code and rd.get("release_date"):
                            fecha_estreno = rd["release_date"].split("T")[0]
                            found_priority_date = True
                            break
                    if found_priority_date: break
            if found_priority_date: break

        # Lógica de Plataformas
        all_providers = data.get("watch/providers", {}).get("results", {})
        es_providers = all_providers.get("ES", {})
        us_providers = all_providers.get("US", {})
        
        es_streaming = [p["provider_name"] for p in es_providers.get("flatrate", [])]
        us_streaming = [p["provider_name"] for p in us_providers.get("flatrate", [])]
        
        final_streaming_list = []
        if es_streaming:
            final_streaming_list = es_streaming
        elif us_streaming:
            final_streaming_list = [f"{p} (US)" for p in us_streaming]

        platforms = {
            "streaming": final_streaming_list
        }
        has_streaming = bool(platforms["streaming"])

        enriched_data = {
            "id": tmdb_id,
            "titulo": data["title"],
            "fecha_estreno": fecha_estreno,
            "generos": generos,
            "sinopsis": sinopsis,
            "actors": top_actors,  # <--- GUARDADO AQUÍ
            "poster_principal": poster_principal,
            "has_poster": bool(poster_principal),
            "platforms": platforms,
            "has_streaming": has_streaming
        }
        if trailer_url:
            enriched_data["trailer_url"] = trailer_url
        return enriched_data
    except Exception as e:
        logging.error(f"Error enrich básico '{movie_name}' (ID: {tmdb_id}): {e}")
        return None
    
# --- CAMBIO: get_synopsis_chain ahora es un simple FALLBACK ---
def get_synopsis_chain(title: str, year: int, tmdb_id: str) -> str:
    """FALLBACK: Obtiene datos fiables de TMDB y usa la IA para reescribirlos."""
    logging.warning(f"⚠️ Usando FALLBACK de sinopsis (TMDB + IA) para '{title}'...")
    try:
        movie_data = api_get(f"/movie/{tmdb_id}", {"language": "en-US"})
        if not movie_data:
            return ""

        tmdb_overview = movie_data.get('overview', '')
        tmdb_tagline = movie_data.get('tagline', '')
        genres = [g['name'] for g in movie_data.get('genres', [])]

        fact_sheet = f"Título Original: {movie_data.get('original_title')}\n"
        fact_sheet += f"Sinopsis de TMDB: {tmdb_overview}\n"
        fact_sheet += f"Tagline: {tmdb_tagline}\n"
        fact_sheet += f"Géneros: {', '.join(genres)}\n"

        prompt = f"""
        Eres un guionista experto de cine. Tu tarea es escribir una sinopsis atractiva y concisa en español.
        **Ficha Técnica:**
        ---
        {fact_sheet}
        ---
        **Instrucciones:**
        1. USA SOLO ESTOS DATOS.
        2. Idioma: Español.
        3. Longitud: 50-70 palabras.
        4. Output: Solo el texto.
        """

        config = load_config()
        genai.configure(api_key=config["GEMINI_API_KEY"])
        model = genai.GenerativeModel(GEMINI_MODEL)
        safety_settings = {HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_NONE}
        
        response = model.generate_content(prompt, safety_settings=safety_settings)
        final_synopsis = response.text.strip() if response.parts else ""

        if not final_synopsis:
            logging.error(f"La IA no pudo generar una sinopsis.")
            return ""

        logging.info(f"Sinopsis generada con IA (reescritura): {final_synopsis}")
        return final_synopsis

    except Exception as e:
        logging.error(f"Error crítico en get_synopsis_chain para '{title}': {e}", exc_info=True)
        return ""

# --- NUEVA FUNCIÓN: Deep Research Agent ---
def get_deep_research_data(title: str, year: int, main_actor: str, tmdb_id: str) -> dict | None:
    """
    Usa el Agente Deep Research (Asíncrono) para obtener datos enriquecidos y citados.
    Retorna sinopsis, referencia de actor y plataforma.
    """
    logging.info(f"🧠 Iniciando Deep Research (asíncrono) para '{title}' (ID: {tmdb_id})...")
    
    config = load_config()
    if not config:
        return None
    
    try:
        # Configurar cliente para Interactions API
        client = genai.Client(api_key=config["GEMINI_API_KEY"])

        # Prompt complejo que pide los 4 elementos clave
        research_prompt = f"""
        Realiza una investigación detallada y verificada de la película '{title}' ({year}).
        Céntrate en obtener los siguientes datos, citando las fuentes web:
        1. **Sinopsis Concisa y Gamberra:** Reescribe la trama en español (50-70 palabras), manteniendo un tono de 'chisme' o cotilleo.
        2. **Referencia Ácida del Actor:** Busca un dato curioso, un papel icónico, o un escándalo reciente del actor '{main_actor}' que pueda ser usado como referencia ácida en un guion de comedia.
        3. **Director:** Nombre del director/a principal.
        4. **Plataforma ES:** Confirma la plataforma de estreno en España (Cine, Netflix, HBO, etc.).
        
        El resultado debe ser un JSON limpio y usable, con las claves: 'synopsis', 'actor_reference', 'director', 'platform'.
        """
        
        # Iniciar la tarea en segundo plano
        interaction = client.interactions.create(
            agent="deep-research-pro-preview-12-2025",
            input=research_prompt,
            background=True
        )
        logging.info(f"   -> Tarea ID: {interaction.id}. Estado inicial: {interaction.status.name}")

        # --- Polling (Sondeo) ---
        max_wait_time = 180 # 3 minutos (configurable según la latencia real)
        start_time = time.time()
        
        while interaction.status.name in ["PENDING", "PROCESSING"] and (time.time() - start_time) < max_wait_time:
            time.sleep(10) # Esperar 10 segundos entre consultas
            interaction = client.interactions.get(interaction.id)
            logging.info(f"   -> Sondeando... {int(time.time() - start_time)}s. Estado: {interaction.status.name}")

        if interaction.status.name == "COMPLETED":
            # El agente Deep Research devuelve un informe citado.
            # Usaremos un modelo estándar (limpiador) para extraer el JSON que necesitamos.
            logging.info("   -> Investigación COMPLETA. Limpiando el resultado...")
            
            cleaner_prompt = f"""
            Eres un extractor de datos de IA. Tu única tarea es extraer los cuatro campos requeridos del informe de investigación citado a continuación.
            Responde SÓLO con un objeto JSON válido con las claves: 'synopsis', 'actor_reference', 'director', 'platform'.
            
            Reporte de Deep Research:
            ---
            {interaction.result.text}
            ---
            """
            
            model_cleaner = genai.GenerativeModel(GEMINI_MODEL)
            clean_response = model_cleaner.generate_content(cleaner_prompt)
            
            final_json_str = clean_response.text.strip().lstrip("```json").rstrip("```").strip()
            
            final_data = json.loads(final_json_str)
            logging.info(f"✅ Deep Research procesado con éxito.")
            return final_data
            
        elif interaction.status.name == "FAILED":
            logging.error(f"❌ Tarea de Deep Research fallida: {interaction.error_message}")
            return None
        
        else: # TIMEOUT
            logging.error(f"⌛ Tarea de Deep Research excedió el tiempo límite ({max_wait_time}s).")
            return None

    except Exception as e:
        logging.error(f"Error crítico en la llamada a Deep Research/Limpiador: {e}")
        return None