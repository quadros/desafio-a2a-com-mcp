# SPEC: A Ponte, um agente A2A com MCP por dentro

> **Para quem é:** Claude Code, trabalhando no fork de `https://github.com/devfullcycle/desafio-a2a-com-mcp`, branch `main`.
> **O que é:** a especificação completa do que construir, derivada do enunciado, dos 11 arquivos de `exemplos/wire/` e da leitura do `validador/validar.py` (36 verificações). Os nomes de API citados foram conferidos no pacote **`mcp==2.2.0`** (Python).
> **Regra de ouro:** `exemplos/wire/` e `validador/validar.py` são o contrato. Se esta spec divergir deles, **eles vencem**. Registre a divergência em `docs/NOTES.md`.
> **Proibido tocar:** `dados/`, `validador/`, `exemplos/`.

---

## 0. Resumo em 10 linhas

1. Dois processos Python separados: `servidor-mcp` (porta **7301**, `POST /mcp`) e `agente` (porta **7300**, `POST /a2a` e `GET /.well-known/agent-card.json`).
2. O servidor MCP usa o SDK oficial `mcp` v2 (`MCPServer`), Streamable HTTP **stateless**, revisão **2026-07-28**.
3. São 3 tools (`listar_salas`, `consultar_disponibilidade`, `reservar_sala`) e 1 resource (`politica://uso`).
4. Em conflito de horário, `reservar_sala` responde **`resultType: "input_required"`**, com **1** elicitation form e um `requestState` **selado pelo utilitário do SDK** (`RequestStateSecurity`), com chave vinda de `REQUEST_STATE_SECRET`.
5. O agente é **cliente MCP** do próprio servidor, via HTTP e com **`allow_input_required=True`**: ele nunca responde elicitation sozinho.
6. O agente é **servidor A2A v1.0** JSON-RPC: `SendMessage` e `GetTask`.
7. **Ponte:** ao receber `input_required`, a Task vai para `TASK_STATE_INPUT_REQUIRED` com o texto `alternativas: a, b`. O `requestState` fica num `dict[taskId]`. Quando chega `escolha=<id>`, o agente refaz o `tools/call` **com id novo**, os mesmos `arguments`, `inputResponses` e o `requestState` intacto.
8. O `traceparent` do header A2A é propagado em `_meta.traceparent` de todo request MCP daquela Task. O servidor loga no stderr o método, o id e o `traceparent` de todo request.
9. Sem LLM, sem banco, sem ORM. As reservas ficam em memória no servidor MCP; as Tasks e pendências ficam em memória no agente.
10. Critério final: `python3 validador/validar.py --agente http://localhost:7300 --mcp http://localhost:7301` com **36/36 PASS** e exit code 0, com os dois processos **recém-iniciados**.

---

## 1. Stack e decisões fixas

| Item | Decisão | Motivo |
|---|---|---|
| Linguagem | **Python ≥ 3.10**, nos dois processos | Os wires do starter foram capturados com o SDK Python: a chave `__main__:escolha_de_sala` e o `requestState` com prefixo `v1.` são do `AESGCMRequestStateCodec`. O validador também é Python |
| SDK MCP | `mcp==2.2.0` (ou a versão 2.x mais recente disponível, **fixada com `==`**) | Exigência do enunciado: SDK oficial v2, alinhado a 2026-07-28 |
| Servidor HTTP do agente | `starlette` + `uvicorn` (versões fixadas), com JSON-RPC A2A **escrito à mão** | São só 2 métodos. Controle total do formato exigido pelo wire. O `a2a-sdk` é permitido, mas não é necessário |
| Gerência de dependências | Um `pyproject.toml` por pasta (`servidor-mcp/`, `agente/`) com versões **exatas**. `uv` é opcional; o fallback é `python -m venv` + `pip install -e .` | "Versões travadas" é exigência do enunciado |
| Persistência | **Memória** | O enunciado proíbe banco e ORM |
| LLM | **Nenhum** | Proibido |

> **Alternativa TypeScript:** o enunciado aceita `@modelcontextprotocol/server` (Node ≥ 20, ESM). Se escolhida, os equivalentes são: `createMcpHandler`, `inputRequired(...)` com `inputRequired.elicit(...)`, `createRequestStateCodec({ key, ttlSeconds })` e, no cliente, `allowInputRequired: true` ou `inputRequired: { autoFulfill: false }`. **Não misture stacks.** O resto desta spec vale igual.

---

## 2. Fase 0: leitura obrigatória antes de codar

1. Leia **todos** os arquivos de `exemplos/wire/` (01 a 11). Eles são o formato exato que o validador envia e espera.
2. Leia `validador/validar.py` inteiro. A seção 11 desta spec mapeia as 36 verificações.
3. Instale o SDK e confirme, lendo o código em `site-packages/mcp/`, os nomes usados nesta spec (seção 12). Anote tudo em `docs/NOTES.md`.
4. Gere um segredo local, **sem commitar**: `export REQUEST_STATE_SECRET=$(python3 -c "import secrets; print(secrets.token_hex(32))")`.

---

## 3. Estrutura do repositório

