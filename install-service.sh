#!/bin/bash
# Installation script for drone-mapper systemd service

set -e

SERVICE_NAME="drone-mapper"
SERVICE_FILE="drone-mapper.service"
SERVICE_PATH="/etc/systemd/system/${SERVICE_NAME}.service"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "Installing ${SERVICE_NAME} systemd service..."

# Check if running as root or with sudo
if [ "$EUID" -ne 0 ]; then 
    echo "This script must be run as root (use sudo)"
    exit 1
fi

# Check if service file exists
if [ ! -f "${SCRIPT_DIR}/${SERVICE_FILE}" ]; then
    echo "Error: ${SERVICE_FILE} not found in ${SCRIPT_DIR}"
    exit 1
fi

# Copy service file
echo "Copying service file to ${SERVICE_PATH}..."
cp "${SCRIPT_DIR}/${SERVICE_FILE}" "${SERVICE_PATH}"

# Reload systemd
echo "Reloading systemd daemon..."
systemctl daemon-reload

# Enable service to start on boot
echo "Enabling ${SERVICE_NAME} to start on boot..."
systemctl enable ${SERVICE_NAME}.service

echo ""
echo "Service installed successfully!"
echo ""
echo "To start the service now:"
echo "  sudo systemctl start ${SERVICE_NAME}"
echo ""
echo "To check service status:"
echo "  sudo systemctl status ${SERVICE_NAME}"
echo ""
echo "To view logs:"
echo "  sudo journalctl -u ${SERVICE_NAME} -f"
echo ""
echo "To stop the service:"
echo "  sudo systemctl stop ${SERVICE_NAME}"
echo ""
echo "To disable auto-start:"
echo "  sudo systemctl disable ${SERVICE_NAME}"


