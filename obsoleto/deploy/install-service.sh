#!/bin/bash
# install-service.sh — Instala cuartodepazsearch como servicio systemd
# Uso: sudo ./install-service.sh [USER]
#   USER por defecto: root (este server)
#
# Para el server sebastianvernis (legacy):
#   sudo ./install-service.sh sebastianvernis

set -e

USER="${1:-root}"
UNIT="cuartodepazsearch@${USER}.service"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEMPLATE="${SCRIPT_DIR}/cuartodepazsearch.service.template"
DEST="/etc/systemd/system/${UNIT}"

if [ ! -f "${TEMPLATE}" ]; then
    echo "ERROR: no se encontró ${TEMPLATE}" >&2
    exit 1
fi

echo "Instalando ${UNIT} desde ${TEMPLATE}..."
cp "${TEMPLATE}" "${DEST}"
sed -i "s/%I/${USER}/g" "${DEST}"

# Si USER != root, ajustar paths absolutos al home del user
if [ "${USER}" != "root" ]; then
    # Los paths canónicos de sebastianvernis (compatibilidad legacy)
    sed -i "s|/root/proyecto_kyc|/home/${USER}/proyectos/kyc|g" "${DEST}"
    sed -i "s|/root/ine_server/.venv|/home/${USER}/.venv|g" "${DEST}"
fi

systemctl daemon-reload
systemctl enable "${UNIT}"
systemctl start "${UNIT}"

echo "✓ Servicio instalado y arrancado"
systemctl status "${UNIT}" --no-pager