```
.
├── README.md                      # substituído (seção 10)
├── dados/  validador/  exemplos/  # NÃO ALTERAR
├── scripts/
│   ├── subir-mcp.sh               # cria venv se preciso e sobe o servidor
│   └── subir-agente.sh
├── docs/NOTES.md                  # notas de Fase 0 e evidências de SDK
├── servidor-mcp/
│   ├── pyproject.toml             # mcp==X.Y.Z, uvicorn==…, starlette==… (exatos)
│   └── servidor_mcp/
│       ├── __main__.py            # python -m servidor_mcp
│       ├── app.py                 # MCPServer, tools, resource, ASGI + log middleware
│       ├── dominio.py             # carga dos JSON, regras, alternativas, reservas em memória
│       └── log.py                 # log de request no stderr
└── agente/
    ├── pyproject.toml             # mcp==X.Y.Z, starlette==…, uvicorn==… (exatos)
    └── agente/
        ├── __main__.py            # python -m agente
        ├── a2a_http.py            # rotas: card + JSON-RPC (SendMessage, GetTask)
        ├── card.py                # Agent Card v1.0
        ├── tasks.py               # modelo de Task e máquina de estados
        ├── pedido.py              # parser do formato fixo
        ├── mcp_host.py            # cliente MCP: discovery, resource, call_tool cru
        ├── ponte.py               # NÚCLEO: suspender() / retomar()
        └── trace.py               # traceparent
```

- Os caminhos para `dados/` são resolvidos a partir da raiz do repositório (ex.: `Path(__file__).resolve().parents[2] / "dados"`), com override por `DADOS_DIR`.
- Variáveis de ambiente com defaults: `MCP_PORT=7301`, `AGENTE_PORT=7300`, `MCP_URL=http://127.0.0.1:7301/mcp`, `AGENTE_URL_PUBLICA=http://127.0.0.1:7300`. `REQUEST_STATE_SECRET` **não tem default**.

---

## 4. Domínio (servidor MCP, `dominio.py`)

### 4.1 Dados

- `salas.json` é carregado uma vez. `reservas.json` é carregado na subida numa **lista em memória**.
- Novas reservas recebem ids sequenciais `res-0003`, `res-0004`… (o próximo número é o maior existente + 1, com 4 dígitos).
- `politica-de-uso.md` é lido do disco. A versão é a primeira linha, no formato `versao: <valor>`, e o valor atual é `2026-11-01`.

### 4.2 Validações

Aplique nesta ordem. É a mesma função para `consultar_disponibilidade` e `reservar_sala`. As mensagens são **exatas**:

| # | Condição | Mensagem (`isError: true`) |
|---|---|---|
| 1 | `sala` não existe em `salas.json` | `Sala inexistente: <id informado>` |
| 2 | `fim <= inicio` | `Intervalo invalido: fim deve ser posterior a inicio` |
| 3 | `inicio` ou `fim`, convertidos para `-03:00`, fora de `[08:00, 20:00]` | `Fora da janela de uso: a politica permite reservas entre 08:00 e 20:00` |
| 4 | `fim - inicio > 2h` | `Duracao acima do limite: a politica permite no maximo 2 horas` |

- Datas em ISO 8601 com offset (`datetime.fromisoformat`). Um parse inválido gera erro de execução com mensagem clara. Isso não é testado.
- Nenhuma regra depende da data de hoje: reservar no passado é permitido.
- O erro de execução é levantado de forma que o SDK devolva `result` com `isError: true` e a mensagem no bloco de texto. O SDK pode prefixar a mensagem com `Error executing tool …:`, e isso é aceito. Use `ToolError` (`mcp.server.mcpserver.exceptions`) ou o mecanismo equivalente do SDK.

### 4.3 Conflito e alternativas

- **Sobreposição:** `a.inicio < b.fim and b.inicio < a.fim`. Intervalos que apenas se tocam não conflitam.
- **Alternativas** (regra fechada): as salas **≠ da pedida**, **livres** no intervalo, com `capacidade >= capacidade da pedida`, ordenadas por `(capacidade, id)` crescente, no máximo **3**.
- Exemplo obrigatório: `sala-garagem` de 14:00 a 15:00 em 2026-11-03 (estado inicial) resulta em `["sala-fusca", "sala-mirante"]`.
- Conflito **sem** alternativas gera `isError` com `Sem alternativas disponiveis no intervalo`, e **não há elicitation**.

---

## 5. Servidor MCP

### 5.1 Transporte e protocolo

- `MCPServer(name="central-de-salas", ...)` com `streamable_http_app(stateless_http=True, json_response=True)` (confirme a assinatura), servido por uvicorn em `0.0.0.0:7301`, caminho **`/mcp`**.
- Declare as capabilities `tools` e `resources` (o `MCPServer` faz isso ao registrar tools e resources; confira em `server/discover` ou `tools/list`).
- **Validação de `_meta`:** um request sem `io.modelcontextprotocol/protocolVersion` ou sem `io.modelcontextprotocol/clientCapabilities` recebe **`-32602` com HTTP 400**. O SDK 2.x provavelmente já faz isso no transporte moderno; **confirme com as verificações 04 e 05**. Se não fizer, adicione um middleware ASGI que valide antes de despachar. Nunca infira esses valores de um request anterior.
- Headers `MCP-Protocol-Version`, `Mcp-Method` e `Mcp-Name` divergentes do corpo recebem `-32020` (feito pelo SDK).

### 5.2 Log no stderr (obrigatório e usado pelo avaliador)

Crie um **middleware ASGI** que envolve o app MCP, lê o corpo JSON do POST e **re-injeta o corpo** para o app. Para cada request, ele escreve **uma linha no stderr**:

```
[mcp] method=tools/call id=4f2a9c1b7e3d name=reservar_sala traceparent=00-<trace-id>-<span-id>-01 retry=true
```

- Campos mínimos: `method`, `id`, `traceparent` (de `params._meta.traceparent`, ou `-` se ausente). Campos extras úteis: `name` (tool ou uri) e `retry` (`true` se `params.requestState` estiver presente).
- **Nunca** logue o `requestState` inteiro. No máximo os 8 primeiros caracteres.
- Loga também os requests que serão rejeitados, porque o log acontece antes do dispatch.
- Use `sys.stderr` / `logging` para stderr. Nada de `notifications/message`, que é o logging depreciado.

