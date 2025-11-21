# scripts/check_models.py
import google.generativeai as genai
import sys
from pathlib import Path

# Añadir directorio actual al path para importar movie_utils
sys.path.append(str(Path(__file__).resolve().parent))
from movie_utils import load_config

config = load_config()
if config:
    genai.configure(api_key=config["GEMINI_API_KEY"])
    print("\n--- MODELOS DISPONIBLES PARA TI ---")
    for m in genai.list_models():
        if 'generateContent' in m.supported_generation_methods:
            print(f"ID: {m.name}")
else:
    print("No se pudo cargar la config.")