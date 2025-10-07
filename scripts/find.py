# test/find_with_ai.py
import sys
import logging
import json
from pathlib import Path
from datetime import datetime, timedelta, timezone
import unicodedata

# --- Dependencias de terceros ---
import yt_dlp  # type: ignore
import google.generativeai as genai
import requests

# --- Configuración de Paths ---
ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = ROOT / "config"
STATE_DIR = ROOT / "output" / "state"
STATE_DIR.mkdir(parents=True, exist_ok=True)
NEXT_FILE = STATE_DIR / "next_release.json"

# --- Configuración del Logging ---
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

# --- Configuración de APIs y Constantes ---
try:
    TMDB_API_KEY = (CONFIG_DIR / "tmdb_api_key.txt").read_text().strip()
    TMDB_BASE_URL = "https://api.themoviedb.org/3"
    IMG_BASE_URL = "https://image.tmdb.org/t/p"
    POSTER_SIZE = "w500"
    BACKDROP_SIZE = "w1280"
except FileNotFoundError:
    logging.error(f"ERROR: No se encuentra el archivo de API Key de TMDB.")
    TMDB_API_KEY = None

class SilentLogger:
    def debug(self, msg): pass
    def info(self, msg): pass
    def warning(self, msg): pass
    def error(self, msg): logging.error(msg)

# --- FUNCIONES ADAPTADAS ---

def api_get(path, params=None):
    p = {"api_key": TMDB_API_KEY}
    if params: p.update(params)
    r = requests.get(f"{TMDB_BASE_URL}{path}", params=p, timeout=20)
    r.raise_for_status()
    return r.json()

def enrich_movie(tmdb_id: int):
    try:
        data = api_get(
            f"/movie/{tmdb_id}",
            {"language": "es-ES", "append_to_response": "images,credits", "include_image_language": "es,null,en"}
        )
        if not data.get("title") or not data.get("overview"):
            return None

        posters = [f"{IMG_BASE_URL}/{POSTER_SIZE}{p['file_path']}" for p in data.get("images", {}).get("posters", [])[:5]]
        backdrops = [f"{IMG_BASE_URL}/{BACKDROP_SIZE}{b['file_path']}" for b in data.get("images", {}).get("backdrops", [])[:8]]
        
        return {
            "id": data["id"], "titulo": data["title"], "fecha_estreno": data["release_date"],
            "sinopsis": data["overview"], "generos": [g["name"] for g in data.get("genres", [])],
            "poster_principal": posters[0] if posters else None, "posters": posters, "backdrops": backdrops,
            "popularity": data.get("popularity", 0.0),
            "reparto_top": [c["name"] for c in data.get("credits", {}).get("cast", [])[:5]]
        }
    except Exception as e:
        logging.error(f"  -> Error al enriquecer película ID {tmdb_id}: {e}")
        return None

# --- LÓGICA PRINCIPAL DE BÚSQUEDA Y ANÁLISIS ---

def get_youtube_videos(search_query="official movie trailer", limit=50) -> list[dict]:
    """Busca en YouTube y devuelve una lista de diccionarios con título y URL."""
    logging.info(f"Buscando en YouTube: '{search_query}' (obteniendo hasta {limit} vídeos)...")
    ydl_opts = {
        'quiet': True, 'no_warnings': True, 'skip_download': True, 'logger': SilentLogger(),
        'extract_flat': 'in_playlist', 'playlistend': limit, 'forceipv4': True, 'no_check_certificate': True,
    }
    videos = []
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            search_results = ydl.extract_info(f"ytsearch{limit}:{search_query}", download=False)
            for entry in search_results.get('entries', []):
                if entry and entry.get('title') and entry.get('url'):
                    videos.append({'title': entry['title'], 'url': entry['url']})
            logging.info(f"Se obtuvieron {len(videos)} vídeos de YouTube.")
            return videos
    except Exception as e:
        logging.error(f"No se pudo completar la búsqueda en YouTube: {e}")
        return []