### 5.3 Tools

Os schemas de saída devem **bater com `exemplos/wire/01-tools-list.json`**. Use modelos Pydantic com os mesmos nomes de campo; os títulos gerados podem variar.

| Tool | Entrada (`inputSchema` type object) | `structuredContent` |
|---|---|---|
| `listar_salas` | sem parâmetros | `{ "salas": [ {id, nome, capacidade, recursos} ] }` (modelo `ListaDeSalas` / `SalaOut`) |
| `consultar_disponibilidade` | `sala`, `inicio`, `fim` (string, obrigatórios) | `{ "sala", "livre": bool, "conflitos": [ {id, inicio, fim, responsavel} ] }` (modelo `Disponibilidade` / `ConflitoOut`) |
| `reservar_sala` | `sala`, `inicio`, `fim`, `responsavel` (string, obrigatórios) | `ReservaOut`: `reserva`, `reservado`, `sala`, `inicio`, `fim`, `responsavel`, `politica`, `motivo` (todos anuláveis exceto `reservado`) |

Regras de saída:

- **Todas** devolvem `structuredContent` **e** um bloco de texto. Em `listar_salas`, o texto precisa ser **exatamente** o JSON serializado do `structuredContent`: o validador faz `json.loads(texto) == structuredContent`. Garanta que haja **um único** bloco de texto.
- A reserva concluída tem `reservado: true`, `reserva: "res-000N"`, os dados efetivos, `politica: "2026-11-01"` e `motivo: null`. Veja o wire 02 e o wire 04.
- A recusa (`decline` ou `cancel`) tem `resultType: "complete"`, `isError` ausente ou `false` e `structuredContent` = `{reserva: null, reservado: false, sala: null, inicio: null, fim: null, responsavel: null, politica: null, motivo: "recusado"}`. Veja o wire 11.
- Uma tool inexistente é recusada pelo próprio SDK, com `-32602` ou `isError`; os dois são aceitos.

### 5.4 Resource

- URI **`politica://uso`**, `mimeType: "text/markdown"`, conteúdo = texto integral de `dados/politica-de-uso.md` (wire 05).
- `resources/read` de URI inexistente (ex.: `politica://inexistente`) recebe **`-32602`**. É proibido devolver `contents: []`. Registre o resource como **estático**, não como template: um template `politica://{x}` capturaria URIs inexistentes.

### 5.5 MRTR em `reservar_sala`: caminho recomendado com resolver do SDK

O SDK Python 2.x tem MRTR de primeira classe via **injeção por resolver** (`mcp.server.mcpserver`: `Resolve`, `Elicit`, `ElicitationResult`). Esse caminho gera exatamente o formato do wire 03 (chave `módulo:função`), aplica o `-32021` automaticamente e sela o `requestState` pelo `RequestStateBoundary`.

Esboço (confirme as assinaturas no SDK):

```python
from typing import Annotated, Literal
from pydantic import BaseModel, Field, create_model
from mcp.server.mcpserver import MCPServer, Resolve, Elicit, ElicitationResult, RequestStateSecurity
from mcp.server.elicitation import AcceptedElicitation  # conferir o caminho de import

MENSAGEM = "A sala pedida esta ocupada nesse intervalo. Escolha uma alternativa."

def _schema_escolha(alternativas: list[str]) -> type[BaseModel]:
    # schema PLANO com uma única propriedade `sala`, restrita às alternativas.
    # >=2 alternativas: enum; 1 alternativa: o gerador pode emitir const (aceito).
    return create_model("EscolhaDeSala",
        sala=(Literal[tuple(alternativas)], Field(title="Sala", description="Sala alternativa escolhida")))

def escolha_de_sala(sala: str, inicio: str, fim: str) -> str | Elicit:
    # resolver: roda ANTES do corpo da tool, em todo round
    dominio.validar(sala, inicio, fim)            # levanta ToolError com a mensagem exata
    if dominio.livre(sala, inicio, fim):
        return sala                               # sem conflito, não pergunta
    alternativas = dominio.alternativas(sala, inicio, fim)
    if not alternativas:
        raise ToolError("Sem alternativas disponiveis no intervalo")
    return Elicit(MENSAGEM, _schema_escolha(alternativas))

@mcp.tool(name="reservar_sala", description="Reserva uma sala. Se o intervalo estiver ocupado, pergunta qual alternativa usar.")
def reservar_sala(sala: str, inicio: str, fim: str, responsavel: str,
                  escolha: Annotated[ElicitationResult[...], Resolve(escolha_de_sala)]) -> ReservaOut:
    # accept -> reserva na sala escolhida (ou na pedida, se não houve conflito)
    # decline/cancel -> ReservaOut(reservado=False, motivo="recusado") SEM isError
    ...
```

Pontos a confirmar no SDK instalado e anotar em `docs/NOTES.md`:

- Como o consumidor recebe o **desfecho completo** (accept, decline, cancel), anotando o parâmetro como `ElicitationResult[T]` em vez de `T`. Com `T` puro, um decline aborta a chamada, e a verificação 19 exige `complete` sem `isError`.
- Como o resolver devolve um valor **sem perguntar** quando não há conflito. Se o SDK exigir o mesmo tipo em todos os ramos, embrulhe o valor em `AcceptedElicitation`.
- O schema dinâmico: o SDK valida as propriedades renderizadas (`_validate_rendered_properties`); um `Literal` gera `enum` ou `const`.
- No **retry**, o resolver roda de novo. O SDK consulta a resposta gravada **só se a mesma pergunta for refeita**, com a mesma renderização. Ao reservar, **revalide** que a sala escolhida está livre e pertence às alternativas.

