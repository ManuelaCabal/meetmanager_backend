import os
import logging
import requests
import sqlite3
import json
import locale
import re
import httpx
from datetime import datetime
from dotenv import load_dotenv
import dateparser
from dateparser.search import search_dates  
from telegram import (
    Update, 
    InlineKeyboardButton, 
    InlineKeyboardMarkup, 
    ReplyKeyboardMarkup,  
    KeyboardButton        
)
from telegram.constants import ChatAction
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, MessageHandler, filters, CallbackQueryHandler

HISTORIAL = []
# --- 1. CONFIGURACIÓN E INICIALIZACIÓN ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Intentar poner fechas en español
try:
    locale.setlocale(locale.LC_TIME, 'es_ES.UTF-8')
except locale.Error:
    logger.warning("No se pudo establecer locale a español. Se usará idioma por defecto.")

load_dotenv()
TOKEN = os.getenv("TELEGRAM_TOKEN")
MODEL_NAME = os.getenv("MODEL_NAME", "mistral:7b")
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434/api/generate")

# --- 2. BASE DE DATOS ---
def init_db():
    conn = sqlite3.connect('meetmanager.db')
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS citas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            fecha TEXT,
            hora TEXT,
            asunto TEXT
        )
    ''')
    
    conn.commit()
    conn.close()


def guardar_cita_db(user_id, fecha, hora, asunto):
    conn = sqlite3.connect('meetmanager.db')
    c = conn.cursor()
    # Verifica si ya existe para no duplicar al crear
    c.execute("SELECT * FROM citas WHERE user_id=? AND fecha=? AND hora=?", (user_id, fecha, hora))
    if c.fetchone():
        conn.close()
        return False
    c.execute("INSERT INTO citas (user_id, fecha, hora, asunto) VALUES (?, ?, ?, ?)", (user_id, fecha, hora, asunto))
    conn.commit()
    conn.close()
    return True

def eliminar_cita_db(user_id, fecha):
    conn = sqlite3.connect('meetmanager.db')
    c = conn.cursor()
    # Ejecutamos el borrado real
    c.execute("DELETE FROM citas WHERE user_id=? AND fecha=?", (user_id, fecha))
    # rowcount nos dice cuántas filas se borraron
    borrados = c.rowcount
    conn.commit()
    conn.close()
    return borrados > 0

def obtener_citas_db(user_id):
    conn = sqlite3.connect('meetmanager.db')
    c = conn.cursor()
    c.execute("SELECT fecha, hora, asunto FROM citas WHERE user_id=? ORDER BY fecha, hora", (user_id,))
    data = c.fetchall()
    conn.close()
    return data


def limpiar_todo_db(user_id):
    conn = sqlite3.connect('meetmanager.db')
    c = conn.cursor()
    
    # 1. Borramos las citas del usuario
    c.execute("DELETE FROM citas WHERE user_id=?", (user_id,))
    
    # 2. LÓGICA DE REINICIO DE ID
    # Verificamos si la tabla 'citas' está completamente vacía (sin datos de nadie)
    c.execute("SELECT COUNT(*) FROM citas")
    total_filas = c.fetchone()[0]
    
    if total_filas == 0:
        # Si no queda nada, borramos la memoria del contador para que empiece en 1
        c.execute("DELETE FROM sqlite_sequence WHERE name='citas'")
    
    conn.commit()
    conn.close()

# --- 3. FUNCIONES DE FECHA Y IA ---
def extraer_datos_cita(texto_usuario):
    ahora = datetime.now()
    
    # 1. TRUCO DE MAGIA: Convertir "1 pm" a "13:00" manualmente con Regex
    def convertir_hora(match):
        hora_num = int(match.group(1))
        periodo = match.group(2).lower()
        if periodo == 'pm' and hora_num != 12:
            hora_num += 12
        elif periodo == 'am' and hora_num == 12:
            hora_num = 0
        return f" {hora_num:02d}:00 "

    # Reemplazamos patrones como "1pm", "1 pm", "10am" por "13:00", "10:00"
    texto_procesado = re.sub(r'\b(\d{1,2})\s*(pm|am)\b', convertir_hora, texto_usuario.lower())
    
    # Quitamos conectores molestos
    texto_procesado = texto_procesado.replace(" a la ", " ").replace(" a las ", " ")

    try:
        # Usamos search_dates sobre el texto ya "arreglado"
        resultados = search_dates(
            texto_procesado, 
            languages=['es'], 
            settings={'RELATIVE_BASE': ahora, 'PREFER_DATES_FROM': 'future'}
        )
        
        if resultados:
            # Cogemos la fecha detectada
            fecha_obj = resultados[-1][1]
            fecha_db = fecha_obj.strftime("%Y-%m-%d") # Formato para la base de datos (YYYY-MM-DD)
            hora = fecha_obj.strftime("%H:%M")
            
            # Limpiamos el asunto quitando la fecha encontrada
            texto_encontrado = resultados[-1][0]
            asunto = texto_procesado.replace(texto_encontrado, "")
        else:
            return {} # Retornar diccionario vacío si falla

    except Exception as e:
        logger.error(f"Error extraction: {e}")
        return {}

    # Limpieza final del asunto
    palabras_basura = ["agendar", "cita", "reunion", "reunión", " el ", " la ", " las "]
    for p in palabras_basura:
        asunto = asunto.replace(p, " ")
    
    asunto = " ".join(asunto.split()).strip().capitalize() or "Reunión"

    return {"fecha": fecha_db, "hora": hora, "asunto": asunto}

def consultar_chat_libre(mensaje, system_extra=""):
    dias_semana = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
    meses_year = ["", "enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
    
    fecha_dt = datetime.now()
    fecha_str = f"{dias_semana[fecha_dt.weekday()]}, {fecha_dt.day} de {meses_year[fecha_dt.month]} de {fecha_dt.year}"
    system = (
        f"Usted es MeetManager, el Asistente Ejecutivo Senior de esta empresa. Hoy es {fecha_str}. y cada que le pregunten lo dira: hoy es: {fecha_str} "
        "REGLA DE ORO: Debe hablar EXCLUSIVAMENTE de 'usted'. Está terminantemente prohibido usar 'tú', 'te', 'ayudarte', 'quieres', 'puedes' o cualquier forma para referirse de forma amistosa. "
        "Su forma de hablar tiene que ser extremadamente formal como si hablara con el jefe superior de una empresa multinacional"
        "LIMITACIÓN DE TEMAS: Solo responda sobre productividad, gestión de tiempo, correos y empresas. "
        "TIENE UNA ORTOGRAFIA PERFECTA, responda con mensajes cortos a menos que el usuario le pida textualemente un mensaje largo, debe seguir la instruccion al pie de la letra ."
        "Si el usuario pregunta por temas personales, mascotas o bromas, responda: 'Como su asistente ejecutivo, mi jurisdicción se limita a asuntos profesionales'."
        "IMPORTANTE: No añada líneas, barras bajas (____) ni separadores al final del mensaje. "
        f"{system_extra}"
    )
   
    payload = {"model": MODEL_NAME, "prompt": mensaje, "system": system, "stream": False}

    print(f"⏳ Intentando conectar con: {OLLAMA_URL}") 
    print(f"📦 Modelo solicitado: {MODEL_NAME}")

    try:
        r = requests.post(OLLAMA_URL, json=payload, timeout=None)        
        if r.status_code == 200:
            return r.json().get("response", "Error: Respuesta vacía de Ollama.")
        else:
            print(f"❌ Error HTTP: {r.status_code} - {r.text}") 
            return f"⚠️ Error interno de Ollama: {r.status_code}"

    except Exception as e:
        print(f"❌ ERROR CRÍTICO DE CONEXIÓN: {e}") 
        return "⚠️ No puedo pensar ahora mismo (Mira la consola para ver el error)."


def modificar_cita(id_cita, nueva_descripcion):
    conn = sqlite3.connect('meetmanager.db')
    c = conn.cursor()
    
    c.execute("UPDATE citas SET asunto=? WHERE id=?", (nueva_descripcion, id_cita))
    
    cambios = c.rowcount 
    conn.commit()
    conn.close()
    return cambios > 0

def reprogramar_cita_db(id_cita, fecha_new, hora_new):
    conn = sqlite3.connect('meetmanager.db')
    c = conn.cursor()
    
    # UPDATE sobrescribe fecha y hora en el registro existente.
    # La fecha antigua se borra automáticamente.
    c.execute("UPDATE citas SET fecha=?, hora=? WHERE id=?", (fecha_new, hora_new, id_cita))
    
    cambios = c.rowcount
    conn.commit()
    conn.close()
    
    if cambios > 0:
        return "exito"
    else:
        return "no_encontrado"
    
async def reprogramar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if len(args) < 4:
        await update.message.reply_text(
            "⚠️ **Uso:** `/reprogramar [fecha_vieja] [hora_vieja] [fecha_nueva] [hora_nueva]`\n"
            "Ej: `/reprogramar 2026-01-30 13:00 2026-02-05 16:00`",
            parse_mode='Markdown'
        )
        return

    # Extraemos los 4 datos
    fecha_old, hora_old, fecha_new, hora_new = args[0], args[1], args[2], args[3]
    user_id = update.effective_user.id
    
    resultado = reprogramar_cita_db(user_id, fecha_old, hora_old, fecha_new, hora_new)
    
    if resultado == "exito":
        await update.message.reply_text(
            f"🔄 **¡Cita movida!**\nLa reunión del `{fecha_old}` a las `{hora_old}` ahora es el `{fecha_new}` a las `{hora_new}`.",
            parse_mode='Markdown'
        )
    else:
        await update.message.reply_text(
            f"❌ **No se pudo mover:** No encontré ninguna cita exacta el `{fecha_old}` a las `{hora_old}`.\n"
            "Revise su `/agenda` para copiar la fecha y hora exactamente igual.",
            parse_mode='Markdown'
        )

def buscar_citas_por_fecha_db(user_id, fecha):
    conn = sqlite3.connect('meetmanager.db')
    c = conn.cursor()
    # Seleccionamos hora y asunto solo de esa fecha específica
    c.execute("SELECT hora, asunto FROM citas WHERE user_id=? AND fecha=? ORDER BY hora", (user_id, fecha))
    data = c.fetchall()
    conn.close()
    return data

# --- 4. COMANDOS TELEGRAM ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [KeyboardButton("ℹ️ Ayuda")]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)
    await update.message.reply_text(
        "👋 ¡Hola! Soy MeetManager, su asistente ejecutivo.\n"
        "Estoy aquí para ayudarle a planificar sus reuniones, tareas y correos.\n\n "
        "Pulse help para ver el indice de funciones de este bot.\n "
        "ℹ️ /help - Mostrar ayuda\n\n"
        
        "Tambien puede escribirme libremente y siempre recibirá respuestas profesionales y cercanas.",
        parse_mode='Markdown',
        reply_markup=reply_markup
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # 1. Definimos los botones
    keyboard = [
        [KeyboardButton("📋 Ver Agenda"), KeyboardButton("🟢 Estado del Bot")],
        [KeyboardButton("📅 Agendar Cita"), KeyboardButton("📧 Redactar Email")],
        [KeyboardButton("✏️ Editar Cita"), KeyboardButton("🔄 Reprogramar")],
        [KeyboardButton("🔍 Buscar Cita"), KeyboardButton("❌ Cancelar/Limpiar")]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)
    await update.message.reply_text("Menú desplegado. Seleccione una opción:", reply_markup=reply_markup)

    help_text = (
        "🤖 *Bienvenido a MeetManager*\n"
        "_Su asistente ejecutivo para optimizar la gestión de reuniones._\n\n"
        
        "🎯 *Objetivo Principal*\n"
        "Mi misión es ahorrar tiempo, reducir conflictos de agendas y mejorar la productividad de su equipo "
        "automatizando la planificación y el seguimiento.\n\n"
        
        "🏢 *Aplicación en tu Empresa*\n"
        "• *Startups/Pymes:* Formalizo la gestión de agendas propensa a errores.\n"
        "• *Equipos Remotos:* Centralizo la información para coordinar mejor.\n\n"
        
        "🚀 *Problemas que Resuelvo*\n"
        "✅ *Sin Conflictos:* Verifico disponibilidad antes de agendar (solapamientos).\n"
        "✅ *Puntualidad:* Organizo tu calendario para evitar olvidos.\n"
        "✅ *Eficiencia:* Uso IA para redactar correos y tareas repetitivas.\n\n"
        
        "👇 *ACCIONES RÁPIDAS* 👇\n"
        "Seleccione una opción o escriba directamente (ej: `/agendar Reunión mañana 10am`)\n\n"
        
        "💡 *Comandos Útiles:*\n"
        "📅 /agendar [texto] - Agendar una reunión.\n"
        "📋 /agenda - Ver su agenda.\n"
        "✏️ /editar [fecha] [descripción] - Modificar asunto de una cita.\n"
        "📧 /email [tema] - Redactar un email.\n"
        "🟢 /estado - Verificar el estado del sistema.\n"
        "❌ /cancelar [fecha] - Cancelar una cita.\n"
        "🔄 /reprogramar [fecha antigua] [nueva fecha] [nueva hora] - Reprogramar una cita.\n"
        "🧹 /limpiar - Eliminar todas las citas.\n"
        "🔍 /Buscar cita [fecha] - Obtener información sobre una cita específica."
    )

    await update.message.reply_text(
        help_text, 
        parse_mode="Markdown", 
        reply_markup=reply_markup
    )
     
async def estado(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get("http://localhost:11434/api/tags")

        if r.status_code == 200:
            await update.message.reply_text(
                "🟢 *Estado del sistema*\n\n"
                "✔ Ollama conectado\n"
                "✔ Servicio activo",
                parse_mode="Markdown"
            )
        else:
            await update.message.reply_text(
                "🔴 *Estado del sistema*\n\n"
                "✖ Ollama respondió con error",
                parse_mode="Markdown"
            )
    except Exception:
        await update.message.reply_text(
            "🔴 *Estado del sistema*\n\n"
            "✖ No se pudo conectar con Ollama",
            parse_mode="Markdown"
        )
async def agendar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    texto = " ".join(context.args)
    if not texto:
        await update.message.reply_text("⚠️ Ej: `/agendar Reunión mañana 10am`")
        return

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
    
    # 1. Extraemos los datos
    datos = extraer_datos_cita(texto)
    
    if not datos.get('fecha') or not datos.get('hora'):
        await update.message.reply_text("⚠️ No entendí la fecha. Intenta ser más claro (ej: 'mañana 10am').")
        return
    
    if len(datos['asunto']) > 100:
        await update.message.reply_text(
            f"⛔ **Texto demasiado largo**\n\n"
            f"El asunto tiene `{len(datos['asunto'])}` caracteres. El límite es **100** para mantener la agenda ordenada.\n\n"
            "Por favor, resume el título.",
            parse_mode='Markdown'
        )
        return
    
    # --- 2. VALIDACIÓN ESTRICTA DE FECHA ---
    try:
        # Construimos el objeto fecha para comparar
        fecha_completa_str = f"{datos['fecha']} {datos['hora']}"
        cita_dt = datetime.strptime(fecha_completa_str, "%Y-%m-%d %H:%M")
        ahora = datetime.now()

        # Check: ¿Es pasado?
        if cita_dt < ahora:
            await update.message.reply_text(
                f"⛔ **Fecha inválida:**\n"
                f"Estás intentando agendar para el `{datos['fecha']} {datos['hora']}`, que ya pasó.\n",
                parse_mode='Markdown'
            )
            return # Detenemos si es fecha pasada

        # --- 3. GUARDADO Y FORMATO SOLICITADO ---
        user_id = update.effective_user.id
        exito = guardar_cita_db(user_id, datos['fecha'], datos['hora'], datos['asunto'])
        
        if exito:
            # AQUÍ ESTÁ EL FORMATO EXACTO QUE PEDISTE
            await update.message.reply_text(
                f"✅ **¡Cita agendada con éxito!**\n\n"
                f"📌 **Asunto:** {datos['asunto']}\n"
                f"📅 **Fecha:** {datos['fecha']}\n"
                f"⏰ **Hora:** {datos['hora']}\n\n"
                "Se ha registrado correctamente en su agenda profesional.",
                parse_mode='Markdown'
            )
        else:
            await update.message.reply_text("⛔ Ya existe una cita exacta en ese horario.")

    except ValueError:
        await update.message.reply_text("⚠️ Error interno de fecha. Inténtalo de nuevo.")
async def email(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tema = " ".join(context.args)
    if not tema:
        return
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
    res = consultar_chat_libre(f"Redacta un email profesional sobre: {tema}")
    await update.message.reply_text(res)

async def cancelar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if len(args) != 1:
        await update.message.reply_text("⚠️ Uso correcto: /cancelar [fecha YYYY-MM-DD]")
        return
    
    fecha = args[0]
    user_id = update.effective_user.id
    
    # Llamamos a la función de la base de datos
    eliminado = eliminar_cita_db(user_id, fecha)
    
    if eliminado:
        await update.message.reply_text(f"✅ Se han eliminado las citas del día **{fecha}** correctamente.", parse_mode='Markdown')
    else:
        await update.message.reply_text(f"⚠️ No encontré ninguna cita en la fecha **{fecha}** para borrar.", parse_mode='Markdown')

async def ver_agenda(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    conn = sqlite3.connect('meetmanager.db')
    c = conn.cursor()
    # Traemos el ID explícitamente
    c.execute("SELECT id, fecha, hora, asunto FROM citas WHERE user_id=? ORDER BY fecha, hora", (user_id,))
    citas = c.fetchall()
    conn.close()

    if not citas:
        await update.message.reply_text("📂 Su agenda está vacía.")
        return

    msg = "📋 **Su Agenda:**\n(Use el número ID para editar o reprogramar)\n\n"
    for cid, fecha, hora, asunto in citas:
        # --- TRUCO VISUAL ---
        # Si el asunto tiene más de 40 letras, lo cortamos y ponemos "..."
        # Si es corto, lo dejamos igual.
        if len(asunto) > 40:
            asunto_visual = asunto[:40] + "..."
        else:
            asunto_visual = asunto
            
        msg += f"🆔 `{cid}` | 🔹 {fecha} {hora} | {asunto_visual}\n"
    
    msg += "\n_(Use /cita [fecha] para leer los textos completos)_"
    
    # Telegram tiene un límite de 4096 caracteres por mensaje. 
    # Si la agenda es gigante, cortamos el mensaje para que no de error.
    if len(msg) > 4000:
        msg = msg[:4000] + "\n\n⚠️ (Agenda cortada por exceso de longitud)"
    
    await update.message.reply_text(msg, parse_mode='Markdown')

async def editar_descripcion(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    # Ahora esperamos: [ID] [Nuevo Texto]
    if len(args) < 2:
        await update.message.reply_text(
            "⚠️ **Modo correcto:**\n`/editar [ID] [nuevo texto]`\n\n"
            "Ej: `/editar 5 Reunión con Cliente`\n"
            "(Mire el número ID escribiendo /agenda)",
            parse_mode='Markdown'
        )
        return

    try:
        cita_id = args[0] # El primer argumento es el ID
        nueva_descripcion = " ".join(args[1:]) # El resto es el texto
        
        # Llamamos a la DB pasando el ID
        exito = modificar_cita(cita_id, nueva_descripcion)
        
        if exito:
            await update.message.reply_text(f"✅ Cita **#{cita_id}** actualizada correctamente.", parse_mode='Markdown')
        else:
            await update.message.reply_text("❌ No encontré ese ID. Revise su /agenda.")
            
    except Exception as e:
         await update.message.reply_text("❌ El primer valor debe ser el número ID.")

async def reprogramar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    # Ahora esperamos 3 argumentos: [ID] [Fecha] [Hora]
    if len(args) != 3:
        await update.message.reply_text(
            "⚠️ **Modo correcto:**\n`/reprogramar [ID] [Nueva_Fecha] [Nueva_Hora]`\n\n"
            "Ej: `/reprogramar 5 2026-02-20 16:00`\n"
            "(Mire el número ID escribiendo /agenda)",
            parse_mode='Markdown'
        )
        return

    cita_id = args[0]
    fecha_new = args[1]
    hora_new = args[2]
    
    # Llamamos a la función DB que actualiza (UPDATE) sin duplicar
    resultado = reprogramar_cita_db(cita_id, fecha_new, hora_new)
    
    if resultado == "exito":
        await update.message.reply_text(
            f"🔄 **¡Cita Reprogramada!**\n"
            f"La cita **#{cita_id}** se ha movido al `{fecha_new}` a las `{hora_new}`.",
            parse_mode='Markdown'
        )
    else:
        await update.message.reply_text("❌ No encontré ese número de ID en su agenda.")

async def limpiar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    limpiar_todo_db(user_id)
    await update.message.reply_text("🗑️ **Agenda reseteada:** Todas sus citas han sido eliminadas.", parse_mode='Markdown')

async def cita(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if len(args) != 1:
        await update.message.reply_text("🔎 Uso: `/Buscar cita [fecha YYYY-MM-DD]`\nEjemplo: `/cita 2026-01-30`", parse_mode='Markdown')
        return

    fecha = args[0]
    user_id = update.effective_user.id
    
    # Buscamos en la DB
    resultados = buscar_citas_por_fecha_db(user_id, fecha)
    
    if resultados:
        # Construimos el mensaje con todas las reuniones encontradas
        mensaje = f"📅 **Citas para el {fecha}:**\n\n"
        for hora, asunto in resultados:
            mensaje += f"🔹 `{hora}` - {asunto}\n"
        
        await update.message.reply_text(mensaje, parse_mode='Markdown')
    else:
        await update.message.reply_text(f"📂 No tiene nada programado para el día `{fecha}`.", parse_mode='Markdown')

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.is_bot: return
    
    msg = update.message.text
    msg_lower = msg.lower()

    # --- BOTONES ACTUALIZADOS ---
    if msg == "📋 Ver Agenda":
        await ver_agenda(update, context)
        return

    elif msg == "🟢 Estado del Bot":
        await estado(update, context)
        return

    elif "Ayuda" in msg:
        await help_command(update, context)
        return

    elif msg == "📅 Agendar Cita":
        await update.message.reply_text(
            "📅 **Para agendar:**\nEscribe: `/agendar [asunto] [fecha] [hora]`", 
            parse_mode="Markdown"
        )
        return

    elif msg == "📧 Redactar Email":
        await update.message.reply_text("📧 Escribe: `/email [tema]`", parse_mode="Markdown")
        return

    elif msg == "✏️ Editar Cita":
        # Instrucción corregida para usar ID
        await update.message.reply_text(
            "✏️ **Para editar:**\nUse: `/editar [ID] [Nuevo Texto]`\n(Mire el ID en /agenda)", 
            parse_mode="Markdown"
        )
        return

    elif msg == "🔄 Reprogramar":
        # Instrucción corregida para usar ID
        await update.message.reply_text(
            "🔄 **Para mover:**\nUse: `/reprogramar [ID] [Fecha] [Hora]`\n(Mire el ID en /agenda)", 
            parse_mode="Markdown"
        )
        return

    elif msg == "🔍 Buscar Cita":
        await update.message.reply_text("🔍 Use: `/cita [fecha]`")
        return

    elif msg == "❌ Cancelar/Limpiar":
        await update.message.reply_text("🗑️ Use: `/cancelar [fecha]` o `/limpiar`")
        return

    if msg.startswith("/"): return

    # --- LÓGICA IA ---
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
    contexto_str = "\n".join(HISTORIAL[-4:])
    res = consultar_chat_libre(msg, system_extra=f"\nHistorial previo:\n{contexto_str}")   
    HISTORIAL.append(f"U: {msg}")
    HISTORIAL.append(f"A: {res}")
    if len(HISTORIAL) > 10: HISTORIAL.pop(0)
    await update.message.reply_text(res)



# --- 5. EJECUCIÓN PRINCIPAL ---
if __name__ == '__main__':
    init_db()
    if not TOKEN:
        print("❌ Falta TELEGRAM_TOKEN en .env")
        exit()
    application = ApplicationBuilder().token(TOKEN).build()
    application.add_handler(CommandHandler('start', start))
    application.add_handler(CommandHandler('agendar', agendar))
    application.add_handler(CommandHandler('agenda', ver_agenda))
    application.add_handler(CommandHandler('editar', editar_descripcion))
    application.add_handler(CommandHandler('email', email))
    application.add_handler(CommandHandler("estado", estado))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler('cancelar', cancelar))
    application.add_handler(CommandHandler('reprogramar', reprogramar))
    application.add_handler(CommandHandler('limpiar', limpiar))
    application.add_handler(CommandHandler('cita', cita))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("🤖 MeetManager activo. DB conectada.")
    application.run_polling()
