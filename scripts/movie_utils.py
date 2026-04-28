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
HISTORIC_FILE = STATE_DIR / "historic.json"
DISCARDS_FILE = STATE_DIR / "discards.json"

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

def _is_non_latin(text: str) -> bool:
    """Detecta títulos en CJK (japonés, chino, coreano) u otros scripts no latinos."""
    for ch in text:
        cp = ord(ch)
        if 0x3000 <= cp <= 0x9FFF or 0xAC00 <= cp <= 0xD7FF or 0xF900 <= cp <= 0xFAFF:
            return True
    return False
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

def mark_published(selection_data: dict, short_id: str):
    state = _load_state()
    timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    
    # Extraer todos los datos relevantes de la selección
    tmdb_id = selection_data.get("tmdb_id")
    title = selection_data.get("titulo", "N/A")

    new_entry = {
      "id": tmdb_id,
      "title": title,
      "timestamp": timestamp,
      "trailer_url": selection_data.get("trailer_url"),
      "score": selection_data.get("score"),
      "trailer_views_at_selection": selection_data.get("views"),
      "trailer_fps": selection_data.get("trailer_fps"),
      "strategy": selection_data.get("hook_angle"),
      "movie_release_date": selection_data.get("fecha_estreno"),
      "short_id": short_id,
      "short_views": 0,
      "short_likes": 0,
      "short_comments": 0
    }

    if not any(pub.get("id") == tmdb_id for pub in state["published_ids"]):
        state["published_ids"].append(new_entry)
        _save_state(state)
        _save_to_historic(new_entry)
        logging.info(f"✅ Película marcada como publicada: {title} (ID: {tmdb_id})")
    else:
        logging.warning(f"Ya publicada: {title} (ID: {tmdb_id})")

def is_published(tmdb_id: int) -> bool:
    state = _load_state()
    return any(pub.get("id") == tmdb_id for pub in state["published_ids"])

