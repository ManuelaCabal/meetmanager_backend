import os
import requests
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv("TELEGRAM_TOKEN")

if not TOKEN:
    print("❌ Error: No hay token en el .env")
else:
    # Esta instrucción borra cualquier conflicto en los servidores de Telegram
    url = f"https://api.telegram.org/bot{TOKEN}/deleteWebhook?drop_pending_updates=True"
    
    try:
        res = requests.get(url)
        print(f"📡 Resultado de la limpieza: {res.text}")
        print("✅ ¡Conexión reiniciada! Ahora intenta ejecutar main.py")
    except Exception as e:
        print(f"Error: {e}")