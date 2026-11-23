#!/usr/bin/env bash
# Run ON THE CLOUD VM (Ubuntu). Installs Ollama and the same model as the edge.
# Ollama stays bound to 127.0.0.1 on the VM: reach it from the laptop through an SSH
# tunnel, so no inference port is exposed to the internet.
#
#   scp ~/ollama-linux-amd64.tar.zst cloud_setup.sh USER@VM_IP:~     # from the laptop (optional)
#   ssh USER@VM_IP 'bash cloud_setup.sh'
set -euo pipefail
MODEL="${MODEL:-qwen2.5:1.5b}"

if ! command -v ollama >/dev/null; then
  if [ -f "$HOME/ollama-linux-amd64.tar.zst" ]; then
    echo ">>> installing Ollama from the copied archive"
    sudo apt-get update -y && sudo apt-get install -y zstd
    sudo tar -C /usr/local --zstd -xf "$HOME/ollama-linux-amd64.tar.zst"
    sudo useradd -r -s /bin/false -U -m -d /usr/share/ollama ollama 2>/dev/null || true
    sudo tee /etc/systemd/system/ollama.service >/dev/null <<'UNIT'
[Unit]
Description=Ollama
After=network-online.target
[Service]
ExecStart=/usr/local/bin/ollama serve
User=ollama
Group=ollama
Restart=always
[Install]
WantedBy=multi-user.target
UNIT
    sudo systemctl daemon-reload && sudo systemctl enable --now ollama
  else
    echo ">>> installing Ollama with the official script"
    curl -fsSL https://ollama.com/install.sh | sh
  fi
fi

sleep 3
ollama pull "$MODEL"
ollama run "$MODEL" "Say hello in one sentence."
echo
echo ">>> record for the paper:"
ollama --version
lscpu | grep -E "Model name|^CPU\(s\)"
free -h | grep Mem
nvidia-smi --query-gpu=name,memory.total --format=csv 2>/dev/null || echo "no GPU"
echo
echo ">>> on the LAPTOP, open the tunnel and leave it running:"
echo "    ssh -N -L 11435:127.0.0.1:11434 $(whoami)@<VM_IP>"
