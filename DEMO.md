# Demo — Bloquear un agente cuando se supera el presupuesto

Guía **lista para enseñar al cliente**. La Azure Function ya está desplegada y probada; solo tienes que copiar y pegar los comandos de cada escenario y mostrar el resultado.

Todo es **reversible y no destructivo**: cada bloqueo se puede deshacer y ningún recurso se borra.

> **En una frase:** cuando un presupuesto de Azure se supera, una alerta llama a esta Function y esta bloquea el agente por uno de tres mecanismos. Es el equivalente automatizado al botón *"Block agent"* del Centro de Administración de M365 (que no tiene API pública).

---

## 0. Preparación (una sola vez, ~30 s)

Abre **PowerShell** y pega esto.

```powershell
# Inicia sesión si aún no lo estás
az login

# Recupera la clave de la Function
$key = az functionapp keys list `
  --name fa-block-agent-jykza1 `
  --resource-group rg-block-agent `
  --query "functionKeys.default" `
  -o tsv

if (-not $key) {
  throw "No se pudo recuperar la function key"
}

# Construye la URL de la Function
$Url = "https://fa-block-agent-jykza1.azurewebsites.net/api/budget-alert?code=${key}"
$Url

# Recurso Foundry
$Rid = "/subscriptions/72dc9a1e-135b-49cb-86e6-80630340cade/resourceGroups/rg-agent-verse/providers/Microsoft.CognitiveServices/accounts/agent-verse-resource"

# Identidad del agente de demo (SimplePromptAgent)
$AgentId = "f55c4a61-23bf-46fd-b3d9-694d78a9138c"

if (-not $AgentId) {
  throw "AgentId vacío"
}

$GraphSpUrl = "https://graph.microsoft.com/v1.0/servicePrincipals/${AgentId}?`$select=displayName,accountEnabled"
$GraphAccountUrl = "https://graph.microsoft.com/v1.0/servicePrincipals/${AgentId}?`$select=accountEnabled"
$GraphPatchUrl = "https://graph.microsoft.com/v1.0/servicePrincipals/${AgentId}"
$GraphSpUrl

"Listo. URL preparada."
```

La salida de `$Url` debe incluir un valor después de `?code=`. Si ves `?code=` vacío, no sigas con la demo: vuelve a ejecutar el bloque anterior en la misma ventana de PowerShell.

La salida de `$GraphSpUrl` debe incluir el GUID del agente después de `/servicePrincipals/`. Si ves `/servicePrincipals/?`, no sigas con la parte de Graph: vuelve a ejecutar el bloque anterior en la misma ventana de PowerShell.

Si `$key` aparece vacío o recibes errores de autenticación:

```powershell
az account show
```

Si necesitas recomponer la URL manualmente:

```powershell
if (-not $key) {
  throw "No se pudo recuperar la function key"
}

$Url = "https://fa-block-agent-jykza1.azurewebsites.net/api/budget-alert?code=${key}"
$Url
```

---

## Datos del despliegue

| Elemento | Valor |
|----------|--------|
| Function App | `fa-block-agent-jykza1` |
| Runtime | Python 3.11 |
| Plan | Flex Consumption |
| Resource Group | `rg-block-agent` |
| Región | `swedencentral` |
| Recurso Foundry | `agent-verse-resource` |
| Proyecto | `agent-verse-project` |
| Agente demo | `SimplePromptAgent` |

---

# Escenario 1 — La Function está viva

## Qué demuestras

El servicio está desplegado y responde correctamente.

```powershell
Invoke-RestMethod `
  -Uri "https://fa-block-agent-jykza1.azurewebsites.net/api/health"
```

### Resultado esperado

```json
{
  "status": "ok",
  "mechanisms": ["foundry","graph","tag"]
}
```

---

# Escenario 2 — Bloqueo por etiqueta ARM (mecanismo C)

Este es el escenario más visual porque se ve directamente en el Portal de Azure.

---

## 2.1 Estado ANTES

```powershell
az resource show `
  --ids $Rid `
  --query "tags" `
  -o json
