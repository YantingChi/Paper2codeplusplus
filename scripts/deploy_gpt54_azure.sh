#!/usr/bin/env bash
set -euo pipefail

SUBSCRIPTION_ID="${SUBSCRIPTION_ID:-3d34634e-2b3a-4da3-8182-46c2164df3bb}"
RESOURCE_GROUP="${RESOURCE_GROUP:-csci8980-spring26}"
ACCOUNT_NAME="${ACCOUNT_NAME:-csci8980-group11-resource}"
DEPLOYMENT_NAME="${DEPLOYMENT_NAME:-gpt-5.4}"
MODEL_NAME="${MODEL_NAME:-gpt-5.4}"
MODEL_VERSION="${MODEL_VERSION:-2026-03-05}"
MODEL_PUBLISHER="${MODEL_PUBLISHER:-OpenAI}"
SKU_NAME="${SKU_NAME:-GlobalStandard}"
CAPACITY="${CAPACITY:-1}"
CONTENT_FILTER_POLICY="${CONTENT_FILTER_POLICY:-Microsoft.DefaultV2}"

if ! command -v az >/dev/null 2>&1; then
  echo "Azure CLI is required. Install it first, then rerun this script."
  exit 1
fi

az extension add -n cognitiveservices --only-show-errors >/dev/null
az account set --subscription "$SUBSCRIPTION_ID"

echo "Checking whether $MODEL_NAME is visible for $ACCOUNT_NAME ..."
az cognitiveservices account list-models \
  -n "$ACCOUNT_NAME" \
  -g "$RESOURCE_GROUP" \
  --query "[?name=='$MODEL_NAME'].[name,version,skus[0].name]" \
  -o table

TEMPLATE_FILE="$(mktemp)"
trap 'rm -f "$TEMPLATE_FILE"' EXIT

cat >"$TEMPLATE_FILE" <<'EOF'
@description('Name of the Azure AI services account')
param accountName string

@description('Deployment name to create')
param deploymentName string

@description('Name of the model to deploy')
param modelName string

@description('Version of the model to deploy')
param modelVersion string

@allowed([
  'AI21 Labs'
  'Cohere'
  'Core42'
  'DeepSeek'
  'xAI'
  'Meta'
  'Microsoft'
  'Mistral AI'
  'OpenAI'
])
@description('Model provider')
param modelPublisherFormat string

@allowed([
  'GlobalStandard'
  'DataZoneStandard'
  'Standard'
  'GlobalProvisioned'
  'Provisioned'
])
@description('Model deployment SKU name')
param skuName string = 'GlobalStandard'

@description('Content filter policy name')
param contentFilterPolicyName string = 'Microsoft.DefaultV2'

@description('Model deployment capacity')
param capacity int = 1

resource modelDeployment 'Microsoft.CognitiveServices/accounts/deployments@2024-04-01-preview' = {
  name: '${accountName}/${deploymentName}'
  sku: {
    name: skuName
    capacity: capacity
  }
  properties: {
    model: {
      format: modelPublisherFormat
      name: modelName
      version: modelVersion
    }
    raiPolicyName: contentFilterPolicyName == null ? 'Microsoft.Nill' : contentFilterPolicyName
  }
}
EOF

echo "Creating deployment $DEPLOYMENT_NAME ..."
az deployment group create \
  --resource-group "$RESOURCE_GROUP" \
  --template-file "$TEMPLATE_FILE" \
  --parameters \
    accountName="$ACCOUNT_NAME" \
    deploymentName="$DEPLOYMENT_NAME" \
    modelName="$MODEL_NAME" \
    modelVersion="$MODEL_VERSION" \
    modelPublisherFormat="$MODEL_PUBLISHER" \
    skuName="$SKU_NAME" \
    capacity="$CAPACITY" \
    contentFilterPolicyName="$CONTENT_FILTER_POLICY"

echo "Deployment status:"
az cognitiveservices account deployment show \
  --deployment-name "$DEPLOYMENT_NAME" \
  -n "$ACCOUNT_NAME" \
  -g "$RESOURCE_GROUP" \
  --query "properties.provisioningState" \
  -o tsv