**Caminho alternativo, manual:** se o resolver não atender a algum item, a tool pode retornar `CallToolResult | InputRequiredResult` explicitamente e ler `ctx.input_responses` / `ctx.request_state` (texto já desselado pelo boundary). O SDK **proíbe combinar** `Resolve(...)` com retorno manual de `InputRequiredResult` na mesma tool. Nesse caminho, o próprio código precisa:

- checar `ctx.client_capabilities.elicitation.form` e levantar `MCPError(-32021, data={"requiredCapabilities": {"elicitation": {"form": {}}}})`, garantindo HTTP 400;
- montar o `requestState` com os argumentos originais e deixar o boundary do SDK selar;
- reconstruir o pedido a partir do estado no retry.

### 5.6 `requestState`: integridade (terceira fricção)

- Configure explicitamente, na criação do servidor:

```python
secret = os.environ.get("REQUEST_STATE_SECRET")
if not secret or len(bytes.fromhex(secret)) < 32:
    sys.exit("REQUEST_STATE_SECRET ausente ou com menos de 32 bytes (use: python3 -c \"import secrets; print(secrets.token_hex(32))\")")
mcp = MCPServer(
    name="central-de-salas",
    request_state_security=RequestStateSecurity(keys=[bytes.fromhex(secret)], ttl=600),  # 10 min (faixa exigida: 5 a 30)
)
```

- **Não** use o default. O default do `MCPServer` é `RequestStateSecurity.ephemeral()`: uma chave aleatória por processo, que **quebra o retry após restart** (passo 12 do avaliador).
- O boundary do SDK usa AES-256-GCM (cifra e autentica), com expiração, audience = nome do servidor e **binding de (tool, digest dos `arguments`)**. Consequências:
  - Um `requestState` adulterado recebe **`-32602`** `"Invalid or expired requestState"` (verificação 17).
  - Um retry com **argumentos adulterados** recebe **`-32602`** (verificação 18, caminho "rejeita o estado").
  - O retry funciona **após restart**, com a mesma chave e sem nada guardado em memória no servidor.
- O mesmo `requestState` **pode ser reapresentado** mais de uma vez: a verificação 19 reutiliza o estado da 13 para um decline. **Não implemente uso único (nonce).**
- **Nada de segredo no código, no README ou em `.env` commitado.** Garanta que `.gitignore` cubra `.env`.

### 5.7 Capability de elicitation (segunda fricção)

- Vale apenas a elicitation em **form mode**: `_meta["io.modelcontextprotocol/clientCapabilities"].elicitation.form`.
- Se o conflito exigir pergunta e o cliente não declarou essa capability, a resposta é **`-32021`**, `data.requiredCapabilities = {"elicitation": {"form": {}}}` e **HTTP 400** (wire 06, verificação 15). No caminho de resolver, o SDK faz isso.
- Sem conflito, a tool funciona mesmo sem a capability.

### 5.8 Proibido (primeira fricção)

- **Nunca** chamar `ctx.elicit(...)`, `ctx.session.elicit…`, `create_message` ou `list_roots` de forma síncrona dentro da tool. No transporte stateless não há canal de volta, e isso gera erro. A pergunta sempre sai como **fim da resposta**, com `resultType: "input_required"`.

---

## 6. Agente: lado MCP (host)

### 6.1 Cliente

- Use o cliente oficial do SDK (`mcp.client`) sobre Streamable HTTP apontando para `MCP_URL`, com protocolo **2026-07-28**. Manter o objeto cliente vivo entre chamadas é permitido; nenhum estado de protocolo pode ser inferido de chamadas anteriores.
- **Auto-fulfill desligado.** Toda chamada de `tools/call` usa `client.session.call_tool(name, arguments, input_responses=..., request_state=..., meta=..., allow_input_required=True)`, que devolve `CallToolResult | InputRequiredResult`. **Nunca** use `Client.call_tool(...)` de alto nível, que roda o driver `run_input_required_driver` e responde sozinho.
- **Capability declarada:** no SDK 2.2.0, `ClientSession._build_capabilities` só declara elicitation se houver `elicitation_callback` não-default, e nesse caso declara `form` **e** `url`. O `_meta` de capabilities é **sobrescrito** pelo SDK (não dá para mudar via `meta=`). Faça assim:
  1. Registre um `elicitation_callback` que **levanta exceção** ("a ponte nunca responde elicitation"). Ele nunca deve ser chamado; é uma defesa contra regressão.
  2. Para declarar **exatamente** `{"elicitation": {"form": {}}}`, sobrescreva `_build_capabilities` numa subclasse mínima de `ClientSession` que retorne `ClientCapabilities(elicitation=ElicitationCapability(form=FormElicitationCapability()))`. Se isso não for viável sem reescrever o protocolo, mantenha `form + url` e documente no README com o trecho do SDK como evidência.
- Os headers `MCP-Protocol-Version`, `Mcp-Method` e `Mcp-Name` são aplicados pelo SDK. Confirme no log do servidor.
- **Id novo em cada request:** o `ClientSession` gera ids próprios por request. Confirme no stderr que o retry tem id diferente do inicial.

### 6.2 Descoberta e resource

- Na **primeira Task** (lazy, com o `traceparent` dessa Task), faça `tools/list` e guarde o catálogo em cache. Antes de chamar `reservar_sala`, confira que ela está no catálogo. **Não** carregue uma lista fixa de tools. Se a tool não existir, a Task vai para `FAILED`.
- Leia `resources/read politica://uso` (lazy, em cache) e extraia a versão da primeira linha (`versao: <v>`). É esse valor, **lido do resource**, que vai no campo `politica` do artifact.
- O log do servidor deve mostrar `tools/list` **antes** do primeiro `tools/call` do agente.

