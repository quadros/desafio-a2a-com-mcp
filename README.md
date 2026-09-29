# A Ponte: um agente A2A com MCP por dentro

Entrega do desafio "A Ponte" (MBA Engenharia de Software com IA — curso de MCP e A2A).
Dois processos Python: um servidor MCP (Streamable HTTP, porta `7301`) que expõe as
salas da Hill Valley Tech, e um agente que é cliente desse servidor por dentro e
servidor A2A por fora (porta `7300`).

## Como rodar

Requer Python 3.10+.

```bash
python3 --version   # >= 3.10

# Gere o segredo de integridade do requestState (nunca comite este valor):
export REQUEST_STATE_SECRET=$(python3 -c "import secrets; print(secrets.token_hex(32))")

# Terminal 1 — servidor MCP, porta 7301, com o stderr visível
./scripts/subir-mcp.sh

# Terminal 2 — agente, porta 7300 (não precisa do segredo)
./scripts/subir-agente.sh

# Terminal 3 — validador do starter
python3 validador/validar.py --agente http://localhost:7300 --mcp http://localhost:7301
```

Os scripts criam o `venv` de cada pasta na primeira execução (`servidor-mcp/.venv` e
`agente/.venv`) e instalam as dependências travadas do respectivo `pyproject.toml`.
Se preferir rodar à mão, sem os scripts:

```bash
cd servidor-mcp && python3 -m venv .venv && ./.venv/bin/pip install -e . && REQUEST_STATE_SECRET=... ./.venv/bin/python -m servidor_mcp
cd agente        && python3 -m venv .venv && ./.venv/bin/pip install -e . && ./.venv/bin/python -m agente
```

**Importante:** para reiniciar o servidor MCP e um `requestState` emitido antes do
restart continuar válido, exporte o **mesmo** valor de `REQUEST_STATE_SECRET` nos dois
processos (antes e depois do restart). Variáveis de ambiente com valor padrão:
`MCP_PORT=7301`, `AGENTE_PORT=7300`, `MCP_URL=http://127.0.0.1:7301/mcp`,
`AGENTE_URL_PUBLICA=http://127.0.0.1:7300`. `REQUEST_STATE_SECRET` não tem padrão — o
servidor recusa subir sem ele.

### Exemplos com `curl`

Agent Card:

```bash
curl -s http://localhost:7300/.well-known/agent-card.json | python3 -m json.tool
```

`SendMessage` pedindo uma sala ocupada (o corpo é o de `exemplos/wire/08-a2a-send-message.json`):

```bash
curl -s -X POST http://localhost:7300/a2a -H "Content-Type: application/json" \
  -H "traceparent: 00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01" \
  -d '{"jsonrpc":"2.0","id":1,"method":"SendMessage","params":{"message":{"messageId":"msg-1","role":"ROLE_USER","parts":[{"text":"reservar sala=sala-garagem inicio=2026-11-03T14:00:00-03:00 fim=2026-11-03T15:00:00-03:00 responsavel=Marty"}]}}}'
```

A resposta traz a Task em `TASK_STATE_INPUT_REQUIRED` com `id` da Task. `GetTask`:

```bash
curl -s -X POST http://localhost:7300/a2a -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":2,"method":"GetTask","params":{"id":"<id da task acima>"}}'
```

Continuação, escolhendo uma alternativa:

```bash
curl -s -X POST http://localhost:7300/a2a -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":3,"method":"SendMessage","params":{"message":{"messageId":"msg-2","role":"ROLE_USER","parts":[{"text":"escolha=sala-fusca"}],"taskId":"<id da task>"}}}'
```

## Onde a ponte acontece