def analyze_videos_with_gemini(videos: list[dict]) -> list[dict] | None:
    """Usa Gemini para analizar una lista de vídeos y asociar la URL original."""
    logging.info("🧠 Contactando a Gemini para analizar los títulos...")
    try:
        api_key_path = CONFIG_DIR / "google_api_key.txt"
        genai.configure(api_key=api_key_path.read_text().strip())
    except Exception as e:
        logging.error(f"Fallo al configurar la API de Google: {e}"); return None

    # <<< CORRECCIÓN AQUÍ >>>
    # 1. Creamos la cadena de texto con los títulos y saltos de línea primero.
    video_titles = [v['title'] for v in videos]
    titles_str = "\n".join(f"{i+1}. {title}" for i, title in enumerate(video_titles))

    # 2. Luego, insertamos esa variable ya creada en el prompt.
    prompt = f"""
    Eres un analista experto en cine... Tu misión es analizar la siguiente lista de títulos de vídeos y seleccionar las 10 películas más prometedoras.
    **Reglas estrictas:**
    1.  **Filtro temporal OBLIGATORIO**: Descarta CUALQUIER película cuyo año de estreno no sea 2025 o 2026.
    2.  **Excluye**: Series, cortos, fakes, compilaciones y trailers para el mercado indio.
    3.  **Formato OBLIGATORIO**: Responde SÓLO con un array JSON de objetos con claves "title" y "year".
    **Títulos a analizar:**
    ---
    {titles_str}
    ---
    """
    # <<< FIN DE LA CORRECCIÓN >>>
    
    try:
        model = genai.GenerativeModel('models/gemini-2.5-pro')
        response = model.generate_content(prompt)
        cleaned_response = response.text.strip().replace("```json", "").replace("```", "").strip()
        ai_movies = json.loads(cleaned_response)
        
        # Re-asociar la URL original con el título limpio de la IA
        results_with_url = []
        for ai_movie in ai_movies:
            ai_title_simple = ''.join(filter(str.isalnum, ai_movie['title'])).lower()
            for video in videos:
                video_title_simple = ''.join(filter(str.isalnum, video['title'])).lower()
                if ai_title_simple in video_title_simple:
                    results_with_url.append({**ai_movie, 'trailer_url': video['url']})
                    break # Pasar a la siguiente película de la IA
        
        logging.info(f"✅ Gemini ha analizado y asociado URL a {len(results_with_url)} películas.")
        return results_with_url
    except Exception as e:
        logging.error(f"Ocurrió un error con Gemini: {e}"); return None

def search_and_filter_tmdb(movies: list[dict]) -> list[dict]:
    if not TMDB_API_KEY: logging.error("No hay API key de TMDB."); return []
    logging.info("\n🔎 Verificando películas en TMDB y filtrando por fecha...")
    final_movies = []
    four_months_ago = datetime.now() - timedelta(days=120)
    for movie in movies:
        title, year = movie.get('title'), movie.get('year')
        if not title or not year: continue
        logging.info(f"Buscando en TMDB: '{title}' ({year})")
        try:
            params = {'api_key': TMDB_API_KEY, 'query': title, 'year': year, 'language': 'en-US'}
            response = requests.get(f"{TMDB_BASE_URL}/search/movie", params=params)
            response.raise_for_status()
            results = response.json().get('results', [])
            if not results: logging.warning(f"  -> No se encontró en TMDB."); continue
            
            best_match = results[0]
            release_date_str = best_match.get('release_date')
            has_poster = bool(best_match.get('poster_path'))
            
            if release_date_str and datetime.strptime(release_date_str, '%Y-%m-%d') >= four_months_ago:
                final_movies.append({
                    "tmdb_id": best_match.get('id'),
                    "title": best_match.get('title'),
                    "release_date": release_date_str,
                    "has_poster": has_poster,
                    "trailer_url": movie.get('trailer_url') # <-- Llevamos la URL hasta el final
                })
                logging.info(f"  ✓ Aceptada: '{best_match.get('title')}' | Póster: {'Sí' if has_poster else 'No'}")
            else:
                logging.warning(f"  -> Rechazada (fecha inválida o antigua): '{best_match.get('title')}'")
        except Exception as e:
            logging.error(f"  -> Error buscando '{title}': {e}")
    return final_movies

