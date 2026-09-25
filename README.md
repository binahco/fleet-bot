# fleet-bot

Ciclo matutino de la flota (semana 9): un run deja la flota auditada, la evidencia
regenerada y un **digest LLM del día**. El bot no es un daemon: es una función
disparable cuando quieras (cron, launchd, GH Actions, a mano) — la **recurrencia
programada** la decide `bot-base`, con idempotencia de día (`.bot/state.json`).

## Demo

```bash
uv sync
uv run fleet-bot run --repos ../commit-cli ../release-scribe ../sec-check \
    ../bench-runner ../eval-api ../ci-scribe ../llm-gateway \
    ../fleet-bot ../content-ray ../evidence-api
# - audit: ok — 10 repos, lints OK
# - evidence: ok — 10 consumidores, $0.0000 acumulados
# - digest: ok — Auditoría matutina de 10 repos de la flota: evidencia del día registrada
#   en los 10, sin fallos de verificación pendientes. (watch: …)
```

Pasa la lista **explícita** de repos: un `..` haría lints sobre todos los proyectos
hermanos y parece un cuelgue. El texto del digest varía (es LLM); lo garantizado por
el bot es que valida contra `daily-digest-v1` o marca `failed` (la próxima corrida
reintenta). Los números (repos/consumidores) son de la corrida: crecen con la flota.

Un segundo `run` el mismo día es `skipped` (idempotencia): no vuelve a auditar ni a
pagar. Un `digest` cuya salida no valida contra `daily-digest-v1` es `failed` y **no**
marca la tarea — la próxima corrida lo reintenta. Nada se corta en silencio (§5.1).

## Cómo funciona

```
fleet-bot run
  ├─ Task( audit,   every=86400s, run=ci_pack.lints sobre cada repo )   → detalle o failed
  ├─ Task( evidence, every=86400s, run=ci_pack.metrics collect+render ) → docs/metrics/evidence.html
  └─ Task( digest,  every=86400s, run=daily-digest-generator (LLM) )     → resumen validado
  └─ bot_base.run_tasks( …, store=JsonRunStore(".bot/state.json") )     → Digest
```

El digest es el único LLM: recibe el día y los hechos (heurística) y devuelve JSON
validado (`daily-digest-v1`, `schema-validate`). La caché y el límite de ritmo
(`cache-ratelimit`) rodean el proveedor con TTL diario — la petición idéntica del mismo
día no vuelve a cruzar.

## Recicla de

| Módulo | Uso |
|---|---|
| `llm-client` | la llamada del digest (retry + reparación + span de 20 campos) |
| `schema-validate` | la salida del digest valida contra `daily-digest-v1` o falla |
| `test-kit` | el prompt nace evaluado: dataset congelado + replay determinista (D4) |
| `ci-pack` | lints del audit y `ci_pack.metrics` para la página de evidencia |
| `cache-ratelimit` | caché TTL + token bucket por delante del proveedor |
| `bot-base` | recurrencia programada con reloj inyectable, idempotencia y `Digest` |

## Limitaciones

- El bot corre las tareas que tú le pasas: no hay "descubrimiento" de consumidores.
- `Schedule` es de intervalo (86400s) o predicado de calendario: no hay expresiones
  cron completas en v1 (queda para el segundo consumidor de `bot-base`).
- El digest describe heurística (mensaje del mantenedor), no lee los spans todavía —
  llega con `cost-obs` (sem. 39).
- Sin daemon ni horario propio: el transport (cron/launchd/actions) es externo.

## Roadmap

- [x] Ciclo matutino: audit + evidencia + digest en un run, idempotente por día (sem. 9)
- [x] Digest validado contra `daily-digest-v1` con baseline congelado (3 casos)
- [ ] Segundo consumidor de `bot-base`: horarios cron + retry por tarea con backoff
- [ ] Digest alimentado por spans reales de `llm-client` (`cost-obs`, sem. 39)