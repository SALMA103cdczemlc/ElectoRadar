#!/bin/bash
exec > /var/log/electoradar-setup.log 2>&1
set -eux

API_KEY="__API_KEY__"

if [ ! -f /swapfile ]; then
  fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
  echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y python3-venv python3-pip
mkdir -p /opt/electoradar/{app,data,model}

cat > /opt/electoradar/app/server.py <<'PYEOF'
__SERVER_PY__
PYEOF

cat > /opt/electoradar/app/train.py <<'PYEOF'
__TRAIN_PY__
PYEOF

cat > /opt/electoradar/app/requirements.txt <<'PYEOF'
__REQUIREMENTS__
PYEOF

cat > /opt/electoradar/data/entrainement.csv <<'CSVEOF'
__TRAIN_CSV__
CSVEOF

python3 -m venv /opt/electoradar/venv
/opt/electoradar/venv/bin/pip install --upgrade pip
/opt/electoradar/venv/bin/pip install -r /opt/electoradar/app/requirements.txt

cd /opt/electoradar/app
MODEL_DIR=/opt/electoradar/model TRAIN_CSV=/opt/electoradar/data/entrainement.csv /opt/electoradar/venv/bin/python train.py

cat > /etc/systemd/system/electoradar.service <<EOF
[Unit]
Description=ElectoRadar - serveur du modele NLP
After=network-online.target

[Service]
WorkingDirectory=/opt/electoradar/app
Environment=API_KEY=${API_KEY}
Environment=MODEL_DIR=/opt/electoradar/model
Environment=TRAIN_CSV=/opt/electoradar/data/entrainement.csv
ExecStart=/opt/electoradar/venv/bin/uvicorn server:app --host 0.0.0.0 --port 8000 --workers 1
Restart=always

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now electoradar
echo "ELECTORADAR PRET"
