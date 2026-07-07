# Demo — Bloquear un agente cuando se supera el presupuesto

Guía copy-paste para enseñar el flujo. Todo es **reversible y no destructivo**:
cada bloqueo se puede deshacer y ningún recurso se borra.

> **En una frase:** cuando el consumo de tokens de un agente supera un umbral, una
> alerta llama a esta Function y esta **deshabilita el agente**. Es el equivalente
> automatizado del botón *"Block agent"* del Admin Center de M365 (sin API pública).

Asume que ya has desplegado la solución con el Bicep (ver [`README.md`](README.md)):
cuenta y proyecto de Foundry, Function App, Action Group y alerta métrica ya existen.

---

## 0. Variables (rellena una sola vez)

Abre **PowerShell**, edita los valores `<...>` con los de **tu** despliegue y pega
el bloque:

```powershell
az login

# --- Rellena con tus valores ---
$FunctionApp   = "<FUNCTION_APP>"          # p. ej. fa-blockagent-miorg
$Rg            = "<RG>"                     # RG de la Function, p. ej. rg-block-agent
$FoundryAcct   = "<FOUNDRY_ACCOUNT>"        # cuenta Foundry, p. ej. aif-blockagent-miorg
$Project       = "<PROJECT>"               # proyecto, p. ej. block-agent-project
$AgentId       = "<AGENT_ID>"              # agent ID que creaste en Foundry
$SubId         = "<SUBSCRIPTION_ID>"
# --------------------------------

# Clave de la Function y URL del endpoint
$key = az functionapp keys list --name $FunctionApp --resource-group $Rg `
  --query "functionKeys.default" -o tsv
if (-not $key) { throw "No se pudo recuperar la function key" }
$Url = "https://$FunctionApp.azurewebsites.net/api/budget-alert?code=$key"

# Endpoint data-plane del proyecto Foundry y resource id de la cuenta
$FoundryEp = "https://$FoundryAcct.services.ai.azure.com/api/projects/$Project"
$Rid = "/subscriptions/$SubId/resourceGroups/$Rg/providers/Microsoft.CognitiveServices/accounts/$FoundryAcct"

"URL: $Url"
"Foundry endpoint: $FoundryEp"
```

Comprobación rápida de salud (el host está vivo y cargó los mecanismos):

```powershell
Invoke-RestMethod -Uri "https://$FunctionApp.azurewebsites.net/api/health"
# → {"status":"ok","mechanisms":["foundry","graph","tag"]}
```

---

## 1. Preparación en Foundry (modelo + agente)

El Bicep crea la **cuenta y el proyecto** de Foundry, pero **no** el modelo ni el
agente. Hazlo una vez en el portal:

1. **Portal de Azure AI Foundry** → tu proyecto (`<PROJECT>`).
2. **Despliega un modelo** (p. ej. `gpt-4o-mini`).
3. **Crea un agente** con ese modelo.
4. **Copia el agent ID** → es el valor de `$AgentId` de arriba.
5. **Mapea el agente** en la Function (para que resuelva el id de la alerta):

   ```powershell
   $map = @{ $AgentId = @{ foundry_agent_id = $AgentId } } | ConvertTo-Json -Compress
   az functionapp config appsettings set --name $FunctionApp --resource-group $Rg `
     --settings AGENT_TARGET_MAP=$map | Out-Null
   ```

6. **Presupuesto/alerta:** la alerta métrica `budget-<FOUNDRY_ACCOUNT>` ya existe
   (creada por el Bicep, `TotalTokens > umbral` sobre la cuenta Foundry) y está
   conectada al Action Group → Function. No hay que crear nada más.

---

## 2. ⭐ Escenario estrella — Trigger real de extremo a extremo

**El escenario que convence:** saturas el agente desde el playground y, a los pocos
minutos, queda bloqueado **solo** — sin llamar a la Function a mano — igual que en
producción cuando se dispara el presupuesto.

```text
Playground de Foundry (saturas el agente con un prompt grande)
        │  se disparan miles de TotalTokens
        ▼
Alerta métrica  budget-<FOUNDRY_ACCOUNT>  (TotalTokens > umbral, ventana 1 min)
        │  monitorCondition = Fired
        ▼
Action Group  (webhook, esquema común)
        │  POST del payload de alerta
        ▼
Azure Function  →  mecanismo por defecto (foundry)  →  POST /agents/<AGENT_ID>:disable  →  state = "disabled"
        │
        ▼
Agente bloqueado automáticamente
```

### 2.1 Estado ANTES

