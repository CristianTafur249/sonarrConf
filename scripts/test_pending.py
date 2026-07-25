#!/usr/bin/env python3
"""
Script de prueba para verificar que PendingFilesMixin se conecta a la DB correctamente
"""

import sys
import logging
from pathlib import Path

# Configurar logging para ver todo
logging.basicConfig(
    level=logging.DEBUG,
    format="[%(asctime)s] [%(levelname)s] %(message)s"
)

# Importar el mixin con logs
sys.path.insert(0, str(Path(__file__).parent))
from mediajelly_pending_files import PendingFilesMixin
from mediajelly_filename_normalizer import FilenameNormalizerMixin
from mediajelly_db import get_pending_files, get_all_completed_files

# Crear una clase de prueba que herede de los mixins
class TestPending(FilenameNormalizerMixin, PendingFilesMixin):
    def __init__(self):
        self.base_dir = Path("/mediajelly")
        self.scripts_dir = self.base_dir / "scripts"
        self.tmp_dir = self.scripts_dir / "tmp"
        self.pending_file = self.tmp_dir / "pending-compression.txt"
        self.completed_file = self.tmp_dir / "completed.txt"
        self.logger = logging.getLogger("test")


# Ejecutar prueba
if __name__ == "__main__":
    print("=" * 60)
    print("TEST DE CONEXIÓN A DB")
    print("=" * 60)

    # 1. Verificar DB directamente
    print("\n📊 Verificando DB directamente:")
    try:
        pending = get_pending_files()
        print(f"   get_pending_files(): {len(pending)} archivos")
        if pending:
            print(f"   Primeros 3: {[str(p) for p in pending[:3]]}")
    except Exception as e:
        print(f"   ❌ Error: {e}")

    try:
        completed = get_all_completed_files()
        print(f"   get_all_completed_files(): {len(completed)} archivos")
    except Exception as e:
        print(f"   ❌ Error: {e}")

    # 2. Probar el mixin
    print("\n🔍 Probando PendingFilesMixin:")
    test = TestPending()
    pending_files = test._get_pending_files()
    print(f"   _get_pending_files(): {len(pending_files)} archivos")
    if pending_files:
        print(f"   Primeros 3: {[str(p) for p in pending_files[:3]]}")

    # 3. Verificar archivo físico pending-compression.txt
    pending_txt = Path("/mediajelly/scripts/tmp/pending-compression.txt")
    if pending_txt.exists():
        with open(pending_txt, "r") as f:
            lines = [l.strip() for l in f if l.strip()]
        print(f"\n📄 pending-compression.txt: {len(lines)} líneas")
        if lines:
            print(f"   Primeras 3: {lines[:3]}")
    else:
        print(f"\n📄 pending-compression.txt NO EXISTE")