O `input_required` do MCP vira `TASK_STATE_INPUT_REQUIRED` em
`agente/agente/ponte.py::_executar`, no ramo `isinstance(resultado, mcp_host.PedeInput)`:
a pendência (`chave`, `alternativas`, `request_state`, `tool`, `arguments`) é gravada em
`_pendencias[task.id]` **antes** de transicionar a Task, e a mensagem de status vira
exatamente `"alternativas: " + ", ".join(alternativas)`. O `requestState` volta ao
servidor em `agente/agente/ponte.py::enviar_mensagem` (ramo de continuação): ele é lido
de `_pendencias[task_id].request_state`, ecoado sem nenhuma decodificação para
`mcp_host.call_tool(..., request_state=pendencia.request_state)`
(`agente/agente/mcp_host.py::call_tool`), que o repassa cru em `params.requestState` de
um `tools/call` com **id de JSON-RPC novo**. Do lado do servidor, o mesmo ciclo nasce em
`servidor-mcp/servidor_mcp/app.py::escolha_de_sala` (o resolver `Resolve`/`Elicit` de
`reservar_sala`): quando há conflito e alternativas, ele devolve `Elicit(mensagem, schema)`
e o próprio SDK (`RequestStateBoundary`, ver `docs/NOTES.md`) sela o `requestState` e
monta o `inputRequests`.

O agente nunca decide nada de domínio: `mcp_host.py` só traduz o resultado cru do MCP
(`Completo` / `ErroTool` / `PedeInput` / `ErroProtocolo`) para a ponte, que só conhece
estado de protocolo (Task, `requestState` opaco), nunca a regra de conflito ou a lista de
alternativas — essa lista vem pronta do `requestedSchema.properties.sala.enum` que o
servidor calculou.

## Decisões técnicas

- **Integridade do `requestState`:** protegido por
  `RequestStateSecurity(keys=[REQUEST_STATE_SECRET], ttl=600)` — AES-256-GCM (cifra e
  autentica) com HKDF-SHA256, aplicado pelo próprio SDK via `RequestStateBoundary`. O
  envelope selado liga o token à dupla `(tool, digest(arguments))` e a uma expiração;
  qualquer bit alterado, ou um retry com argumentos diferentes dos originais, falha na
  verificação com `-32602 "Invalid or expired requestState"` — verificado manualmente
  (ver seção seguinte) trocando os últimos caracteres do token. **TTL de 10 minutos**
  (`servidor-mcp/servidor_mcp/segredo.py::TTL_SEGUNDOS`), dentro da faixa exigida de 5 a
  30 minutos. A chave nunca é gerada no código: `REQUEST_STATE_SECRET` é lida de
  variável de ambiente e validada (mínimo 32 bytes); a ausência ou um valor curto faz o
  processo recusar subir. Não usamos a chave efêmera padrão do `MCPServer`
  (`RequestStateSecurity.ephemeral()`) porque ela é gerada de novo a cada processo e
  invalidaria qualquer `requestState` pendente depois de um restart.
- **Estado das Tasks e pendências:** dicionários em memória no processo do agente
  (`agente/agente/ponte.py`: `_tasks`, `_pendencias`, `_trace_da_task`, um
  `asyncio.Lock` por Task em `_locks`). Não sobrevive a um restart do agente — fora de
  escopo do desafio, que só exige sobrevivência do `requestState` (que mora no servidor
  MCP, selado, não no agente).
- **Reservas do servidor MCP:** lista em memória (`servidor_mcp/dominio.py`), carregada
  de `dados/reservas.json` na subida; novas reservas recebem id sequencial `res-000N`.
  Não sobrevivem a um restart, por decisão explícita do enunciado.
- **Sessão MCP única no agente:** `agente/agente/mcp_host.py` mantém uma única
  `ClientSession` viva durante todo o processo (aberta/fechada no lifespan do
  Starlette), em vez de abrir uma conexão por chamada. Isso não é só uma otimização: o
  gerador de id de JSON-RPC do SDK reinicia em zero a cada nova `ClientSession`; com uma
  conexão por chamada, o par inicial/retry de uma reserva pausada poderia colidir no
  mesmo id por coincidência (dois contadores distintos, cada um começando do zero). Uma
  sessão única garante um contador sempre crescente, satisfazendo a exigência de que o
  retry use um id diferente do request inicial mesmo que a pausa dure horas.
