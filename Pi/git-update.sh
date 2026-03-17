#!/bin/bash
# Script de mise à jour au démarrage

set -e

# Charger les variables d'environnement
set -a
[ -f /home/sysop/tibeer/.env ] && . /home/sysop/tibeer/.env
set +a

# Vérifier si les variables sont définies
if [ -z "$GIT_REPO" ] || [ -z "$GIT_BRANCH" ]; then
    echo "⚠️  GIT_REPO ou GIT_BRANCH non défini dans .env, saut de la mise à jour"
    exit 0
fi

echo "🔄 Mise à jour depuis $GIT_REPO (branche: $GIT_BRANCH)..."

cd /home/sysop/tibeer

# Sauvegarder les modifications locales non commitées
if [ -n "$(git status --porcelain)" ]; then
    echo "💾 Sauvegarde des modifications locales..."
    git stash push -m "Auto-stash avant git pull $(date)"
    STASHED=true
else
    STASHED=false
fi

# Faire le git pull
echo "⬇️  Git pull..."
if git pull origin "$GIT_BRANCH"; then
    echo "✅ Mise à jour réussie"
    
    # Restaurer les modifications locales si elles existent
    if [ "$STASHED" = true ]; then
        echo "📦 Restauration des modifications locales..."
        git stash pop || echo "⚠️  Conflit lors de la restauration du stash"
    fi
else
    echo "❌ Erreur lors du git pull"
    exit 1
fi

# Mettre à jour les permissions
chown -R sysop:sysop /home/sysop/tibeer

echo "✅ Mise à jour terminée"
exit 0