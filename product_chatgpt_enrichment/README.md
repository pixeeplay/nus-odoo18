<div align="center">
  <img src="static/description/icon.png" width="128" height="128" alt="Odoo AI Enrichment Icon">
  <h1>Odoo AI Enrichment & Market Intelligence</h1>
  <p><i>The Ultimate AI-Powered Product Management Suite for Odoo 18</i></p>
</div>

---

# 🇬🇧 English Documentation

## 1. Overview
The **Odoo AI Enrichment** module is a professional-grade suite designed to automate the lifecycle of product data. It goes beyond simple text generation by integrating real-time market research, competitive price tracking, and intelligent pricing automation.

---

## 2. Core Workflows
The module operates in three distinct, sequential phases:

### Phase A: Technical Research (Web Search)
The AI uses **Perplexity** or a similar Search-enabled model to scan the web for the product's technical specifications.
- **Input**: Product Name.
- **Output**: A clean HTML summary of specs (dimensions, weight, features).
- **Goal**: Provide the "ground truth" to the subsequent AI steps.

### Phase B: Market Intelligence (Deep Enrichment)
This phase uses **SerpApi (Google Search)** and **ScrapingBee** to dig deeper.
1.  **Discovery**: Odoo searches Google for the product name.
2.  **Scraping**: It visits the top 5 websites found (e.g., Amazon, Fnac, Leroy Merlin).
3.  **Extraction**: It extracts raw content from these pages to find real-world pricing and media.

### Phase C: AI Processing & Mapping
The AI combines the technical research (Phase A) and the scraped market data (Phase B) to:
- Generate SEO descriptions.
- Detect competitor prices.
- Identify official YouTube videos.
- Map numeric fields (Weight, Volume) directly to Odoo products.

---

## 3. Market Price Alignment & Automation
This is the most powerful feature for e-commerce managers.

### How Pricing Intelligence Works:
- **Competitor Tracking**: The system stores up to 5 individual competitor prices with their source URL in a dedicated list.
- **Suggested Price**: Odoo calculates a "Suggested Price" in real-time based on your strategy:
    - **Match Lowest**: Targets the lowest price found among competitors.
    - **Match Average**: Targets the average market price.
- **Smart Offset**: Apply a "Correction" to be more competitive:
    - **Fixed Amount**: "-0.01" to be 1 cent cheaper than the lowest.
    - **Percentage**: "-5.0" to be 5% cheaper.

### Fully Automated Scheduler (The "Price Bot"):
In the configuration, enable **Automated Enrichment**.
- **Intervals**: Run the market scan every **2h, 4h, or 24h**.
- **Execution**: The system will automatically refresh prices and descriptions for all products where **"Auto-Align Market Price"** is checked.
- **Logs**: Every automated change is logged in the product Chatter for full traceability.

---

## 4. The Expert Prompt Library
The module comes pre-loaded with **20 expert prompts**. You can activate or customize them in the "Prompts" tab:
1.  **SEO Description (Long)**: Optimized for search engines with H2/UL tags.
2.  **Short Pitch**: For mobile apps or quick previews.
3.  **JSON Technical Mapping**: Extracts numbers (weight, specs) for automatic field updates.
4.  **Pros & Cons**: Based on real user reviews found online.
5.  **Instagram/Social Post**: Ready-to-use social media copy with emojis.
6.  **FAQ Generator**: Generates 3 relevant questions/answers for the product.
7.  **Translations**: Marketing-level translations into EN, FR, DE, ES.

---

## 5. UI Components Breakdown
- **AI Enrichment Tab**: The main hub. Contains the Activity Log (Chatter-like AI history) and the list of active prompts.
- **Web Search Tab**: Shows the real-time technical research results found by the AI.
- **Deep Enrichment Tab**: 
    - **Competitor Prices**: Real-time list of detected prices with direct links to competitor pages.
    - **Market Research Details**: The raw list of Google results used for the scraping phase.
    - **Official Media**: Auto-discovered YouTube videos and images.

---

## 6. Setup Guide
1.  **API Keys**: 
    - **AI**: OpenAI or Gemini Key.
    - **Search**: SerpApi Key (Essential for finding competitors).
    - **Scrape**: ScrapingBee Key (Essential for "Reading" competitor pages).
2.  **Target Competitors**: (Optional) In Settings, list domains like `amazon.fr` or `darty.com` to force the AI to look at these sources first.
3.  **Model Selection**: We recommend **GPT-4o** or **GPT-4o-mini** for the best balance of cost and quality.

---

# 🇫🇷 Documentation Française

## 1. Vue d'ensemble
Le module **Odoo AI Enrichment** est une suite professionnelle conçue pour automatiser le cycle de vie des données produits. Il va au-delà de la simple génération de texte en intégrant des études de marché en temps réel, le suivi des prix concurrents et l'automatisation intelligente des tarifs.

