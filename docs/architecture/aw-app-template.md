---
repo: architecture
path: docs/architecture/aw-app-template.md
source: generated
edited: false
checksum: sha256:91dd858c9d18b5aecfa012976a85127b1ccae35362093b206b5abec78125e008
---
# App Template

- **repo**: aw-app-template
- **layer**: app
- **technologies**: python, react
- **health** (derived): planned

TEMPLATE — the fastest way to start a new aw-workspace app. Install it and you have a working app on day one: a `template` CLI that prints a configurable greeting, its own window in the Apps grid, and an HTTP + WebSocket backend — with tests and marketplace release already wired. Rename everything marked TEMPLATE/template to make it yours; see README.md.

## Connections
- `http` → **aw-workspace** — routes mounted at /api/apps/aw-app-template

## MCP tools
_none exposed_

## Requirements
### O mesmo sub-app serve nos dois modos, sob o mesmo prefixo
- Given um app gerado deste template precisa rodar integrado (montado pelo runtime atrás do IdentityGuard) e standalone (montado por ele mesmo, sem guarda)
- When os dois caminhos chamam a mesma fábrica (repos/aw-app-template/template_app/routes.py::build_routes:48, usada por plugin.py via ctx.routes.register e por __main__.py na montagem própria)
- Then todo path declarado é relativo (/template, /ws/echo) e os dois modos expõem /api/apps/aw-app-template/... idêntico, então código cliente e documentação usam uma forma só — se o standalone montasse com prefixo próprio, todo exemplo do template estaria certo num modo e errado no outro, e quem gera um app novo herdaria a divergência sem perceber. O WebSocket in-process fica em /ws/&lt;nome&gt; dentro do sub-app: a raiz /ws/* é reservada para sockets de controle do core
- intended_status: `not_implemented` · derived health: `not_implemented`
- tests: `repos/aw-app-template/tests/test_routes.py` (passing), `repos/aw-app-template/tests/test_standalone.py` (passing)

### A documentação de tasks é conferida contra o validador que vive no core
- Given docs/contributing-tasks.md descreve regras cuja implementação está em outro repo (aw-workspace, src/apps/manifest.py), e a doc dizia que agent_slug só era exigido para agent_prompt quando o core já exigia também para agentic_output
- When o teste extrai a regra viva do fonte do core e compara com um marcador declarado na doc (repos/aw-app-template/tests/test_docs_match_the_validator.py::test_task_types_requiring_agent_slug_match_the_docs:31)
- Then os dois conjuntos batem, e a comparação é feita contra a linha `&lt;!-- agent_slug-required: ... --&gt;` e não contra a prosa — duas versões anteriores deste teste casavam com o texto que existiam para rejeitar, porque coocorrência de palavras não é afirmação. Sem aw-workspace no checkout ao lado o teste faz skip, o que significa que ele só protege de verdade onde os dois repos convivem: no CI standalone do app ele não roda
- intended_status: `not_implemented` · derived health: `not_implemented`
- tests: `repos/aw-app-template/tests/test_docs_match_the_validator.py` (passing)

### O standalone sobe sem a UI construída, e fica mais estrito quando ela existe
- Given um checkout fresco onde ninguém rodou `npm run build`, e portanto ui/dist/ não existe
- When o app standalone é montado e exercitado (repos/aw-app-template/template_app/__main__.py::build_standalone_app, via repos/aw-app-template/tests/test_standalone.py::test_standalone_app_boots_and_mounts_api:22)
- Then a API responde em /api/apps/&lt;slug&gt;/template de qualquer jeito, e a asserção sobre o HTML servido na raiz só entra em cena quando ui/dist/ existe de fato (test_standalone_serves_ui_dist_when_built:29) — um template cujo teste exige build quebra no primeiro clone, que é exatamente o momento em que ele precisa funcionar. O custo é honesto e vale nomear: num checkout sem build a cobertura da montagem estática é zero e o teste passa mesmo assim, com um return silencioso
- intended_status: `not_implemented` · derived health: `not_implemented`
- tests: `repos/aw-app-template/tests/test_standalone.py` (passing)
