# Bloquear un agente al superar su presupuesto — Azure Function

Solución que **bloquea automáticamente un agente de Azure AI Foundry cuando se
supera un presupuesto de tokens**, sin intervención manual. Es el equivalente
automatizado del botón **"Block agent"** del Microsoft 365 Admin Center (que hoy
**no** tiene API pública).

Cuando el gasto/consumo de tokens de un agente supera el umbral, una alerta de
Azure Monitor llama a una Azure Function que **deshabilita el agente** mediante
uno de tres mecanismos reversibles.

> 🎬 ¿Quieres ver los escenarios en acción? Ve a **[`DEMO.md`](DEMO.md)**.
> Para probar la lógica sin desplegar nada, mira **[`TESTING.md`](TESTING.md)**.

---

## 1. ¿Es posible automatizar el "Block agent" del Admin Center?

- **No literalmente.** La acción *Block* de *Agents & connectors* del Admin
  Center **no tiene API pública documentada**. El registro unificado **Agent 365**
  está en preview y no es una superficie de automatización estable.
- **Sí es posible un bloqueo equivalente y automatizado**, que es lo que
  implementa este repo.
- **Granularidad del presupuesto:** los presupuestos de *Cost Management* se
  fijan a suscripción / grupo de recursos / recurso / etiqueta, **no a un agente
  concreto**. Por eso aquí usamos una **alerta métrica sobre `TotalTokens`** de la
  cuenta de Foundry (más cercana al consumo real del agente). Para presupuestos
  estrictamente por-agente haría falta medición de tokens por agente
  (App Insights / Log Analytics) — queda como trabajo futuro.

---

## 2. Los tres mecanismos (todos reversibles, nunca destructivos)

| # | Mecanismo | Bloqueo | Desbloqueo | Ámbito | Nota |
|---|-----------|---------|------------|--------|------|
| **A** | **Estado nativo de Foundry** | `POST /agents/{id}:disable` → `state=disabled` | `:enable` → `state=enabled` | un agente | **Recomendado**. Lo *enforcea* el propio servicio. La identidad administrada lo hace **sin Global Admin**. |
| **B** | **Identidad de Entra** | `servicePrincipal accountEnabled=false` | `accountEnabled=true` | identidad del agente | Corta el acceso a nivel de identidad. Para identidades de agente de Foundry (preview) **requiere Global Admin**. |
| **C** | **Etiqueta ARM** | etiqueta `MS-AOAI-Feature-Assistants=Disabled` | etiqueta `=Enabled` | **toda la cuenta** | Contundente: afecta a *todos* los assistants clásicos de la cuenta. Solo para comparar. |

> **Regla dura:** ningún mecanismo borra el agente, su identidad ni concesiones
> de permisos. Cada bloqueo es reversible y guarda el estado previo.

### Mecanismo A: estado nativo (no es un flag de metadatos)

Los agentes del **Foundry Agent Service** (API moderna `/agents`, `api-version=v1`)
tienen un campo de primera clase `state` (`enabled`/`disabled`). El bloqueo
primario usa las **acciones de estado nativas**:

```
POST {project-endpoint}/agents/{id}:disable?api-version=v1   → state = "disabled"
POST {project-endpoint}/agents/{id}:enable?api-version=v1    → state = "enabled"
```

Si el entorno apuntara a una API antigua sin estas acciones (`404`/`405`), hay un
**fallback** que publica una nueva versión con `metadata.blocked=true`
**preservando la `definition`** (la API moderna rechaza actualizaciones solo de
metadatos con `400 required: definition`). Ese flag es advisory (debe aplicarlo un
gateway/cliente); el camino primario y probado es el estado nativo.

### ¿Assistant clásico o New Agent? Cómo distinguirlos

| | **Assistant clásico** | **New Agent (Agent Service)** |
|--|----------------------|-------------------------------|
| API | `/assistants` (estilo OpenAI Assistants) | `/agents` con `api-version=v1` |
| Estado | no tiene `state`; solo `metadata` | tiene `state` (`enabled`/`disabled`) y `versions` con `definition` |
| Bloqueo A | flag `metadata.blocked` (advisory) | acción nativa `:disable`/`:enable` (enforced) |
| Identidad Entra | service principal normal | `servicePrincipal` tipo `agentIdentity` (preview, más protegido) |

