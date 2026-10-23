#!/usr/bin/env bash
# Gera uma CA própria e um certificado de servidor para os experimentos com TLS.
# Uso exclusivo em laboratório: a chave do servidor fica legível (0644) para que os
# containers, que rodam com usuários diferentes, consigam carregá-la.
set -euo pipefail

OUT="${1:-infra/certs}"
DAYS=825
# Nomes dos serviços no Compose + acesso local.
SANS="DNS:mosquitto,DNS:rabbitmq,DNS:http-server,DNS:ws-server,DNS:coap-server,DNS:localhost,IP:127.0.0.1"

mkdir -p "$OUT"
cd "$OUT"

if [[ -f ca.crt && -f server.crt ]]; then
  echo "certificados já existem em $OUT (apague-os para regenerar)"
  exit 0
fi

openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:P-256 -nodes \
  -keyout ca.key -out ca.crt -days "$DAYS" -subj "/CN=iotsim lab CA"

openssl req -newkey ec -pkeyopt ec_paramgen_curve:P-256 -nodes \
  -keyout server.key -out server.csr -subj "/CN=iotsim server"

openssl x509 -req -in server.csr -CA ca.crt -CAkey ca.key -CAcreateserial \
  -out server.crt -days "$DAYS" \
  -extfile <(printf 'subjectAltName=%s\nextendedKeyUsage=serverAuth\n' "$SANS")

rm -f server.csr ca.srl
chmod 600 ca.key
chmod 644 ca.crt server.crt server.key
openssl verify -CAfile ca.crt server.crt
