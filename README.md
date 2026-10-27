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

## TLS/DTLS

A segurança é uma variável experimental: a mesma pilha roda com ou sem criptografia.

```bash
make up       # sem TLS
make up-tls   # com TLS/DTLS (compose.yaml + compose.tls.yaml)
```

| Protocolo | Porta | Mecanismo |
|---|---|---|
| MQTT | 8883 | TLS 1.2+, CA própria (`scripts/gen-certs.sh`) |
| HTTPS | 443 | TLS, CA própria |
| WSS | 443 | TLS, CA própria |
| AMQP 1.0 | 5671 | TLS + SASL PLAIN |
| CoAP | 5684 | DTLS 1.2 com chave pré-compartilhada (PSK, `COAP_PSK`) |

- O CoAP usa PSK, e não certificado, porque o transporte DTLS do aiocoap (tinydtls) só
  suporta PSK; é também o perfil típico de nós restritos (RFC 7925).
- O DTLS depende do pacote `DTLSSocket` (extra `dtls`), compilado na imagem Docker. No
  ambiente local ele é opcional (`uv sync --extra dtls` exige autotools).
- Os certificados ficam em `infra/certs/` (não versionado) e são gerados por `make certs`.

## Uso

Requisitos: Docker (Compose v2+), [uv](https://docs.astral.sh/uv/) e Python 3.12.

```bash
make install   # dependências + hooks
make check     # ruff + mypy (strict) + pytest
make up        # sobe a pilha sem TLS (cria .env e certificados)
```

- Grafana: <http://localhost:3000> (credenciais no `.env`) → pasta *IoT Protocols*.
- InfluxDB: <http://localhost:8086>.
- RabbitMQ (console): <http://localhost:15672> (`AMQP_USER`/`AMQP_PASSWORD` do `.env`).
- Dados brutos: `data/<IOTSIM_RUN_ID>/`.

Parâmetros (variáveis `IOTSIM_*` no `.env`): `RUN_ID`, `DEVICES`, `INTERVAL_S`, `SEED`,
`MQTT_QOS`, `COAP_CONFIRMABLE` (CON/NON), `AMQP_CONFIRM` (liquidado/pré-liquidado),
`LOG_LEVEL`.

## Experimentos

| # | Experimento | Comando | Saída |
|---|---|---|---|
| E1 | Bytes e pacotes por mensagem, custo de estabelecimento (com/sem TLS) | `make e1` | `results/e1/<run>/summary.csv` + `.pcap` |
| E2 | Dispositivo sonolento: bytes, pacotes e tempo de rádio por ciclo (com/sem TLS) | `make e2` | `results/e2/<run>/summary.csv` + `.pcap` |
| E3/E4 | Latência (p50/p95/p99) e entrega por modo de QoS sob perda de 0, 1, 5 e 10% | `make e3` | `results/e3/<run>/summary.csv` + `.pcap` |
| E5 | Escala: CPU e memória dos servidores com 10, 100 e 1000 dispositivos (com/sem TLS) | `make e5` | `results/e5/<run>/summary.csv` |

Figuras (PDF/PNG) e tabelas (CSV e LaTeX) do artigo: `make report` → `results/report/`.
As rodadas usadas, e os pontos descartados com o motivo, ficam em `analysis/runs.toml`.

E1: um dispositivo por cenário, em container próprio, com `tshark` no mesmo namespace de
rede (`nicolaka/netshoot`). Cada cenário envia exatamente N mensagens (`IOTSIM_MAX_MESSAGES`);
as métricas em regime excluem a primeira e a última mensagem.

E2: mesmo arranjo, com `IOTSIM_SLEEPY=true` — a cada leitura o dispositivo conecta, envia e
desconecta. Ciclo = do despertar até o despertar seguinte (o primeiro é descartado);
"tempo de rádio" = do primeiro ao último pacote do ciclo, aproximação do rádio ligado.

E3/E4: perda de pacotes IP com `tc netem` (semente fixa) nos dois sentidos — na saída do
dispositivo e na saída do servidor, filtrada pelo IP do dispositivo (o kernel do Docker
Desktop não tem `ifb`). 100 mensagens por combinação, sem TLS. Entrega e latência medidas
no receptor; o CoAP NON espera a resposta por no máximo um intervalo.

E5: um modo por protocolo (MQTT QoS 1, CoAP CON, HTTP, AMQP liquidado, WebSocket), 1 leitura/s
por dispositivo, 60 s de aquecimento e 60 s de medição. CPU e memória vêm do cgroup v2 do
servidor, do consumidor (brokers) e do próprio simulador. As frotas `*-devices` ficam paradas
durante o E5. O AMQP vai só até 100 dispositivos: o cliente `python-qpid-proton` cria um
contexto OpenSSL por conexão, e com centenas de conexões simultâneas o simulador satura antes
do broker. Medições longas devem rodar com o Mac sem repouso (`caffeinate`, tampa aberta).

Configuração uniforme de rede: todos os servidores usam `TCP_NODELAY` (padrão no RabbitMQ
e nos servidores asyncio; no Mosquitto, `set_tcp_nodelay true`). Sem isso, o Mosquitto
retém o CONNACK até o ACK atrasado do cliente (~40 ms por conexão TLS).

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
  experiments/     execução e análise dos experimentos (E1...)
infra/             Mosquitto, RabbitMQ, provisionamento do Grafana
scripts/           geração da CA e do certificado do servidor
tests/             testes (AMQP: integração, requer IOTSIM_TEST_AMQP_URL)
```

## Situação

| Protocolo | Dispositivo | Receptor | TLS/DTLS |
|---|---|---|---|
| MQTT | ✅ | ✅ | ✅ |
| HTTP | ✅ | ✅ | ✅ |
| CoAP (NON/CON) | ✅ | ✅ | ✅ DTLS-PSK |
| AMQP 1.0 | ✅ | ✅ | ✅ |
| WebSocket | ✅ | ✅ | ✅ |