Regla rápida: **si `GET /agents/{id}?api-version=v1` devuelve `state` y `versions`,
es un New Agent**. Si solo existe bajo `/assistants` y no tiene `state`, es clásico.

---

## 3. Arquitectura

```
Alerta métrica de Azure Monitor  (scope: cuenta Foundry, métrica TotalTokens)
        │  se supera el umbral
        ▼
   Action Group (webhook, common alert schema)
        │  JSON de la alerta
        ▼
   Azure Function   POST /api/budget-alert   (este repo)
        │  parsea alerta → resuelve agente → despacha
        ├─ A) Foundry REST   (:disable / :enable)   ← recomendado
        ├─ B) Entra Graph     (accountEnabled=false)
        └─ C) ARM tag         (MS-AOAI-Feature-Assistants=Disabled)
```

La Function usa su **identidad administrada (system-assigned)** para pedir tokens
y llamar a cada API. No hay secretos en el código.

---

## 4. Qué despliega el Bicep (todo desde cero)

`deploy/main.bicep` crea **toda** la infraestructura de forma genérica y
autocontenida:

| Recurso | Para qué |
|---------|----------|
| **Cuenta Azure AI Foundry** (`Microsoft.CognitiveServices/accounts`, kind `AIServices`) + **proyecto** | Aloja los agentes. `allowProjectManagement` + subdominio para el endpoint `<nombre>.services.ai.azure.com`. **No** despliega modelo ni agente (eso lo haces tú). |
| **Storage** (sin clave compartida, por identidad) | Paquete de despliegue + estado del runtime |
| **Plan Flex Consumption + Function App (Python 3.11)** + **identidad administrada** | Ejecuta la lógica de bloqueo |
| **Log Analytics + Application Insights** | Trazas/telemetría de la Function |
| **Action Group** (webhook → `/api/budget-alert`, common alert schema) | Puente alerta → Function |
| **Alerta métrica** (`TotalTokens` sobre la cuenta Foundry) | Dispara el bloqueo al superar el umbral |
| **Role assignments** (mecanismos A y C + storage) | Permisos mínimos, ya en la plantilla |

> El **Mecanismo B (Graph `Application.ReadWrite.All`)** queda **fuera** del Bicep:
> requiere consentimiento de un **Global Admin** → se concede con
> `deploy/grant-graph-permission.ps1`.

---

## 5. Prerrequisitos