### 6.3 `_meta` de todo request MCP do agente

```json
{
  "io.modelcontextprotocol/protocolVersion": "2026-07-28",
  "io.modelcontextprotocol/clientInfo": {"name": "agente-central-de-salas", "version": "1.0.0"},
  "io.modelcontextprotocol/clientCapabilities": {"elicitation": {"form": {}}},
  "traceparent": "00-<trace-id da Task>-<span-id novo>-01"
}
```

### 6.4 Tipo devolvido à ponte (`mcp_host.py`)

```python
@dataclass
class Completo:      estruturado: dict; texto: str            # resultType complete, isError falso
@dataclass
class ErroTool:      mensagem: str                            # complete + isError true (texto integral dos blocos)
@dataclass
class PedeInput:     chave: str; alternativas: list[str]; request_state: str   # input_required
@dataclass
class ErroProtocolo: codigo: int; mensagem: str               # JSON-RPC error (MCPError)
```

- A decisão é feita pelo **tipo ou `resultType`** (`InputRequiredResult`), **não** pela presença de campos.
- As `alternativas` vêm do `requestedSchema.properties.sala.enum`, ou `[const]` se vier `const`, **na ordem recebida**. O agente **não calcula** alternativas: isso é domínio do servidor.
- `PedeInput` **sem** `input_requests` (apenas estado) gera retry imediato com o mesmo `request_state`. É raro aqui, mas trate.

---

## 7. Agente: lado A2A (servidor)

### 7.1 Agent Card (`GET /.well-known/agent-card.json`)

Igual ao wire 07, com a URL vinda de `AGENTE_URL_PUBLICA`:

```json
{
  "name": "Central de Salas",
  "description": "Reserva salas de reuniao da Hill Valley Tech.",
  "provider": {"organization": "Hill Valley Tech", "url": "https://hillvalley.example"},
  "version": "1.0.0",
  "supportedInterfaces": [{"url": "http://127.0.0.1:7300/a2a", "protocolBinding": "JSONRPC", "protocolVersion": "1.0"}],
  "capabilities": {"streaming": false, "pushNotifications": false, "extendedAgentCard": false},
  "defaultInputModes": ["text/plain"],
  "defaultOutputModes": ["text/plain"],
  "skills": [{
    "id": "reservar-sala",
    "name": "Reservar sala",
    "description": "Reserva uma sala em um intervalo. Se houver conflito, pergunta qual alternativa usar.",
    "tags": ["salas", "agenda"],
    "inputModes": ["text/plain"],
    "outputModes": ["text/plain"],
    "examples": ["reservar sala=sala-garagem inicio=2026-11-03T14:00:00-03:00 fim=2026-11-03T15:00:00-03:00 responsavel=Marty"]
  }]
}
```

- O card **não** usa `url`, `preferredTransport`, `additionalInterfaces` nem `interfaces`, que são campos da v0.x.
- Não tem `securitySchemes` e não menciona MCP. Essa é a opacidade.

### 7.2 JSON-RPC em `POST /a2a`

| Método | Params | Resposta |
|---|---|---|
| `SendMessage` | `{"message": {messageId, role: "ROLE_USER", parts: [{"text": ...}], taskId?}}` | `{"result": {"task": Task}}` |
| `GetTask` | `{"id": "<taskId>"}` | `{"result": {"task": Task}}` |
| Outro método | — | `error -32601` (Method not found) |

Erros de negócio A2A (a verificação 31 só exige que exista `error`):

| Situação | `error.code` | `message` |
|---|---|---|
| `taskId` desconhecido | `-32001` | `Task not found` |
| `taskId` em estado terminal (`COMPLETED`, `CANCELED`, `FAILED`) | `-32004` | `Task em estado terminal nao aceita novas mensagens` |
| Params ou JSON inválidos | `-32602` / `-32700` | — |

### 7.3 Task

```json
{
  "id": "task-<12 hex>", "contextId": "ctx-<12 hex>",
  "status": {"state": "TASK_STATE_…", "message": {"messageId": "msg-…", "role": "ROLE_AGENT", "parts": [{"text": "…"}], "taskId": "…", "contextId": "…"}},
  "history": [ /* mensagens do usuário e do agente, em ordem */ ],
  "artifacts": []
}
```

- Máquina de estados: `SUBMITTED → WORKING → (COMPLETED | FAILED | INPUT_REQUIRED)`; `INPUT_REQUIRED → WORKING → (COMPLETED | CANCELED | FAILED | INPUT_REQUIRED)`.
- **Estado terminal é definitivo.** Implemente isso como uma função `transicionar(task, novo)` que recusa sair de um estado terminal.
- Toda transição é logada no stderr do agente: `[a2a] task=… state=… trace=…`.
- `GetTask` devolve o estado corrente a qualquer momento.
- Mensagens do agente, **exatas** onde indicado:

| Estado | `status.message` texto |
|---|---|
| `INPUT_REQUIRED` | **`alternativas: <id1>, <id2>`** (separador `", "`, na ordem do enum, sem nenhum outro texto) |
| `COMPLETED` | `Reserva <res-id> confirmada na <sala>.` |
| `CANCELED` | `Reserva recusada pelo solicitante.` |
| `FAILED` (erro da tool) | **o texto integral da tool**, ex.: `Sala inexistente: sala-delorean` (com ou sem prefixo do SDK) |
| `FAILED` (formato inválido) | `Pedido invalido: use reservar sala=<id> inicio=<iso8601> fim=<iso8601> responsavel=<nome>` |

- Toda mensagem do usuário e do agente entra em `history`.