```

### Resultado esperado

```json
{
  "MS-AOAI-Feature-Assistants": "Enabled"
}
```

---

## 2.2 BLOQUEAR

```powershell
$body = '{"agentId":"demo","mechanism":"tag","action":"block","budgetName":"budget-demo","spend":150,"budget":100}'

(
  Invoke-RestMethod `
    -Uri $Url `
    -Method Post `
    -ContentType "application/json" `
    -Body $body
).results
```

### Resultado esperado

```json
{
  "success": true,
  "previous_state": "Enabled",
  "detail": "MS-AOAI-Feature-Assistants=Disabled"
}
```

Comprobar en Azure:

```powershell
az resource show `
  --ids $Rid `
  --query "tags" `
  -o json
```

### Resultado esperado

```json
{
  "MS-AOAI-Feature-Assistants": "Disabled"
}
```

---

## 2.3 DESBLOQUEAR

```powershell
$body = '{"agentId":"demo","mechanism":"tag","action":"unblock"}'

(
  Invoke-RestMethod `
    -Uri $Url `
    -Method Post `
    -ContentType "application/json" `
    -Body $body
).results
```

Esperar unos segundos:

```powershell
Start-Sleep 15
```

Verificar:

```powershell
az resource show `
  --ids $Rid `
  --query "tags" `
  -o json
```

### Resultado esperado

```json
{
  "MS-AOAI-Feature-Assistants": "Enabled"
}
```

> La etiqueta `MS-AOAI-Feature-Assistants` afecta a todos los assistants clásicos del recurso.

---

# Escenario 3 — Deshabilitar la identidad del agente (mecanismo B)

## Qué demuestras

El equivalente más cercano al botón **"Block Agent"** del centro de administración.

---

## 3.a Agente clásico (Service Principal normal)

Ya validado.

```powershell
$body = '{"agentId":"<spObjectId>","mechanism":"graph","action":"block"}'

(
  Invoke-RestMethod `
    -Uri $Url `
    -Method Post `
    -ContentType "application/json" `
    -Body $body
).results
```

---

## 3.b Agente Foundry (preview)

### Estado inicial

```powershell
az rest `
  --method GET `
  --uri $GraphSpUrl
```

---

### Bloquear

```powershell
'{"accountEnabled":false}' |
  Set-Content `
    "$env:TEMP\b.json" `
    -Encoding ascii `
    -NoNewline
```

```powershell
az rest `
  --method PATCH `
  --uri $GraphPatchUrl `
  --headers "Content-Type=application/json" `
  --body "@$env:TEMP\b.json"
```

---

### Verificar

```powershell
az rest `
  --method GET `
  --uri $GraphAccountUrl
```

### Resultado esperado

```json
{
  "accountEnabled": false
}
```

---

### Desbloquear

```powershell
'{"accountEnabled":true}' |
  Set-Content `
    "$env:TEMP\b.json" `
    -Encoding ascii `
    -NoNewline
```

```powershell
az rest `
  --method PATCH `
  --uri $GraphPatchUrl `
  --headers "Content-Type=application/json" `
  --body "@$env:TEMP\b.json"
```

---

# Escenario 4 — Manejo de errores

## Sin agente

```powershell
try {
    Invoke-RestMethod `
      -Uri $Url `
      -Method Post `
      -ContentType "application/json" `
      -Body '{}'
}
catch {
    "HTTP $([int]$_.Exception.Response.StatusCode) (esperado 422)"
}
```

---

## Mecanismo inválido

```powershell
try {
    Invoke-RestMethod `
      -Uri $Url `
      -Method Post `
      -ContentType "application/json" `
      -Body '{"agentId":"x","mechanism":"foo"}'
}
catch {
    "HTTP $([int]$_.Exception.Response.StatusCode) (esperado 400)"
}
```

---

# Escenario 5 (opcional) — Todo local

```powershell
python -m venv .venv

.venv\Scripts\python.exe -m pip install -r requirements.txt

.venv\Scripts\python.exe -m tests.test_harness
```

### Resultado esperado

```text
Ran 3 tests

