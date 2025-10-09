#!/usr/bin/env python3
"""
MediaJelly Telegram Notifier Python - Sistema de notificaciones optimizado
"""

import os
import sys
import json
import time
import requests
from pathlib import Path
from datetime import datetime
from typing import List, Optional, Dict
import logging

from mediajelly_emoji import EmojiGenerator

# Constantes
REQUEST_TIMEOUT = 30  # Timeout para requests HTTP (segundos)

class TelegramNotifier:
    """Notificador de Telegram optimizado"""
    
    def __init__(self):
        # Detecta el entorno
        self.is_container = Path("/mediajelly").exists()
        self.base_dir = Path("/mediajelly" if self.is_container else "/home/tafurc/mediaJelly")
        self.scripts_dir = self.base_dir / "scripts"
        self.config_file = self.base_dir / "config" / "telegram.conf"
        self.state_file = self.scripts_dir / "tmp" / "last_notification_state"
        
        # Crea directorio tmp
        (self.scripts_dir / "tmp").mkdir(parents=True, exist_ok=True)
        
        # Carga la configuración
        self.config = self.load_config()
        
    # Configura logging
        logging.basicConfig(level=logging.INFO)
        self.logger = logging.getLogger(__name__)
        
    def load_config(self) -> Dict[str, str]:
        """Cargar configuración de Telegram desde archivo bash"""
        config = {}
        if self.config_file.exists():
            with open(self.config_file, 'r') as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#') and '=' in line:
                        key, value = line.split('=', 1)
                        # Remueve comillas
                        value = value.strip('"\'')
                        config[key] = value
        
        # Verifica configuración requerida
        required = ['TELEGRAM_BOT_TOKEN', 'TELEGRAM_CHAT_ID']
        for req in required:
            if req not in config:
                self.logger.error(f"Configuración faltante: {req}")
                
        return config
    
    def send_telegram_message(self, message: str, parse_mode: Optional[str] = None) -> bool:
        """Enviar mensaje a Telegram"""
        if 'TELEGRAM_BOT_TOKEN' not in self.config or 'TELEGRAM_CHAT_ID' not in self.config:
            self.logger.error("Credenciales de Telegram no configuradas")
            return False
        
        url = f"https://api.telegram.org/bot{self.config['TELEGRAM_BOT_TOKEN']}/sendMessage"
        
        data = {
            'chat_id': self.config['TELEGRAM_CHAT_ID'],
            'text': message
        }
        
        if parse_mode:
            data['parse_mode'] = parse_mode
        
        try:
            response = requests.post(url, data=data, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            
            result = response.json()
            if result.get('ok'):
                return True
            else:
                self.logger.error(f"Error en respuesta de Telegram: {result}")
                return False
                
        except requests.RequestException as e:
            self.logger.error(f"Error enviando mensaje a Telegram: {e}")
            return False
    
    def send_long_message(self, message: str) -> bool:
        """Enviar mensaje largo dividiéndolo en partes si es necesario"""
        max_length = 4096
        chunk_size = 3000
        
        if len(message) <= max_length:
            return self.send_telegram_message(message)
        
        # Divide mensaje en chunks
        start = 0
        part_num = 1
        
        while start < len(message):
            end = start + chunk_size
            if end > len(message):
                end = len(message)
            
            chunk = message[start:end]
            
            # Agrega indicador de parte si hay múltiples
            if len(message) > chunk_size:
                chunk = f"(Parte {part_num}) {chunk}"
            
            if not self.send_telegram_message(chunk):
                return False
            
            start = end
            part_num += 1
            
            # Pausa para evitar rate limits
            if start < len(message):
                time.sleep(0.5)
        
        return True
    
    def _get_compression_summary(self) -> str:
        """Obtener resumen de compresión del log"""
        success_log = self.scripts_dir / "logs" / "compression-success.log"
        if success_log.exists():
            with open(success_log, 'r') as f:
                for line in reversed(f.readlines()):
                    if "RESUMEN:" in line:
                        return f"{EmojiGenerator.stats()} Resumen del procesamiento:\n{line.strip()}\n\n"
        return ""
    
    def _get_error_summary(self) -> str:
        """Obtener resumen de errores"""
        error_tmp = self.scripts_dir / "tmp" / "error_files.tmp"
        if not error_tmp.exists():
            return ""
        
        with open(error_tmp, 'r') as f:
            errors = [line.strip() for line in f if line.strip()]
        
        if not errors:
            return ""
        
        summary = f"{EmojiGenerator.error()} Errores encontrados: {len(errors)}\n"
        # Muestra hasta 5 errores
        for error in errors[:5]:
            summary += f"• {error}\n"
        
        if len(errors) > 5:
            summary += f"... y {len(errors) - 5} más\n"
        
        return summary + "\n"
    
    def _get_no_spanish_summary(self) -> str:
        """Obtener resumen de archivos sin español"""
        no_spanish_tmp = self.scripts_dir / "tmp" / "no_spanish_files.tmp"
        if not no_spanish_tmp.exists():
            return ""
        
        with open(no_spanish_tmp, 'r') as f:
            no_spanish = [line.strip() for line in f if line.strip()]
        
        if not no_spanish:
            return ""
        
        summary = f"{EmojiGenerator.earth()} Archivos sin español detectados: {len(no_spanish)}\n\n"
        # Muestra hasta 10 archivos
        for file_name in no_spanish[:10]:
            summary += f"• {file_name}\n"
        
        if len(no_spanish) > 10:
            summary += f"... y {len(no_spanish) - 10} más\n"
        
        return summary

    def create_processing_summary(self) -> str:
        """Crear resumen de procesamiento usando archivos temporales"""
        summary = ""
        
        # Resumen del log de compresión
        summary += self._get_compression_summary()
        
        # Errores de la ejecución actual
        summary += self._get_error_summary()
        
        # Archivos sin español de la ejecución actual
        summary += self._get_no_spanish_summary()
        
        return summary
    
    def has_new_processing(self) -> bool:
        """Verificar si hubo procesamiento nuevo"""
        success_log = self.scripts_dir / "logs" / "compression-success.log"
        pending_file = self.scripts_dir / "pending-compression.txt"
        completed_file = self.scripts_dir / "completed.txt"
        
        # Estado actual
        current_pending = 0
        current_completed = 0
        current_last_log = ""
        
        if pending_file.exists():
            with open(pending_file, 'r') as f:
                current_pending = len([line for line in f if line.strip()])
        
        if completed_file.exists():
            with open(completed_file, 'r') as f:
                current_completed = len([line for line in f if line.strip()])
        
        if success_log.exists():
            with open(success_log, 'r') as f:
                lines = f.readlines()
                if lines:
                    current_last_log = lines[-1].strip()
        
        # Estado anterior
        previous_state = {'pending': 0, 'completed': 0, 'last_log': ''}
        if self.state_file.exists():
            try:
                with open(self.state_file, 'r') as f:
                    lines = f.readlines()
                    if len(lines) >= 3:
                        previous_state['pending'] = int(lines[0].strip())
                        previous_state['completed'] = int(lines[1].strip())
                        previous_state['last_log'] = lines[2].strip()
            except (ValueError, IndexError):
                pass
        
        # Guarda estado actual
        with open(self.state_file, 'w') as f:
            f.write(f"{current_pending}\n")
            f.write(f"{current_completed}\n")
            f.write(f"{current_last_log}\n")
        
        # Determina si hubo cambios
        has_changes = (
            current_completed != previous_state['completed'] or
            current_last_log != previous_state['last_log']
        )
        
        return has_changes
    
    def notify_scan_result(self, status: str, files_found: int, files_new: int, 
                          files_processed: int, files_compressed: int, 
                          files_renamed: int, files_skipped: int) -> bool:
        """Notificar resultado del escaneo"""
        
        # Verifica si hubo procesamiento nuevo
        if not self.has_new_processing():
            # No hubo procesamiento nuevo
            message = f"{EmojiGenerator.info()} MediaJelly - Escaneo Completado\n\n"
            message += f"{EmojiGenerator.folder()} Archivos encontrados: {files_found}\n"
            message += f"{EmojiGenerator.new()} Archivos nuevos: {files_new}\n\n"
            message += f"{EmojiGenerator.info()} No se encontraron archivos nuevos para procesar\n"
            message += f"{EmojiGenerator.success()} El sistema está al día\n\n"
            message += f"{EmojiGenerator.time()} Fecha: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            
            return self.send_long_message(message)
        
        # Hubo procesamiento nuevo
        emoji = EmojiGenerator.success() if status == "success" else EmojiGenerator.error()
        title = "MediaJelly - Procesamiento Completado" if status == "success" else "MediaJelly - Error en Procesamiento"
        
        message = f"{emoji} {title}\n\n"
        message += f"{EmojiGenerator.folder()} Archivos encontrados: {files_found}\n"
        message += f"{EmojiGenerator.new()} Archivos nuevos: {files_new}\n"
        
        if files_processed > 0:
            message += f"{EmojiGenerator.gear()} Archivos procesados: {files_processed}\n"
            message += f"{EmojiGenerator.compression()} Comprimidos: {files_compressed}\n"
            message += f"{EmojiGenerator.memo()} Renombrados: {files_renamed}\n"
            message += f"{EmojiGenerator.next_track()} Omitidos: {files_skipped}\n"
        
        message += f"\n{EmojiGenerator.time()} Fecha: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        
        # Agrega resumen de errores si existen
        summary = self.create_processing_summary()
        if summary:
            message += summary
        
        return self.send_long_message(message)
    
    def notify_no_pending_files(self, total_files: int, completed_files: int) -> bool:
        """Notificar cuando no hay archivos pendientes"""
        message = f"{EmojiGenerator.info()} MediaJelly - Escaneo Completado\n\n"
        message += f"{EmojiGenerator.folder()} Total de archivos: {total_files}\n"
        message += f"{EmojiGenerator.success()} Archivos completados: {completed_files}\n"
        message += f"{EmojiGenerator.clipboard()} Archivos pendientes: 0\n\n"
        message += f"{EmojiGenerator.info()} No hay archivos pendientes para procesar\n"
        message += f"{EmojiGenerator.party()} ¡Todo está actualizado!\n\n"
        message += f"{EmojiGenerator.time()} Fecha: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        
        return self.send_long_message(message)
    
    def notify_critical_error(self, error_message: str, log_file: Optional[str] = None) -> bool:
        """Notificar errores críticos"""
        message = f"{EmojiGenerator.siren()} MediaJelly - Error Crítico\n\n"
        message += f"{EmojiGenerator.error()} Error: {error_message}\n"
        message += f"{EmojiGenerator.time()} Fecha: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        
        if log_file and Path(log_file).exists():
            message += "\n\n📄 Últimas líneas del log:\n"
            with open(log_file, 'r') as f:
                lines = f.readlines()
                last_lines = lines[-3:] if len(lines) >= 3 else lines
                for line in last_lines:
                    message += f"• {line.strip()}\n"
        
        return self.send_long_message(message)
    
    def send_test_message(self) -> bool:
        """Enviar mensaje de prueba"""
        message = f"{EmojiGenerator.test_tube()} MediaJelly - Prueba de notificación\n\n"
        message += "Si recibes este mensaje, las notificaciones están funcionando correctamente.\n\n"
        message += f"{EmojiGenerator.time()} {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        
        return self.send_long_message(message)
    
    def reset_state(self) -> bool:
        """Resetear estado de notificaciones"""
        if self.state_file.exists():
            self.state_file.unlink()
        self.logger.info("Estado de notificaciones reseteado")
        return True
    
    def notify_completed_cleanup(self, files_checked: int, files_removed: int, files_kept: int) -> bool:
        """Notificar limpieza del archivo completed.txt"""
        message = f"{EmojiGenerator.cleanup()} MediaJelly - Limpieza de archivos completados\n\n"
        message += f"{EmojiGenerator.stats()} Archivos verificados: {files_checked}\n"
        message += f"{EmojiGenerator.wastebasket()} Archivos eliminados: {files_removed}\n"
        message += f"{EmojiGenerator.success()} Archivos mantenidos: {files_kept}\n\n"
        message += f"{EmojiGenerator.time()} {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        
        return self.send_long_message(message)

def main():
    """Función principal"""
    if len(sys.argv) < 2:
        print("Uso: mediajelly_notifier.py {scan_result|no_pending|critical_error|test|reset_state} [argumentos...]")
        sys.exit(1)
    
    notifier = TelegramNotifier()
    command = sys.argv[1]
    
    if command == "scan_result" and len(sys.argv) >= 9:
        status = sys.argv[2]
        files_found = int(sys.argv[3])
        files_new = int(sys.argv[4])
        files_processed = int(sys.argv[5])
        files_compressed = int(sys.argv[6])
        files_renamed = int(sys.argv[7])
        files_skipped = int(sys.argv[8])
        
        success = notifier.notify_scan_result(
            status, files_found, files_new, files_processed,
            files_compressed, files_renamed, files_skipped
        )
        
    elif command == "no_pending" and len(sys.argv) >= 4:
        total_files = int(sys.argv[2])
        completed_files = int(sys.argv[3])
        success = notifier.notify_no_pending_files(total_files, completed_files)
        
    elif command == "critical_error" and len(sys.argv) >= 3:
        error_message = sys.argv[2]
        log_file = sys.argv[3] if len(sys.argv) > 3 else None
        success = notifier.notify_critical_error(error_message, log_file)
        
    elif command == "test":
        success = notifier.send_test_message()
        
    elif command == "reset_state":
        success = notifier.reset_state()
        
    elif command == "completed_cleanup" and len(sys.argv) >= 5:
        files_checked = int(sys.argv[2])
        files_removed = int(sys.argv[3])
        files_kept = int(sys.argv[4])
        success = notifier.notify_completed_cleanup(files_checked, files_removed, files_kept)
        
    else:
        print("Comando no válido o argumentos insuficientes")
        sys.exit(1)
    
    sys.exit(0 if success else 1)

if __name__ == "__main__":
    main()