### 7.4 Artifact de sucesso

```json
{"artifactId": "art-<12 hex>", "name": "reserva",
 "parts": [{"text": "{\"reserva\": \"res-0005\", \"sala\": \"sala-mirante\", \"inicio\": \"…\", \"fim\": \"…\", \"responsavel\": \"Marty\", \"politica\": \"2026-11-01\"}"}]}
```

- O conteúdo é `json.dumps` com exatamente essas 6 chaves, tiradas do `structuredContent` da tool. **Exceção:** `politica` vem da versão lida do resource pelo agente.

### 7.5 Parser do formato fixo (`pedido.py`)

- Pedido: `^reservar sala=(\S+) inicio=(\S+) fim=(\S+) responsavel=(.+)$` (após `strip()`). O agente **não valida** sala, datas nem política; ele só repassa ao MCP.
- Continuação: `^escolha=(\S+)$`. `recusar` é palavra reservada.
- O texto vem da concatenação das parts com `text`.

---

## 8. A Ponte (`ponte.py`): o núcleo avaliado

### 8.1 Estado em memória

```python
@dataclass
class Pendencia:
    chave: str                   # chave do inputRequests, devolvida idêntica
    alternativas: list[str]      # ordem do enum
    request_state: str           # OPACO: nunca decodificar, parsear ou modificar
    tool: str                    # "reservar_sala"
    arguments: dict              # EXATAMENTE os do tools/call original

tasks: dict[str, Task] = {}
pendencias: dict[str, Pendencia] = {}      # chave = task.id  (estado pausado é POR TASK)
locks: dict[str, asyncio.Lock] = {}        # serializa mensagens da mesma Task
trace_da_task: dict[str, str] = {}         # trace-id associado à Task
```

### 8.2 Algoritmo

```
SendMessage(msg, header_traceparent):
  trace = trace_id(header_traceparent) or trace_da_task.get(msg.taskId) or novo_trace_id()

  if not msg.taskId:                                   # ---- Task nova
      task = criar(SUBMITTED); history += msg; trace_da_task[task.id] = trace
      pedido = parse(msg)                              # inválido → FAILED
      transicionar(task, WORKING)
      await garantir_discovery(trace)                  # tools/list + resources/read (cache)
      args = {sala, inicio, fim, responsavel}
      return await executar(task, "reservar_sala", args, None, None, trace)

  task = tasks.get(msg.taskId) or erro -32001
  async with locks[task.id]:
    if task terminal: erro -32004
    if task.state != INPUT_REQUIRED: erro -32004
    history += msg
    p = pendencias[task.id]
    escolha = parse_escolha(msg)
    if escolha == "recusar":   resposta = {"action": "decline"}
    elif escolha in p.alternativas: resposta = {"action": "accept", "content": {"sala": escolha}}
    else:                                              # fora do enum ou texto inválido
        repetir mensagem "alternativas: …" (novo messageId); continua INPUT_REQUIRED
        return task
    del pendencias[task.id]                            # consome ANTES do retry
    transicionar(task, WORKING)
    return await executar(task, p.tool, p.arguments, {p.chave: resposta}, p.request_state, trace,
                          recusou=(escolha == "recusar"))

executar(task, tool, args, input_responses, request_state, trace, recusou=False):
  r = await mcp_host.call_tool(tool, args, input_responses, request_state, traceparent=novo_span(trace))
  match r:
    Completo(e) if recusou or e.get("reservado") is False:
        transicionar(task, CANCELED, "Reserva recusada pelo solicitante.")
    Completo(e):
        task.artifacts = [artifact(e, politica_do_resource)]
        transicionar(task, COMPLETED, f"Reserva {e['reserva']} confirmada na {e['sala']}.")
    ErroTool(m):      transicionar(task, FAILED, m)
    ErroProtocolo(c, m): transicionar(task, FAILED, f"Erro de protocolo MCP {c}: {m}")
    PedeInput(chave, alts, rs):
        pendencias[task.id] = Pendencia(chave, alts, rs, tool, args)      # grava ANTES de responder
        transicionar(task, INPUT_REQUIRED, "alternativas: " + ", ".join(alts))
  return task
```

### 8.3 Mapeamento de estados

| Evento MCP | Estado da Task A2A |
|---|---|
| (antes do `tools/call`) | `SUBMITTED` → `WORKING` |
| `InputRequiredResult` com a elicitation | `INPUT_REQUIRED` + `alternativas: …` |
| `complete`, `reservado: true` | `COMPLETED` + artifact `reserva` |
| `complete`, `reservado: false` (após `escolha=recusar` → `decline`) | `CANCELED` |
| `complete` + `isError: true` | `FAILED` com o texto da tool |
| JSON-RPC `error` | `FAILED` |

### 8.4 Invariantes (cada uma com teste)

| # | Invariante | Verificação |
|---|---|---|
| I1 | O agente nunca preenche `inputResponses` sozinho: só `escolha=` do cliente A2A as origina | 27–30 |
| I2 | `SendMessage` retorna assim que a Task pausa, sem bloquear esperando o usuário | 27 |
| I3 | A pendência é gravada **antes** da resposta A2A | — |
| I4 | O `requestState` é ecoado **byte a byte**, sem decode, parse, strip nem re-encode | 30 |
| I5 | O retry usa **id JSON-RPC diferente** do inicial | log (passo 6 do avaliador) |
| I6 | O retry usa **os mesmos** `name` e `arguments` do original, e a **mesma chave** em `inputResponses` | 30 |
| I7 | A pendência é por `taskId`: duas Tasks pausadas não trocam de estado | 33 |
| I8 | O `requestState` nunca aparece no card, no artifact, na mensagem, no history nem nos erros A2A | 34 |
| I9 | Uma escolha fora do enum não chama o MCP e repete as alternativas | 29 |
| I10 | Uma Task terminal nunca volta a `WORKING`; nova mensagem gera erro JSON-RPC | 31 |
| I11 | Determinismo: o mesmo pedido gera a mesma mensagem de pausa | 36 |