OK
```

---

# Escenario 6 — Trigger REAL de extremo a extremo (saturar el agente → bloqueo automático)

Este es **el escenario que convence**: el flujo completo **sin llamar a la Function a mano**. Saturas el agente desde el **portal de Foundry** y, a los pocos minutos, el agente queda bloqueado **solo**, igual que en producción cuando se dispara el presupuesto.

## Qué está desplegado (ya montado, no hay que crear nada)

| Elemento | Nombre | Qué hace |
|----------|--------|----------|
| Grupo de acciones | `ag-block-agent` | Webhook que llama a la Function cuando salta una alerta (esquema común activado) |
| Alerta de métrica | `budget-SimplePromptAgent` | Salta cuando `TotalTokens > 1000` en 1 min sobre `agent-verse-resource` |
| Presupuesto | `budget-SimplePromptAgent` | Presupuesto de coste (1 €, aviso al 80 %) → mismo grupo de acciones |

La Function lee el nombre de la regla de alerta (`budget-<agente>`) del payload y resuelve el agente (`SimplePromptAgent`). La alerta real **no** trae mecanismo, así que la Function usa el mecanismo por defecto (**`DEFAULT_BLOCK_MECHANISM=foundry`**): deshabilita el **estado nativo** del agente de Foundry (`state=disabled`) de forma autónoma. Resultado visible y contundente: **el agente deja de responder en el propio playground**, no es un simple flag.

## El flujo

```text
Portal de Foundry (saturas el agente con un prompt grande)
        │  se disparan miles de TotalTokens
        ▼
Alerta de métrica  budget-SimplePromptAgent  (TotalTokens > 1000, ventana 1 min)
        │  monitorCondition = Fired
        ▼
Grupo de acciones  ag-block-agent  (webhook, esquema común)
        │  POST del payload de alerta
        ▼
Azure Function  →  mecanismo por defecto (foundry)  →  POST /agents/SimplePromptAgent:disable  →  state = "disabled"
        │
        ▼
Agente bloqueado automáticamente (visible en el portal de Azure)
```

---

## 6.1 Estado ANTES

```powershell
$tok = az account get-access-token `
  --scope "https://ai.azure.com/.default" `
  --query accessToken -o tsv

$FoundryEp = "https://agent-verse-resource.services.ai.azure.com/api/projects/agent-verse-project"

Invoke-RestMethod `
  -Uri "$FoundryEp/agents/SimplePromptAgent?api-version=v1" `
  -Headers @{ Authorization = "Bearer $tok" } |
  Select-Object id, state
```

### Resultado esperado

```text
id                state
--                -----
SimplePromptAgent enabled
```

---

## 6.2 Saturar el agente desde el portal de Foundry

1. Abre el **portal de Azure AI Foundry** → proyecto `agent-verse-project` → agente **`SimplePromptAgent`** → **Playground / Chat**.
2. Pega el siguiente **prompt de saturación** y envíalo (genera miles de tokens, muy por encima del umbral de 1000 en la ventana de 1 min):

```text
Escribe un ensayo técnico de al menos 2000 palabras que explique en profundidad,
paso a paso y con ejemplos, la arquitectura completa de un sistema de bloqueo
automático de agentes de IA por presupuesto en Azure: incluye Azure Functions,
Azure Monitor, grupos de acciones, Cost Management, Microsoft Graph y ARM.
Desarrolla cada sección con el máximo detalle posible, añade ventajas,
inconvenientes, alternativas y un resumen final extenso. No omitas nada.
```

Si con una vez no basta, **envíalo 2–3 veces seguidas** para acumular tokens dentro de la misma ventana de 1 minuto.

---

## 6.3 Esperar a que salte la alerta (~1–5 min)

Azure Monitor evalúa la métrica cada minuto. En cuanto `TotalTokens` supera 1000, la alerta pasa a **Fired**, llama al grupo de acciones y este a la Function.

Seguir el estado de la alerta:

```powershell
az monitor metrics alert show `
  --name budget-SimplePromptAgent `
  --resource-group rg-block-agent `
  --query "enabled" `
  -o json
```

En el portal: **Monitor → Alertas** → verás una alerta `Fired` para `budget-SimplePromptAgent`.

---

## 6.4 Comprobar que el agente se bloqueó SOLO

```powershell
Invoke-RestMethod `
  -Uri "$FoundryEp/agents/SimplePromptAgent?api-version=v1" `
  -Headers @{ Authorization = "Bearer $tok" } |
  Select-Object id, state
