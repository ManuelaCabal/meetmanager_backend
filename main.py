import os
import logging
import requests
from dotenv import load_dotenv
from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, MessageHandler, filters

# --- 1. CONFIGURACIÓN DE LOGS ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- 2. CARGA DE VARIABLES ---
load_dotenv()
TOKEN = os.getenv("TELEGRAM_TOKEN")
MODEL_NAME = os.getenv("MODEL_NAME", "mistral:7b") 
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434/api/generate")

# --- 3. FUNCIÓN DE CONEXIÓN CON IA ---
def consultar_ollama(prompt_usuario, system_instruction):
    try:
        payload = {
            "model": MODEL_NAME, 
            "prompt": prompt_usuario, 
            "system": system_instruction,
            "stream": False,
            "options": {
                "temperature": 0.4,   # EQUILIBRADO: Creativo pero profesional
                "num_predict": 300,   
                "num_ctx": 4096       
            }
        }
        # Timeout de seguridad
        response = requests.post(OLLAMA_URL, json=payload, timeout=120)
        return response.json().get("response", "Lo siento, tuve un problema interno al pensar la respuesta.")
    except Exception as e:
        logger.error(f"Error conectando con Ollama: {e}")
        return "⚠️ Vaya, parece que no puedo conectar con mi cerebro local (Ollama). Por favor revisa la terminal."

# --- 4. COMANDOS ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    nombre = update.effective_user.first_name
    await update.message.reply_text(
        f"¡Hola, {nombre}! 👋\n\n"
        "Soy MeetManager, tu asistente personal inteligente. "
        "Estoy aquí para ayudarte a organizar tu agenda, redactar correos y hacer tu trabajo más fácil.\n\n"
        "Puedes escribirme como si fuera una persona o usar /help para ver mis herramientas."
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    texto = (
        "🚀 **¿CÓMO PUEDO AYUDARTE?**\n\n"
        "Aquí tienes mis funciones principales:\n\n"
        "🔹 **/estado** → Comprobar mi conexión.\n"
        "🔹 **/tips** → Un consejo rápido de productividad.\n"
        "🔹 **/cita [texto]** → Organizar una reunión.\n"
        "🔹 **/email [texto]** → Redactar un correo profesional.\n"
        "🔹 **/resumir [texto]** → Sintetizar información compleja.\n\n"
        "💬 **Chat Libre:** También puedes preguntarme lo que quieras."
    )
    await update.message.reply_text(texto, parse_mode='Markdown')

async def sobre(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 **Sobre Mí**\n"
        "Soy un asistente potenciado por Inteligencia Artificial (Mistral 7B) ejecutándose localmente en tu equipo.\n"
        "Diseñado para ser privado, rápido y útil."
    )

async def estado(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        requests.get(OLLAMA_URL.replace("/api/generate", ""))
        msg = "🟢 **Sistemas Operativos:** Estoy conectado y listo para trabajar."
    except:
        msg = "🔴 **Error de Conexión:** No detecto el servidor de Ollama. ¿Está encendido?"
    await update.message.reply_text(msg, parse_mode='Markdown')

async def tips(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_chat_action(ChatAction.TYPING)
    prompt = "Dame un consejo útil, motivador y práctico para ser más eficiente en el trabajo hoy."
    res = consultar_ollama(prompt, "Eres un coach de productividad amable y claro.")
    await update.message.reply_text(f"💡 {res}")

async def cita(update: Update, context: ContextTypes.DEFAULT_TYPE):
    texto = " ".join(context.args)
    if not texto:
        await update.message.reply_text("🤔 Necesito que me des los detalles. Prueba