.PHONY: help install lint typecheck test check certs up up-tls down logs clean e1 e2 e3 e5

help:  ## Lista os alvos disponíveis
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-10s %s\n", $$1, $$2}'

install:  ## Instala dependências e hooks de pre-commit
	uv sync
	uv run pre-commit install

lint:  ## Formatação e lint (ruff)
	uv run ruff format --check src tests
	uv run ruff check src tests

typecheck:  ## Verificação de tipos (mypy strict)
	uv run mypy

test:  ## Testes unitários
	uv run pytest

check: lint typecheck test  ## Todas as verificações

.env:
	cp .env.example .env

certs:  ## Gera a CA própria e o certificado do servidor (infra/certs)
	./scripts/gen-certs.sh infra/certs

up: .env certs  ## Sobe a pilha sem TLS (simuladores, servidores, InfluxDB, Grafana)
	mkdir -p data
	docker compose up -d --build

up-tls: .env certs  ## Sobe a pilha com TLS/DTLS em todos os protocolos
	mkdir -p data
	docker compose -f compose.yaml -f compose.tls.yaml up -d --build

down:  ## Para a pilha (mantém os volumes)
	docker compose -f compose.yaml -f compose.tls.yaml down

e1:  ## E1 (bytes na rede): roda sem TLS e com TLS, trocando o modo da pilha
	$(MAKE) up
	uv run python -m iotsim.experiments.e1_bytes --mode plain
	$(MAKE) up-tls
	uv run python -m iotsim.experiments.e1_bytes --mode tls

e2:  ## E2 (dispositivo sonolento): roda sem TLS e com TLS, trocando o modo da pilha
	$(MAKE) up
	uv run python -m iotsim.experiments.e2_sleepy --mode plain
	$(MAKE) up-tls
	uv run python -m iotsim.experiments.e2_sleepy --mode tls

e3:  ## E3/E4 (latência e entrega sob perda de 0, 1, 5 e 10%), pilha sem TLS
	$(MAKE) up
	uv run python -m iotsim.experiments.e3_loss

e5:  ## E5 (escala: 10/100/1000 dispositivos): roda sem TLS e com TLS
	$(MAKE) up
	uv run python -m iotsim.experiments.e5_scale --mode plain
	$(MAKE) up-tls
	uv run python -m iotsim.experiments.e5_scale --mode tls

logs:  ## Acompanha os logs
	docker compose logs -f --tail 50

clean:  ## Para a pilha e apaga volumes e dados brutos
	docker compose -f compose.yaml -f compose.tls.yaml down -v
	rm -rf data