```

### Resultado esperado

```text
id                state
--                -----
SimplePromptAgent disabled
```

**Prueba visual definitiva:** vuelve al **playground de Foundry** e intenta usar `SimplePromptAgent` → el servicio ya **no lo sirve** (el agente está deshabilitado). No es un flag: es el estado nativo enforced por Foundry.

Comprobar que la Function se ejecutó (traza en Application Insights):

```powershell
az monitor app-insights query `
  --app fa-block-agent-jykza1 `
  --resource-group rg-block-agent `
  --analytics-query "requests | where timestamp > ago(15m) | where name contains 'budget-alert' | project timestamp, resultCode | order by timestamp desc" `
  -o table
```

También en el portal: **Function App → Functions → budget-alert → Invocations**.

> **Mensaje para el cliente:** nadie ha tocado nada tras enviar el prompt. El agente ha quedado bloqueado por sí solo porque su consumo disparó la alerta. En producción ese mismo mecanismo se ata al **presupuesto de coste** real del agente.

---

## 6.5 Desbloquear (revertir tras la demo)

El bloqueo automático **no se deshace solo** al resolverse la alerta: hay que desbloquear explícitamente (es intencionado — el admin decide cuándo reactivar).

```powershell
$body = '{"agentId":"SimplePromptAgent","mechanism":"foundry","action":"unblock"}'

(
  Invoke-RestMethod `
    -Uri $Url `
    -Method Post `
    -ContentType "application/json" `
    -Body $body
).results

Start-Sleep 5

Invoke-RestMethod `
  -Uri "$FoundryEp/agents/SimplePromptAgent?api-version=v1" `
  -Headers @{ Authorization = "Bearer $tok" } |
  Select-Object id, state
```

### Resultado esperado

```text
id                state
--                -----
SimplePromptAgent enabled
```

> **Presupuesto vs. alerta de métrica:** el **presupuesto** (`budget-…`) también está montado y apunta al mismo grupo de acciones, pero Cost Management factura el coste con **horas de retraso**, así que no sirve para una demo en vivo. Para demostrar el trigger *ahora* usamos la **alerta de métrica** sobre `TotalTokens`, que salta en 1–5 min. La lógica de bloqueo es idéntica en ambos casos.

---

# Escenario 7 — Bloqueo nativo del agente de Foundry (mecanismo A) ⭐ el más fuerte y autónomo

**Qué demuestras:** el bloqueo real de un agente de Foundry usando su **estado nativo** (`state=disabled`), ejecutado por la **identidad administrada de la Function sin Global Admin**. Es el equivalente más fiel al botón *"Block agent"*: el propio servicio deja de servir el agente.

## 7.1 Estado ANTES

```powershell
$tok = az account get-access-token `
  --scope "https://ai.azure.com/.default" `
  --query accessToken -o tsv

$FoundryEp = "https://agent-verse-resource.services.ai.azure.com/api/projects/agent-verse-project"

Invoke-RestMethod `
  -Uri "$FoundryEp/agents/SimplePromptAgent?api-version=v1" `
  -Headers @{ Authorization = "Bearer $tok" } |
  Select-Object id, state
```

### Resultado esperado

```text
id                state
--                -----
SimplePromptAgent enabled
```

## 7.2 BLOQUEAR (mecanismo A vía la Function)

```powershell
$body = '{"agentId":"f55c4a61-23bf-46fd-b3d9-694d78a9138c","mechanism":"foundry","action":"block"}'

(
  Invoke-RestMethod `
    -Uri $Url `
    -Method Post `
    -ContentType "application/json" `
    -Body $body
).results
```

### Resultado esperado

`success = true` y `detail` indica `Native state action :disable ... -> state=disabled (was enabled)`.

Comprobar el estado nativo del agente:

```powershell
Invoke-RestMethod `
  -Uri "$FoundryEp/agents/SimplePromptAgent?api-version=v1" `
  -Headers @{ Authorization = "Bearer $tok" } |
  Select-Object id, state
