# scripts/check_models.py
from google import genai
import sys
from pathlib import Path

# Añadir directorio actual al path para importar movie_utils
sys.path.append(str(Path(__file__).resolve().parent))
from movie_utils import load_config
from gemini_config import GEMINI_MODEL

config = load_config()
if config:
    client = genai.Client(api_key=config["GEMINI_API_KEY"])
    print("\n--- MODELOS DISPONIBLES PARA TI ---")
    found_configured = False
    for m in client.models.list():
        methods = getattr(m, 'supported_generation_methods', [])
        if (methods and 'generateContent' in methods) or ('gemini' in m.name):
            print(f"ID: {m.name}")
            if GEMINI_MODEL in m.name:
                found_configured = True
    
    if found_configured:
        print(f"\n✅ El modelo configurado '{GEMINI_MODEL}' está disponible y listo para usar.")
    else:
        print(f"\n❌ ADVERTENCIA: El modelo '{GEMINI_MODEL}' NO aparece en tu lista. Verifica los permisos en Google AI Studio.")
else:
    print("No se pudo cargar la config.")