- `az login` con permisos para crear recursos y **asignar roles**.
- [Azure Functions Core Tools v4](https://learn.microsoft.com/azure/azure-functions/functions-run-local) (`func`).
- Bicep (`az bicep`).

---

## 6. Despliegue

### 6.1 Rellena los parámetros

Edita `deploy/main.parameters.json` con **nombres únicos globalmente** (Function
App, storage y cuenta Foundry deben ser únicos):

```jsonc
{
  "functionAppName":    { "value": "fa-blockagent-miorg" },
  "storageAccountName": { "value": "stblkagentmiorg" },
  "foundryAccountName": { "value": "aif-blockagent-miorg" },
  "foundryProjectName": { "value": "block-agent-project" },
  "foundryApiVersion":  { "value": "v1" },
  "agentTargetMap":     { "value": "{}" },
  "defaultBlockMechanism": { "value": "foundry" },
  "budgetTokenThreshold":  { "value": 1000 }
}
```

`agentTargetMap` empieza **vacío** (`{}`): lo rellenarás tras crear el agente.

### 6.2 Valida con what-if (no despliega nada)

```powershell
az group create --name rg-block-agent --location swedencentral

az deployment group what-if `
  --resource-group rg-block-agent `
  --template-file deploy/main.bicep `
  --parameters "@deploy/main.parameters.json"
```

Revisa que la lista de recursos a crear es la esperada.

### 6.3 Despliega y publica el código

```powershell
./deploy/deploy.ps1 -ResourceGroup rg-block-agent -Location swedencentral
```

El script despliega el Bicep (infra + roles) y publica el código Python
(`func azure functionapp publish <app> --python`). Comprueba salud:

```
GET https://<functionAppName>.azurewebsites.net/api/health
→ {"status":"ok","mechanisms":["foundry","graph","tag"]}
```

### 6.4 (Opcional) Mecanismo B — consentimiento de Graph

```powershell
./deploy/grant-graph-permission.ps1 -PrincipalId <objectId-de-la-identidad>
```

Necesita **Global Admin**. El `objectId` sale de los outputs del despliegue
(`managedIdentityPrincipalId`).

---

## 7. Pasos manuales en Foundry (después de desplegar)

El Bicep crea la **cuenta y el proyecto** de Foundry, pero **no** el modelo ni el
agente. Créalos tú en el portal de Foundry:

1. **Despliega un modelo** en la cuenta de Foundry (p. ej. `gpt-4o-mini`).
2. **Crea un agente** en el proyecto usando ese modelo.
3. **Copia el agent ID** del agente.
4. **Rellena `AGENT_TARGET_MAP`** para mapear el id que llega en la alerta al
   agente de Foundry, y aplícalo en la Function App:

   ```jsonc
   {
     "<AGENT_ID>": {
       "foundry_agent_id": "<AGENT_ID>",
       "service_principal_id": "<opcional, solo Mecanismo B>"
     }
   }
   ```

   ```powershell
   az functionapp config appsettings set `
     --name <functionAppName> --resource-group rg-block-agent `
     --settings AGENT_TARGET_MAP='<json-en-una-linea>'
   ```

   > Si no mapeas nada, la Function asume que el id de la alerta **es** el
   > `foundry_agent_id` (fallback en `config.py`).

5. **Ajusta el presupuesto/alerta.** La alerta métrica `budget-<foundryAccountName>`
   ya existe (creada por el Bicep). Cambia el umbral con `budgetTokenThreshold` o
   directamente en el portal si quieres otro valor para la demo.

---

## 8. Cómo funciona todo (permisos y comunicación)

### 8.1 Flujo de un bloqueo

1. El consumo de tokens supera el umbral → la **alerta métrica** se dispara.
2. La alerta invoca el **Action Group** → webhook `POST /api/budget-alert` (con
   *common alert schema*).
3. La Function parsea la alerta, resuelve el agente (`AGENT_TARGET_MAP` /
   `alertContext.AgentId` / nombre `budget-<agentId>`) y **despacha** al mecanismo
   (por defecto `DEFAULT_BLOCK_MECHANISM=foundry`).
4. El mecanismo pide un **token** con la identidad administrada y llama a la API
   correspondiente. El agente queda `disabled`.

### 8.2 Permisos (mínimo privilegio)

| Mecanismo | Permiso | Ámbito | Lo concede |
|-----------|---------|--------|------------|
| A – Foundry | `Azure AI Developer` **+** `Cognitive Services User` | cuenta Foundry | Bicep |
| C – Etiqueta ARM | `Tag Contributor` | cuenta Foundry | Bicep |
| B – Graph | `Application.ReadWrite.All` | tenant (Graph) | `grant-graph-permission.ps1` (**Global Admin**) |
| Runtime | `Storage Blob Data Owner` + `Storage Queue Data Contributor` | storage | Bicep |

> **🔑 Detalle clave del Mecanismo A:** `Azure AI Developer` por sí solo **no**
> cubre el data-plane de agentes (`Microsoft.CognitiveServices/*/agents/*`) →
> devuelve `403 does not have permissions for .../agents/read`. Por eso hace falta
> **también `Cognitive Services User`**. Tras asignarlo, el plano de datos tarda
> **2-5 min** en propagar.

Las 6 data-actions de agentes son: `agents/read`, `/write`, `/delete`,
`/state/disable/action`, `/state/enable/action`,
`/endpoints/UserIdentityImpersonation/action`. El Mecanismo A solo necesita
`read` + `state/disable/action` + `state/enable/action` (+`write` para el fallback).

### 8.3 Flujo del token (identidad administrada → Entra → RBAC)

1. `DefaultAzureCredential` pide el token al **IMDS** (endpoint interno de
   metadatos, `169.254.169.254`).
2. IMDS habla con **Entra ID**, que devuelve un **JWT firmado** para la *audiencia*
   del scope pedido:
   - Foundry: `https://ai.azure.com/.default`
   - ARM (etiqueta): `https://management.azure.com/.default`
   - Graph: `https://graph.microsoft.com/.default`
3. La Function hace el **POST directo por HTTPS** al endpoint del servicio con ese
   token (no pasa por la identidad).
4. **Azure RBAC se evalúa en el recurso, por llamada** (no viaja en el token): ARM
   comprueba si la identidad tiene el data-action necesario. Por eso un fallo de
   rol es un `403` del recurso, no un problema del token. (Los *app roles* de Graph
   sí van dentro del token.)

> **Mecanismo B** deshabilita la **identidad de Entra** del agente, no Foundry en
> sí: si el agente fuera un assistant clásico respaldado por un SP normal, poner
> `accountEnabled=false` le corta el inicio de sesión y, por tanto, el acceso.

---

## 9. Endpoints y payloads

| Método | Ruta | Auth | Propósito |
|--------|------|------|-----------|
| `POST` | `/api/budget-alert` | function key | Bloquea/desbloquea según el payload |
| `GET`  | `/api/health` | anónima | Liveness + lista de mecanismos |

Payload simplificado (pruebas manuales, ver `samples/simplified_block.json`):

```json
{ "agentId": "<AGENT_ID>", "spend": 128.55, "budget": 100,
  "action": "block", "mechanism": "foundry" }
```

- `action`: `block` (por defecto) o `unblock`.
- `mechanism`: `foundry` | `graph` | `tag` | `all` (por defecto
  `DEFAULT_BLOCK_MECHANISM`).
- Resolución del agente: campo `agentId` → `alertContext.AgentId` → presupuesto
  llamado `budget-<agentId>`.

Formato real: *Common Alert Schema*, ver `samples/common_alert.json`.

---

## 10. Configuración (App Settings)

Ver `local.settings.json.example`. Ajustes clave:

| Ajuste | Propósito |
|--------|-----------|
| `AZURE_SUBSCRIPTION_ID`, `AZURE_RESOURCE_GROUP`, `FOUNDRY_ACCOUNT_NAME` | Localizan la cuenta Foundry (Mecanismo C) |
| `FOUNDRY_PROJECT_ENDPOINT`, `FOUNDRY_API_VERSION` (`v1`) | Data-plane de Foundry (Mecanismo A) |
| `GRAPH_SCOPE` | Audiencia de Graph (Mecanismo B) |
| `AGENT_TARGET_MAP` | JSON `agentId → { foundry_agent_id, service_principal_id }` |
| `DEFAULT_BLOCK_MECHANISM` | Mecanismo por defecto si la alerta no lo especifica |
| `AZURE_TENANT_ID/CLIENT_ID/CLIENT_SECRET` | **Solo dev local.** En Azure se usa la identidad administrada — déjalos vacíos. |

El Bicep rellena todos estos ajustes automáticamente (salvo `AGENT_TARGET_MAP`,
que ajustas tras crear el agente).

---

## 11. Ejecutar y probar en local

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt

# Prueba offline: bloquea, verifica y comprueba que el desbloqueo restaura (sin Azure)
.venv\Scripts\python.exe -m unittest discover -s tests
```

Para levantar el host localmente necesitas Azure Functions Core Tools:

```powershell
copy local.settings.json.example local.settings.json   # rellena los valores
func start
# en otra shell:
curl -X POST http://localhost:7071/api/budget-alert -H "Content-Type: application/json" -d "@samples/simplified_block.json"
```

---

## 12. Desbloqueo

El bloqueo **nunca** se revierte solo (no hay temporizador; una alerta "Resolved"
no rehabilita). Para desbloquear, envía el mismo payload con `"action": "unblock"`
(ver `samples/simplified_unblock.json`) o, para el Mecanismo A, reactiva el
`state` del agente desde el portal de Foundry. Cada mecanismo restaura el estado
previo (`state=enabled`, `accountEnabled=true`, etiqueta `=Enabled`). Nada se borra.

---

## 13. Estructura del repo

```
function_app.py             # Entrypoint Azure Functions v2 (rutas HTTP)
host.json                   # Config del host
requirements.txt            # Dependencias Python
local.settings.json.example # Copia a local.settings.json para dev local
blockagent/
  config.py                 # Config por entorno + mapeo agentId→targets
  auth.py                   # Tokens de identidad administrada / app-registration
  budget_alert.py           # Parsea common-alert-schema o payload simplificado
  dispatcher.py             # Enruta alerta → mecanismo(s), block/unblock
  mechanisms/
    base.py                 # BlockResult
    foundry.py              # Mecanismo A (estado nativo)
    graph.py                # Mecanismo B (identidad Entra)
    arm_tag.py              # Mecanismo C (etiqueta ARM)
samples/                    # Payloads de ejemplo
tests/test_harness.py       # Prueba offline: block → verify → unblock restaura
deploy/                     # Bicep + scripts (ver deploy/README.md)
```
