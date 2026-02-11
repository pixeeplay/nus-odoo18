FTP/SFTP/IMAP Tariff Import (Odoo 18)
=================================

Module Odoo pour importer des tarifs (prix de vente ``list_price``) depuis des fichiers CSV UTF‑8 stockés sur des serveurs FTP/SFTP, ou depuis des pièces jointes d'e-mails via IMAP.

Fonctionnalités
---------------

- Gestion de plusieurs fournisseurs FTP/SFTP/IMAP (CRUD, multi‑société).
- Test de connexion et aperçu des fichiers disponibles.
- Sélection précise des fichiers à télécharger et/ou traiter.
- Téléchargement local (un fichier ou un ZIP de plusieurs).
- Source IMAP: recherche par critères, pièces jointes filtrées par ``file_pattern``, marquage « Vu » et déplacement automatique vers les boîtes « Processed »/« Error » (optionnel).
- Import CSV en streaming (volumes importants), avec mise à jour des ``list_price``.
- Résolution et nettoyage des code‑barres dupliqués (produits en conflit).
- Job planifié quotidien à 07:00.
- Journaux d’import détaillés.

Emplacement
-----------

- addons path: ``Doscaal/ftp/ftp_tariff_import``

Dépendances
-----------

- Odoo 18.0
- Modules: ``base``, ``product``
- Python: ``paramiko`` (pour SFTP)
- IMAP: aucune dépendance externe (``imaplib`` et ``email`` de la bibliothèque standard)

  Pour installer la dépendance Python ::

    pip install paramiko

Fonctionnement
--------------

1) Fournisseurs FTP/SFTP/IMAP

   - Menu: Produits » Configuration » FTP/SFTP/IMAP Tariff Import » Fournisseurs FTP/SFTP/IMAP
   - Créer un fournisseur par serveur :

     - Protocole (ftp/sftp/imap), hôte, port.
     - Authentification: utilisateur/mot de passe et/ou clé privée PEM (passphrase optionnelle).
     - Options: passive (FTP), timeout, retries, keepalive (SFTP), host key fingerprint (optionnel).
     - Répertoires: ``remote_dir_in``, ``remote_dir_processed``, ``remote_dir_error``.
     - Filtrage: ``file_pattern`` (ex: ``tarifs_*.csv``), ``exclude_pattern`` (optionnel).
     - CSV: délimiteur (``;`` par défaut), en‑tête (oui/non), séparateur décimal, colonnes code‑barres candidates, colonne prix.
     - Multi‑société: ``company_id`` associé.
     - ``auto_process``: si coché, le cron 07:00 traitera automatiquement.
     - Onglet « Planification »: expose ``auto_process`` et ``schedule_level``; le cron quotidien à 07:00 s'appuie sur ces réglages.
     - ``max_files_per_run`` et ``max_preview`` pour contrôler la charge.

2) Test / Aperçu / Téléchargement

   - Dans la fiche fournisseur :
     - « Tester la connexion » : ouvre le wizard d’aperçu pré‑rempli si succès.
     - « Aperçu des fichiers » : liste des fichiers avec cases à cocher, filtre texte, tri par date.
     - « Aperçu du contenu » : affiche les N premières lignes d’un fichier (N configurable).
     - « Télécharger en local » : télécharge le(s) fichier(s) sélectionné(s) (ZIP si plusieurs).

3) Import manuel

   - Wizard « Importer maintenant » (bouton sur la fiche fournisseur) :
     - Mode « pattern » (traite tous les fichiers correspondant au pattern).
     - Ou liste de chemins distants fournis manuellement.

4) Import planifié

   - Un cron quotidien (07:00) appelle l’import pour chaque fournisseur actif avec ``auto_process=True``.
   - Verrouillage par fournisseur via advisory lock pour éviter les chevauchements.

Règles d’import et mapping
--------------------------

- Format des fichiers: CSV UTF‑8.

  - Par défaut: délimiteur « ; », en‑tête présent.
  - Exemple de colonnes: ``Code barre 1;Prix de vente``.

- Identifiant produit: code‑barres.

  - Le module teste les colonnes candidates par fournisseur (ex: « Code barre 1..6 », « barcode », « ean », « ean13 »).

- Doublons de code‑barres en base:

  - Si un même code‑barres est présent sur ≥ 2 produits, le module vide le champ ``barcode`` de ces produits (``product.product.barcode`` → ``False``) et ignore ces lignes pour le run en cours.
  - Un log liste les corrections réalisées.

- Cible de mise à jour: ``list_price`` (``product.template``).

  - Agrégation « dernier prix gagne » si plusieurs lignes pour le même template dans un fichier.
  - Arrondi selon précision monétaire de la société.

- Volumétrie:

  - Lecture en streaming, résolution par lots (chunks) de 2 000 à 5 000 codes‑barres.
  - Écritures par template.

Règles de qualité appliquées et colonnes virtuelles (mapping)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

