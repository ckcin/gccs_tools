#!/usr/bin/env bash

PROFILE="gccs-partner"
SSO_URL="https://d-90661fb7b9.awsapps.com/start/#/"
ROLE="GCCS_Mission_Partner"

GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'

echo "================================================"
echo " AWS SSO Refresh: $ROLE"
echo " Start URL: $SSO_URL"
echo " Profile:   $PROFILE"
echo "================================================"

if ! command -v aws &> /dev/null; then
    echo -e "${RED}Error: AWS CLI v2 is required but not installed.${NC}"
    return 1 2>/dev/null || exit 1
fi

echo "Initiating browser authentication..."
if aws sso login --profile "$PROFILE"; then
    echo -e "${GREEN}# SSO Login successful.${NC}\n"
else
    echo -e "${RED}# SSO Login failed. Please check ~/.aws/config.${NC}"
    return 1 2>/dev/null || exit 1
fi

echo "Retrieving active identity..."
if ACCOUNT_INFO=$(aws sts get-caller-identity --profile "$PROFILE" 2>/dev/null); then
    echo "$ACCOUNT_INFO"
    echo -e "\n${GREEN}# Valid credentials active.${NC}"
else
    echo -e "\n${RED}# Unable to verify identity. Check account ID in config.${NC}"
    return 1 2>/dev/null || exit 1
fi

if [[ "${BASH_SOURCE[0]}" != "${0}" ]]; then
    export AWS_PROFILE="$PROFILE"
    echo -e "${GREEN}AWS_PROFILE set to '$PROFILE' in active terminal session.${NC}"
else
    echo -e "\nTo export credentials to your current shell session, run:"
    echo "  export AWS_PROFILE=$PROFILE"
fi
