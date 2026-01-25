# --- Primer bloque de codigo ---
import os
import logging
import requests
import json
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, filters


# --- Segundo bloque de codigo ---
# --- CONFIGURACIÓN INICIAL ---
load_dotenv() # Cargar variables del .env
TOKEN = os.getenv('TELEGRAM_TOKEN')
OLLAMA_URL = os.getenv('OLLAMA_URL')
MODEL = os.getenv('MODEL_NAME')


# --- Tercer bloque de codigo ---
# Configuración de Logs (Hardening)
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)


# --- Cuarto bloque de codigo ---
# --- LÓGICA DE CONEXIÓN CON OLLAMA (BACKEND IA) ---
def consultar_ollama(prompt, system_prompt=""):
    """
    Envía una petición POST al servidor local de Ollama.
    Maneja el JSON request y response.
    """
    headers = {'Content-Type': 'application/json'}
    


    # Configuración Avanzada del Modelo (Payload)
    payload = {
        "model": MODEL,
        "prompt": prompt,
        "system": system_prompt, # Personalidad del bot
        "stream": False,         # False para recibir todo el texto de una vez
        "options": {
            "temperature": 0.3,  # Baja creatividad para ser más preciso en agendas
            "num_ctx": 2048      # Ventana de contexto
        }
    }

    try:
        response = requests.post(OLLAMA_URL, headers=headers, data=json.dumps(payload), timeout=60)
        response.raise_for_status() # Lanza error si no es 200 OK
        
        # Parseo de la respuesta JSON de Ollama
        respuesta_json = response.json()
        return respuesta_json.get('response', 'Error: No se recibió texto del modelo.')

    except requests.exceptions.ConnectionError:
        logger.error("No se pudo conectar con Ollama. ¿Está corriendo 'ollama serve'?")
        return "⚠️ Error crítico: No puedo conectar con mi cerebro local (Ollama caído)."
    except requests.exceptions.Timeout:
        logger.error("Ollama tardó demasiado en responder.")
        return "⚠️ Error: El modelo está tardando demasiado. Intenta con un texto más corto."
    except Exception as e:
        logger.error(f"Error desconocido: {e}")
        return "⚠️ Ocurrió un error interno al procesar tu solicitud."


# --- COMANDOS DEL BOT (HANDLERS) ---
# --- Quinto bloque de codigo ---
# 1. Bienvenida y Manual (/start y /help)
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    usuario = update.effective_user.first_name
    await update.message.reply_text(
        f"Hola {usuario}! Soy MeetManager 🤖.\n"
        "Estoy aquí para ayudarte a organizar tus reuniones y redactar correos.\n"
        "Usa /help para ver qué puedo hacer."
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    manual = """
    **Manual de Usuario - MeetManager**
    
    **Comandos Básicos:**
    /start - Iniciar el bot.
    /sobre - Información del proyecto.
    
    **Herramientas (Sin parámetros):**
    /estado - Verifica si el servidor de IA está activo.
    /tips - Dame un consejo rápido para reuniones eficientes.
    
    **Acciones (Con parámetros):**
    /resumir [texto] - Resume un texto largo.
    /agenda [tema] - Crea una agenda para una reunión sobre el tema.
    /email [idea] - Redacta un correo formal basado en tu idea.
    
    💡 *También puedes simplemente escribirme y charlaré contigo.*
    """
    await update.message.reply_text(manual, parse_mode='Markdown')


# --- Sexto bloque de codigo ---
# 2. Comandos SIN parámetros (Adicionales)
async def sobre(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Proyecto: MeetManager v1.0\nArquitectura: Telegram API + Python Middleware + Ollama (Mistral 7B) en Apple Silicon.")

async def check_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Verifica conexión real
    try:
        requests.get(OLLAMA_URL.replace("/api/generate", "")) # Ping a la raíz
        await update.message.reply_text("✅ Estado: ONLINE. El motor de IA está listo.")
    except:
        await update.message.reply_text("❌ Estado: OFFLINE. Revisa tu terminal.")

async def tips_reunion(update: Update, context: ContextTypes.DEFAULT_TYPE):
    prompt = "Dame un solo consejo breve y profesional para tener reuniones efectivas."
    respuesta = consultar_ollama(prompt, system_prompt="Eres un experto en productividad.")
    await update.message.reply_text(f"💡 Consejo: {respuesta}")


# --- Septimo bloque de codigo ---
# 3. Comandos CON parámetros
async def resumir(update: Update, context: ContextTypes.DEFAULT_TYPE):
    texto_a_resumir = ' '.join(context.args)
    if not texto_a_resumir:
        await update.message.reply_text("❌ Debes escribir el texto después del comando. Ej: /resumir Texto largo...")
        return
    
    await update.message.reply_text("⏳ Leyendo y resumiendo... espera un momento.")
    respuesta = consultar_ollama(f"Resume esto brevemente: {texto_a_resumir}")
    await update.message.reply_text(respuesta)

async def crear_agenda(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tema = ' '.join(context.args)
    if not tema:
        await update.message.reply_text("❌ Indica el tema. Ej: /agenda Lanzamiento de producto")
        return

    prompt = f"Crea una agenda de reunión estructurada con tiempos para el tema: {tema}"
    await update.message.reply_text("📅 Creando agenda...")
    respuesta = consultar_ollama(prompt, system_prompt="Eres un asistente ejecutivo experto.")
    await update.message.reply_text(respuesta)

async def redactar_email(update: Update, context: ContextTypes.DEFAULT_TYPE):
    idea = ' '.join(context.args)
    if not idea:
        await update.message.reply_text("❌ Faltan detalles. Ej: /email Solicitar presupuesto a proveedor")
        return

    prompt = f"Redacta un correo formal y profesional sobre: {idea}. Incluye Asunto."
    await update.message.reply_text("✉️ Redactando borrador...")
    respuesta = consultar_ollama(prompt)
    await update.message.reply_text(respuesta)


# --- Octavo bloque de codigo ---
# 4. Chat General (Manejo de mensajes de texto sueltos)
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    texto_usuario = update.message.text
    # System Prompt Global: Define la personalidad
    system_role = "Eres MeetManager, un asistente útil y profesional. Responde de forma concisa."
    
    # Feedback visual al usuario
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action='typing')
    
    respuesta = consultar_ollama(texto_usuario, system_prompt=system_role)
    await update.message.reply_text(respuesta)



# --- EJECUCIÓN PRINCIPAL ---
if __name__ == '__main__':
    if not TOKEN:
        print("Error: No se encontró el token en el archivo .env")
        exit()

    application = ApplicationBuilder().token(TOKEN).build()

    # Registro de comandos
    application.add_handler(CommandHandler('start', start))
    application.add_handler(CommandHandler('help', help_command))
    application.add_handler(CommandHandler('ayuda', help_command))
    application.add_handler(CommandHandler('sobre', sobre))
    application.add_handler(CommandHandler('estado', check_status))
    application.add_handler(CommandHandler('tips', tips_reunion))
    application.add_handler(CommandHandler('resumir', resumir))
    application.add_handler(CommandHandler('agenda', crear_agenda))
    application.add_handler(CommandHandler('email', redactar_email))
    application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))

    print("🤖 MeetManager Backend iniciado... Presiona Ctrl+C para detener.")

    application.run_polling()
