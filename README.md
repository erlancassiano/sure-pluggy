# pluggy-sure

Sincronização **unidirecional** Pluggy (Open Finance, somente leitura) → [Sure](https://github.com/we-promise/sure) (fork do Maybe Finance).

Ferramenta pessoal: backfill de 2026 e sync diária idempotente. **Não contém dados financeiros reais** — fixtures e exemplos são fictícios.

## Requisitos

- Python 3.11+ (desenvolvido com 3.12)
- Instância Sure acessível (Docker no Windows em `localhost:3001`, ou rede Docker)
- Credenciais Pluggy e chave de API do Sure

## Configuração

1. Copie os exemplos e preencha **localmente** (arquivos reais ficam no `.gitignore`):

```bash
cp .env.example .env
cp accounts_map.example.json accounts_map.json
cp rules.example.yaml rules.yaml
```

2. Variáveis obrigatórias (validadas na inicialização). O CLI e `load_settings()`
   carregam automaticamente o arquivo `.env` da pasta atual (stdlib; variáveis já
   definidas no ambiente têm precedência — não são sobrescritas):

| Variável | Uso |
|---|---|
| `PLUGGY_CLIENT_ID` | Auth Pluggy (`POST /auth`) |
| `PLUGGY_CLIENT_SECRET` | Auth Pluggy |
| `PLUGGY_ITEM_IDS` | IDs dos items Pluggy (lista separada por vírgula) |
| `SURE_API_KEY` | Header `X-Api-Key` |
| `SURE_URL` | Base URL do Sure |

Opcionais: `SYNC_LOOKBACK_DAYS` (15), `SYNC_STATE_PATH`, `SYNC_RULES_PATH`, `SYNC_ACCOUNTS_MAP_PATH`, `SYNC_TRANSFER_MATCH_DAYS` (3), `LOG_LEVEL`, `LOG_AMOUNTS` (false).

3. No Sure, **crie manualmente** as contas (corrente/cartão). A API do Sure **não cria contas**. Preencha `accounts_map.json` com `pluggy_account_id → sure_account_id`.

4. Ajuste `rules.yaml` (motor de regras ordenado; primeira que casa vence).

## Instalação

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

## Uso

```bash
# Backfill desde 2026-01-01
python -m pluggy_sure sync --since 2026-01-01

# Dry-run (lê APIs, imprime contagens, não grava)
python -m pluggy_sure sync --since 2026-01-01 --dry-run

# Sync diária (janela de N dias; padrão SYNC_LOOKBACK_DAYS=15)
python -m pluggy_sure sync --days 15
```

Relatório por execução: criadas, atualizadas, sem mudança, sumidas, transferências sem par, saldos para revisão.

### Onde rodar (Windows / Docker)

- **No host Windows** (Agendador de Tarefas): `SURE_URL=http://localhost:3001`
- **Container na mesma rede do compose do Sure**: `SURE_URL=http://web:3000` (ajuste o nome do serviço)
- **Container apontando para o host**: `SURE_URL=http://host.docker.internal:3001` — **não** use `localhost` de dentro do container

Há um `docker-compose.yml` de exemplo para o sync.

## Idempotência

- Chave no Sure: `(account_id, source="pluggy", external_id=<pluggy_tx_id>)`
- Estado local SQLite (`SYNC_STATE_PATH`): mapa `pluggy_id → sure_id` + hash + pendências
- Reexecutar a mesma janela não duplica; mudanças (pendente→postado, valor/data) viram `update`
- Transação que sumiu na Pluggy: só tag `removida-na-origem` — **nunca apaga** no Sure

## Conciliação (resumo)

- Transferências próprias e pagamento de fatura: casamento por valor Decimal **exato** e até 3 dias corridos; ponta sem par → tag `pendente-revisao` + relatório (nunca vira gasto de envelope)
- Pagamento de fatura: fora dos envelopes (caixa/transferência); compra no cartão conta no envelope pela **data da compra** (competência)
- Ao fim de cada sync: compara saldo Pluggy × Sure; `|diff| > R$ 1,00` → revisão (nada é ajustado automaticamente)

## Testes

```bash
# Suíte padrão (100% unitária, sem rede real; exclui integration)
pytest -q

# Teste de integração opcional (Sure local com external_id/source)
# Com .env preenchido, basta definir SURE_TEST_ACCOUNT_ID (conta "Teste"):
# PowerShell:
#   $env:SURE_TEST_ACCOUNT_ID = "<uuid>"
#   python -m pytest -m integration tests/integration/test_sure_idempotency.py -q
SURE_TEST_ACCOUNT_ID=... pytest -m integration tests/integration/test_sure_idempotency.py
```

O teste `test_sure_idempotency_supported` **não** roda na suíte padrão (marcador `integration`). Use-o para confirmar se a imagem Docker do Sure já aceita `external_id`/`source` no POST.

## Segurança

- Segredos **somente** por env; nunca em código, logs, exceções ou fixtures versionadas
- Logs padrão: só IDs truncados, contagens e datas — sem valores/descrições/nomes
- Headers `Authorization` / `X-Api-Key` mascarados em erros HTTP

## Pacote

```
pluggy_sure/
  pluggy_client.py   # auth + contas + /v2/transactions (cursor)
  sure_client.py     # CRUD transações, categorias, tags, contas/saldo
  normalize.py       # UTC→SP, sinal BANK/CREDIT, Decimal
  categorize.py      # motor de rules.yaml
  plan.py            # create/update/noop/skip/defer/tag_missing
  reconcile.py       # pareamento + bater saldo
  state.py           # SQLite
  sync.py / cli.py
```
