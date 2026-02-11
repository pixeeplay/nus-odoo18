#!/bin/bash
# ===========================================
# Installation des modules OCA pour Odoo 18
# ===========================================

set -e

ADDONS_DIR="./addons"
BRANCH="18.0"

# Couleurs
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${GREEN}=== Installation des modules OCA pour Odoo 18 ===${NC}"

# Créer le dossier addons s'il n'existe pas
mkdir -p $ADDONS_DIR

cd $ADDONS_DIR

# Fonction pour cloner un repo OCA
clone_oca_repo() {
    REPO=$1
    if [ -d "$REPO" ]; then
        echo -e "${YELLOW}Mise à jour de $REPO...${NC}"
        cd $REPO && git pull && cd ..
    else
        echo -e "${GREEN}Clonage de $REPO...${NC}"
        git clone --depth 1 --branch $BRANCH https://github.com/OCA/$REPO.git || echo "Branche $BRANCH non disponible pour $REPO"
    fi
}

# ===========================================
# Repos OCA recommandés
# ===========================================

# Web & UI
clone_oca_repo "web"

# Product Management
clone_oca_repo "product-attribute"

# Stock & Inventory
clone_oca_repo "stock-logistics-workflow"

# Connector & Queue
clone_oca_repo "queue"

# Server tools
clone_oca_repo "server-tools"

# Reporting
clone_oca_repo "reporting-engine"

# E-commerce (si besoin PrestaShop)
# clone_oca_repo "connector-ecommerce"

echo ""
echo -e "${GREEN}=== Installation terminée ===${NC}"
echo ""
echo "Modules installés dans $ADDONS_DIR"
echo ""
echo "Prochaines étapes :"
echo "1. Redémarrer Odoo"
echo "2. Activer le mode développeur"
echo "3. Mettre à jour la liste des applications"
echo "4. Installer les modules souhaités"
