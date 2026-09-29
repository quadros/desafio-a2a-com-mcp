# Notas de Fase 0: evidências do SDK `mcp==2.2.0`

Notas colhidas lendo o código-fonte do pacote `mcp` instalado (`site-packages/mcp/`)
antes e durante a implementação. Servem de evidência para as decisões do README.

## Servidor

- `mcp.server.mcpserver.MCPServer(name=..., request_state_security=RequestStateSecurity(keys=[...], ttl=...))`.
  Sem `request_state_security`, o servidor usa `RequestStateSecurity.ephemeral()`
  (chave aleatória por processo) — por isso o requestState não sobreviveria a um
  restart se essa opção fosse omitida.
- `MCPServer.streamable_http_app(streamable_http_path="/mcp", json_response=True, stateless_http=True)`
  devolve um `Starlette` pronto; não expõe hook de middleware, por isso o log e a
  validação de `_meta` ficam num middleware ASGI próprio (`log.py`) que envolve esse app.
- **MRTR via resolver** (`mcp.server.mcpserver.resolve`): `Resolve(fn)` marca um parâmetro
  do tool para ser preenchido por um resolver; `Elicit(mensagem, schema)` é o retorno do
  resolver quando precisa perguntar. O framework:
  - atribui a chave do `inputRequests` como `f"{modulo}:{qualname}"` da função resolver
    (`resolve.py:_state_key`) — por isso a chave observada é `servidor_mcp.app:escolha_de_sala`,
    e não `__main__:escolha_de_sala` como no wire do enunciado (a spec permite: "não
    fixamos o texto dela, porque cada SDK a gera de um jeito").
  - verifica a capability de elicitation form automaticamente (`resolve.py:_require_capability`)
    e levanta `MCPError(code=-32021, data={"requiredCapabilities": {...}})` quando falta —
    nenhum código extra foi necessário para a segunda fricção.
  - resela e revalida o `requestState` via `RequestStateBoundary`
    (`mcp/server/request_state.py`), instalada automaticamente pelo `MCPServer` quando
    `request_state_security` é passado. A verificação de integridade inclui, no envelope
    selado, o par `(target, digest(arguments))` da chamada (`_request_identity`); por isso
    um retry com argumentos divergentes dos que originaram o `input_required` já é
    rejeitado com `-32602 "Invalid or expired requestState"` **pelo próprio framework**,
    sem nenhuma verificação manual da nossa parte.
  - re-executa o resolver a cada rodada (inicial e retry); a resposta gravada só é
    consultada se a *mesma pergunta* (mesmo schema/render) for feita de novo. Isso dá,
    de graça, a revalidação que a seção 5.5 da spec pede ("revalide que a sala escolhida
    está livre e pertence às alternativas"): se o estado de domínio mudar entre o
    `input_required` e o retry, o resolver simplesmente não repete a mesma pergunta e a
    resposta antiga é descartada.
- `ToolError(mensagem)` (`mcp.server.mcpserver.exceptions`) levantado dentro do resolver
  ou do corpo da tool vira `CallToolResult(isError=True, content=[TextContent(text=str(exc))])`.
  O SDK prefixa com `"Error executing tool <nome>: "` (`tools/base.py:Tool.run`), o que o
  enunciado aceita explicitamente.
- `resources/read` de URI não registrada levanta `ResourceNotFoundError` internamente
  (`resource_manager.py`), convertido pelo servidor em `MCPError(code=-32602, ...)`
  automaticamente — nenhum código extra necessário.
- Tool desconhecida também é tratada pelo próprio SDK como `isError: true` com a
  mensagem `"Unknown tool: <nome>"`.

## Cliente (agente)

- `mcp.client.streamable_http.streamable_http_client(url)` é um async context manager
  que produz `(read_stream, write_stream)`; `mcp.client.session.ClientSession` é
  construído sobre esse par.
- **Sem sessão real**: como o servidor é stateless, o agente nunca chama `initialize()`.
  Em vez disso, chama `session.discover()` uma única vez por conexão — isso apenas faz
  o SDK instalar a função de "stamp" que preenche `MCP-Protocol-Version`, `Mcp-Method`,
  `Mcp-Name` nos headers HTTP e `_meta.io.modelcontextprotocol/*` no corpo de toda
  chamada seguinte nessa sessão (`client/session.py:_make_modern_stamp`).
- **Limitação de capability encontrada**: `ClientSession._build_capabilities` só declara
  `elicitation` quando um `elicitation_callback` não-default foi passado, e nesse caso
  declara **as duas** formas — `{"elicitation": {"form": {}, "url": {}}}` — nunca só
  `form` (`client/session.py:634-638`):

  ```python
  elicitation = (
      types.ElicitationCapability(form=types.FormElicitationCapability(), url=types.UrlElicitationCapability())
      if self._elicitation_callback is not _default_elicitation_callback
      else None
  )
  ```

  Isso não é sobrescrevível via `meta=` no `call_tool`/`list_tools`/`read_resource`: o
  "stamp" reescreve `_meta["io.modelcontextprotocol/clientCapabilities"]`
  incondicionalmente a cada request (`_make_modern_stamp`, linha `meta[CLIENT_CAPABILITIES_META_KEY] = capabilities`).
  A spec aceita esse caminho explicitamente ("mantenha form + url e documente no README
  com o trecho do SDK como evidência"), porque o servidor considera satisfeita a
  capability de form sempre que `elicitation.form is not None`
  (`resolve.py:_require_capability`), então `{form:{}, url:{}}` também passa.
  O `traceparent` (uma chave fora do namespace reservado) não é tocado pelo stamp e
  chega intacto.
- **Geração de id por sessão**: o `JSONRPCDispatcher` interno de cada `ClientSession`
  começa seu contador de id em 0 (`shared/jsonrpc_dispatcher.py:self._next_id = 0`) e a
  API pública (`call_tool`, `list_tools`, `read_resource`) não expõe um jeito de
  sobrescrever esse id. Abrir uma conexão nova por chamada faria dois `ClientSession`
  distintos gerarem, cada um, ids começando do zero — o par inicial/retry de uma
  reserva pausada poderia colidir no mesmo id por coincidência. Por isso o agente mantém
  **uma única sessão MCP viva durante todo o processo** (`agente/mcp_host.py`), aberta
  no lifespan do Starlette: o contador cresce monotonicamente para qualquer par
  inicial/retry, não importa quanto tempo a Task fique pausada entre os dois.
- `session.call_tool(..., allow_input_required=True)` devolve o `InputRequiredResult`
  cru (em vez de resolver a elicitation sozinho) sempre que o servidor pede input. O
  `elicitation_callback` registrado apenas levanta uma exceção — é uma defesa contra
  regressão, nunca deve ser de fato chamado, porque a ponte nunca usa o caminho
  síncrono de elicitation.
