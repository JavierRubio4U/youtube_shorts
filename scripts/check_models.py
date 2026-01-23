# scripts/check_models.py
from google import genai
import sys
from pathlib import Path

# Añadir directorio actual al path para importar movie_utils
sys.path.append(str(Path(__file__).resolve().parent))
from movie_utils import load_config

config = load_config()
if config:
    client = genai.Client(api_key=config["GEMINI_API_KEY"])
    print("\n--- MODELOS DISPONIBLES PARA TI ---")
    for m in client.models.list():
        methods = getattr(m, 'supported_generation_methods', [])
        if (methods and 'generateContent' in methods) or ('gemini' in m.name):
            print(f"ID: {m.name}")
else:
    print("No se pudo cargar la config.")