```powershell
$tok = az account get-access-token --scope "https://ai.azure.com/.default" --query accessToken -o tsv
Invoke-RestMethod -Uri "$FoundryEp/agents/$AgentId?api-version=v1" `
  -Headers @{ Authorization = "Bearer $tok" } | Select-Object id, state
# → state = enabled
```

### 2.2 Saturar el agente desde el portal de Foundry

Abre el **playground** del agente y envía este **prompt de saturación** (genera
miles de tokens, muy por encima del umbral). Si hace falta, mándalo 2–3 veces
seguidas dentro de la misma ventana de 1 minuto:

```text
Escribe un ensayo técnico de al menos 2000 palabras que explique en profundidad,
paso a paso y con ejemplos, la arquitectura completa de un sistema de bloqueo
automático de agentes de IA por presupuesto en Azure: incluye Azure Functions,
Azure Monitor, grupos de acciones, Cost Management, Microsoft Graph y ARM.
Desarrolla cada sección con el máximo detalle posible, añade ventajas,
inconvenientes, alternativas y un resumen final extenso. No omitas nada.
```

### 2.3 Esperar a que salte la alerta (~1–5 min)

Azure Monitor evalúa la métrica cada minuto. Cuando `TotalTokens` supera el umbral,
la alerta pasa a **Fired**, llama al Action Group y este a la Function. En el
portal: **Monitor → Alertas** → verás `budget-<FOUNDRY_ACCOUNT>` en `Fired`.

### 2.4 Comprobar que el agente se bloqueó SOLO

```powershell
Invoke-RestMethod -Uri "$FoundryEp/agents/$AgentId?api-version=v1" `
  -Headers @{ Authorization = "Bearer $tok" } | Select-Object id, state
# → state = disabled
```

**Prueba visual definitiva:** vuelve al playground e intenta usar el agente → el
servicio ya **no lo sirve**. No es un flag: es el estado nativo *enforced* por
Foundry.

Traza de que la Function se ejecutó (App Insights):

```powershell
az monitor app-insights query --app $FunctionApp --resource-group $Rg `
  --analytics-query "requests | where timestamp > ago(15m) | where name contains 'budget-alert' | project timestamp, resultCode | order by timestamp desc" `
  -o table
```

> **Mensaje:** nadie tocó nada tras enviar el prompt. El agente se bloqueó solo
> porque su consumo disparó la alerta. En producción ese mismo mecanismo se ata al
> **presupuesto de coste** real.

### 2.5 Desbloquear (revertir tras la demo)

El bloqueo **no se deshace solo** al resolverse la alerta (es intencionado — el
admin decide cuándo reactivar):

```powershell
$body = "{`"agentId`":`"$AgentId`",`"mechanism`":`"foundry`",`"action`":`"unblock`"}"
(Invoke-RestMethod -Uri $Url -Method Post -ContentType "application/json" -Body $body).results
Start-Sleep 5
Invoke-RestMethod -Uri "$FoundryEp/agents/$AgentId?api-version=v1" `
  -Headers @{ Authorization = "Bearer $tok" } | Select-Object id, state
# → state = enabled
```

> **Presupuesto vs. alerta métrica:** el presupuesto de Cost Management factura con
> horas de retraso, así que no sirve para una demo en vivo. Por eso el trigger usa
> la **alerta métrica** sobre `TotalTokens` (salta en 1–5 min). La lógica de bloqueo
> es idéntica.

---

## 3. Bloqueo nativo directo (mecanismo A, sin esperar la alerta)

Mismo mecanismo que el escenario estrella, pero invocando la Function a mano — útil
para enseñarlo al instante.

```powershell
# BLOQUEAR
$body = "{`"agentId`":`"$AgentId`",`"mechanism`":`"foundry`",`"action`":`"block`"}"
(Invoke-RestMethod -Uri $Url -Method Post -ContentType "application/json" -Body $body).results
# → success=true, detail: "Native state action :disable ... -> state=disabled (was enabled)"

# DESBLOQUEAR
$body = "{`"agentId`":`"$AgentId`",`"mechanism`":`"foundry`",`"action`":`"unblock`"}"
(Invoke-RestMethod -Uri $Url -Method Post -ContentType "application/json" -Body $body).results
# → state vuelve a enabled
```

Lo ejecuta la **identidad administrada de la Function, sin Global Admin**, y lo
*enforcea* el propio servicio. Es el bloqueo recomendado para agentes de Foundry.

---

## 4. (Secundario) Mecanismo C — Etiqueta ARM

Bloqueo **contundente** a nivel de cuenta: pone `MS-AOAI-Feature-Assistants=Disabled`
sobre la cuenta Foundry, lo que desactiva **todos** los assistants clásicos de esa
cuenta. Solo para comparar.

```powershell
# Estado ANTES (etiqueta ausente o Enabled)
az tag list --resource-id $Rid --query "properties.tags" -o json

