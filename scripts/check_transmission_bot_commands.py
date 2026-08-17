#!/usr/bin/env python3
"""Comprueba los comandos registrados en el bot de Transmission y lista los faltantes."""
import os
import sys
import json
import logging
from logging.handlers import RotatingFileHandler
from urllib.request import urlopen, Request

# Logging
LOG_DIR = os.path.join(os.path.dirname(__file__), 'logs')
os.makedirs(LOG_DIR, exist_ok=True)
LOG_FILE = os.path.join(LOG_DIR, 'check_transmission_bot_commands.log')
logger = logging.getLogger('check_transmission_bot_commands')
if not logger.handlers:
    logger.setLevel(logging.INFO)
    fh = RotatingFileHandler(LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=3, encoding='utf-8')
    fh.setFormatter(logging.Formatter('%(asctime)s %(levelname)s: %(message)s'))
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(logging.Formatter('%(asctime)s %(levelname)s: %(message)s'))
    logger.addHandler(fh)
    logger.addHandler(sh)


def load_token():
    # Preferir variable de entorno
    token = os.environ.get("TELEGRAM_TRANSMISSION_BOT")
    if token:
        return token
    # Intentar leer .env
    env_path = os.path.join(os.path.dirname(__file__), '..', '.env')
    env_path = os.path.normpath(env_path)
    if os.path.exists(env_path):
        with open(env_path, 'r') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                if line.startswith('TELEGRAM_TRANSMISSION_BOT='):
                    val = line.split('=', 1)[1].strip()
                    if val.startswith('"') and val.endswith('"'):
                        val = val[1:-1]
                    return val
    # Intentar config/telegram.conf
    conf = os.path.join('config', 'telegram.conf')
    if os.path.exists(conf):
        with open(conf, 'r') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                if line.startswith('TELEGRAM_TRANSMISSION_BOT='):
                    val = line.split('=', 1)[1].strip()
                    if val.startswith('"') and val.endswith('"'):
                        val = val[1:-1]
                    return val
                if line.startswith('TELEGRAM_BOT_TOKEN='):
                    val = line.split('=', 1)[1].strip()
                    if val.startswith('"') and val.endswith('"'):
                        val = val[1:-1]
                    return val
    return None


def get_commands(token):
    url = f'https://api.telegram.org/bot{token}/getMyCommands'
    req = Request(url)
    try:
        with urlopen(req, timeout=15) as resp:
            data = resp.read()
            j = json.loads(data.decode('utf-8'))
            logger.info('getMyCommands response: %s', json.dumps(j, ensure_ascii=False))
            if not j.get('ok'):
                logger.error('API returned not ok: %s', j)
                sys.exit(1)
            return [c.get('command') for c in j.get('result', [])]
    except Exception as e:
        logger.exception('Error al llamar a la API: %s', e)
        sys.exit(1)


def main():
    token = load_token()
    if not token:
        print('No se encontró token TELEGRAM_TRANSMISSION_BOT en entorno, .env o config/telegram.conf')
        sys.exit(1)

    existing = get_commands(token)

    desired = [
        "list","li","head","he","tail","ta","down","dl","seeding","sd",
        "paused","pa","checking","ch","active","ac","errors","er","sort","so",
        "trackers","tr","add","ad","search","se","latest","la","info","in",
        "stop","sp","start","st","check","ck","del","deldata","stats","sa",
        "speed","ss","count","co","help","version"
    ]

    missing = [d for d in desired if d not in existing]

    logger.info('Registered: %s', existing)
    print('Registered:')
    for c in existing:
        print(' -', c)
    print('\n---')
    if missing:
        logger.warning('Missing commands: %s', ', '.join(missing))
        print('Missing:')
        print(', '.join(missing))
        sys.exit(2)
    else:
        logger.info('All commands registered')
        print('All registered')
        sys.exit(0)


if __name__ == '__main__':
    main()