- Les règles suivantes sont appliquées dans les deux flux (standard et mapping) au moment de l’import:

  - ``ref_clean``: référence épurée dérivée du nom de fichier (sans extension), ne contenant que A‑Z et 0‑9.
  - ``date_du_jour``: date courante (``fields.Date.context_today(self)``) respectant le contexte Odoo.
  - Déduplication intra‑fichier par (référence normalisée, code‑barres): si plusieurs lignes partagent la même paire, une seule est conservée.

- Dans le flux mapping, deux colonnes virtuelles sont disponibles même si absentes du CSV:

  - ``ref_clean``: valeur calculée ci‑dessus.
  - ``date_du_jour``: date courante au format de date Odoo.

Vous pouvez utiliser ces noms de colonnes dans le mappage (casse insensible). Un bloc « Règles appliquées » est ajouté au journal HTML avec les compteurs.

Menus et sécurité
-----------------

- Menus :

  - Produits » Configuration » FTP/SFTP/IMAP Tariff Import » Fournisseurs FTP/SFTP/IMAP.
  - Produits » Configuration » FTP Tariff Import » Journaux d’import.

- Groupes :

  - « FTP Tariff User » : lecture fournisseurs, lancement des wizards.
  - « FTP Tariff Manager » : CRUD fournisseurs, lancement import, lecture logs.

- Multi‑société :

  - Record rules limitant la visibilité aux sociétés de l’utilisateur.

Journaux
--------

- Modèle: ``ftp.tariff.import.log``.
- Un enregistrement par fichier traité: compteurs lignes/ok/erreurs, durée, message, détails HTML.

Paramétrage recommandé
----------------------

- ``file_pattern``: ``tarifs_*.csv``
- ``remote_dir_in``: ``/incoming``
- ``remote_dir_processed``: ``/processed``
- ``remote_dir_error``: ``/error``
- ``csv_delimiter``: ``;``
- ``csv_has_header``: ``True``
- ``barcode_columns``: ``Code barre 1,Code barre 2,Code barre 3,Code barre 4,Code barre 5,Code barre 6,barcode,ean,ean13``
- ``price_column``: ``Prix de vente``
- ``clear_duplicate_barcodes``: ``True``
- ``max_preview``: 500 (ajuster si nécessaire)

Exemple CSV
-----------

- Voir ``examples/sample_tarifs.csv``.

Installation
------------

1. Copier le répertoire « ftp_tariff_import » dans la liste des addons de votre serveur Odoo.
2. Installer la dépendance Python ``paramiko`` ::

     pip install paramiko

3. Mettre à jour la liste des applications et installer « FTP/SFTP/IMAP Tariff Import ».
4. Dans Paramètres Techniques » Actions planifiées, régler l’heure d’exécution sur 07:00 (en fonction du fuseau du serveur) si besoin.

Utilisation
-----------

- Créer un fournisseur FTP/SFTP/IMAP, saisir les accès et répertoires.
- Cliquer « Tester la connexion » pour vérifier et voir la liste des fichiers.
- Dans l’aperçu, cocher les fichiers :

  - « Aperçu du contenu » pour voir les premières lignes.
  - « Télécharger en local » pour récupérer les fichiers côté navigateur.
  - « Télécharger + Traiter » pour lancer l’import immédiatement.

- Pour l’automatisation quotidienne, cocher ``auto_process``.

Dépannage
---------

- Auth SFTP par clé :

  - Coller la clé privée PEM (champ texte) et la passphrase si nécessaire.
  - Optionnel: fournir l’empreinte de clé hôte (fingerprint) pour un contrôle strict.

- Erreurs de pare‑feu :

  - Activer le mode passif (FTP) et vérifier les ports ouverts côté serveur FTP.

- Doublons de code‑barres :

  - Le module vide les barcodes en conflit; corrigez ensuite vos données si nécessaire.

- Dépendances Python :

  - Installer ``paramiko`` dans le même environnement que le service Odoo.

Notes
-----

- Ce module n’écrit pas de pièces jointes pour les fichiers importés afin d’éviter des volumes de stockage importants. Les téléchargements locaux sont streamés et les temporaires sont purgés.
- Le job journalise fichier par fichier; un échec n’arrête pas les autres fichiers.
- Les fichiers distants ne sont jamais déplacés/renommés/supprimés par ce module (politique: lecture/traitement uniquement), sauf IMAP où le message peut être déplacé vers « Processed »/« Error » si l’option est activée. Tout déplacement/archivage hors IMAP se fait hors Odoo.
- IMAP (aperçu) : pas de préchargement automatique à l’ouverture du wizard pour garder l’interface réactive. Cliquez sur « Rafraîchir » pour lister les pièces jointes selon ``imap_search_criteria`` (par défaut: UNSEEN) et ``file_pattern``. L’énumération utilise ``UID FETCH (BODYSTRUCTURE INTERNALDATE)`` sans télécharger les messages complets.
- IMAP (métadonnées) : en aperçu, la taille peut être indiquée à 0 (non connue sans téléchargement). La date est dérivée d’``INTERNALDATE`` (ou à défaut du header Date). Utilisez ``max_preview`` pour borner le nombre d’éléments listés et ajustez ``imap_search_criteria``/``file_pattern`` pour réduire le périmètre scanné.

