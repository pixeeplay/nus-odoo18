<div align="center">
  <img src="static/description/icon.png" width="128" height="128" alt="Odoo AI Enrichment Icon">
  <h1>Odoo AI Enrichment & Market Intelligence</h1>
  <p><i>The Ultimate AI-Powered Product Management Suite for Odoo 18</i></p>
</div>

---

# 🚀 English Documentation

### Overview
Transform your Odoo product catalog with advanced AI capabilities. This module doesn't just "generate descriptions"—it performs real-world market research, tracks your competitors' prices in France, and automatically aligns your pricing strategy to keep you competitive 24/7.

### 🌟 Key Features
- 🤖 **Universal AI Support**: Connect to OpenAI (GPT-4o), Google Gemini, Anthropic Claude, Perplexity, or run locally with Ollama/Llama.cpp.
- 🔍 **Deep Market Research**: Uses **SerpApi** to find real technical specs and **ScrapingBee** to extract data from competitor websites.
- 💸 **Smart Price Alignment**: 
  - Tracks up to 5+ French competitors per product.
  - Automatically calculates suggested prices based on **Match Lowest** or **Match Average** strategies.
  - Support for **Fixed amount** (e.g., -0.01€) or **Percentage** (e.g., -5%) offsets.
- � **Full Automation**: Scheduled cron job (2h, 4h, or 24h) to refresh market data and update prices while you sleep.
- � **20+ Expert Prompts**: Pre-loaded templates for SEO, E-commerce, Instagram, Amazon style bullets, FAQ, and technical mapping.
- 🎥 **Media Discovery**: Automatically extracts official YouTube videos and images from the web.
- 📋 **Rich Dashboard**: Interactive "Deep Enrichment" tab with raw search results, snippets, and activity logs.

### 🛠️ Configuration
1. **Providers**: Go to **Settings > AI Enrichment** to create a configuration. 
2. **Technical Keys**: Add your SerpApi and ScrapingBee keys for the "Deep Enrichment" features.
3. **Prompts**: Activate the templates you need in the **Prompts tab**.
4. **Scheduler**: Enable "Automated Enrichment" and choose your frequency to start the price-bot.

### 📊 Dashboard View
*   **Enrichment Log**: See every step of the AI logic.
*   **Market Data**: Interactive list of Google results found for your product.
*   **Price Comparison**: Visual alert showing your current price vs the AI suggested market price.

---

# 🇫🇷 Documentation Française

### Présentation
Transformez votre catalogue Odoo avec l'IA la plus avancée. Ce module ne se contente pas de "rédiger des descriptions" : il effectue des études de marché en temps réel, suit les prix de vos concurrents en France et aligne automatiquement vos tarifs pour vous maintenir compétitif 24h/24.

### 🌟 Fonctionnalités Clés
- 🤖 **Multi-IA Universel** : Connectez-vous à OpenAI (GPT-4o), Google Gemini, Anthropic Claude, Perplexity, ou utilisez l'IA locale avec Ollama/Llama.cpp.
- 🔍 **Étude de Marché Approfondie** : Utilise **SerpApi** pour trouver les fiches techniques et **ScrapingBee** pour extraire les données des sites concurrents.
- 💸 **Alignement des Prix Intelligent** : 
  - Suit plus de 5 concurrents français par produit.
  - Calcule automatiquement un prix suggéré (Stratégies **Prix le plus bas** ou **Moyenne**).
  - Support des décalages en **Montant fixe** (ex: -0.01€) ou **Pourcentage** (ex: -5%).
- 🕒 **Automatisation Totale** : Tâche planifiée (toutes les 2h, 4h ou 24h) pour rafraîchir les données de marché et mettre à jour vos prix sans intervention.
- 📚 **20+ Prompts Experts** : Gabarits pré-chargés pour le SEO, l'E-commerce, Instagram, le style Amazon, les FAQ et le mapping technique.
- 🎥 **Découverte de Médias** : Extraction automatique des vidéos YouTube officielles et des images depuis le web.
- 📋 **Dashboard Riche** : Onglet "Deep Enrichment" interactif avec résultats de recherche Google bruts et logs d'activité.

### 🛠️ Configuration
1. **Fournisseurs** : Allez dans **Configuration > AI Enrichment** pour créer une config. 
2. **Clés Techniques** : Ajoutez vos clés SerpApi et ScrapingBee pour activer la recherche approfondie.
3. **Prompts** : Activez les modèles dont vous avez besoin dans l'onglet **Prompts**.
4. **Planificateur** : Activez l'"Automated Enrichment" et choisissez votre fréquence pour lancer le bot de prix.

### 📊 Tableau de Bord
*   **Log d'Enrichissement** : Suivez chaque étape de la réflexion de l'IA.
*   **Données Marché** : Liste interactive des résultats Google trouvés pour votre produit.
*   **Comparaison de Prix** : Alerte visuelle comparant votre prix actuel avec la suggestion IA.

---

### 🛡️ Credits & License
- **Author**: Pixeeplay
- **License**: LGPL-3
- **Repository**: [https://github.com/pixeeplay/nus-odoo18](https://github.com/pixeeplay/nus-odoo18)
