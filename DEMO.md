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

# Identidad del agente de demo
$AgentId = "39f26b00-03d9-4e0c-bd70-cdfa22f21df9"

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
| Agente demo | `AgentVerseIntakeAgent` |

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

# Resumen

| Escenario | Qué demuestra |
|------------|---------------|
| Salud | La Function está desplegada |
| Etiqueta ARM | Bloqueo/desbloqueo visible en Azure |
| Identidad SP | Deshabilitar un agente clásico |
| Identidad Foundry | Deshabilitar un agente preview |
| Errores | Robustez y validación |
| Offline | Lógica sin dependencia de Azure |

---

# Nota sobre el mecanismo A (Foundry REST)

Los agentes de `agent-verse-project` usan Foundry Agent Service y no la API clásica basada en Assistants.

Actualmente el bloqueo efectivo de estos agentes se consigue mediante el **mecanismo B (identidad)**.

La adaptación del mecanismo A a la API moderna de Foundry Agents queda como mejora futura y no bloquea la demo.

---

# Limpieza final

Verificar que la etiqueta ha vuelto a Enabled:

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