Initialisation automatique des fournisseurs (seed)
--------------------------------------------------

Pour éviter de ressaisir les accès après chaque test/mise à jour, le module peut initialiser/mettre à jour les enregistrements ``ftp.provider`` à partir d’un fichier local.

Configuration du chemin
~~~~~~~~~~~~~~~~~~~~~~~

Ordre de résolution du chemin :

1) Variable d’environnement ``IVSPRO_FTP_PROVIDERS_PATH`` (recommandé)
2) Paramètre système Odoo ``ir.config_parameter``: ``ftp_tariff_import.providers_path``

Exemples:

- Windows (PowerShell) ::

    $env:IVSPRO_FTP_PROVIDERS_PATH = "C:\\Users\\planete\\Desktop\\conexxxion eet ftp .txt"

- Odoo (interface): Paramètres techniques » Paramètres système
  - Clé: ``ftp_tariff_import.providers_path``
  - Valeur: chemin absolu du fichier

Quand l’initialisation s’exécute
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

- Au post‑installation du module (``post_init_hook``).
- Pour relancer l’opération, mettez à jour/réinstaller le module.
- L’opération est idempotente par (``name``, ``company``) et ne remplace pas le mot de passe si omis dans le fichier.

Formats supportés
~~~~~~~~~~~~~~~~~

1) JSON (liste d’objets ou objet unique) ::

    [
      {
        "name": "EET",
        "company": "My Company",
        "protocol": "SFTP",
        "host": "ftp.eetgroup.com",
        "port": 22,
        "username": "user",
        "password": "pass",
        "remote_dir_in": "/in",
        "remote_dir_processed": "/processed",
        "remote_dir_error": "/error",
        "file_pattern": "tarifs_*.csv",
        "timeout": 60,
        "retries": 3,
        "keepalive": 30,
        "partner": "EET"
      }
    ]

2) CSV
   - Avec entête: colonnes libres (synonymes supportés), ex: ``host;login;mdp;nom;protocol;port;remote_dir_in;...``
   - Sans entête: fallback 4 colonnes ``host;login;mdp;nom``

   Exemple (point‑virgule) ::

    host;login;mdp;nom;protocol;port;remote_dir_in;remote_dir_processed;remote_dir_error;file_pattern
    ftp.eetgroup.com;user;pass;EET;SFTP;22;/in;/processed;/error;tarifs_*.csv

3) Texte Q/A (blocs « Clé? Valeur », séparés par une ligne vide) ::

    Name? EET
    Company? My Company
    Fournisseur? EET
    Protocol? SFTP
    Host? ftp.eetgroup.com
    Port? 22
    Username? user
    Password? pass
    Remote Dir In? /in
    Remote Dir Processed? /processed
    Remote Dir Error? /error
    File Pattern? tarifs_*.csv
    Timeout? 60
    Retries? 3
    Keepalive? 30

    Name? AUTRE FOURNISSEUR
    Host? ftp.example.com
    Username? foo
    Password? bar

4) Ligne simple « host;login;mdp;nom » (fallback)

Correspondance des champs
~~~~~~~~~~~~~~~~~~~~~~~~~

- ``name/nom`` → ``ftp.provider.name``
- ``company/societe`` (nom exact) → ``company_id`` (sinon société courante)
- ``protocol/protocole`` → ``ftp|sftp|imap`` (par défaut: ``sftp``)
- ``host``, ``port``, ``username/login``, ``password/mdp``
- ``remote_dir_in``, ``remote_dir_processed``, ``remote_dir_error``, ``file_pattern``
- ``timeout``, ``retries``, ``keepalive``
- IMAP: ``imap_use_ssl``, ``imap_search_criteria``, ``imap_mark_seen``, ``imap_move_processed``, ``imap_move_error``
- ``partner/fournisseur`` → création/recherche du ``res.partner`` associé

Sécurité
~~~~~~~~

- Ne versionnez jamais ce fichier (mots de passe). Gardez‑le local sur la machine/serveur d’exécution.
- Les logs n’affichent pas les mots de passe.

Compatibilité Odoo.sh et SFTP optionnel
---------------------------------------

- Par défaut SFTP est activé. Vous pouvez le désactiver via le paramètre système Odoo (ir.config_parameter) : ``ftp_tariff_import.enable_sftp = 0``.
- Si le package Python ``paramiko`` n’est pas installé dans l’environnement, les connexions SFTP échoueront proprement avec un message explicite. Le FTP reste fonctionnel.
- Sur Odoo.sh, selon votre build, ``paramiko`` peut ne pas être disponible. Dans ce cas, laissez le protocole sur FTP ou désactivez SFTP via le paramètre ci‑dessus.
- Ce module n’altère jamais les fichiers distants (aucun move/rename/delete), sauf IMAP où le message peut être déplacé vers « Processed »/« Error » si l’option est activée. Les flux preview/import effectuent uniquement des lectures et des téléchargements temporaires côté serveur Odoo.

Licence
-------

- LGPL‑3
