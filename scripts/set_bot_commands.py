#!/usr/bin/env python3
"""Registrar comandos del bot Transmission en Telegram (setMyCommands).

Forma de uso:
  - Exporta `TELEGRAM_TRANSMISSION_BOT` o define el token en `.env` o `config/telegram.conf`.
  - Ejecuta: `python3 scripts/set_bot_commands.py`
"""

import os
import sys
import json
import logging
from logging.handlers import RotatingFileHandler
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

# Logging
LOG_DIR = os.path.join(os.path.dirname(__file__), 'logs')
os.makedirs(LOG_DIR, exist_ok=True)
LOG_FILE = os.path.join(LOG_DIR, 'set_bot_commands.log')
logger = logging.getLogger('set_bot_commands')
if not logger.handlers:
    logger.setLevel(logging.INFO)
    fh = RotatingFileHandler(LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=3, encoding='utf-8')
    fh.setFormatter(logging.Formatter('%(asctime)s %(levelname)s: %(message)s'))
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(logging.Formatter('%(asctime)s %(levelname)s: %(message)s'))
    logger.addHandler(fh)
    logger.addHandler(sh)


def load_token():
    # Priorizar variable de entorno específica del bot de Transmission
    token = os.environ.get("TELEGRAM_TRANSMISSION_BOT")
    if token:
        return token.strip()

    # Buscar en .env en la raíz del repo (priorizando TELEGRAM_TRANSMISSION_BOT)
    script_dir = os.path.dirname(__file__)
    env_path = os.path.normpath(os.path.join(script_dir, '..', '.env'))
    if os.path.exists(env_path):
        env_map = {}
        with open(env_path, 'r') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                if '=' in line:
                    k, v = line.split('=', 1)
                    env_map[k.strip()] = v.strip().strip('"').strip("'")
        if env_map.get('TELEGRAM_TRANSMISSION_BOT'):
            return env_map['TELEGRAM_TRANSMISSION_BOT']
        if env_map.get('TELEGRAM_BOT_TOKEN'):
            return env_map['TELEGRAM_BOT_TOKEN']

    # Buscar en config/telegram.conf (priorizando TELEGRAM_TRANSMISSION_BOT)
    conf_path = os.path.normpath(os.path.join(script_dir, '..', 'config', 'telegram.conf'))
    if os.path.exists(conf_path):
        conf_map = {}
        with open(conf_path, 'r') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                if '=' in line:
                    k, v = line.split('=', 1)
                    conf_map[k.strip()] = v.strip().strip('"').strip("'")
        if conf_map.get('TELEGRAM_TRANSMISSION_BOT'):
            return conf_map['TELEGRAM_TRANSMISSION_BOT']
        if conf_map.get('TELEGRAM_BOT_TOKEN'):
            return conf_map['TELEGRAM_BOT_TOKEN']

    return None


