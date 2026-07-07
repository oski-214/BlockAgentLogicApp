# Despliegue (`deploy/`)

Infraestructura como código (Bicep) + scripts para desplegar **toda** la solución
con permisos mínimos. La guía completa de despliegue está en el
**[`README.md`](../README.md)** raíz; aquí solo se describe el contenido de esta
carpeta.

## Contenido

| Archivo | Qué hace |
|---------|----------|
| `main.bicep` | Crea **todo** desde cero: cuenta Foundry (`AIServices`) + proyecto, storage (por identidad), Function App **Flex Consumption** (Python 3.11) + identidad administrada, Log Analytics + Application Insights, **Action Group** (webhook → `/api/budget-alert`) + **alerta métrica** (`TotalTokens`), y **todos los role assignments** (mecanismos A y C + storage). **No** crea modelo ni agente. |
| `main.parameters.json` | Parámetros genéricos (placeholders `CHANGEME`, `agentTargetMap` vacío). Rellénalos con nombres únicos. |
| `deploy.ps1` | `what-if` → despliega el Bicep → publica el código (`func ... --python`). |
| `grant-graph-permission.ps1` | Concede el permiso de Graph del Mecanismo B (necesita **Global Admin**). |

## Permisos que concede el Bicep (mínimo privilegio)

| Mecanismo | Permiso | Ámbito |
|-----------|---------|--------|
| A – Foundry | `Azure AI Developer` + `Cognitive Services User` | cuenta Foundry |
| C – Etiqueta ARM | `Tag Contributor` | cuenta Foundry |
| Runtime | `Storage Blob Data Owner` + `Storage Queue Data Contributor` | storage |
| B – Graph | `Application.ReadWrite.All` (**fuera del Bicep** → `grant-graph-permission.ps1`, Global Admin) | tenant (Graph) |

> **🔑 Mecanismo A:** `Azure AI Developer` por sí solo **no** cubre el data-plane de
> agentes (`.../agents/*`) → `403`. Por eso el Bicep asigna **también**
> `Cognitive Services User`. El bloqueo usa el **estado nativo** del agente
> (`POST /agents/{id}:disable` / `:enable`, `api-version=v1`), *enforced* por el
> servicio, ejecutado por la identidad administrada **sin Global Admin**.

## Uso rápido

```powershell
az login
# 1) Edita main.parameters.json (nombres únicos globalmente).
# 2) Valida sin desplegar:
az deployment group what-if -g rg-block-agent `
  --template-file deploy/main.bicep --parameters "@deploy/main.parameters.json"
# 3) Despliega + publica:
./deploy/deploy.ps1 -ResourceGroup rg-block-agent -Location swedencentral
# 4) (Opcional, Global Admin) Mecanismo B:
./deploy/grant-graph-permission.ps1 -PrincipalId <objectId-de-la-identidad>
```

Tras desplegar, crea el modelo y el agente en el portal de Foundry y rellena
`AGENT_TARGET_MAP` (ver [`README.md`](../README.md), sección "Pasos manuales en
Foundry").

## Nota de política del tenant

Si tu tenant **prohíbe la autenticación por clave compartida en Storage** y
**deshabilita el basic auth de SCM**, el plan de **Consumo clásico (Y1) no
funciona** (su content share necesita claves). Por eso el Bicep usa **Flex
Consumption** con storage por **identidad administrada**
(`AzureWebJobsStorage__accountName` + `__credential=managedidentity`,
`allowSharedKeyAccess:false`).