# --- BLOQUE PRINCIPAL SIMPLIFICADO ---

def find_and_select_next():
    """
    Orquesta todo el proceso de descubrimiento y selección, y devuelve el resultado.
    """
    # 1. Obtener vídeos (título y URL) de YouTube
    youtube_videos = get_youtube_videos()
    if not youtube_videos:
        logging.error("🛑 No se pudieron obtener vídeos de YouTube. Abortando.")
        return None
        
    # 2. Analizar con Gemini para obtener lista limpia con URL asociada
    ai_recommendations = analyze_videos_with_gemini(youtube_videos)
    if not ai_recommendations:
        logging.error("🛑 La IA no pudo procesar la lista. Abortando.")
        return None
    
    # 3. Buscar en TMDB y filtrar por fecha
    candidate_list = search_and_filter_tmdb(ai_recommendations)
    candidate_list.sort(key=lambda x: x['has_poster'], reverse=True)

    # 4. Encontrar el primer candidato válido y enriquecerlo
    selected_movie_payload = None
    for candidate in candidate_list:
        if not candidate['has_poster'] or not candidate.get('trailer_url'):
            continue
            
        logging.info(f"\n✨ Intentando seleccionar y enriquecer a '{candidate['title']}'...")
        enriched_data = enrich_movie(candidate['tmdb_id'])
        
        if enriched_data and enriched_data.get("poster_principal"):
            selected_movie_payload = {
                **enriched_data,
                "trailer_url": candidate['trailer_url'],
                "seleccion_generada": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            }
            logging.info(f"🎉 ¡Película seleccionada: {enriched_data['titulo']}!")
            break
        else:
            logging.warning(f"  -> Descartado. El enriquecimiento falló o no devolvió póster.")

    # 5. Guardar el archivo de salida y devolver el payload
    if selected_movie_payload:
        final_payload = {
            "tmdb_id": selected_movie_payload["id"], "titulo": selected_movie_payload["titulo"],
            "fecha_estreno": selected_movie_payload["fecha_estreno"], "popularity": selected_movie_payload["popularity"],
            "generos": selected_movie_payload["generos"], "sinopsis": selected_movie_payload["sinopsis"],
            "poster_principal": selected_movie_payload["poster_principal"], "posters": selected_movie_payload["posters"],
            "backdrops": selected_movie_payload["backdrops"], "trailer_url": selected_movie_payload["trailer_url"],
            "reparto_top": selected_movie_payload.get("reparto_top"),
            "seleccion_generada": selected_movie_payload["seleccion_generada"]
        }
        NEXT_FILE.write_text(json.dumps(final_payload, ensure_ascii=False, indent=2), encoding="utf-8")
        logging.info(f"Candidato guardado en: {NEXT_FILE}")
        return final_payload # <-- Devuelve el resultado
    else:
        logging.error("NO SE ENCONTRARON CANDIDATOS VÁLIDOS.")
        return None # <-- Devuelve None si falla

# El bloque __main__ ahora solo llama a la nueva función
if __name__ == "__main__":
    print("--- Ejecutando 'find.py' en modo de prueba ---")
    result = find_and_select_next()
    if result:
        print("\n" + "="*60)
        print("      ✅ PRUEBA COMPLETADA CON ÉXITO")
        print("="*60)
        print(f" Título: {result['titulo']}")
        print(f" Fichero: {NEXT_FILE}")
    else:
        print("\n" + "="*60)
        print("      🛑 PRUEBA FALLIDA: NO SE SELECCIONÓ CANDIDATO")
        print("="*60)