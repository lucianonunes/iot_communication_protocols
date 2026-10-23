#!/usr/bin/env bash
# Gera as definições a partir das variáveis de ambiente (a senha não fica versionada).
# Com load_definitions, o RabbitMQ não cria o usuário padrão; por isso ele é declarado aqui.
set -euo pipefail

python3 - <<'PY'
import json, os

user = os.environ["AMQP_USER"]
queue = os.environ.get("AMQP_QUEUE", "iotsim.telemetry")
definitions = {
    "users": [{"name": user, "password": os.environ["AMQP_PASSWORD"], "tags": ["administrator"]}],
    "vhosts": [{"name": "/"}],
    "permissions": [
        {"user": user, "vhost": "/", "configure": ".*", "write": ".*", "read": ".*"}
    ],
    "queues": [
        {"name": queue, "vhost": "/", "durable": True, "auto_delete": False, "arguments": {}}
    ],
    "bindings": [
        {
            "source": "amq.topic",
            "vhost": "/",
            "destination": queue,
            "destination_type": "queue",
            "routing_key": "sensors.#",
            "arguments": {},
        }
    ],
}
with open("/tmp/iotsim-definitions.json", "w") as f:
    json.dump(definitions, f)
os.chmod("/tmp/iotsim-definitions.json", 0o644)
PY

exec docker-entrypoint.sh "$@"
