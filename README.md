# IoT Communication Protocols — simulador comparativo

Implementação da **Nota 3** da disciplina *Internet das Coisas* (PGE00043/2026/2, PPGEE/UNESP):
simulação e medição comparativa dos protocolos **MQTT, CoAP, HTTP/HTTPS, AMQP e WebSocket**,
dando continuidade ao estudo dirigido (Nota 2), que estimou o overhead apenas analiticamente.

## Arquitetura

```
 simuladores (1 container por protocolo, N dispositivos asyncio)
   mqtt-devices ──► mosquitto ──► mqtt-consumer ─┐
   http-devices ──────────────► http-server ─────┤
   (CoAP, AMQP, WebSocket — próximas fases)      │
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
- No MQTT a latência inclui dois saltos (dispositivo → broker → assinante), característica
  do modelo publicação–assinatura.

## Uso

Requisitos: Docker (Compose v2+), [uv](https://docs.astral.sh/uv/) e Python 3.12.

```bash
make install   # dependências + hooks
make check     # ruff + mypy (strict) + pytest
make up        # sobe a pilha (cria .env a partir de .env.example)
```

- Grafana: <http://localhost:3000> (credenciais no `.env`) → pasta *IoT Protocols*.
- InfluxDB: <http://localhost:8086>.
- Dados brutos: `data/<IOTSIM_RUN_ID>/`.

Parâmetros (variáveis `IOTSIM_*` no `.env`): `RUN_ID`, `DEVICES`, `INTERVAL_S`, `SEED`,
`MQTT_QOS`, `LOG_LEVEL`.

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
infra/             Mosquitto, provisionamento do Grafana
tests/             testes unitários
```

## Situação

| Protocolo | Dispositivo | Receptor | TLS |
|---|---|---|---|
| MQTT | ✅ | ✅ | pendente |
| HTTP | ✅ | ✅ | pendente |
| CoAP | pendente | pendente | DTLS a avaliar |
| AMQP 1.0 | pendente | pendente | pendente |
| WebSocket | pendente | pendente | pendente |