def log_discard(title: str, reason: str, tmdb_id: int = None):
    """Guarda el motivo del descarte en un JSON estructurado para investigación."""
    discards = []
    if DISCARDS_FILE.exists():
        try:
            content = DISCARDS_FILE.read_text(encoding="utf-8")
            if content:
                discards = json.loads(content)
        except: pass
    
    discards.append({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "title": title,
        "tmdb_id": tmdb_id,
        "reason": reason
    })
    
    try:
        # Guardamos los últimos 300 descartes para tener historial de sobra
        DISCARDS_FILE.write_text(json.dumps(discards[-300:], ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        logging.error(f"Error al guardar discards.json: {e}")

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

        # Fallback al backdrop si no hay póster (común en anuncios tempranos)
        if not poster_principal:
            backdrops = [f"{IMG_BASE_URL}/{BACKDROP_SIZE}{b['file_path']}" for b in data.get("images", {}).get("backdrops", [])]
            poster_principal = backdrops[0] if backdrops else None

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

        titulo = data["title"]
        if _is_non_latin(titulo):
            data_en = api_get(f"/movie/{tmdb_id}", {"language": "en-US"})
            titulo_en = data_en.get("title", "") if data_en else ""
            if titulo_en and not _is_non_latin(titulo_en):
                logging.info(f"⚠️ Título no latino '{titulo}' → usando inglés: '{titulo_en}'")
                titulo = titulo_en
            else:
                logging.warning(f"⚠️ Sin traducción latina para '{titulo}', se usará el original")

        enriched_data = {
            "tmdb_id": tmdb_id,
            "titulo": titulo,
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
    """FALLBACK simple: Traduce o mejora la sinopsis si existe en otro idioma."""
    try:
        # Intentamos obtener la sinopsis en inglés como fallback
        movie_data = api_get(f"/movie/{tmdb_id}", {"language": "en-US"})
        if not movie_data or not movie_data.get('overview'): 
            return "" # Si no hay ni en inglés, no inventamos
        
        overview_en = movie_data.get('overview', '')
        prompt = f"Escribe una sinopsis corta (50 palabras) y gamberra en español para la película '{title}'. Basate estrictamente en esta trama: {overview_en}"
        
        config = load_config()
        client = genai.Client(api_key=config["GEMINI_API_KEY"])
        response = client.models.generate_content(model=GEMINI_MODEL, contents=prompt)
        return response.text.strip() if response.text else ""
    except Exception:
        return ""

# --- DEEP RESEARCH AGENT (STANDARD API) ---
def get_deep_research_data(title: str, year: int, main_actor: str, tmdb_id: str, overview: str = "") -> dict | None:
    """
    Obtiene salseo y DECIDE cuál es el mejor ángulo de venta (Gancho) usando la API Estándar.
    Si no hay sinopsis, intenta buscar en la web primero.
    """
    logging.info(f"🧠 Deep Research: Analizando estrategia editorial para '{title}'...")
    
    config = load_config()
    if not config: return None

    # Si no tenemos sinopsis de TMDB, intentamos una búsqueda web rápida con Gemini (si el modelo lo soporta)
    # o simplemente le pedimos a Gemini que use sus herramientas de búsqueda si están activas.
    
    try:
        client = genai.Client(api_key=config["GEMINI_API_KEY"])

        contexto_sinopsis = f"\nSINOPSIS OFICIAL (TMDB): {overview}" if overview else "\n⚠️ ATENCIÓN: No tengo sinopsis oficial de TMDB para esta película."

        research_prompt = f"""
        Investiga a fondo la película '{title}' estrenada o por estrenar en el año {year}. 
        {contexto_sinopsis}
        
        **TU MISIÓN:**
        1. Si la sinopsis oficial está vacía, BUSCA información real sobre de qué trata esta película específica de {year}. No inventes.
        2. Decide CÓMO venderla en un video corto con humor canalla, divertido y mucho salseo.
        
        **ESTILO:** No seas un crítico de cine aburrido. Sé gamberro, usa lenguaje de la calle (jerga española moderna), evita palabras rebuscadas. Hazlo MUY gracioso y exagerado. Queremos salseo de bar, anécdotas locas y humor gamberro que den ganas de ver la peli o de reírse con ella.
        
        **REGLAS CRÍTICAS:**
        - Si NO encuentras información real de la trama o es una película diferente a la del año {year}, responde con "ERROR: NO_INFO".
        - **NOMBRES AL MÍNIMO:** No aburras con una lista de nombres de actores o personajes. Como mucho menciona a uno y, si no es una superestrella mundial, explica brevemente quién es o qué ha hecho.
        - Prohibido alucinar. Si no hay datos, no hay vídeo.
        - Usa al actor principal ({main_actor}) como referencia si es relevante.
        
        Responde SÓLO con un JSON válido o la palabra "ERROR: NO_INFO".
        Formato JSON:
        {{
            "synopsis": "Sinopsis MUY divertida, gamberra y exagerada de la trama REAL (evita listas de nombres)",
            "actor_reference": "Dato curioso o salseo sobre {main_actor} (si es famoso) o sobre la producción",
            "director": "Nombre del director y su estilo (solo si es relevante para la gracia)",
            "movie_curiosity": "El salseo/dato impactante o gracioso real de esta película",
            "hook_angle": "ACTOR" | "DIRECTOR" | "CURIOSITY" | "PLOT",
            "platform": "SOLO el nombre de la plataforma (ej: Cine, Netflix, Disney+, Amazon Prime Video). Prohibido añadir chistes, comentarios o descripciones largas."
        }}
        """
        
        logging.info(f"DEBUG - Enviando consulta a Gemini para investigar '{title}' ({year})...")
        
        # Implementación de reintentos para errores 503/Timeout
        max_retries = 3
        response = None
        
        for attempt in range(max_retries):
            try:
                # Usamos google-search si está disponible para evitar alucinaciones
                response = client.models.generate_content(
                    model=GEMINI_MODEL, 
                    contents=research_prompt,
                    config={"tools": [{"google_search": {}}]}
                )
                break # Éxito, salimos del bucle
            except Exception as e:
                error_str = str(e)
                # Filtramos errores recuperables: 503 (Service Unavailable), Deadline Exceeded, 429 (Too Many Requests)
                if "503" in error_str or "Deadline" in error_str or "429" in error_str:
                    if attempt < max_retries - 1:
                        wait_time = (attempt + 1) * 5
                        logging.warning(f"⚠️ Error temporal de Gemini ({e}). Reintentando en {wait_time}s... (Intento {attempt+1}/{max_retries})")
                        time.sleep(wait_time)
                        continue
                raise e # Si no es un error temporal o se acabaron los intentos, lanzamos la excepción
        
        text = response.text.strip()
        
        if "ERROR: NO_INFO" in text:
            logging.error(f"❌ La IA no ha encontrado información fiable para '{title}' y ha abortado para no inventar.")
            return None

        if "```json" in text:
            text = text.split("```json")[1].split("```")[0].strip()
        elif "```" in text:
            text = text.split("```")[1].split("```")[0].strip()
            
        final_json = json.loads(text)

        # Doble check de seguridad: si la sinopsis generada es demasiado corta o genérica
        if not final_json.get("synopsis") or len(final_json["synopsis"]) < 20:
             logging.error("❌ La sinopsis generada es demasiado pobre. Abortando por seguridad.")
             return None

        return final_json

    except Exception as e:
        logging.error(f"Error Deep Research (Standard): {e}")
        # Si no hay overview de TMDB y falló el research, DEBEMOS abortar
        if not overview:
            return None
            
        # Si al menos tenemos el overview de TMDB, podemos seguir con un fallback básico
        return {
            "synopsis": overview,
            "actor_reference": main_actor,
            "director": "Desconocido",
            "movie_curiosity": "Se dice que es una de las pelis del año.",
            "hook_angle": "PLOT",
            "platform": "Cine"
        }

def _save_to_historic(entry):
    """Guarda la entrada en el fichero histórico (acumulativo, nunca se borra)."""
    historic_data = []
    if HISTORIC_FILE.exists():
        try:
            content = HISTORIC_FILE.read_text(encoding="utf-8")
            if content:
                historic_data = json.loads(content)
        except Exception as e:
            logging.error(f"Error al leer historic.json: {e}")
    
    historic_data.append(entry)
    
    try:
        HISTORIC_FILE.write_text(json.dumps(historic_data, ensure_ascii=False, indent=2), encoding="utf-8")
        logging.info(f"📈 Registro añadido al histórico.")
    except Exception as e:
        logging.error(f"Error al guardar en historic.json: {e}")