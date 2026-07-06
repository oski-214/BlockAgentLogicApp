# Demo — Bloquear un agente cuando se supera el presupuesto

Guía **lista para enseñar al cliente**. La Azure Function ya está desplegada y
probada; solo tienes que copiar y pegar los comandos de cada escenario y mostrar
el resultado. Todo es **reversible y no destructivo**: cada bloqueo se puede
deshacer y ningún recurso se borra.

> **En una frase:** cuando un presupuesto de Azure se supera, una alerta llama a
> esta Function y esta **bloquea el agente** por uno de tres mecanismos. Es el
> equivalente automatizado al botón *"Block agent"* del Centro de Administración
> de M365 (que no tiene API pública).

---

## 0. Preparación (una sola vez, ~30 s)

Abre **PowerShell** y pega esto. Guarda la URL de la Function con su clave en una
variable para el resto de la demo.

```powershell
$az  = "C:\Program Files\Microsoft SDKs\Azure\CLI2\wbin\az.cmd"
# Inicia sesión si aún no lo estás:
& $az login   # (omite si ya tienes sesión)

# Recupera la clave de la Function y compón la URL
$key = & $az functionapp keys list --name fa-block-agent-jykza1 `
        --resource-group rg-block-agent --query "functionKeys.default" -o tsv
$Url = "https://fa-block-agent-jykza1.azurewebsites.net/api/budget-alert?code=$key"

# Recurso de Foundry y agente de la demo (identidad de Entra del agente publicado)
$Rid     = "/subscriptions/72dc9a1e-135b-49cb-86e6-80630340cade/resourceGroups/rg-agent-verse/providers/Microsoft.CognitiveServices/accounts/agent-verse-resource"
$AgentId = "39f26b00-03d9-4e0c-bd70-cdfa22f21df9"   # AgentVerseIntakeAgent
"Listo. URL preparada."
```

Datos del despliegue (por si el cliente pregunta):

| Elemento | Valor |
|----------|-------|
| Function App | `fa-block-agent-jykza1` (Flex Consumption, Python 3.11) |
| Grupo de recursos | `rg-block-agent` (swedencentral) |
| Recurso Foundry | `agent-verse-resource` / proyecto `agent-verse-project` |
| Agente de demo | `AgentVerseIntakeAgent` (identidad `39f26b00-…`) |

---

## Escenario 1 — La Function está viva

**Qué demuestras:** el servicio está desplegado y sabe qué mecanismos ofrece.

```powershell
Invoke-RestMethod -Uri "https://fa-block-agent-jykza1.azurewebsites.net/api/health"
```

**Resultado esperado:**

```json
{ "status": "ok", "mechanisms": ["foundry", "graph", "tag"] }
```

---

## Escenario 2 — Bloqueo por etiqueta ARM (mecanismo C) ✅ demo estrella

**Qué demuestras:** el bloqueo real de extremo a extremo y su reversión. Este es
el más visual porque puedes enseñar el cambio en el **portal de Azure**.

### 2.1 Estado ANTES

```powershell
& $az resource show --ids $Rid --query "tags" -o json
# Esperado: { "MS-AOAI-Feature-Assistants": "Enabled" }
```

### 2.2 BLOQUEAR

```powershell
$body = '{"agentId":"demo","mechanism":"tag","action":"block","budgetName":"budget-demo","spend":150,"budget":100}'
(Invoke-RestMethod -Uri $Url -Method Post -ContentType "application/json" -Body $body).results
```

**Resultado esperado:** `success = true`, `detail` indica
`MS-AOAI-Feature-Assistants=Disabled` y `previous_state = Enabled`.

Comprueba en el portal (o por CLI) que la etiqueta cambió:

```powershell
& $az resource show --ids $Rid --query "tags" -o json
# Esperado: { "MS-AOAI-Feature-Assistants": "Disabled" }
```

### 2.3 DESBLOQUEAR (revertir)

```powershell
$body = '{"agentId":"demo","mechanism":"tag","action":"unblock"}'
(Invoke-RestMethod -Uri $Url -Method Post -ContentType "application/json" -Body $body).results

