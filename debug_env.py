print("Hola mundo")  # prueba rápida
from dotenv import load_dotenv
import os

load_dotenv()
print("Archivos en carpeta actual:", os.listdir('.'))
print("TOKEN leido:", os.getenv("TELEGRAM_TOKEN"))

from dotenv import load_dotenv
import os

load_dotenv()  # carga las variables del .env
print("Archivos en carpeta actual:", os.listdir('.'))
print("TOKEN leido:", os.getenv("TELEGRAM_TOKEN"))
