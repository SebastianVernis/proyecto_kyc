#!/usr/bin/env bash
# rsync seguro desde la máquina remota (100.65.144.78) a esta máquina
#   - verifica checksums
#   - omite lo que ya existe
#   - reanuda si se corta la conexión
#   - muestra progreso

REMOTE_USER="sebastianvernis"
REMOTE_HOST="100.65.144.78"
REMOTE_PATH="/home/sebastianvernis/proyectos/kyc/proyecto_kyc/bases/"
LOCAL_PATH="$HOME/busquedas/proyecto_kyc/bases/"

# Asegurar que el destino existe
mkdir -p "$LOCAL_PATH"

echo "=== rsync: $REMOTE_HOST:$REMOTE_PATH -> $LOCAL_PATH ==="
echo "Fecha: $(date)"
echo ""

rsync -avz \
  --checksum \
  --ignore-existing \
  --partial \
  --progress \
  --stats \
  -e "ssh -o ServerAliveInterval=30 -o ServerAliveCountMax=6 -o ConnectTimeout=10" \
  "$REMOTE_USER@$REMOTE_HOST:$REMOTE_PATH" \
  "$LOCAL_PATH"

EXIT=$?
echo ""
echo "=== rsync terminó con código: $EXIT ==="
echo "Fecha: $(date)"

if [ $EXIT -eq 0 ]; then
  echo "✅ Copia completada exitosamente."
  echo "Archivos en destino: $(ls "$LOCAL_PATH" 2>/dev/null | grep -v rclone | wc -l)"
  echo "Tamaño: $(du -sh "$LOCAL_PATH" 2>/dev/null | grep -v rclone)"
else
  echo "⚠️  rsync salió con código $EXIT. Revisa el mensaje de arriba."
fi
