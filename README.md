# IoT Communication Protocols — simulador comparativo

Implementação da **Nota 3** da disciplina *Internet das Coisas* (PGE00043/2026/2, PPGEE/UNESP):
simulação e medição comparativa dos protocolos **MQTT, CoAP, HTTP/HTTPS, AMQP e WebSocket**,
dando continuidade ao estudo dirigido (Nota 2), que estimou o overhead apenas analiticamente.

## Arquitetura

```
 simuladores (1 container por protocolo, N dispositivos asyncio)
   mqtt-devices ──► mosquitto ──► mqtt-consumer ─┐
   http-devices ──────────────► http-server ─────┤
   coap-devices ───── UDP ────► coap-server ─────┤
   amqp-devices ──► rabbitmq ───► amqp-consumer ─┤
   ws-devices ─── quadros ────► ws-server ───────┤
                                                 ▼
                      data/<run_id>/*.csv (dados brutos → análise do artigo)
                      InfluxDB 2.7 ──► Grafana (visualização)
```

Princípios de medição:

- **Carga útil idêntica** em todos os protocolos (`iotsim.message.Reading`, JSON compacto).
- **Sensor sintético determinístico** (seed fixa) → execuções reproduzíveis.
- Cada dispositivo grava `sent_<proto>.csv` (envio e confirmação) e cada receptor grava
  `recv_<proto>.csv` (chegada). Latência = `recv_ns − sent_ns`; entrega = recebidas / enviadas.
  Todos os containers compartilham o relógio do mesmo host (VM do Docker), o que torna a
  diferença de timestamps válida.
- No MQTT e no AMQP a latência inclui dois saltos (dispositivo → broker → consumidor),
  característica do modelo publicação–assinatura.
- AMQP 1.0 no RabbitMQ 4.x: envio ao exchange `amq.topic` pelo endereço v2
  `/exchanges/amq.topic/<routing-key>` (destino fixado no *attach* do enlace); a fila
  `iotsim.telemetry` recebe `sensors.#`.
- WebSocket: conexão persistente por dispositivo, recurso fixado no handshake e um quadro
  binário por leitura, sem compressão (`permessage-deflate` desativado). Sem confirmação
  nativa: o envio termina quando o quadro é entregue ao TCP.

## Uso

Requisitos: Docker (Compose v2+), [uv](https://docs.astral.sh/uv/) e Python 3.12.

```bash
make install   # dependências + hooks
make check     # ruff + mypy (strict) + pytest
make up        # sobe a pilha (cria .env a partir de .env.example)
```

- Grafana: <http://localhost:3000> (credenciais no `.env`) → pasta *IoT Protocols*.
- InfluxDB: <http://localhost:8086>.
- RabbitMQ (console): <http://localhost:15672> (`AMQP_USER`/`AMQP_PASSWORD` do `.env`).
- Dados brutos: `data/<IOTSIM_RUN_ID>/`.

Parâmetros (variáveis `IOTSIM_*` no `.env`): `RUN_ID`, `DEVICES`, `INTERVAL_S`, `SEED`,
`MQTT_QOS`, `COAP_CONFIRMABLE` (CON/NON), `AMQP_CONFIRM` (liquidado/pré-liquidado),
`LOG_LEVEL`.

## Estrutura

```
src/iotsim/
  config.py        configuração tipada (pydantic-settings)
  message.py       mensagem comum a todos os protocolos
  sensors.py       sensor sintético determinístico
  sink.py          gravação CSV + InfluxDB
  adapters/        um ProtocolAdapter por protocolo (lado dispositivo)
  devices/         execução da frota de dispositivos e CLI
  servers/         receptores (servidores e consumidores)
infra/             Mosquitto, RabbitMQ, provisionamento do Grafana
tests/             testes (AMQP: integração, requer IOTSIM_TEST_AMQP_URL)
```

## Situação

| Protocolo | Dispositivo | Receptor | TLS |
|---|---|---|---|
| MQTT | ✅ | ✅ | pendente |
| HTTP | ✅ | ✅ | pendente |
| CoAP (NON/CON) | ✅ | ✅ | DTLS a avaliar |
| AMQP 1.0 | ✅ | ✅ | pendente |
| WebSocket | ✅ | ✅ | pendente |