# BLOQUEAR (solo mecanismo tag)
$body = "{`"agentId`":`"$AgentId`",`"mechanism`":`"tag`",`"action`":`"block`"}"
(Invoke-RestMethod -Uri $Url -Method Post -ContentType "application/json" -Body $body).results

az tag list --resource-id $Rid --query "properties.tags.\"MS-AOAI-Feature-Assistants\"" -o tsv
# → Disabled

# DESBLOQUEAR
$body = "{`"agentId`":`"$AgentId`",`"mechanism`":`"tag`",`"action`":`"unblock`"}"
(Invoke-RestMethod -Uri $Url -Method Post -ContentType "application/json" -Body $body).results
# → etiqueta vuelve a Enabled
```

---

## 5. (Secundario) Mecanismo B — Identidad de Entra

Deshabilita el `servicePrincipal` del agente (`accountEnabled=false`), cortando su
acceso a nivel de identidad.

> ⚠️ Para **identidades de agente de Foundry** (tipo `agentIdentity`, preview), esta
> operación **requiere Global Admin**: la identidad administrada recibe `403` aunque
> tenga `Application.ReadWrite.All`. Para **agentes clásicos** respaldados por un
> service principal normal, la identidad administrada con `Application.ReadWrite.All`
> es suficiente.

```powershell
$SpUrl = "https://graph.microsoft.com/v1.0/servicePrincipals/$AgentId?`$select=displayName,accountEnabled"
$gtok  = az account get-access-token --scope "https://graph.microsoft.com/.default" --query accessToken -o tsv

# Estado ANTES
Invoke-RestMethod -Uri $SpUrl -Headers @{ Authorization = "Bearer $gtok" }

# BLOQUEAR (solo mecanismo graph)
$body = "{`"agentId`":`"$AgentId`",`"mechanism`":`"graph`",`"action`":`"block`"}"
(Invoke-RestMethod -Uri $Url -Method Post -ContentType "application/json" -Body $body).results

# DESBLOQUEAR
$body = "{`"agentId`":`"$AgentId`",`"mechanism`":`"graph`",`"action`":`"unblock`"}"
(Invoke-RestMethod -Uri $Url -Method Post -ContentType "application/json" -Body $body).results
```

---

## 6. (Secundario) Manejo de errores

```powershell
# Sin agente → 422
try {
  Invoke-RestMethod -Uri $Url -Method Post -ContentType "application/json" -Body '{}'
} catch { $_.Exception.Response.StatusCode.value__ }   # → 422

# Mecanismo inválido → 400
$body = "{`"agentId`":`"$AgentId`",`"mechanism`":`"nope`",`"action`":`"block`"}"
try {
  Invoke-RestMethod -Uri $Url -Method Post -ContentType "application/json" -Body $body
} catch { $_.Exception.Response.StatusCode.value__ }   # → 400
```

---

## Resumen

| Escenario | Mecanismo | Qué demuestra |
|-----------|-----------|---------------|
| ⭐ Estrella (2) | A – estado nativo | Bloqueo **automático** por saturación de tokens, sin tocar nada |
| Directo (3) | A – estado nativo | El mismo bloqueo, invocado a mano |
| Etiqueta (4) | C – ARM tag | Bloqueo contundente a nivel de cuenta (comparación) |
| Identidad (5) | B – Entra | Corte a nivel de identidad (requiere GA para agentes Foundry) |
| Errores (6) | — | Validación de entradas (`422`/`400`) |

---

## Limpieza final

Deja el agente **enabled** tras la demo:

```powershell
$body = "{`"agentId`":`"$AgentId`",`"mechanism`":`"foundry`",`"action`":`"unblock`"}"
(Invoke-RestMethod -Uri $Url -Method Post -ContentType "application/json" -Body $body).results

$tok = az account get-access-token --scope "https://ai.azure.com/.default" --query accessToken -o tsv
Invoke-RestMethod -Uri "$FoundryEp/agents/$AgentId?api-version=v1" `
  -Headers @{ Authorization = "Bearer $tok" } | Select-Object id, state
# → state = enabled
```

Si probaste el Mecanismo C, revisa que la etiqueta quede en `Enabled`; si probaste
el B, que `accountEnabled=true`.