---

## 2. Flux de Travail (Workflows)
Le module fonctionne en trois phases distinctes et séquentielles :

### Phase A : Recherche Technique (Web Search)
L'IA utilise **Perplexity** ou un modèle similaire avec accès Web pour scanner les spécifications techniques du produit.
- **Entrée** : Nom du produit.
- **Sortie** : Un résumé HTML propre (dimensions, poids, caractéristiques).
- **But** : Fournir une "vérité technique" aux étapes suivantes.

### Phase B : Intelligence Marché (Deep Enrichment)
Cette phase utilise **SerpApi (Google Search)** et **ScrapingBee** pour approfondir la recherche.
1.  **Découverte** : Odoo cherche le produit sur Google.
2.  **Scraping** : Il visite les 5 sites les plus pertinents (ex: Amazon, Fnac, Boulanger).
3.  **Extraction** : Il extrait le contenu brut de ces pages pour trouver les prix réels et les médias.

### Phase C : Traitement IA & Mapping
L'IA combine les recherches techniques (Phase A) et les données marché (Phase B) pour :
- Générer des descriptions SEO.
- Détecter les prix des concurrents.
- Identifier les vidéos YouTube officielles.
- Mapper les champs numériques (Poids, Volume) directement sur le produit Odoo.

---

## 3. Alignement des Prix & Automatisation
C'est la fonctionnalité la plus puissante pour les e-commerçants.

### Fonctionnement de l'Intelligence Tarifaire :
- **Suivi Concurrents** : Le système stocke jusqu'à 5 prix concurrents individuels avec leur URL source.
- **Prix Suggéré** : Odoo calcule un "Prix Suggéré" en temps réel selon votre stratégie :
    - **Match Lowest** : S'aligne sur le prix le plus bas trouvé.
    - **Match Average** : S'aligne sur le prix moyen du marché.
- **Offset Intelligent** : Appliquez une "Correction" pour être plus compétitif :
    - **Montant Fixe** : "-0.01" pour être 1 centime moins cher que le plus bas.
    - **Pourcentage** : "-5.0" pour être 5% moins cher.

### Planificateur Automatique (Le "Price Bot") :
Dans la configuration, activez l'**Automated Enrichment**.
- **Intervalles** : Lancez le scan de marché toutes les **2h, 4h ou 24h**.
- **Exécution** : Le système rafraîchira automatiquement les prix et descriptions pour tous les produits où **"Auto-Align Market Price"** est coché.
- **Historique** : Chaque changement automatique est tracé dans le Chatter du produit.

---

## 4. Bibliothèque de 20 Prompts Experts
Le module est livré avec **20 prompts experts** pré-chargés que vous pouvez activer ou modifier :
1.  **Description eCommerce Longue** : Optimisée SEO avec balises H2/UL.
2.  **Pitch de Vente Court** : Pour les apps mobiles ou aperçus rapides.
3.  **Mapping Technique JSON** : Extrait les chiffres (poids, specs) pour mise à jour automatique.
4.  **Points Forts/Faibles** : Basé sur les avis clients réels trouvés en ligne.
5.  **Post Instagram** : Texte prêt à l'emploi avec emojis.
6.  **Générateur de FAQ** : Génère 3 questions/réponses pertinentes.
7.  **Traductions** : Traductions marketing de haute qualité vers EN, FR, DE, ES.

---

## 5. Composants de l'Interface
- **Onglet AI Enrichment** : Le centre de contrôle. Contient le log d'activité et la liste des prompts actifs.
- **Onglet Web Search** : Affiche les résultats de la recherche technique trouvés par l'IA.
- **Onglet Deep Enrichment** : 
    - **Competitor Prices** : Liste en temps réel des prix détectés avec liens directs.
    - **Market Research Details** : Liste brute des résultats Google utilisés pour le scraping.
    - **Official Media** : Vidéos YouTube et images découvertes automatiquement.

---

## 6. Guide de Configuration
1.  **Clés API** : 
    - **IA** : Clé OpenAI ou Gemini.
    - **Search** : Clé SerpApi (Essentiel pour trouver les concurrents).
    - **Scrape** : Clé ScrapingBee (Essentiel pour "Lire" les pages concurrentes).
2.  **Concurrents Cibles** : (Optionnel) Dans les réglages, listez les domaines comme `amazon.fr` ou `cdiscount.com` pour forcer l'IA à regarder ces sources en priorité.
3.  **Modèle** : Nous recommandons **GPT-4o** pour le meilleur rapport qualité/prix.

---

### 🛡️ Crédits & Licence
- **Auteur** : Pixeeplay
- **Licence** : LGPL-3
- **Dépôt** : [https://github.com/pixeeplay/nus-odoo18](https://github.com/pixeeplay/nus-odoo18)