# state = disabled
```

## 7.3 DESBLOQUEAR (revertir)

```powershell
$body = '{"agentId":"f55c4a61-23bf-46fd-b3d9-694d78a9138c","mechanism":"foundry","action":"unblock"}'

(
  Invoke-RestMethod `
    -Uri $Url `
    -Method Post `
    -ContentType "application/json" `
    -Body $body
).results
# state vuelve a enabled
```

> **Mensaje para el cliente:** este es el bloqueo más limpio para agentes de Foundry: lo aplica la Function **por sí sola** (sin Global Admin), lo **enforcea el servicio** (no es un flag) y es **totalmente reversible**. El trigger real del Escenario 6 **ya usa este mecanismo** (`DEFAULT_BLOCK_MECHANISM=foundry`), así que la saturación desde el playground acaba deshabilitando el estado nativo del agente.

---

# Resumen

| Escenario | Qué demuestra |
|------------|---------------|
| Salud | La Function está desplegada |
| Etiqueta ARM | Bloqueo/desbloqueo visible en Azure |
| Identidad SP | Deshabilitar un agente clásico |
| Identidad Foundry | Deshabilitar un agente preview |
| Errores | Robustez y validación |
| Offline | Lógica sin dependencia de Azure |
| **Trigger real E2E** | **Saturar en Foundry → alerta → bloqueo automático** |
| **Estado nativo Foundry (A)** | **Deshabilitar el agente de Foundry, autónomo y enforced** |

---

# Nota sobre el mecanismo A (Foundry Agent Service)

Los agentes de `agent-verse-project` usan el **Foundry Agent Service** (agentes persistentes en `/agents` con `api-version=v1`), no la API clásica basada en Assistants. Cada agente tiene un campo nativo `state` (`enabled` / `disabled`).

El mecanismo A se ha **adaptado a esta API moderna** y ahora funciona de verdad: usa las **acciones de estado nativas** del servicio:

```text
POST /agents/{id}:disable?api-version=v1   ->  state = "disabled"
POST /agents/{id}:enable?api-version=v1    ->  state = "enabled"
```

Ventajas frente al mecanismo B para agentes de Foundry:

- **Es autónomo:** lo ejecuta la propia identidad administrada de la Function (roles `Azure AI Developer` **+ `Cognitive Services User`**); **no** requiere Global Administrator (el mecanismo B sobre la `agentIdentity` preview sí lo requiere). El data-plane de agentes (`Microsoft.CognitiveServices/*/agents/*`) exige `Cognitive Services User`: sin él, la API devuelve `403 UserError: ...does not have permissions for ...agents/read`.
- **Es enforced por el servicio:** `state=disabled` es un estado de primera clase del agente, no un simple flag de metadatos.
- **Es reversible y no destructivo:** `:enable` restaura el estado y no se borra nada.

Si el entorno apuntara a una API antigua sin estas acciones (`404`/`405`), la Function cae automáticamente a un flag reversible `metadata.blocked=true` publicando una nueva versión que **preserva la `definition`** existente (la API moderna rechaza actualizaciones solo-de-metadatos con `400 required: definition`). Ese flag es advisory y lo debe aplicar un gateway (APIM) o el cliente.

---

# Limpieza final

Verificar que el agente de Foundry ha vuelto a `enabled` (si hiciste el Escenario 6 o 7):

```powershell
Invoke-RestMethod `
  -Uri "$FoundryEp/agents/SimplePromptAgent?api-version=v1" `
  -Headers @{ Authorization = "Bearer $tok" } |
  Select-Object id, state
# state = enabled
```

Verificar que la etiqueta sigue en Enabled (mecanismo C):

```powershell
az resource show `
  --ids $Rid `
  --query "tags" `
  -o json
```

Verificar que la identidad está habilitada:

```powershell
az rest `
  --method GET `
  --uri $GraphAccountUrl
```

### Resultado esperado

```json
{
  "accountEnabled": true
}
```

---

> Si has hecho el **Escenario 6**, recuerda desbloquear con el paso 6.5 para dejar el agente en `enabled`. La alerta de métrica y el presupuesto pueden quedarse desplegados: no bloquean nada por sí mismos, solo llaman a la Function cuando se supera el consumo.