---

## 9. Tracing (`traceparent`)

1. O agente lê o header HTTP `traceparent` de **cada** POST A2A e extrai o trace-id (formato W3C `00-<32hex>-<16hex>-<2hex>`). Se o header vier ausente numa continuação, ele usa o trace-id guardado da Task. Se não houver nenhum, gera um novo e loga isso.
2. Em **todo** request MCP daquela Task (`tools/list`, `resources/read`, `tools/call`, retry), o `_meta.traceparent` leva **o mesmo trace-id** e um **span-id novo** (`secrets.token_hex(8)`).
3. O servidor MCP loga o `traceparent` recebido (seção 5.2).
4. **Critério:** `grep <trace-id impresso pelo validador>` no stderr do servidor MCP encontra `tools/call` do agente, incluindo o par inicial + retry de uma reserva pausada, com ids diferentes.
5. Spans e atributos OpenTelemetry estão **fora de escopo**.

---

## 10. README.md (substitui o do starter)

Seções **obrigatórias**, com estes títulos:

1. **Como rodar:** a partir de um clone limpo:
   ```bash
   python3 --version                          # >= 3.10
   export REQUEST_STATE_SECRET=$(python3 -c "import secrets; print(secrets.token_hex(32))")
   ./scripts/subir-mcp.sh                     # terminal 1 (stderr visível), porta 7301
   ./scripts/subir-agente.sh                  # terminal 2, porta 7300
   python3 validador/validar.py --agente http://localhost:7300 --mcp http://localhost:7301
   ```
   Explique que o **mesmo** `REQUEST_STATE_SECRET` precisa ser usado ao reiniciar o servidor. Inclua os exemplos `curl` para o card, o wire 08, `GetTask` e a continuação.
2. **Onde a ponte acontece:** um parágrafo com os caminhos e funções exatos:
   - onde `InputRequiredResult` vira `TASK_STATE_INPUT_REQUIRED` (`agente/agente/ponte.py::executar`, ramo `PedeInput`);
   - onde o `requestState` volta ao servidor (`agente/agente/ponte.py::retomar` → `mcp_host.call_tool(..., request_state=p.request_state)`).
3. **Decisões técnicas:**
   - `requestState` selado por `RequestStateSecurity(keys=[REQUEST_STATE_SECRET])` (AES-256-GCM, binding de tool e argumentos, audience), com TTL de 10 min;
   - por que não usar a chave efêmera;
   - estado das Tasks e pendências em `dict` no agente, perdido no restart, o que é aceitável no escopo;
   - reservas em memória no servidor;
   - auto-fulfill desligado;
   - sem LLM;
   - limitações de SDK encontradas, com trecho de evidência (ex.: capabilities `form + url`).
4. **Saída do validador:** a saída **completa** da última execução, em bloco de código, incluindo a linha do trace-id e `resumo: 36 passaram, 0 falharam, de 36 verificacoes`.

**Nunca** inclua o valor do segredo.

---

## 11. Mapa das 36 verificações do validador

> O validador roda **na ordem** e o estado de reservas **se acumula** no mesmo processo. Sempre rode com os dois processos **recém-iniciados**.

| # | O que verifica | Requisito |
|---|---|---|
| 01 | `tools/list` traz as 3 tools | 5.3 |
| 02 | Toda tool com `inputSchema.type == "object"` | 5.3 |
| 03 | `listar_salas`: `json.loads(texto) == structuredContent` | 5.3 |
| 04 | Sem `protocolVersion` → `-32602` + HTTP 400 | 5.1 |
| 05 | Sem `clientCapabilities` → `-32602` + HTTP 400 | 5.1 |
| 06 | Tool `voar_delorean` recusada (`-32602` ou `isError`) | 5.3 |
| 07 | `politica://uso` contém `2026-11-01` | 5.4 |
| 08 | `politica://inexistente` → `-32602` | 5.4 |
| 09 | `reservar_sala` com `sala-delorean` → `Sala inexistente: sala-delorean` | 4.2 |
| 10 | `consultar_disponibilidade` 07:00–08:00 → janela | 4.2 |
| 11 | `consultar_disponibilidade` 09:00–12:00 → duração | 4.2 |
| 12 | `consultar_disponibilidade` 10:00–09:00 → intervalo inválido | 4.2 |
| 13 | Garagem 14–15 → `input_required`, 1 chave, `requestState` presente | 5.5 |
| 14 | `mode == "form"` e enum `["sala-fusca","sala-mirante"]` | 4.3, 5.5 |
| 15 | O mesmo pedido com `clientCapabilities: {}` → `-32021` + `requiredCapabilities` + HTTP 400 | 5.7 |
| 16 | Fusca 16–17 (conflito com res-0002) → retry com `accept sala-garagem` → `complete` na garagem | 5.5 |
| 17 | `requestState` com os 6 últimos caracteres trocados → `-32602` | 5.6 |
| 18 | Retry com argumentos adulterados (mirante 13–14, Biff) → erro **ou** valores selados (09:00, Doc) | 5.6 |
| 19 | O `requestState` da 13, reusado com `decline` → `complete`, sem `isError`, `reservado: false` | 5.3, 5.6 (sem nonce) |
| 20 | Mirante 11–12 duas vezes → a segunda dá `Sem alternativas disponiveis no intervalo` | 4.3 |
| 21 | Card 200 com JSON | 7.1 |
| 22 | `supportedInterfaces[]` com `protocolBinding` JSONRPC, `url` e `protocolVersion` 1.0 | 7.1 |
| 23 | Skill `reservar-sala` | 7.1 |
| 24 | Porão 09–10 livre → `COMPLETED` | 8.2 |
| 25 | Artifact `reserva` com `politica == "2026-11-01"` e `sala == "sala-porao"` | 7.4 |
| 26 | `GetTask {"id"}` → `id`, `contextId`, estado `COMPLETED` | 7.2 |
| 27 | Garagem 14–15 (Marty) → `INPUT_REQUIRED` | 8.2 |
| 28 | Mensagem contém `alternativas: sala-fusca, sala-mirante` | 7.3 |
| 29 | `escolha=sala-aquario` (fora do enum) → continua `INPUT_REQUIRED` | I9 |
| 30 | `escolha=sala-fusca` → `COMPLETED` com artifact na fusca | 8.2 |
| 31 | Nova mensagem para a Task concluída → `error` | I10 |
| 32 | Garagem 14:30–15:30 → pausa → `escolha=recusar` → `CANCELED` | 8.2 |
| 33 | Duas Tasks pausadas (fusca 16–17 e garagem 14–15), ambas escolhendo `sala-mirante` → cada uma com a sua reserva | I7 (aqui o enum pode ter **um só** elemento; trate `const`) |
| 34 | Os primeiros 40 caracteres do `requestState` da 13 não aparecem em nenhuma resposta A2A | I8 |
| 35 | `sala-delorean` via A2A → `FAILED` com `Sala inexistente: sala-delorean` em `status.message` | 7.3 |
| 36 | O mesmo pedido (porão 09–10, já ocupado) duas vezes → a mesma mensagem, começando com `alternativas:` | I11 |