- **Auto-fulfill desligado:** todo `tools/call` do agente usa
  `session.call_tool(..., allow_input_required=True)`; o `elicitation_callback`
  registrado levanta exceção e nunca deve ser de fato chamado — é uma defesa contra
  regressão, não o caminho normal.
- **Sem LLM:** o parser do pedido (`agente/agente/pedido.py`) é uma expressão regular
  sobre o formato fixo `reservar sala=<id> inicio=<iso8601> fim=<iso8601>
  responsavel=<nome>`; toda decisão de conflito, política e alternativas é do servidor
  MCP. Nenhum `pyproject.toml` depende de SDK de provedor de LLM.
- **Limitação de SDK documentada:** o agente não consegue declarar exatamente
  `{"elicitation": {"form": {}}}` via a API pública do `ClientSession` — o SDK sempre
  declara `{"form": {}, "url": {}}` quando um `elicitation_callback` não-default está
  registrado (não há como sobrescrever isso via `meta=` em `call_tool`/`list_tools`, o
  SDK reescreve essa chave a cada request). O trecho de evidência e a análise completa
  estão em `docs/NOTES.md`. O servidor aceita esse formato normalmente, porque considera
  a capability de form satisfeita sempre que `elicitation.form` está presente.

## Saída do validador

Execução com os dois processos recém-iniciados (`REQUEST_STATE_SECRET` gerado na hora):

```
trace-id desta execucao: fb68ccc3b3e5a76b6dbfc86dc6e389d1
procure esse valor no stderr do servidor MCP para conferir a propagacao do traceparent.

PASS 01 tools/list traz as tres tools
PASS 02 toda tool tem inputSchema de objeto
PASS 03 listar_salas devolve structuredContent e o mesmo JSON em texto
PASS 04 _meta sem protocolVersion devolve -32602 e HTTP 400
PASS 05 _meta sem clientCapabilities devolve -32602 e HTTP 400
PASS 06 tool inexistente e recusada, por -32602 ou por isError
PASS 07 resources/read de politica://uso devolve a politica
PASS 08 resources/read de URI inexistente devolve -32602
PASS 09 sala inexistente devolve isError com a mensagem exata
PASS 10 fora da janela devolve isError com a mensagem exata
PASS 11 duracao acima de 2h devolve isError com a mensagem exata
PASS 12 intervalo invertido devolve isError com a mensagem exata
PASS 13 conflito devolve input_required com inputRequests e requestState
PASS 14 a elicitation e form mode e oferece as alternativas na ordem certa
PASS 15 conflito sem a capability elicitation devolve -32021 e HTTP 400
PASS 16 retry com inputResponses e requestState conclui a reserva
PASS 17 requestState adulterado e rejeitado com -32602
PASS 18 argumentos adulterados no retry nao tomam efeito
PASS 19 recusa conclui sem reservar e sem isError
PASS 20 conflito sem alternativa possivel devolve isError com a mensagem exata

PASS 21 agent card responde 200 no well-known com JSON
PASS 22 o card declara a interface JSON-RPC com url e versao 1.0
PASS 23 o card declara a skill reservar-sala
PASS 24 SendMessage com sala livre conclui a Task
PASS 25 o artifact chama reserva e traz a versao da politica
PASS 26 GetTask devolve id, contextId e estado corrente
PASS 27 SendMessage com sala ocupada pausa a Task
PASS 28 a Task pausada lista as alternativas na ordem certa
PASS 29 escolha fora do enum mantem a Task pausada
PASS 30 a continuacao conclui a Task na sala escolhida
PASS 31 SendMessage em Task terminal e recusado
PASS 32 a recusa termina a Task em CANCELED
PASS 33 duas Tasks pausadas ao mesmo tempo concluem cada uma com a sua reserva
PASS 34 nenhuma resposta A2A carrega o requestState
PASS 35 sala inexistente termina a Task em FAILED com a mensagem da tool
PASS 36 o agente e deterministico: o mesmo pedido produz a mesma pausa

resumo: 36 passaram, 0 falharam, de 36 verificacoes
```
