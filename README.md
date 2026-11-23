# ElectoRadar

Projet S6 — module Cloud Computing, ESI Rabat (thème : Élections).

ElectoRadar repère les rumeurs électorales qui circulent sur X et dans la presse en ligne, les regroupe, calcule un score de viralité et prévient la commission électorale quand une rumeur dépasse le seuil d'alerte. Un fact-checkeur peut ensuite qualifier la rumeur et publier un démenti.

Réalisé par Hilali Salma, Benhaddou Safae, Rian Nada, Oukaddi Douae et Larhlimi Aya, sous l'encadrement de Pr. Imane Hilal.

## Architecture

| Fournisseur | Services | Rôle |
|---|---|---|
| Cloudflare | Workers, Cron Triggers, Queues, Workers KV | collecte toutes les 2 minutes, file d'attente, doublons |
| Microsoft Azure | VM (modèle NLP), Functions, Blob Storage, Queue Storage, PostgreSQL, App Service | analyse, regroupement, score, alertes, API |
| Google Firebase | Hosting, Firestore, Authentication | tableau de bord public en direct, espace fact-checkeur |
| SaaS | API X, Telegram, Brevo | source des données, notifications |

Tout tourne sur des offres gratuites (Cloudflare Free, Azure for Students, Firebase Spark).

## Organisation du dépôt

```
cloudflare/collecte        Worker de collecte (X, RSS) + déclenchement du rapport
cloudflare/normalisation   Worker qui lit la file : langue, doublons, pseudonymes
azure/vm-modele            serveur du modèle (FastAPI) et entraînement du classifieur
azure/fonctions            Azure Functions : analyze, score, alerte, rapport, reinitialiser
azure/api                  API Flask déployée sur App Service
firebase                   site public et espace fact-checkeur, règles Firestore
data                       jeu d'entraînement et publications de test
tests                      tests locaux (sans cloud)
build.py                   prépare les paquets à déployer dans dist/
```

## Données de test

`data/publications_test.json` contient 25 publications fictives écrites pour la démonstration (français, arabe, darija, arabizi). En mode `replay`, le Worker de collecte les envoie par lots de 4 toutes les 2 minutes, comme si elles arrivaient en direct.

## Secrets

Aucune clé n'est dans le dépôt. Les valeurs sont configurées directement dans chaque cloud :

- Cloudflare (`wrangler secret put`) : `ADMIN_TOKEN`, `AZURE_BLOB_SAS_URL`, `ANALYZE_URL`, `RAPPORT_URL`, `RESET_URL`, `PSEUDO_SECRET`, `X_BEARER_TOKEN`
- Azure Functions : `DATABASE_URL`, `VM_URL`, `VM_API_KEY`, `QUEUE_STORAGE`, `FIREBASE_SA_JSON`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `BREVO_API_KEY`, `MAIL_FROM`, `ALERT_EMAIL`, `REPORT_EMAIL`
- App Service : `DATABASE_URL`, `FIREBASE_PROJECT`, `FIREBASE_SA_JSON`, `FACT_CHECKERS`, `ALLOWED_ORIGIN`

## Tests locaux

```
python tests/azure_local.py
node tests/cloudflare_local.mjs
```
