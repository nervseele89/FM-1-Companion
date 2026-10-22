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
    python3-dbus python3-gi python3-mido python3-rtmidi python3-numpy

echo "== Enabling SPI for the OLED HAT, Bluetooth, and permissions"
sudo raspi-config nonint do_spi 0
sudo rfkill unblock bluetooth || true
sudo systemctl enable --now bluetooth
sudo usermod -aG bluetooth,gpio,spi,audio "$USER"

echo "== Letting the bridge shut down, switch Wi-Fi and copy backups to the SD card without a password"
sudo install -m 755 "$DIR/config/fm1-sd-sync" /usr/local/sbin/fm1-sd-sync
SUDOERS=/etc/sudoers.d/fm1-bridge
printf '%s ALL=(root) NOPASSWD: /usr/bin/systemctl poweroff, /usr/bin/nmcli radio wifi on, /usr/bin/nmcli radio wifi off, /usr/local/sbin/fm1-sd-sync\n' \
    "$USER" | sudo tee "$SUDOERS" >/dev/null
sudo chmod 440 "$SUDOERS"
sudo visudo -cf "$SUDOERS"

echo "== Letting the web page use port 80 (http://$(hostname).local) without root"
echo "net.ipv4.ip_unprivileged_port_start=80" | sudo tee /etc/sysctl.d/50-fm1-bridge.conf >/dev/null
sudo sysctl -q -w net.ipv4.ip_unprivileged_port_start=80

echo "== Letting PipeWire run its audio threads in real time (no desktop session on a headless Pi)"
sed "s/FM1_USER/$USER/" "$DIR/config/50-fm1-bridge-rtkit.rules" | sudo tee /etc/polkit-1/rules.d/50-fm1-bridge-rtkit.rules >/dev/null

echo "== Installing the bridge"
install -D -m 755 "$DIR/bridge.py"                              ~/.local/share/fm1-bridge/bridge.py
install -D -m 644 "$DIR/blemidi.py"                             ~/.local/share/fm1-bridge/blemidi.py
install -D -m 644 "$DIR/sloop.py"                               ~/.local/share/fm1-bridge/sloop.py
install -D -m 644 "$DIR/libui.py"                               ~/.local/share/fm1-bridge/libui.py
install -D -m 644 "$DIR/meters.py"                              ~/.local/share/fm1-bridge/meters.py
install -D -m 644 "$DIR/midiui.py"                              ~/.local/share/fm1-bridge/midiui.py
install -D -m 755 "$DIR/library.py"                             ~/.local/share/fm1-bridge/library.py
install -D -m 755 "$DIR/web.py"                                 ~/.local/share/fm1-bridge/web.py
mkdir -p ~/.local/share/fm1-bridge/vendor/sloop ~/fm1-library/backups ~/fm1-library/projects \
    ~/fm1-library/samples ~/fm1-library/firmware
install -m 644 "$DIR"/vendor/sloop/*                            ~/.local/share/fm1-bridge/vendor/sloop/
install -D -m 644 "$DIR/config/pipewire-10-fm1-bridge.conf"     ~/.config/pipewire/pipewire.conf.d/10-fm1-bridge.conf
install -D -m 644 "$DIR/config/wireplumber-51-fm1-bridge.conf"  ~/.config/wireplumber/wireplumber.conf.d/51-fm1-bridge.conf
if wireplumber --version 2>/dev/null | grep -q "Linked with libwireplumber 0\.4"; then   # Bookworm: Lua config
    install -D -m 644 "$DIR/config/wireplumber-51-fm1-bridge.lua" ~/.config/wireplumber/bluetooth.lua.d/51-fm1-bridge.lua
else                                                                                # 0.5+ warns about Lua files
    rm -f ~/.config/wireplumber/bluetooth.lua.d/51-fm1-bridge.lua
fi
install -D -m 644 "$DIR/config/fm1-bridge.service"              ~/.config/systemd/user/fm1-bridge.service
install -D -m 644 "$DIR/config/fm1-web.service"                 ~/.config/systemd/user/fm1-web.service

echo "== Booting faster (a Zero W on Trixie otherwise takes ~3 minutes before the bridge starts)"
# cloud-init only applies Raspberry Pi Imager's settings on the first boot, but costs ~40 s on every boot
sudo touch /etc/cloud/cloud-init.disabled
# start the user services (PipeWire, the bridge) without waiting for NetworkManager
sudo install -m 644 "$DIR/config/systemd-user-sessions.service" /etc/systemd/system/systemd-user-sessions.service
# swap in RAM only: the default file-backed swap is rebuilt on every boot (~20 s)
sudo install -D -m 644 "$DIR/config/rpi-swap-50-fm1-bridge.conf" /etc/rpi/swap.conf.d/50-fm1-bridge.conf
sudo systemctl daemon-reload

echo "== Starting at boot without a login"
sudo loginctl enable-linger "$USER"
systemctl --user daemon-reload
systemctl --user enable pipewire.socket wireplumber.service fm1-bridge.service fm1-web.service
sync                                    # on the card before anyone pulls the power

echo
echo "Done. Reboot now:  sudo reboot"
echo "After the reboot the OLED shows the bridge. If it is upside down, see README.md."
echo "Once everything works, turn Wi-Fi off on the device: joystick right > Wi-Fi."
