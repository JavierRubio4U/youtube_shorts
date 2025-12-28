import json
import os
from pathlib import Path
from google_auth_oauthlib.flow import InstalledAppFlow

# Rutas
ROOT = Path(__file__).resolve().parents[1]
SECRETS_FILE = ROOT / "config" / "client_secret.json"
TOKEN_FILE = ROOT / "output" / "state" / "youtube_token.json"

# Permisos necesarios (Subir videos y Buscar para find.py)
SCOPES = [
    'https://www.googleapis.com/auth/youtube.upload',
    'https://www.googleapis.com/auth/youtube.force-ssl'
]

def main():
    print(f"🔵 Buscando secretos en: {SECRETS_FILE}")
    if not SECRETS_FILE.exists():
        print("❌ ERROR: No encuentro config/client_secret.json")
        return

    # Iniciar el flujo de login (Abre navegador)
    flow = InstalledAppFlow.from_client_secrets_file(str(SECRETS_FILE), SCOPES)
    creds = flow.run_local_server(port=0)

    # Guardar el token nuevo
    token_data = {
        'token': creds.token,
        'refresh_token': creds.refresh_token,
        'token_uri': creds.token_uri,
        'client_id': creds.client_id,
        'client_secret': creds.client_secret,
        'scopes': creds.scopes
    }

    # Asegurar que la carpeta existe
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)

    with open(TOKEN_FILE, 'w') as f:
        json.dump(token_data, f)

    print(f"✅ ¡ÉXITO! Token guardado en: {TOKEN_FILE}")
    print("Ahora puedes ejecutar 'python scripts/publish.py'")

if __name__ == '__main__':
    main()