Start-Sleep 15
& $az resource show --ids $Rid --query "tags" -o json
# Vuelve a: { "MS-AOAI-Feature-Assistants": "Enabled" }
```

> La etiqueta `MS-AOAI-Feature-Assistants` afecta a **todos** los assistants
> clásicos del recurso: es un bloqueo "grueso" pensado para comparación, pero es
> el más fácil de enseñar porque el estado se ve en el portal.

---

## Escenario 3 — Deshabilitar la identidad del agente (mecanismo B)

**Qué demuestras:** el equivalente más fiel al botón *"Block"* del Admin Center:
desactivar el inicio de sesión de la identidad de Entra del agente
(`accountEnabled=false`).

Hay que distinguir **dos tipos de agente**, porque el resultado es distinto:

### 3.a Agente clásico (agent-builder, service principal normal) — funciona solo

Para un agente respaldado por un **service principal normal**, la identidad
administrada de la Function lo deshabilita sola (tiene el permiso de Graph
`Application.ReadWrite.All`). Ya se ha **probado en vivo** con un SP de prueba:
la Function puso `accountEnabled=false` y luego lo revirtió a `true`. ✔

```powershell
# <spObjectId> = objectId del service principal del agente clásico
$body = '{"agentId":"<spObjectId>","mechanism":"graph","action":"block"}'
(Invoke-RestMethod -Uri $Url -Method Post -ContentType "application/json" -Body $body).results
```

### 3.b Agente de Foundry (preview) — lo bloquea un Global Admin

Los agentes que publicas en `agent-verse-project` (como `AgentVerseIntakeAgent`)
se respaldan en una **identidad de agente de Entra** (`agentIdentity`, en
preview). Estas identidades están **más protegidas**: solo un **Global
Administrator** puede deshabilitarlas. Demuéstralo con tu cuenta de admin:

```powershell
# Estado antes
& $az rest --method GET --uri "https://graph.microsoft.com/v1.0/servicePrincipals/$AgentId`?`$select=displayName,accountEnabled"

# BLOQUEAR (requiere Global Admin activo)
'{"accountEnabled":false}' | Set-Content "$env:TEMP\b.json" -Encoding ascii -NoNewline
& $az rest --method PATCH --uri "https://graph.microsoft.com/v1.0/servicePrincipals/$AgentId" `
   --headers "Content-Type=application/json" --body "@$env:TEMP\b.json"

# Comprobar: accountEnabled = false
& $az rest --method GET --uri "https://graph.microsoft.com/v1.0/servicePrincipals/$AgentId`?`$select=accountEnabled"

# DESBLOQUEAR (revertir)
'{"accountEnabled":true}' | Set-Content "$env:TEMP\b.json" -Encoding ascii -NoNewline
& $az rest --method PATCH --uri "https://graph.microsoft.com/v1.0/servicePrincipals/$AgentId" `
   --headers "Content-Type=application/json" --body "@$env:TEMP\b.json"
```

> **Mensaje para el cliente:** el mecanismo B ya funciona de forma autónoma para
> agentes clásicos. Para agentes de Foundry (preview), el bloqueo lo debe
> ejecutar un Global Admin; cuando Microsoft habilite la gestión de estas
> identidades vía permisos de aplicación, la Function lo hará sola sin cambios de
> código.

---

## Escenario 4 — Manejo de errores (robustez)

**Qué demuestras:** la Function valida la entrada y responde con códigos claros.

```powershell
# Sin agente -> 422
try { Invoke-RestMethod -Uri $Url -Method Post -ContentType "application/json" -Body '{}' }
catch { "HTTP $([int]$_.Exception.Response.StatusCode) (esperado 422: no se pudo determinar el agente)" }

# Mecanismo inexistente -> 400
try { Invoke-RestMethod -Uri $Url -Method Post -ContentType "application/json" -Body '{"agentId":"x","mechanism":"foo"}' }
catch { "HTTP $([int]$_.Exception.Response.StatusCode) (esperado 400: mecanismo inválido)" }
```

---

## Escenario 5 (opcional) — Todo sin Azure, en segundos

Para enseñar la lógica **sin tocar la nube** (útil si no hay conectividad):

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m tests.test_harness
```

**Resultado esperado:** `Ran 3 tests ... OK` — valida que los 3 mecanismos
bloquean y que el desbloqueo restaura el estado previo.

---

## Resumen de escenarios

| # | Escenario | Qué prueba | Estado |
|---|-----------|------------|--------|
| 1 | Salud | La Function está desplegada y viva | ✅ probado |
| 2 | Etiqueta ARM (C) | Bloqueo/desbloqueo real, visible en el portal | ✅ probado |
| 3.a | Identidad SP normal (B) | Deshabilitar agente clásico desde la Function | ✅ probado |
| 3.b | Identidad de agente Foundry (B) | Deshabilitar agente preview (requiere Global Admin) | ✅ probado con admin |
| 4 | Errores | Validación de entrada (422 / 400) | ✅ probado |
| 5 | Offline | Lógica completa sin Azure | ✅ 3/3 |

---

## Nota sobre el mecanismo A (Foundry REST)

Los agentes de `agent-verse-project` usan el **Foundry Agent Service** (agentes
persistentes con `state` y versiones), cuya API difiere del marcado simple de
`metadata` que implementa hoy `blockagent/mechanisms/foundry.py`. Para estos
agentes el bloqueo efectivo es el **mecanismo B** (identidad). Adaptar el
mecanismo A a la API nueva de agentes es una mejora pendiente y no bloquea la
demo. Los detalles técnicos completos están en [`deploy/README.md`](deploy/README.md).

---

## Después de la demo (limpieza)

Deja siempre el recurso en su estado original:

```powershell
# La etiqueta debe quedar Enabled
& $az resource show --ids $Rid --query "tags" -o json
# La identidad del agente debe quedar accountEnabled = true
& $az rest --method GET --uri "https://graph.microsoft.com/v1.0/servicePrincipals/$AgentId`?`$select=accountEnabled"
```
