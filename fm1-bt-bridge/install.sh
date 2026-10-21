#!/usr/bin/env bash
# FM-1 Bluetooth bridge installer for Raspberry Pi OS Lite on a Pi Zero W.
# Run as your normal user (not with sudo):  bash install.sh
set -euo pipefail

if [ "$(id -u)" -eq 0 ]; then
    echo "Run this as your normal user, not root: bash install.sh" >&2
    exit 1
fi
DIR="$(cd "$(dirname "$0")" && pwd)"

echo "== Your password is needed once, for sudo"
sudo -v
while kill -0 "$$" 2>/dev/null; do sudo -n true; sleep 50; done 2>/dev/null &   # keep sudo for the whole install

echo "== Installing packages (this takes a while on a Zero W)"
sudo apt-get update
sudo apt-get install -y --no-install-recommends \
    pipewire wireplumber libspa-0.2-bluetooth dbus-user-session rtkit bluez \
    python3-pil python3-spidev python3-libgpiod fonts-dejavu-core \
    python3-dbus python3-gi python3-mido python3-rtmidi

echo "== Enabling SPI for the OLED HAT, Bluetooth, and permissions"
sudo raspi-config nonint do_spi 0
sudo rfkill unblock bluetooth || true
sudo systemctl enable --now bluetooth
sudo usermod -aG bluetooth,gpio,spi,audio "$USER"

echo "== Letting the bridge shut down and switch Wi-Fi without a password (KEY3, settings page)"
SUDOERS=/etc/sudoers.d/fm1-bridge
printf '%s ALL=(root) NOPASSWD: /usr/bin/systemctl poweroff, /usr/bin/nmcli radio wifi on, /usr/bin/nmcli radio wifi off\n' \
    "$USER" | sudo tee "$SUDOERS" >/dev/null
sudo chmod 440 "$SUDOERS"
sudo visudo -cf "$SUDOERS"

echo "== Letting PipeWire run its audio threads in real time (no desktop session on a headless Pi)"
sed "s/FM1_USER/$USER/" "$DIR/config/50-fm1-bridge-rtkit.rules" | sudo tee /etc/polkit-1/rules.d/50-fm1-bridge-rtkit.rules >/dev/null

echo "== Installing the bridge"
install -D -m 755 "$DIR/bridge.py"                              ~/.local/share/fm1-bridge/bridge.py
install -D -m 644 "$DIR/blemidi.py"                             ~/.local/share/fm1-bridge/blemidi.py
install -D -m 644 "$DIR/config/pipewire-10-fm1-bridge.conf"     ~/.config/pipewire/pipewire.conf.d/10-fm1-bridge.conf
install -D -m 644 "$DIR/config/wireplumber-51-fm1-bridge.conf"  ~/.config/wireplumber/wireplumber.conf.d/51-fm1-bridge.conf
if wireplumber --version 2>/dev/null | grep -q "Linked with libwireplumber 0\.4"; then   # Bookworm: Lua config
    install -D -m 644 "$DIR/config/wireplumber-51-fm1-bridge.lua" ~/.config/wireplumber/bluetooth.lua.d/51-fm1-bridge.lua
else                                                                                # 0.5+ warns about Lua files
    rm -f ~/.config/wireplumber/bluetooth.lua.d/51-fm1-bridge.lua
fi
install -D -m 644 "$DIR/config/fm1-bridge.service"              ~/.config/systemd/user/fm1-bridge.service

echo "== Starting at boot without a login"
sudo loginctl enable-linger "$USER"
systemctl --user daemon-reload
systemctl --user enable pipewire.socket wireplumber.service fm1-bridge.service
sync                                    # on the card before anyone pulls the power

echo
echo "Done. Reboot now:  sudo reboot"
echo "After the reboot the OLED shows the bridge. If it is upside down, see README.md."
echo "Once everything works, turn Wi-Fi off on the device: joystick right > Wi-Fi."
