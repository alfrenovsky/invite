#!/usr/bin/env bash
set -e

# Configuration
REMOTE_HOST="root@nos.vamos.acas.ar"
REMOTE_DIR="/home/alfredo/invite"
BRANCH="main"

# Commit message (use argument if provided, otherwise default timestamp)
COMMIT_MSG="${1:-deploy: $(date '+%Y-%m-%d %H:%M:%S')}"

echo "========================================="
echo "🚀 Starting Deployment Process"
echo "========================================="

# 1. Commit local changes if any exist
if [[ -n $(git status --porcelain) ]]; then
    echo "📦 Staging and committing changes..."
    git add .
    git commit -m "$COMMIT_MSG"
else
    echo "✨ No local changes to commit."
fi

# 2. Push to origin
echo "⬆️ Pushing local commits to origin/${BRANCH}..."
git push origin "$BRANCH"

# 3. Connect via SSH and pull on the server
echo "🌐 Connecting to ${REMOTE_HOST}..."
ssh "$REMOTE_HOST" "cd ${REMOTE_DIR} && echo '⬇️ Pulling latest changes...' && git pull origin ${BRANCH}"

echo "========================================="
echo "✅ Deployment completed successfully!"
echo "========================================="