COMMANDS = [
    {"command":"list","description":"Listar todos los torrents; opcional: filtrar por tracker."},
    {"command":"li","description":"Alias: listar torrents (igual que list)."},
    {"command":"head","description":"Listar los primeros N torrents (por defecto 5)."},
    {"command":"he","description":"Alias: head (primeros N)."},
    {"command":"tail","description":"Listar los últimos N torrents (por defecto 5)."},
    {"command":"ta","description":"Alias: tail (últimos N)."},
    {"command":"down","description":"Mostrar torrents en descarga o en cola."},
    {"command":"dl","description":"Alias: down."},
    {"command":"seeding","description":"Mostrar torrents en seed o en cola de seed."},
    {"command":"sd","description":"Alias: seeding."},
    {"command":"paused","description":"Mostrar torrents pausados."},
    {"command":"pa","description":"Alias: paused."},
    {"command":"checking","description":"Mostrar torrents en verificación."},
    {"command":"ch","description":"Alias: checking."},
    {"command":"active","description":"Mostrar torrents activos (subiendo/descargando)."},
    {"command":"ac","description":"Alias: active."},
    {"command":"errors","description":"Mostrar torrents con errores y su mensaje."},
    {"command":"er","description":"Alias: errors."},
    {"command":"sort","description":"Cambiar orden de listado; sin argumentos muestra ayuda."},
    {"command":"so","description":"Alias: sort."},
    {"command":"trackers","description":"Listar trackers y número de torrents por tracker."},
    {"command":"tr","description":"Alias: trackers."},
    {"command":"add","description":"Añadir URL(s) o magnet(s); acepta .torrent."},
    {"command":"ad","description":"Alias: add."},
    {"command":"search","description":"Buscar torrents por nombre."},
    {"command":"se","description":"Alias: search."},
    {"command":"latest","description":"Listar N torrents más recientes (por defecto 5)."},
    {"command":"la","description":"Alias: latest."},
    {"command":"info","description":"Mostrar info detallada de uno o varios IDs."},
    {"command":"in","description":"Alias: info."},
    {"command":"stop","description":"Parar uno o varios torrents (o all)."},
    {"command":"sp","description":"Alias: stop."},
    {"command":"start","description":"Iniciar uno o varios torrents (o all)."},
    {"command":"st","description":"Alias: start."},
    {"command":"check","description":"Verificar uno o varios torrents (o all)."},
    {"command":"ck","description":"Alias: check."},
    {"command":"del","description":"Eliminar uno o varios torrents."},
    {"command":"deldata","description":"Eliminar torrents y sus datos."},
    {"command":"stats","description":"Mostrar estadísticas de Transmission."},
    {"command":"sa","description":"Alias: stats."},
    {"command":"speed","description":"Mostrar velocidades de subida/descarga."},
    {"command":"ss","description":"Alias: speed."},
    {"command":"count","description":"Mostrar conteo de torrents por estado."},
    {"command":"co","description":"Alias: count."},
    {"command":"help","description":"Mostrar esta ayuda."},
    {"command":"version","description":"Mostrar versiones."}
]


def set_commands(token, commands, language_code=None, scope=None):
    url = f"https://api.telegram.org/bot{token}/setMyCommands"
    body = {"commands": commands}
    if language_code:
        body["language_code"] = language_code
    if scope is not None:
        body["scope"] = scope
    payload = json.dumps(body).encode('utf-8')
    req = Request(url, data=payload, headers={"Content-Type": "application/json"})
    try:
        with urlopen(req, timeout=15) as resp:
            resp_data = resp.read().decode('utf-8')
            j = json.loads(resp_data)
            logger.info('API response: %s', json.dumps(j, ensure_ascii=False))
            if j.get('ok'):
                logger.info('Comandos registrados correctamente (language_code=%s, scope=%s)', language_code, scope)
                return 0
            else:
                logger.error('API returned not ok: %s', j)
                return 2
    except HTTPError as e:
        logger.exception('HTTP error: %s %s', e.code, e.reason)
        return 3
    except URLError as e:
        logger.exception('URL error: %s', e.reason)
        return 4
    except Exception as e:
        logger.exception('Error inesperado: %s', e)
        return 5


def register_variants(token, commands):
    # Intentar registrar sin language_code (global/default) y con 'es'
    rc_global = set_commands(token, commands, language_code=None)
    rc_es = set_commands(token, commands, language_code='es')

    # Intentar registrar para el chat específico (si hay TELEGRAM_CHAT_ID)
    chat_id = os.environ.get('TELEGRAM_CHAT_ID')
    rc_chat = 1
    rc_chat_es = 1
    if chat_id:
        try:
            # tratar chat_id como número si es posible
            chat_val = int(chat_id)
        except Exception:
            chat_val = chat_id
        scope = {"type": "chat", "chat_id": chat_val}
        rc_chat = set_commands(token, commands, language_code=None, scope=scope)
        rc_chat_es = set_commands(token, commands, language_code='es', scope=scope)

    # Si alguno fue OK (0), consideramos éxito
    return 0 if (rc_global == 0 or rc_es == 0 or rc_chat == 0 or rc_chat_es == 0) else 1


def main():
    token = load_token()
    if not token:
        logger.error('No se encontró token TELEGRAM_TRANSMISSION_BOT. Exporta la variable o revisa .env/config/telegram.conf')
        return 1

    # Intentar registrar variantes (global, locale 'es', y scope chat si existe)
    res = register_variants(token, COMMANDS)
    if res == 0:
        logger.info('Registro de comandos finalizado con éxito')
    else:
        logger.warning('Registro de comandos finalizó sin éxito completo (ver logs)')
    return res


if __name__ == '__main__':
    sys.exit(main())
