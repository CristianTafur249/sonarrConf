#!/usr/bin/env python3
"""
Script de mantenimiento de permisos para MediaJelly
Se ejecuta periódicamente para asegurar que los permisos estén correctos
"""

import os
import sys
from pathlib import Path
import logging


def setup_logging():
    """Configura el logging básico"""
    logging.basicConfig(
        level=logging.INFO, format="[%(asctime)s] %(levelname)s: %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    )
    return logging.getLogger(__name__)


def ensure_permissions(logger):
    """Asegura que los directorios críticos tengan los permisos correctos"""

    # Detectar el entorno
    is_container = Path("/mediajelly").exists()
    base_dir = Path("/mediajelly" if is_container else "/home/tafurc/mediaJelly")

    critical_dirs = [base_dir / "scripts" / "tmp", base_dir / "scripts" / "logs"]

    success = True

    for directory in critical_dirs:
        try:
            if not directory.exists():
                logger.info(f"Creando directorio: {directory}")
                directory.mkdir(parents=True, exist_ok=True)

            # Cambiar permisos a 0o777
            current_perms = oct(directory.stat().st_mode)[-3:]
            if current_perms != "777":
                logger.info(f"Corrigiendo permisos de {directory}: {current_perms} -> 777")
                os.chmod(directory, 0o777)
            else:
                logger.debug(f"Permisos correctos en {directory}")

            # También corregir permisos de archivos críticos en tmp
            if directory.name == "tmp":
                critical_files = ["progress.json", "processing_metrics.json", "language_cache.json"]
                for filename in critical_files:
                    file_path = directory / filename
                    if file_path.exists():
                        try:
                            # Verificar propietario
                            stat_info = file_path.stat()
                            if stat_info.st_uid == 0:  # Pertenece a root
                                logger.warning(f"Archivo con propietario incorrecto: {filename} (root -> mediauser)")
                                try:
                                    import pwd
                                    mediauser_uid = pwd.getpwnam('mediauser').pw_uid
                                    mediauser_gid = pwd.getpwnam('mediauser').pw_gid
                                    os.chown(file_path, mediauser_uid, mediauser_gid)
                                    logger.info(f"Propietario corregido: {filename}")
                                except (KeyError, PermissionError) as e:
                                    logger.error(f"No se pudo cambiar propietario de {filename}: {e}")

                            # Cambiar permisos del archivo a 0o666 (rw-rw-rw-)
                            current_file_perms = oct(file_path.stat().st_mode)[-3:]
                            if current_file_perms != "666":
                                logger.info(f"Corrigiendo permisos de archivo {filename}: {current_file_perms} -> 666")
                                os.chmod(file_path, 0o666)
                        except Exception as e:
                            logger.error(f"Error al procesar archivo {filename}: {e}")

        except PermissionError as e:
            logger.error(f"Error de permisos en {directory}: {e}")
            logger.warning("Ejecuta este script con sudo si estás fuera del contenedor")
            success = False
        except Exception as e:
            logger.error(f"Error al procesar {directory}: {e}")
            success = False

    return success


def cleanup_old_locks(logger):
    """Elimina archivos de lock antiguos que puedan estar causando problemas"""

    is_container = Path("/mediajelly").exists()
    base_dir = Path("/mediajelly" if is_container else "/home/tafurc/mediaJelly")
    tmp_dir = base_dir / "scripts" / "tmp"

    if not tmp_dir.exists():
        return True

    lock_files = list(tmp_dir.glob("*.lock"))

    for lock_file in lock_files:
        try:
            # Verificar si el archivo tiene permisos incorrectos (pertenece a root)
            stat_info = lock_file.stat()
            if stat_info.st_uid == 0 and os.getuid() != 0:
                logger.warning(f"Archivo de lock con permisos incorrectos: {lock_file.name}")
                try:
                    lock_file.unlink()
                    logger.info(f"Eliminado: {lock_file.name}")
                except PermissionError:
                    logger.error(f"No se pudo eliminar {lock_file.name} - ejecuta con sudo")
            else:
                logger.debug(f"Lock file OK: {lock_file.name}")
        except Exception as e:
            logger.error(f"Error al procesar {lock_file}: {e}")

    return True


def main():
    """Función principal"""
    logger = setup_logging()

    logger.info("=== MediaJelly - Mantenimiento de Permisos ===")

    # Asegurar permisos de directorios
    if not ensure_permissions(logger):
        logger.error("Falló la corrección de permisos")
        return 1

    # Limpiar locks antiguos
    cleanup_old_locks(logger)

    logger.info("=== Mantenimiento completado ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