---

## 12. Referência de API do SDK Python (conferida em `mcp==2.2.0`)

| Necessidade | API |
|---|---|
| Servidor de alto nível | `mcp.server.mcpserver.MCPServer(name=..., request_state_security=...)` |
| HTTP stateless | `MCPServer.streamable_http_app(json_response=..., stateless_http=True)` / `run(..., stateless_http=True)` |
| MRTR via resolver | `Resolve`, `Elicit(message, schema: type[BaseModel])`, `ElicitationResult` (em `mcp.server.mcpserver`) |
| Desfechos | `AcceptedElicitation`, `DeclinedElicitation`, `CancelledElicitation` (`mcp.server.elicitation`) |
| MRTR manual | Retorno `InputRequiredResult`; `ctx.input_responses`, `ctx.request_state`, `ctx.client_capabilities` |
| Selagem do estado | `RequestStateSecurity(keys=[...], ttl=...)`; erro na verificação: `-32602` `"Invalid or expired requestState"` |
| Erro de tool | `mcp.server.mcpserver.exceptions.ToolError` |
| Cliente cru | `ClientSession.call_tool(name, arguments, *, input_responses, request_state, meta, allow_input_required=True)` |
| Driver automático (**não usar** na ponte) | `Client.call_tool` / `run_input_required_driver` |

---

## 13. Plano de execução

| Etapa | Entrega | Saída |
|---|---|---|
| 0 | Fase 0 (seção 2) + `docs/NOTES.md` | APIs confirmadas |
| 1 | Servidor: bootstrap stateless, log no stderr, `listar_salas` | Verificações 01–05 |
| 2 | `consultar_disponibilidade`, domínio e 4 erros; resource | 06–12 |
| 3 | `reservar_sala` no caminho feliz | wire 02 reproduzido por `curl` |
| 4 | MRTR: elicitation, `RequestStateSecurity`, `-32021`, decline, sem alternativas | 13–20; restart manual (passo 12 do avaliador) |
| 5 | Agente como cliente MCP puro (script): discovery, resource, ciclo MRTR respondido à mão | Log com `tools/list` antes de `tools/call` e ids diferentes no retry |
| 6 | A2A: card, `SendMessage`, `GetTask`, máquina de estados | 21–26, 31, 35 |
| 7 | Ponte | 27–30, 32–34, 36 |
| 8 | Tracing | `grep` do trace-id no stderr do MCP |
| 9 | README e scripts | Clone limpo + README = 36/36 |
| 10 | Checklist final (seção 14) | Push em `main` |

---

## 14. Definition of Done

- [ ] `python3 validador/validar.py --agente http://localhost:7300 --mcp http://localhost:7301` retorna **36/36** e exit 0, com os processos recém-iniciados
- [ ] `git diff upstream/main -- dados validador exemplos` vazio
- [ ] Nenhuma chamada síncrona de servidor para cliente no servidor MCP (`grep -rn "\.elicit(\|create_message\|list_roots" servidor-mcp` sem uso na tool)
- [ ] `RequestStateSecurity(keys=[REQUEST_STATE_SECRET])`: sem chave efêmera, sem segredo no repositório, falha na subida sem a variável
- [ ] Retry do MCP funciona após reiniciar o servidor (passo 12 do avaliador)
- [ ] Cliente MCP do agente usa `allow_input_required=True`; o `elicitation_callback` levanta exceção
- [ ] O stderr do MCP mostra `tools/list` antes do primeiro `tools/call`, o trace-id do validador e ids diferentes no par inicial + retry
- [ ] O `requestState` não aparece em nenhuma resposta A2A
- [ ] Sem dependência de LLM nos `pyproject.toml`; todas as versões fixadas com `==`
- [ ] README com as 4 seções e a saída completa do validador
- [ ] Teste final: clonar o fork numa pasta limpa, seguir só o README e percorrer os 13 passos do "Fluxo do avaliador"
