# -*- coding: utf-8 -*-
import os
import csv
import base64
import html
import json
import re
import tempfile
import logging
import unicodedata
import time
import uuid
import threading
import signal

from odoo import api, fields, models, _
from odoo.exceptions import UserError

# =========================================================================
# SHUTDOWN DETECTION: Global flag to detect SIGTERM/SIGINT gracefully
# =========================================================================
_shutdown_requested = False

def _signal_handler(signum, frame):
    """Handle SIGTERM/SIGINT to set shutdown flag before cursor closes."""
    global _shutdown_requested
    _shutdown_requested = True
    _logger.warning("[SHUTDOWN] Signal %s received, setting shutdown flag", signum)

# Register signal handlers
signal.signal(signal.SIGTERM, _signal_handler)
signal.signal(signal.SIGINT, _signal_handler)

_logger = logging.getLogger(__name__)


# =========================================================================
# DATABASE KEEPALIVE: Ping périodique pour garder la connexion active
# =========================================================================
class DatabaseKeepalive:
    """Utilitaire pour pinger la base de données périodiquement et éviter les timeouts.
    
    PROBLÈME RÉSOLU:
    Sur les imports longs (>15min), la connexion DB peut se fermer (idle timeout),
    causant des erreurs "cursor already closed" ou des shutdowns de workers.
    
    SOLUTION:
    Ping léger toutes les 30 secondes pour garder la connexion active.
    Le ping s'exécute en arrière-plan sans bloquer l'import.
    """
    
    def __init__(self, env, ping_interval=30):
        """
        Args:
            env: Odoo environment
            ping_interval: Intervalle entre les pings en secondes (défaut 30s)
        """
        self.env = env
        self.ping_interval = ping_interval
        self._stop_flag = threading.Event()
        self._thread = None
        self._last_ping = time.time()
        self._ping_count = 0
        
    def start(self):
        """Démarre le keepalive en arrière-plan."""
        if self._thread is not None:
            _logger.warning("[KEEPALIVE] Already started")
            return
        
        self._stop_flag.clear()
        self._thread = threading.Thread(target=self._ping_loop, daemon=True)
        self._thread.start()
        _logger.info("[KEEPALIVE] Started (ping every %d sec)", self.ping_interval)
    
    def stop(self):
        """Arrête le keepalive."""
        if self._thread is None:
            return
        
        self._stop_flag.set()
        self._thread.join(timeout=2)
        self._thread = None
        _logger.info("[KEEPALIVE] Stopped after %d pings", self._ping_count)
    
    def _ping_loop(self):
        """Boucle de ping (s'exécute en arrière-plan)."""
        while not self._stop_flag.is_set():
            time.sleep(self.ping_interval)
            
            if self._stop_flag.is_set():
                break
            
            try:
                self._do_ping()
            except Exception as e:
                _logger.warning("[KEEPALIVE] Ping failed: %s", e)
    
    def _do_ping(self):
        """Effectue un ping léger de la base de données."""
        try:
            # ✅ FIX: Pas de savepoint pour éviter les conflits de transaction
            # Un simple SELECT 1 ne modifie rien et garde la connexion active
            self.env.cr.execute("SELECT 1")
            _result = self.env.cr.fetchone()
            
            self._ping_count += 1
            self._last_ping = time.time()
            
            # Log discret (seulement toutes les 10 pings pour ne pas polluer les logs)
            if self._ping_count % 10 == 0:
                _logger.debug("[KEEPALIVE] Ping #%d OK (connection alive)", self._ping_count)
                
        except Exception as e:
            _logger.error("[KEEPALIVE] Ping failed (connection may be dead): %s", e)
            raise


# NOTE: DatabaseKeepalive thread is intentionally kept for backward compatibility
# but should NOT be used in imports. Use _safe_inline_db_ping() instead.


def _safe_inline_db_ping(env, last_ping_ts, interval_sec=30):
    """Ping DB *in the current thread* to keep the worker/cursor alive.

    Why:
        The previous DatabaseKeepalive used a background thread and called
        env.cr.execute() concurrently with the import loop. Odoo cursors are
        not thread-safe => can lead to "cursor already closed" / transaction
        issues.

    Returns:
        float: updated last_ping_ts
    """
    try:
        now = time.time()
        if last_ping_ts and (now - last_ping_ts) < float(interval_sec or 0):
            return last_ping_ts
        # Execute a lightweight query, isolated.
        with env.cr.savepoint():
            env.cr.execute("SELECT 1")
            env.cr.fetchone()
        return now
    except Exception as e:
        # Never block an import on keepalive.
        _logger.debug("[KEEPALIVE] Inline ping failed (ignored): %s", e)
        return last_ping_ts


class PlanetePimImporter(models.AbstractModel):
    _name = "planete.pim.importer"
    _description = "Planète PIM Product Importer (engine)"

    # ---------------------------
    # Job progress helpers
    # ---------------------------
    @api.model
    def _update_job_progress(self, job_id, current, total, status=None, created=0, updated=0, skipped=0, errors=0, progress_override=None):
        """Update job progress for real-time tracking.
        Uses direct SQL on the current cursor to avoid creating new connections
        that could exhaust the connection pool.
        Utilise un savepoint pour isoler les erreurs de transaction.
        
        ⚠️ FIX: progress_override permet de passer une valeur de progression
        explicite (ex: 0-25% pour Passe 1, 25-99% pour Passe 2) au lieu de
        la recalculer naïvement comme current/total*100.
        """
        if not job_id:
            return
        try:
            if progress_override is not None:
                progress = progress_override
            else:
                progress = (current / total * 100) if total > 0 else 0
            
            # Utiliser un savepoint pour isoler les erreurs
            with self.env.cr.savepoint():
                # ⚠️ CORRECTION: NE PLUS marquer "done" automatiquement
                # Laisser run_job() gérer la fin du job
                sql = """
                    UPDATE planete_pim_import_job
                    SET progress = %s,
                        progress_current = %s,
                        progress_total = %s,
                        progress_status = %s,
                        created_count = %s,
                        updated_count = %s,
                        skipped_count = %s,
                        error_count = %s,
                        write_date = NOW(),
                        write_uid = %s
                    WHERE id = %s
                """
                
                params = (progress, current, total, status or '', created, updated, skipped, errors, self.env.uid, job_id)
                self.env.cr.execute(sql, params)
        except Exception as e:
            _logger.warning("Failed to update job progress (isolated): %s", e)

    @api.model
    def _update_job_progress_direct(self, provider_id, progress, current, total, status):
        """Update provider progress using direct SQL on current cursor.
        This avoids creating new connections that could exhaust the connection pool.
        Utilise un savepoint pour isoler les erreurs de transaction.
        """
        if not provider_id:
            return
        try:
            # Utiliser un savepoint pour isoler les erreurs
            with self.env.cr.savepoint():
                sql = """
                    UPDATE ftp_provider
                    SET pim_progress = %s,
                        pim_progress_current = %s,
                        pim_progress_total = %s,
                        pim_progress_status = %s,
                        write_date = NOW(),
                        write_uid = %s
                    WHERE id = %s
                """
                params = (progress, current, total, status or '', self.env.uid, provider_id)
                self.env.cr.execute(sql, params)
        except Exception as e:
            _logger.warning("Failed to update provider progress (isolated): %s", e)

    # ---------------------------
    # EAN/Barcode helpers
    # ---------------------------
    @api.model
    def _convert_scientific_notation(self, s):
        """Convertit la notation scientifique Excel en nombre entier.
        
        Excel peut afficher les EAN en notation scientifique:
        - "7.86256E+12" → 7862560000000
        - "3,52145E+12" → 3521450000000 (format français avec virgule)
        
        Cette fonction détecte et convertit ces formats avant l'extraction des chiffres.
        """
        if not s:
            return s
        
        s_str = str(s).strip()
        
        # Patterns de notation scientifique:
        # - 7.86256E+12, 7.86256e+12, 7.86256E12, 7.86256e12
        # - 3,52145E+12 (format français)
        # - Peut avoir E- pour des petits nombres (rare pour EAN)
        pattern = r'^[\d.,]+[Ee][+-]?\d+$'
        
        if re.match(pattern, s_str):
            try:
                # Remplacer la virgule par un point pour le format français
                s_normalized = s_str.replace(',', '.')
                # Convertir en float puis en int pour obtenir le nombre entier
                value = int(float(s_normalized))
                _logger.debug("Notation scientifique convertie: '%s' → '%s'", s_str, value)
                return str(value)
            except (ValueError, OverflowError) as e:
                _logger.warning("Échec conversion notation scientifique '%s': %s", s_str, e)
                return s_str
        
        return s_str

    @api.model
    def _digits_only(self, s):
        # D'abord convertir la notation scientifique si présente
        s = self._convert_scientific_notation(s)
        return re.sub(r"[^0-9]", "", s or "")

    @api.model
    def _compute_ean13_checksum(self, base12):
        """Return checksum digit for 12-digit string."""
        if not base12 or len(base12) != 12 or not base12.isdigit():
            return None
        total = 0
        # positions from left, index 0..11
        for i, ch in enumerate(base12):
            n = ord(ch) - 48
            total += n * (3 if (i % 2 == 1) else 1)
        chk = (10 - (total % 10)) % 10
        return str(chk)

    @api.model
    def _normalize_ean(self, raw):
        """Normalize to a usable barcode (RÈGLE STRICTE):
        
        ⚠️ NOUVELLE RÈGLE: Un EAN VALIDE = CHIFFRES UNIQUEMENT
        - Si le code contient des LETTRES: REJETÉ (return None)
        - Exemple: "00AXENC002" → ❌ INVALIDE (contient les lettres A,X,E,N,C)
        - Exemple: "0111111x1111" → ❌ INVALIDE (contient la lettre x)
        
        Règles d'acceptation (chiffres uniquement):
        - 11 chiffres -> prefix "00" to make 13 digits (11111111111 -> 0011111111111)
        - 12 chiffres -> prefix "0" to make 13 digits (123456789012 -> 0123456789012)
        - 13 chiffres (EAN-13) -> recalculate checksum for validity
        - Any other length (< 11, 14+) -> return None (rejected)
        
        IMPORTANT: 11, 12, and 13-digit codes are all accepted and normalized to 13.
        """
        if not raw:
            return None
        
        raw_str = str(raw).strip()
        
        # ⚠️ NOUVELLE VALIDATION STRICTE: VÉRIFIER LES LETTRES DANS L'EAN BRUT
        # Si l'EAN original contient des lettres, c'est INVALIDE
        # Exemples:
        # - "00AXENC002" contient A, X, E, N, C → ❌ INVALIDE
        # - "0111111x1111" contient x → ❌ INVALIDE
        # - "0111111111111" ne contient que des chiffres → ✅ VALIDE (13 chiffres)
        has_letters = any(c.isalpha() for c in raw_str)
        
        # Extraire les chiffres
        d = self._digits_only(raw_str)
        
        # Si le code brut a des lettres ET des chiffres → REJETÉ
        # (Les chiffres seuls ne suffisent pas si des lettres étaient présentes)
        if has_letters and d:
            _logger.warning("[EAN] Code invalide: contient des LETTRES - REJETÉ: %s", raw_str)
            return None
        
        if not d:
            return None
        
        # RÈGLE: 11 chiffres -> ajouter "00" devant pour faire 13 (garder les 11 chiffres)
        if len(d) == 11:
            result = "00" + d  # prefix "00" + tous les 11 chiffres = 13 chiffres
            _logger.debug("[EAN] 11 digits converted to EAN-13: %s -> %s", raw, result)
            return result
        
        # RÈGLE: 12 chiffres -> ajouter "0" devant pour faire 13 (garder les 12 chiffres)
        if len(d) == 12:
            result = "0" + d  # prefix "0" + tous les 12 chiffres = 13 chiffres
            _logger.debug("[EAN] 12 digits converted to EAN-13: %s -> %s", raw, result)
            return result
        
        # RÈGLE: EAN-13 (13 chiffres) accepté avec recalcul checksum
        if len(d) == 13:
            chk = self._compute_ean13_checksum(d[:12])
            if chk is None:
                return None
            result = d[:12] + chk
            _logger.debug("[EAN] 13 digits EAN-13 accepted: %s -> %s", raw, result)
            return result
        
        # Tout autre longueur (< 11, 14+) -> REJETÉ
        _logger.debug("[EAN] Code-barres avec %d chiffres REJETÉ (seuls 11, 12 et 13 acceptés): %s", len(d), raw)
        return None

    @api.model
    def _apply_mapping_transform(self, value, line_info, row_data=None, header_index=None):
        """Applique la transformation définie dans le mapping template à une valeur.
        
        Args:
            value: Valeur brute de la colonne CSV
            line_info: Dictionnaire avec les infos de transformation:
                - transform_type: none, strip, upper, lower, replace, divide, multiply, default_if_empty, concat
                - transform_value: paramètre 1 de la transformation
                - transform_value2: paramètre 2 (pour replace)
                - concat_column: colonne(s) à concaténer
                - concat_separator: séparateur pour concaténation
            row_data: Liste complète des valeurs de la ligne (pour concat)
            header_index: Dict {nom_colonne_lower: index} (pour concat)
            
        Returns:
            Valeur transformée
        """
        if value is None:
            value = ""
        if not isinstance(value, str):
            value = str(value)
        
        if not line_info:
            return value
        
        transform = line_info.get("transform_type", "none")
        param1 = line_info.get("transform_value", "")
        param2 = line_info.get("transform_value2", "")
        
        if transform == "none":
            return value
        elif transform == "strip":
            return value.strip()
        elif transform == "upper":
            return value.upper()
        elif transform == "lower":
            return value.lower()
        elif transform == "replace":
            return value.replace(param1, param2)
        elif transform == "divide":
            try:
                divisor = float(param1) if param1 else 1.0
                return float(value) / divisor if divisor != 0 else 0.0
            except (ValueError, TypeError):
                return value
        elif transform == "multiply":
            try:
                multiplier = float(param1) if param1 else 1.0
                return float(value) * multiplier
            except (ValueError, TypeError):
                return value
        elif transform == "default_if_empty":
            return value.strip() if value.strip() else param1
        elif transform == "concat":
            # Concaténation avec une ou plusieurs autres colonnes
            if not row_data or not header_index:
                _logger.warning("Concaténation demandée mais row_data ou header_index manquant")
                return value
            
            concat_cols = line_info.get("concat_column", "")
            separator = line_info.get("concat_separator", " ")
            if separator is None:
                separator = " "
            
            # Parser les colonnes (peuvent être séparées par ; ou ,)
            col_names = [c.strip() for c in concat_cols.replace(",", ";").split(";") if c.strip()]
            
            if not col_names:
                return value
            
            # Construire la liste des valeurs à concaténer
            values_to_concat = [value.strip()] if value.strip() else []
            
            for col_name in col_names:
                col_name_lower = self._normalize_string_for_comparison(col_name)
                col_idx = header_index.get(col_name_lower)
                if col_idx is not None and col_idx < len(row_data):
                    col_value = (row_data[col_idx] or "").strip()
                    if col_value:
                        values_to_concat.append(col_value)
            
            return separator.join(values_to_concat)
        
        return value

    # =========================================================================
    # MAPPING DYNAMIQUE: Appliquer le mapping template aux produits
    # =========================================================================
    
    @api.model
    def _get_mapped_value(self, row, headers, hdr_index, mapping, mapping_lines, target_field, row_data=None):
        """Récupère et transforme la valeur d'un champ selon le mapping template.
        
        Args:
            row: Liste des valeurs de la ligne CSV
            headers: Liste des noms de colonnes
            hdr_index: Dict {nom_colonne_lower: index}
            mapping: Dict {target_field: [source_columns]}
            mapping_lines: Liste des lignes de mapping avec transformations
            target_field: Nom du champ cible (ex: 'name', 'division_id', 'reparabilite')
            row_data: Alias pour row (pour concaténation)
            
        Returns:
            Valeur transformée ou None si non trouvée
        """
        # Log détaillé pour les champs prix importants
        is_price_field = target_field in ("standard_price", "list_price", "price", "pvgc")
        
        if not mapping or target_field not in mapping:
            if is_price_field:
                _logger.warning("[MAPPING-DEBUG] Field '%s' NOT in mapping dict (available: %s)", 
                               target_field, list(mapping.keys()) if mapping else "None")
            return None
        
        source_columns = mapping.get(target_field, [])
        if not source_columns:
            if is_price_field:
                _logger.warning("[MAPPING-DEBUG] Field '%s' has no source columns", target_field)
            return None
        
        if is_price_field:
            _logger.debug("[MAPPING-DEBUG] Field '%s' mapped from columns: %s", target_field, source_columns)
        
        # Trouver la ligne de mapping correspondante pour les transformations
        line_info = None
        for ml in (mapping_lines or []):
            if ml.get("target_field") == target_field:
                line_info = ml
                break
        
        # Récupérer la valeur de la première colonne source trouvée
        raw_value = None
        found_col = None
        for src_col in source_columns:
            # ✅ FIX: Normaliser les accents pour matcher correctement avec hdr_index
            src_col_normalized = self._normalize_string_for_comparison(src_col)
            col_idx = hdr_index.get(src_col_normalized)
            if is_price_field:
                _logger.debug("[MAPPING-DEBUG] Checking column '%s' (lowercase='%s') -> index=%s", 
                             src_col, src_col.lower(), col_idx)
            if col_idx is not None and col_idx < len(row):
                raw_value = (row[col_idx] or "").strip()
                found_col = src_col
                if is_price_field:
                    _logger.debug("[MAPPING-DEBUG] Found value for '%s' in column '%s' (idx=%d): '%s'", 
                                target_field, src_col, col_idx, raw_value[:50] if raw_value else "EMPTY")
                if raw_value:
                    break
        
        if raw_value is None:
            raw_value = ""
            if is_price_field:
                _logger.warning("[MAPPING-DEBUG] NO VALUE found for '%s' - columns %s not in headers: %s", 
                               target_field, source_columns, list(hdr_index.keys())[:20])
        
        # Appliquer la transformation si définie
        if line_info:
            transformed = self._apply_mapping_transform(raw_value, line_info, row_data or row, hdr_index)
            if is_price_field:
                _logger.debug("[MAPPING-DEBUG] Transformed value for '%s': '%s' -> '%s'", 
                            target_field, raw_value, transformed)
            return transformed
        
        return raw_value

    @api.model
    def _apply_mapping_to_product(self, tmpl_rec, variant, row, headers, hdr_index, mapping, mapping_lines, options=None, exclude_name=False):
        """Applique le mapping template complet à un produit.
        
        VERSION DYNAMIQUE: Traite TOUS les champs du mapping template,
        pas seulement une liste statique prédéfinie.
        
        Args:
            tmpl_rec: product.template record
            variant: product.product record
            row: Liste des valeurs de la ligne CSV
            headers: Liste des noms de colonnes
            hdr_index: Dict {nom_colonne_lower: index}
            mapping: Dict {target_field: [source_columns]}
            mapping_lines: Liste des lignes de mapping avec transformations
            options: Options d'import (create_brands, etc.)
            exclude_name: Si True, exclut le champ 'name' du mapping (produits existants)
        """
        if not tmpl_rec:
            _logger.warning("[MAPPING] No template record provided")
            return
            
        if not mapping:
            _logger.warning("[MAPPING] No mapping provided - skipping mapping application")
            return
        
        # =====================================================================
        # NORMALISATION DES NOMS DE CHAMPS (pour product.template uniquement)
        # NOTE: pvgc n'est PAS aliasé car c'est un champ spécifique à supplierinfo
        # =====================================================================
        FIELD_ALIASES = {
            "brand_id": "product_brand_id",
            "brand": "product_brand_id",
            # NOTE: pvgc est géré séparément dans _create_supplierinfo_from_mapping
            # Ne PAS aliaser ici car pvgc n'existe pas sur product.template
        }
        
        # Normaliser le mapping SEULEMENT pour les alias de template (pas pvgc)
        normalized_mapping = {}
        for key, val in mapping.items():
            normalized_key = FIELD_ALIASES.get(key, key)
            normalized_mapping[normalized_key] = val
        mapping = normalized_mapping
        
        # Normaliser aussi les mapping_lines (sauf pvgc)
        if mapping_lines:
            for line in mapping_lines:
                target = line.get("target_field")
                if target in FIELD_ALIASES:
                    line["target_field"] = FIELD_ALIASES[target]
            
        _logger.info("[MAPPING] ====== APPLYING MAPPING to product %s ======", tmpl_rec.id)
        _logger.info("[MAPPING] Mapping contains %d target fields: %s", len(mapping), list(mapping.keys()))
        
        options = options or {}
        ProductTemplate = self.env["product.template"].sudo()
        tmpl_vals = {}
        applied_fields = []
        skipped_fields = []
        
        # =====================================================================
        # MAPPING 100% DYNAMIQUE: Parcourir TOUS les champs du template
        # =====================================================================
        for target_field, source_cols in mapping.items():
            # ✅ NOUVEAU: Exclure 'name' si exclude_name=True (produits existants)
            if exclude_name and target_field == "name":
                skipped_fields.append(f"{target_field} (exclu - produit existant)")
                continue
            
            # Récupérer la valeur mappée
            value = self._get_mapped_value(row, headers, hdr_index, mapping, mapping_lines, target_field, row)
            
            # ✅ CORRECTION OPTION A: En UPDATE (exclude_name=True), écrire TOUS les champs même s'ils sont vides
            # Cela synchronise complètement le produit avec le fichier source
            # Les champs vides du CSV → champs vides en BDD (synchronisation totale)
            if exclude_name:
                # Mode UPDATE: NE PAS skipper les champs vides
                # Écrire les champs vides pour synchroniser complètement
                if value is None or value == "":
                    value = ""  # Forcer à chaîne vide plutôt que None
            else:
                # Mode CREATE: Skipper les champs vides si configuré
                if value is None or value == "":
                    line_info = next((ml for ml in (mapping_lines or []) if ml.get("target_field") == target_field), None)
                    if line_info and line_info.get("skip_if_empty", True):
                        skipped_fields.append(f"{target_field} (vide)")
                        continue

            # Vérifier si le champ existe sur product.template
            if target_field not in ProductTemplate._fields:
                # Peut-être un champ de supplierinfo -> géré séparément
                if target_field in ("price", "supplier_stock", "pvgc"):
                    _logger.debug("[MAPPING] Field %s is supplierinfo field, handled separately", target_field)
                else:
                    skipped_fields.append(f"{target_field} (n'existe pas)")
                continue
            
            field_obj = ProductTemplate._fields[target_field]
            
            try:
                # Traitement selon le type de champ
                if field_obj.type in ("char", "text", "html"):
                    if target_field == "barcode":
                        # Normaliser l'EAN
                        norm_value = self._normalize_ean(value)
                        if norm_value:
                            tmpl_vals[target_field] = norm_value
                            applied_fields.append(f"{target_field}={norm_value[:20]}...")
                    elif value:
                        tmpl_vals[target_field] = self._strip_nul(value)
                        applied_fields.append(f"{target_field}={str(value)[:30]}...")
                        
                elif field_obj.type in ("float", "integer"):
                    float_val = self._to_float(value) if value else 0.0
                    if float_val >= 0:
                        tmpl_vals[target_field] = float_val
                        applied_fields.append(f"{target_field}={float_val}")
                        
                elif field_obj.type == "boolean":
                    if value is not None:
                        val_lower = str(value).strip().lower()
                        if val_lower in ("0", "false", "no", "non"):
                            tmpl_vals[target_field] = False
                            applied_fields.append(f"{target_field}=False")
                        elif val_lower in ("1", "true", "yes", "oui", "x", "vrai", "o"):
                            tmpl_vals[target_field] = True
                            applied_fields.append(f"{target_field}=True")
                        else:
                            tmpl_vals[target_field] = False
                            applied_fields.append(f"{target_field}=False(default)")
                            
                elif field_obj.type == "many2one":
                    if value:
                        comodel = field_obj.comodel_name
                        rel_id = self._find_many2one_record(comodel, value, options)
                        if rel_id:
                            tmpl_vals[target_field] = rel_id
                            applied_fields.append(f"{target_field}=id:{rel_id}")
                        else:
                            skipped_fields.append(f"{target_field} (relation non trouvée: {value})")
                            
                elif field_obj.type == "date":
                    if value:
                        parsed_date = self._parse_date(value)
                        if parsed_date:
                            tmpl_vals[target_field] = parsed_date
                            applied_fields.append(f"{target_field}={parsed_date}")
                            
                elif field_obj.type == "datetime":
                    if value:
                        parsed_date = self._parse_date(value)
                        if parsed_date:
                            from datetime import datetime
                            tmpl_vals[target_field] = datetime.combine(parsed_date, datetime.min.time())
                            applied_fields.append(f"{target_field}={parsed_date}")
                else:
                    skipped_fields.append(f"{target_field} (type {field_obj.type} non géré)")
                    
            except Exception as field_err:
                _logger.warning("[MAPPING] Error on field %s: %s", target_field, field_err)
                skipped_fields.append(f"{target_field} (erreur: {str(field_err)[:50]})")
        
        # =====================================================================
        # ÉCRITURE FINALE sur le template
        # =====================================================================
        _logger.info("[MAPPING] ✅ Applied %d fields: %s", len(applied_fields), applied_fields[:10])
        if skipped_fields:
            _logger.info("[MAPPING] ⏭️ Skipped %d fields: %s", len(skipped_fields), skipped_fields[:10])
        
        if tmpl_vals:
            _logger.info("[MAPPING] Writing to template %s: %s", tmpl_rec.id, list(tmpl_vals.keys()))
            try:
                with self.env.cr.savepoint():
                    tmpl_rec.sudo().write(tmpl_vals)
                    _logger.info("[MAPPING] ✅ SUCCESS writing %d fields to template %s", len(tmpl_vals), tmpl_rec.id)
            except Exception as e:
                _logger.error("[MAPPING] ❌ WRITE ERROR on template %s: %s", tmpl_rec.id, e)
        else:
            _logger.warning("[MAPPING] ⚠️ No values to write for template %s", tmpl_rec.id)

    @api.model
    def _parse_date(self, value):
        """Parse une valeur de date en différents formats."""
        if not value:
            return None
        try:
            from datetime import datetime
            for fmt in ("%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d", "%d-%m-%Y", "%d.%m.%Y", "%d.%m.%y"):
                try:
                    return datetime.strptime(str(value).strip(), fmt).date()
                except ValueError:
                    continue
        except Exception:
            pass
        return None

    @api.model
    def _find_many2one_record(self, model_name, value, options=None):
        """Trouve ou crée un enregistrement Many2one selon la valeur.
        
        ✅ CORRECTION: Utilise SQL direct au lieu de .search() ORM pour éviter
        les erreurs "cursor already closed" après un timeout.
        
        ✅ FIX BRAND: Pour product.brand, applique _clean_brand_name() pour
        supprimer les caractères Unicode invisibles (NBSP, zero-width space, etc.)
        qui empêchent la correspondance exacte avec les marques en base.
        
        Args:
            model_name: Nom du modèle (ex: 'product.division', 'product.brand')
            value: Valeur à rechercher (ID ou nom)
            options: Options (create_brands, create_categories, etc.)
            
        Returns:
            ID de l'enregistrement ou False
        """
        if not value or not model_name:
            return False
        
        options = options or {}
        
        try:
            Model = self.env[model_name].sudo()
        except Exception:
            return False
        
        value = self._strip_nul(str(value).strip())
        
        # ✅ FIX: Pour product.brand, nettoyer agressivement les caractères Unicode
        # invisibles (NBSP, zero-width space, etc.) qui viennent des fichiers CSV
        # et empêchent la correspondance exacte avec les marques en base.
        if model_name == "product.brand":
            value = self._clean_brand_name(value)
            if not value:
                _logger.warning("[MANY2ONE] Brand name is empty after cleaning, skipping")
                return False
        
        # Mapper les noms de modèles aux noms de tables
        TABLE_MAPPING = {
            "product.brand": "product_brand",
            "product.category": "product_category",
            "product.division": "product_division",
            "product.gamme": "product_gamme",
        }
        
        table_name = TABLE_MAPPING.get(model_name)
        if not table_name:
            # Fallback: utiliser ORM si on ne connaît pas la table
            try:
                rec = Model.search([("name", "ilike", value)], limit=1)
                if rec:
                    return rec.id
            except Exception:
                pass
            return False
        
        # 1. Essayer de parser comme ID numérique
        try:
            record_id = int(float(value))
            if record_id > 0:
                rec = Model.browse(record_id)
                if rec.exists():
                    return record_id
        except (ValueError, TypeError):
            pass
        
        # 2. ✅ Rechercher par nom via SQL direct (JAMAIS de .search() ORM pour éviter cursor closed)
        try:
            with self.env.cr.savepoint():
                self.env.cr.execute(
                    "SELECT id FROM %s WHERE LOWER(name) ILIKE LOWER(%%s) LIMIT 1" % table_name,
                    [value]
                )
                result = self.env.cr.fetchone()
                if result:
                    _logger.debug("Found %s by name via SQL: %s -> id=%d", model_name, value, result[0])
                    return result[0]
        except Exception as e:
            _logger.warning("Error searching %s by name via SQL: %s", model_name, e)
        
        # 2b. ✅ FIX BRAND: Rechercher dans les aliases pour product.brand
        # Les marques peuvent avoir des aliases (ex: "SAMSUNG" alias de "Samsung Electronics")
        if model_name == "product.brand":
            try:
                with self.env.cr.savepoint():
                    self.env.cr.execute(
                        "SELECT id, aliases FROM product_brand WHERE aliases IS NOT NULL AND aliases != ''"
                    )
                    brand_key = value.lower()
                    for row in self.env.cr.fetchall():
                        brand_id_check, aliases_str = row
                        if aliases_str:
                            alias_list = [a.strip().lower() for a in aliases_str.split(",") if a.strip()]
                            if brand_key in alias_list:
                                _logger.info("[MANY2ONE] Found brand via alias: %s -> id=%d", value, brand_id_check)
                                return brand_id_check
            except Exception as e:
                _logger.warning("Error searching brand aliases via SQL: %s", e)
        
        # 3. Créer si autorisé
        can_create = False
        if model_name == "product.brand" and options.get("create_brands"):
            can_create = True
        elif model_name == "product.category" and options.get("create_categories"):
            can_create = True
        elif model_name in ("product.division", "product.gamme"):
            # Toujours autoriser la création de divisions/gammes
            can_create = True
        
        if can_create:
            try:
                rec = Model.create({"name": value})
                _logger.info("Créé %s: %s (id=%d)", model_name, value, rec.id)
                return rec.id
            except Exception as e:
                _logger.warning("Échec création %s '%s': %s", model_name, value, e)
        
        return False

    @api.model
    def _create_odr_from_mapping(self, tmpl_rec, row, headers, hdr_index, mapping, mapping_lines):
        """Crée un enregistrement ODR depuis les données mappées.
        
        CORRIGÉ: Cherche les colonnes ODR directement par leurs noms CSV communs
        pour éviter les conflits avec d'autres champs (ex: 'amount' utilisé pour Environ).
        
        Colonnes ODR recherchées (insensible à la casse):
        - Date début: 'date début odr', 'date_begin', 'odr_date_begin', 'date début odr1'
        - Date fin: 'date de fin odr', 'date_end', 'odr_date_end', 'date de fin odr1', 'date fin odr1'
        - Montant: 'montant odr', 'odr_amount', 'montant odr1', 'odr1_amount'
        - Date limite: 'date limite', 'date_limite', 'retour dossier odr1'
        - Nom: 'nom odr', 'odr_name', 'nom odr1', 'description odr1'
        
        Args:
            tmpl_rec: product.template record
            row: Données de la ligne
            headers: En-têtes
            hdr_index: Index des colonnes (lowercase -> index)
            mapping: Mapping des champs (peut être utilisé en fallback)
            mapping_lines: Lignes de mapping avec transformations
        """
        if not tmpl_rec:
            return
        
        # =====================================================================
        # RECHERCHE DIRECTE DES COLONNES ODR PAR LEURS NOMS CSV
        # Cela évite les conflits avec le mapping générique
        # =====================================================================
        
        # Noms de colonnes possibles pour chaque champ ODR (lowercase)
        ODR_COLUMN_NAMES = {
            "date_begin": [
                "date début odr1", "date début odr", "date debut odr1", "date debut odr",
                "odr_date_begin", "odr1_date_begin", "date_begin_odr1",
            ],
            "date_end": [
                "date de fin odr1", "date de fin odr", "date fin odr1", "date fin odr",
                "odr_date_end", "odr1_date_end", "date_end_odr1",
            ],
            "amount": [
                "montant odr1", "montant odr", "odr_amount", "odr1_amount",
                "amount_odr1", "odr montant",
            ],
            "date_limite": [
                "retour dossier odr1", "date limite odr1", "date limite odr",
                "date_limite_odr1", "odr_date_limite",
            ],
            "name": [
                "nom odr1", "nom odr", "odr_name", "odr1_name",
                "description odr1", "libelle odr1", "intitulé odr1",
            ],
        }
        
        def _get_odr_value(field_key):
            """Cherche la valeur d'un champ ODR par son nom de colonne CSV."""
            candidates = ODR_COLUMN_NAMES.get(field_key, [])
            for col_name in candidates:
                col_idx = hdr_index.get(self._normalize_string_for_comparison(col_name))
                if col_idx is not None and col_idx < len(row):
                    val = (row[col_idx] or "").strip()
                    if val:
                        return val
            # Fallback: utiliser le mapping si le target_field correspond
            # MAIS seulement pour les champs ODR spécifiques
            if field_key in mapping:
                # Vérifier si la source_column contient "odr" (évite le conflit avec "environ" pour "amount")
                source_cols = mapping.get(field_key, [])
                for src_col in source_cols:
                    if "odr" in src_col.lower():
                        col_idx = hdr_index.get(self._normalize_string_for_comparison(src_col))
                        if col_idx is not None and col_idx < len(row):
                            val = (row[col_idx] or "").strip()
                            if val:
                                return val
            return None
        
        # Récupérer les valeurs ODR
        date_begin = _get_odr_value("date_begin")
        date_end = _get_odr_value("date_end")
        amount = _get_odr_value("amount")
        date_limite = _get_odr_value("date_limite")
        odr_name = _get_odr_value("name")
        
        _logger.debug("[ODR] Values found: date_begin=%s, date_end=%s, amount=%s, name=%s", 
                     date_begin, date_end, amount, odr_name)
        
        # Convertir les dates
        def parse_date(val):
            if not val:
                return None
            try:
                # Essayer différents formats (inclure %y pour années sur 2 chiffres)
                for fmt in ("%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d", "%d-%m-%Y", "%d.%m.%Y", "%d.%m.%y"):
                    try:
                        from datetime import datetime
                        return datetime.strptime(val, fmt).date()
                    except ValueError:
                        continue
            except Exception:
                pass
            return None
        
        date_begin_parsed = parse_date(date_begin)
        date_end_parsed = parse_date(date_end)
        date_limite_parsed = parse_date(date_limite)
        
        # Convertir le montant
        try:
            amount_float = self._to_float(amount) if amount else 0.0
        except Exception:
            amount_float = 0.0
        
        # Ne pas créer d'ODR si pas de données significatives
        if not date_begin_parsed and not date_end_parsed and amount_float == 0:
            return
        
        try:
            ProductOdr = self.env["product.odr"].sudo()
            
            # Chercher une ODR existante avec les mêmes dates
            domain = [("product_id", "=", tmpl_rec.id)]
            if date_begin_parsed:
                domain.append(("date_begin", "=", date_begin_parsed))
            if date_end_parsed:
                domain.append(("date_end", "=", date_end_parsed))
            
            existing_odr = ProductOdr.search(domain, limit=1)
            
            odr_vals = {
                "product_id": tmpl_rec.id,
                "mode": "fixe",
            }
            if odr_name:
                odr_vals["name"] = self._strip_nul(odr_name)
            if date_begin_parsed:
                odr_vals["date_begin"] = date_begin_parsed
            if date_end_parsed:
                odr_vals["date_end"] = date_end_parsed
            if amount_float > 0:
                odr_vals["amount"] = amount_float
            if date_limite_parsed:
                odr_vals["date_limite"] = date_limite_parsed
            
            if existing_odr:
                existing_odr.write(odr_vals)
                _logger.debug("ODR mise à jour pour template %s", tmpl_rec.id)
            else:
                if not odr_vals.get("name"):
                    odr_vals["name"] = _("ODR Import %s") % fields.Date.today()
                ProductOdr.create(odr_vals)
                _logger.debug("ODR créée pour template %s", tmpl_rec.id)
                
        except Exception as e:
            _logger.warning("Erreur création/MAJ ODR pour template %s: %s", tmpl_rec.id, e)

    @api.model
    def _create_supplierinfo_from_mapping(self, tmpl_rec, supplier_id, row, headers, hdr_index, mapping, mapping_lines):
        """Crée ou met à jour un supplierinfo depuis le mapping template.
        
        RÈGLE: Utilise UNIQUEMENT le mapping template - pas de règles hardcodées.
        
        Le mapping template définit explicitement:
        - Quelle colonne CSV → quel champ cible
        
        Si dans le mapping template vous définissez:
        - "list price" → price : la colonne "list price" sera écrite sur supplierinfo.price
        - "PVGC TTC" → pvgc : la colonne "PVGC TTC" sera écrite sur supplierinfo.pvgc
        - "Stock" → supplier_stock : la colonne "Stock" sera écrite sur supplierinfo.supplier_stock
        
        Champs supportés sur product.supplierinfo:
        - price: Prix d'achat fournisseur
        - pvgc: PVGC TTC (champ IVS)
        - supplier_stock: Stock fournisseur (champ IVS)
        
        Args:
            tmpl_rec: product.template record
            supplier_id: ID du fournisseur (res.partner)
            row: Données de la ligne
            headers: En-têtes
            hdr_index: Index des colonnes
            mapping: Mapping des champs {target_field: [source_columns]}
            mapping_lines: Lignes de mapping avec transformations
        """
        if not tmpl_rec or not supplier_id:
            _logger.warning("[SUPPLIERINFO] Skipped - missing template or supplier (tmpl=%s, supplier=%s)", tmpl_rec, supplier_id)
            return
        
        if not mapping:
            _logger.warning("[SUPPLIERINFO] No mapping provided - skipping supplierinfo creation")
            return
        
        _logger.debug("[SUPPLIERINFO] Processing for product %s (supplier=%s)", tmpl_rec.id, supplier_id)
        _logger.debug("[SUPPLIERINFO] Mapping keys available: %s", list(mapping.keys()))
        
        # =====================================================================
        # UTILISER DIRECTEMENT LE MAPPING TEMPLATE - PAS DE RÈGLES HARDCODÉES
        # =====================================================================
        # Les champs cibles supportés sur product.supplierinfo
        SUPPLIERINFO_FIELDS = {
            "price": "price",                    # Prix d'achat
            "pvgc": "pvgc",                      # PVGC TTC (champ IVS)
            "supplier_stock": "supplier_stock",  # Stock fournisseur (champ IVS)
        }
        
        si_vals = {}
        SupplierInfo = self.env["product.supplierinfo"].sudo()
        
        # Parcourir les champs supportés et vérifier s'ils sont dans le mapping
        for mapping_key, si_field in SUPPLIERINFO_FIELDS.items():
            if mapping_key in mapping:
                # Vérifier que le champ existe sur le modèle
                if si_field not in SupplierInfo._fields:
                    _logger.debug("[SUPPLIERINFO] Field '%s' not in model, skipping", si_field)
                    continue
                
                # Récupérer la valeur via le mapping (utilise les transformations si définies)
                val = self._get_mapped_value(row, headers, hdr_index, mapping, mapping_lines, mapping_key, row)
                
                if val:
                    float_val = self._to_float(val)
                    if float_val >= 0:
                        si_vals[si_field] = float_val
                        _logger.debug("[SUPPLIERINFO] ✅ Mapped '%s' → '%s' = %.2f", mapping_key, si_field, float_val)
        
        # Si aucune donnée à écrire, sortir
        if not si_vals:
            _logger.debug("[SUPPLIERINFO] No supplierinfo fields found in mapping")
            return
        
        try:
            # Chercher un supplierinfo existant
            domain = [
                ("partner_id", "=", supplier_id),
                ("product_tmpl_id", "=", tmpl_rec.id),
            ]
            existing_si = SupplierInfo.search(domain, limit=1)
            
            _logger.debug("[SUPPLIERINFO] 📝 Values to write: %s", si_vals)
            
            if existing_si:
                existing_si.write(si_vals)
                _logger.debug("[SUPPLIERINFO] ✅ SupplierInfo UPDATED for template %s, supplier %s (id=%s)",
                            tmpl_rec.id, supplier_id, existing_si.id)
            else:
                si_vals.update({
                    "partner_id": supplier_id,
                    "product_tmpl_id": tmpl_rec.id,
                    "min_qty": 1.0,
                })
                new_si = SupplierInfo.create(si_vals)
                _logger.debug("[SUPPLIERINFO] ✅ SupplierInfo CREATED for template %s, supplier %s (new_id=%s)",
                            tmpl_rec.id, supplier_id, new_si.id)
                
        except Exception as e:
            _logger.error("[SUPPLIERINFO] ❌ Error creating/updating SupplierInfo for template %s: %s", tmpl_rec.id, e, exc_info=True)

    @api.model
    def _get_value_with_transform(self, row, col_idx, line_info, row_data=None, header_index=None):
        """Récupère une valeur de colonne et applique la transformation.
        
        Args:
            row: Liste des valeurs de la ligne
            col_idx: Index de la colonne source
            line_info: Dictionnaire avec les infos de transformation (peut être None)
            row_data: Alias pour row (pour concaténation)
            header_index: Dict {nom_colonne_lower: index}
            
        Returns:
            Valeur transformée
        """
        if col_idx is None or col_idx >= len(row):
            raw_value = ""
        else:
            raw_value = (row[col_idx] or "").strip()
        
        if line_info:
            return self._apply_mapping_transform(
                raw_value, 
                line_info, 
                row_data or row, 
                header_index
            )
        return raw_value

    @api.model
    def _normalize_string_for_comparison(self, text):
        """Normalise une chaîne pour la comparaison : accents, minuscules, espaces.
        
        Utilisé pour comparer les noms de colonnes CSV qui peuvent avoir des accents
        encodés de différentes façons en Unicode (forme composée vs décomposée).
        
        Exemple:
        - "Libellé marque" (composé: "é" = U+00E9)
        - "Libellé marque" (décomposé: "e" + U+0301)
        Après normalisation, les deux donnent: "libelle marque"
        
        Returns:
            str: Chaîne normalisée (minuscules, sans accents, espaces réduits)
        """
        if not text:
            return ""
        try:
            # 0. Supprimer quelques caractères Unicode invisibles (NBSP, zero-width, etc.)
            # IMPORTANT: ces caractères existent parfois dans les headers CSV (ex: "Libellé\u00a0marque")
            # et empêchent de trouver la bonne colonne.
            s = str(text)
            for ch in ('\u200b', '\u200c', '\u200d', '\ufeff', '\u00a0', '\u2007', '\u202f', '\u2060', '\u180e'):
                s = s.replace(ch, ' ')

            # 1. Normaliser la forme Unicode (NFKD = décomposition canonique)
            normalized = unicodedata.normalize("NFKD", s)
            # 2. Supprimer les caractères combinés (accents)
            normalized = "".join(ch for ch in normalized if not unicodedata.combining(ch))
            # 3. Convertir en minuscules
            normalized = normalized.lower().strip()
            # 4. Réduire les espaces (incluant tabulations multiples)
            normalized = re.sub(r"\s+", " ", normalized).strip()
            return normalized
        except Exception:
            return text.lower().strip() if text else ""

    @api.model
    def _normalize_reference(self, raw):
        """Return alphanumeric-only, de-accented string from reference.
        
        Also removes trailing parentheses and their content (EET format):
        - LH55QM(BLACK) → LH55QM
        - TQ55QMC(3.6mm) → TQ55QMC
        """
        try:
            s = "" if raw is None else str(raw)
            # Supprimer les parenthèses finales et leur contenu (ex: BLACK, 3.6mm, GOLD/WHITE)
            # Format EET: REF(COULEUR) ou REF(SPEC) à la fin
            s = re.sub(r'\([^)]*\)\s*$', '', s)
            s = unicodedata.normalize("NFKD", s)
            s = "".join(ch for ch in s if not unicodedata.combining(ch))  
            s = re.sub(r"[^A-Za-z0-9]", "", s)
            return s
        except Exception:
            return ""

    @api.model
    def _parse_script_flags(self, script_text):
        """Parse simple KEY=VALUE lines into boolean flags."""
        flags = {}
        try:
            for line in (script_text or "").splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                m = re.match(r"([A-Za-z0-9_]+)\s*=\s*(True|False|true|false|1|0)", line)
                if m:
                    key = (m.group(1) or "").upper()
                    val = (m.group(2) or "").lower() in ("true", "1")
                    flags[key] = val
        except Exception:
            pass
        return flags

    @api.model
    def _strip_nul(self, s):
        """Remove NUL characters to avoid DB errors."""
        try:
            return ("" if s is None else str(s)).replace("\x00", "")
        except Exception:
            return s

    @api.model
    def _strip_nul_in(self, obj):
        """Recursively strip NUL chars from strings in nested structures."""
        if isinstance(obj, str):
            return self._strip_nul(obj)
        if isinstance(obj, list):
            return [self._strip_nul_in(x) for x in obj]
        if isinstance(obj, dict):
            return {k: self._strip_nul_in(v) for k, v in obj.items()}
        return obj

    # ---------------------------
    # Preview utilities
    # ---------------------------
    @api.model
    def _detect_delimiter(self, sample_text, default=","):
        """Détection intelligente du délimiteur CSV.
        
        Ordre de priorité:
        1. Tabulation (\t) - fichiers TSV, très fiable
        2. Point-virgule (;) - format européen, évite les conflits avec décimales
        3. Virgule (,) - format international
        4. Pipe (|) - format alternatif
        
        La détection compte les occurrences de chaque délimiteur dans l'échantillon
        et choisit celui qui donne le plus de colonnes cohérentes.
        """
        if not sample_text:
            return default
        
        # Prendre les premières lignes pour l'analyse
        lines = sample_text.split('\n')[:10]
        if not lines:
            return default
        
        # Délimiteurs à tester, par ordre de préférence
        candidates = ['\t', ';', ',', '|']
        best_delimiter = default
        best_score = 0
        
        for delim in candidates:
            try:
                # Compter les colonnes pour chaque ligne
                col_counts = []
                for line in lines:
                    if line.strip():
                        # Utiliser csv.reader pour gérer les guillemets correctement
                        import io
                        reader = csv.reader(io.StringIO(line), delimiter=delim, quotechar='"')
                        try:
                            row = next(reader)
                            col_counts.append(len(row))
                        except StopIteration:
                            col_counts.append(0)
                
                if not col_counts:
                    continue
                
                # Score basé sur:
                # 1. Nombre de colonnes (plus = mieux)
                # 2. Cohérence (même nombre de colonnes sur toutes les lignes)
                avg_cols = sum(col_counts) / len(col_counts)
                consistency = 1.0 - (max(col_counts) - min(col_counts)) / max(max(col_counts), 1)
                
                # Bonus pour tabulation (format très fiable)
                bonus = 1.2 if delim == '\t' else 1.0
                
                score = avg_cols * consistency * bonus
                
                if score > best_score and avg_cols > 1:
                    best_score = score
                    best_delimiter = delim
                    
            except Exception:
                continue
        
        # Fallback: essayer le Sniffer Python
        if best_score == 0:
            try:
                sniffer = csv.Sniffer()
                sniffed = sniffer.sniff(sample_text, delimiters=[",", ";", "|", "\t"])
                if sniffed and sniffed.delimiter:
                    return sniffed.delimiter
            except Exception:
                pass
        
        _logger.debug("Délimiteur détecté: '%s' (score: %.2f)", repr(best_delimiter), best_score)
        return best_delimiter

    @api.model
    def _strip_quotes(self, value):
        """Supprime les guillemets autour d'une valeur si présents."""
        if not value:
            return value
        s = str(value).strip()
        if len(s) >= 2 and s[0] == '"' and s[-1] == '"':
            return s[1:-1]
        if len(s) >= 2 and s[0] == "'" and s[-1] == "'":
            return s[1:-1]
        return s

    @api.model
    def _read_head(self, file_path, enc_candidates):
        """Lit les premiers octets du fichier pour détecter l'encodage.
        
        AMÉLIORÉ: Détection intelligente de l'encodage en vérifiant la présence
        de caractères de remplacement (�) qui indiquent un mauvais encodage.
        
        Priorité pour fichiers européens:
        1. UTF-8 avec BOM (utf-8-sig)
        2. UTF-8 pur
        3. CP1252 (Windows occidental - très courant pour fichiers français)
        4. Latin-1 (ISO-8859-1)
        """
        # Lire les bytes bruts d'abord
        try:
            with open(file_path, "rb") as bf:
                raw_bytes = bf.read(8192)
        except Exception:
            return "utf-8", ""
        
        # Vérifier si c'est UTF-8 avec BOM
        if raw_bytes.startswith(b'\xef\xbb\xbf'):
            try:
                head = raw_bytes.decode("utf-8-sig")
                return "utf-8-sig", head
            except Exception:
                pass
        
        # Préparer la liste des encodages à tester
        # CP1252 en priorité car très courant pour fichiers français Windows
        test_encodings = ["utf-8", "cp1252", "latin-1", "iso-8859-15"]
        if enc_candidates:
            # Mettre les candidats fournis en premier
            test_encodings = list(enc_candidates) + [e for e in test_encodings if e not in enc_candidates]
        
        best_encoding = None
        best_head = ""
        best_score = -1
        
        for enc in test_encodings:
            try:
                head = raw_bytes.decode(enc)
                
                # Calculer un score basé sur la qualité du décodage
                # Pénaliser les caractères de remplacement (�)
                replacement_count = head.count('\ufffd') + head.count('�')
                
                # Compter les caractères accentués français valides
                french_chars = sum(1 for c in head if c in 'éèêëàâäùûüôöîïçÉÈÊËÀÂÄÙÛÜÔÖÎÏÇ')
                
                # Score: beaucoup de caractères français = bon, caractères de remplacement = mauvais
                score = french_chars - (replacement_count * 10)
                
                if score > best_score or (score == best_score and replacement_count == 0):
                    best_score = score
                    best_encoding = enc
                    best_head = head
                
                # Si on a un score parfait (caractères français et pas de remplacement), utiliser cet encodage
                if french_chars > 0 and replacement_count == 0:
                    _logger.debug("[ENCODING] Selected %s (french chars: %d, replacements: 0)", enc, french_chars)
                    return enc, head
                    
            except (UnicodeDecodeError, LookupError):
                continue
        
        if best_encoding:
            _logger.debug("[ENCODING] Selected %s (score: %d)", best_encoding, best_score)
            return best_encoding, best_head
        
        # Fallback avec errors="replace"
        try:
            with open(file_path, "r", encoding="cp1252", errors="replace", newline="") as tf:
                head = tf.read(4096)
            _logger.warning("[ENCODING] Fallback to cp1252 with replacement chars")
            return "cp1252", head
        except Exception:
            return "utf-8", ""

    @api.model
    def build_preview_html(self, file_path, has_header=True, delimiter=None, encoding=None, flags=None, delimiter_regex=None):
        enc_candidates = []
        if encoding:
            enc_candidates.append(encoding)
        for e in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
            if e not in enc_candidates:
                enc_candidates.append(e)

        sel_enc, head = self._read_head(file_path, enc_candidates)
        if not delimiter:
            delimiter = self._detect_delimiter(head or "")
        if (not delimiter_regex) and delimiter and len(delimiter) > 5:
            raise UserError(_("Le délimiteur CSV peut contenir au maximum 5 caractères."))
        # Support literal "\t" entered by user as Tab
        if delimiter == "\\t":
            delimiter = "\t"

        rows = []
        headers = []
        try:
            with open(file_path, "r", encoding=sel_enc, errors="replace", newline="") as f:
                if delimiter_regex:
                    pattern = re.compile(delimiter_regex)
                    def _split_re(line):
                        return pattern.split((line.rstrip("\r\n")))
                    if has_header:
                        try:
                            first = f.readline()
                            headers = _split_re(first) if first else []
                        except Exception:
                            headers = []
                    rows.append(headers)
                    for i, line in enumerate(f, start=1):
                        rows.append(_split_re(line))
                        if i >= 10:
                            break
                elif delimiter and len(delimiter) == 1:
                    reader = csv.reader(f, delimiter=delimiter)
                    if has_header:
                        try:
                            headers = next(reader) or []
                        except StopIteration:
                            headers = []
                    rows.append(headers)
                    for i, row in enumerate(reader, start=1):
                        rows.append(row)
                        if i >= 10:
                            break
                else:
                    def _split(line):
                        return (line.rstrip("\r\n")).split(delimiter)
                    if has_header:
                        try:
                            first = f.readline()
                            headers = _split(first) if first else []
                        except Exception:
                            headers = []
                    rows.append(headers)
                    for i, line in enumerate(f, start=1):
                        rows.append(_split(line))
                        if i >= 10:
                            break
        except Exception as e:
            _logger.exception("Preview read failed: %s", e)
            return _("<p>Prévisualisation impossible: %s</p>") % html.escape(str(e))

        def esc(v):
            try:
                return html.escape("" if v is None else str(v))
            except Exception:
                return str(v)

        # Apply basic preprocessing to preview sample based on flags (dedup, clear duplicate barcodes)
        cleanup_summary = ""
        try:
            _flags = flags or {}
            if has_header and headers and (_flags.get("ENABLE_DEDUP_IDENTICAL_ROWS", False) or _flags.get("ENABLE_CLEAR_DUP_BARCODES", False)):
                hdr_index = {h.strip(): i for i, h in enumerate(headers)}
                bc_candidates = ["ean", "ean13", "barcode", "code barre", "code_barre", "codebarre"]
                sku_candidates = ["default_code", "sku", "reference", "ref", "code", "code article"]

                bc_idx = next((hdr_index.get(nm) for nm in bc_candidates if nm in hdr_index), None)
                ref_idx = next((hdr_index.get(nm) for nm in sku_candidates if nm in hdr_index), None)

                data_rows = [list(r) for r in rows[1:]]

                # Stats containers
                identical_removed = 0
                barcodes_cleared = 0
                invalid_barcodes = 0
                missing_barcodes = 0

                # Pre-scan invalid/missing before conflict clearing (sample-based)
                if bc_idx is not None:
                    for r in data_rows:
                        raw_bc0 = (r[bc_idx].strip() if bc_idx is not None and bc_idx < len(r) else "")
                        norm0 = self._normalize_ean(raw_bc0)
                        if raw_bc0 and not norm0:
                            invalid_barcodes += 1
                        if not norm0:
                            missing_barcodes += 1

                # Dedup identical rows by (reference, normalized barcode)
                if _flags.get("ENABLE_DEDUP_IDENTICAL_ROWS", False):
                    seen = set()
                    deduped = []
                    for r in data_rows:
                        refv = (r[ref_idx].strip() if ref_idx is not None and ref_idx < len(r) else "")
                        raw_bc = (r[bc_idx].strip() if bc_idx is not None and bc_idx < len(r) else "")
                        norm = self._normalize_ean(raw_bc)
                        key = (refv or "", norm or "")
                        if key in seen:
                            identical_removed += 1
                            continue
                        seen.add(key)
                        deduped.append(r)
                    data_rows = deduped

                # Clear barcodes used by multiple distinct references
                if _flags.get("ENABLE_CLEAR_DUP_BARCODES", False) and bc_idx is not None:
                    by_bc = {}
                    for r in data_rows:
                        refv = (r[ref_idx].strip() if ref_idx is not None and ref_idx < len(r) else "")
                        raw_bc = (r[bc_idx].strip() if bc_idx is not None and bc_idx < len(r) else "")
                        norm = self._normalize_ean(raw_bc)
                        if norm:
                            by_bc.setdefault(norm, set()).add(refv)
                    conflicts = {b for b, refs in by_bc.items() if len(refs) > 1}
                    if conflicts:
                        for r in data_rows:
                            raw_bc = (r[bc_idx].strip() if bc_idx is not None and bc_idx < len(r) else "")
                            norm = self._normalize_ean(raw_bc)
                            if norm in conflicts:
                                if r[bc_idx]:
                                    barcodes_cleared += 1
                                r[bc_idx] = ""

                rows = [headers] + data_rows

                # Build cleanup summary HTML (displayed above the table)
                if bc_idx is not None and (_flags.get("ENABLE_DEDUP_IDENTICAL_ROWS", False) or _flags.get("ENABLE_CLEAR_DUP_BARCODES", False)):
                    lines = []
                    if _flags.get("ENABLE_DEDUP_IDENTICAL_ROWS", False):
                        lines.append(_("<li>Lignes identiques supprimées (échantillon): %d</li>") % identical_removed)
                    if _flags.get("ENABLE_CLEAR_DUP_BARCODES", False):
                        lines.append(_("<li>Codes-barres en conflit effacés (échantillon): %d</li>") % barcodes_cleared)
                    lines.append(_("<li>Codes-barres invalides détectés (échantillon): %d</li>") % invalid_barcodes)
                    lines.append(_("<li>Lignes sans EAN utilisable (échantillon): %d</li>") % missing_barcodes)
                    if not _flags.get("ENABLE_CLEAR_DUP_BARCODES", False):
                        lines.append(_("<li style='color:#b94a48'>ATTENTION: ENABLE_CLEAR_DUP_BARCODES=False — l'étape Script est obligatoire avec cette option activée avant le mapping.</li>"))
                    cleanup_summary = "<div class='oe_title'><h5>%s</h5><ul>%s</ul></div>" % (
                        _("Résumé Script (sur échantillon)"),
                        "".join(lines),
                    )
        except Exception:
            pass

        # Append 'Référence modifiée' column in preview if a reference column is detected
        try:
            if has_header and headers and ((flags or {}).get("ENABLE_REF_MODIFIED_COL", True)):
                hdr_index = {h.strip(): i for i, h in enumerate(headers)}
                sku_candidates = ["default_code", "sku", "reference", "ref", "code", "code article"]

                def _get_prev(row, name):
                    idx = hdr_index.get(name)
                    if idx is not None and idx < len(row):
                        return (row[idx] or "").strip()
                    return ""

                def _first_prev(row, names):
                    for nm in names:
                        if nm in hdr_index:
                            v = _get_prev(row, nm)
                            if v:
                                return v
                    return ""

                # update header row
                headers = list(headers)
                headers.append("Référence modifiée")
                rows[0] = headers
                # update data rows
                for ridx in range(1, len(rows)):
                    refv = _first_prev(rows[ridx], sku_candidates)
                    rows[ridx] = list(rows[ridx]) + [self._normalize_reference(refv)]
        except Exception:
            pass

        body = ""
        for idx, row in enumerate(rows):
            cells = "".join(
                "<th>%s</th>" % esc(v) if (has_header and idx == 0) else "<td>%s</td>" % esc(v)
                for v in row
            )
            body += "<tr>%s</tr>" % cells

        summary = "<p>Encodage: %s | Délimiteur: %s</p>" % (html.escape(sel_enc), html.escape(delimiter or ""))
        table = "<table class='o_list_view table table-sm table-striped'><tbody>%s</tbody></table>" % body
        return "<h5>%s</h5>%s%s%s" % (html.escape(os.path.basename(file_path) or ""), summary, cleanup_summary, table)

    # ---------------------------
    # Import entrypoint (scaffold)
    # ---------------------------
    @api.model
    def import_from_binary(self, b64_data, filename, options=None):
        """Scaffold import entrypoint. Creates a log, attaches the file, and performs
        a CSV pass with EAN normalization and basic validations (no product writes yet).
        Returns action to open the created log.
        """
        options = options or {}
        has_header = options.get("has_header", True)
        encoding_opt = options.get("encoding")  # optional
        delimiter_opt = options.get("delimiter")  # optional
        delimiter_regex_opt = options.get("delimiter_regex")  # optional
        provider_id_opt = options.get("provider_id")
        supplier_id_opt = options.get("supplier_id")
        # Provider is required to distinguish vendor lines per file (EET, Ingram, etc.)
        if not provider_id_opt:
            raise UserError(_("Le Provider est obligatoire pour l'import PIM (provider_id manquant)."))
        # Robust parsing of do_write with safe default to True (direct write)
        _do_raw = options.get("do_write", True)
        if isinstance(_do_raw, str):
            do_write = _do_raw.strip().lower() in ("1", "true", "yes", "y", "on")
        elif _do_raw is None:
            do_write = True
        else:
            do_write = bool(_do_raw)

        # Create Log (reuse ftp.tariff.import.log)
        Log = self.env["ftp.tariff.import.log"]
        safe_filename = self._strip_nul(filename or "")
        # NOTE: provider_id is important for planning UI (provider_planning._compute_last_log)
        log = Log.create({
            "name": _("PIM Product Import"),
            "file_name": safe_filename,
            "provider_id": provider_id_opt,
        })
        log.mark_started()

        # Helper to upsert vendor line in the staging vendor matrix (EAN + Provider)
        def _upsert_vendor_line(ean13, qty, cost):
            try:
                if not (ean13 and provider_id_opt):
                    return
                Vendor = self.env["planete.pim.staging.vendor"].sudo()
                supplier_for_provider = supplier_id_opt or self._get_supplier_for_provider(self.env["ftp.provider"].sudo().browse(provider_id_opt))
                Vendor.upsert_from_import(
                    ean13=ean13,
                    provider_id=provider_id_opt,
                    supplier_id=supplier_for_provider,
                    quantity=qty,
                    price=cost,
                    currency_id=self.env.company.currency_id.id,
                    log_id=log.id,
                )
            except Exception:
                # Never block import on vendor-matrix upsert
                pass

        # Materialize temp file
        tmp_path = None
        try:
            tmp_fd, tmp_path = tempfile.mkstemp(prefix="pim_", suffix=("_" + (filename or "upload.csv")))
            os.close(tmp_fd)
            with open(tmp_path, "wb") as tf:
                tf.write(base64.b64decode(b64_data or b""))
            orig_tmp_path = tmp_path

            # Attach source file to the log
            try:
                with open(tmp_path, "rb") as bf:
                    b64 = base64.b64encode(bf.read())
                safe_name = self._strip_nul(os.path.basename(filename or "upload.csv"))
                log.write({"file_data": b64, "file_data_name": safe_name})
            except Exception as att_e:
                _logger.warning("Could not attach source file to PIM log: %s", att_e)

            # Handle archives and Excel:
            # - If .xlsx (zip with xl/...), raise guidance to export to CSV
            # - If zip containing a CSV/TXT, extract the first matching file and proceed
            extracted_path = None
            try:
                import zipfile, tempfile as _tmp
                if zipfile.is_zipfile(tmp_path):
                    with zipfile.ZipFile(tmp_path) as zf:
                        names = [n for n in zf.namelist() if not n.endswith("/")]
                        lower_names = [n.lower() for n in names]
                        # Excel .xlsx archives contain 'xl/' entries
                        if any(n.startswith("xl/") or "/xl/" in n for n in lower_names):
                            raise UserError(_("Le fichier importé semble être un Excel (.xlsx). Veuillez l'exporter en CSV (séparateur ';' ou ',', encodage UTF-8/latin-1), puis réessayer."))
                        # Try to pick a CSV, TXT, or XML file from the archive
                        pick = None
                        for ext in (".csv", ".txt", ".xml"):
                            for n in names:
                                if n.lower().endswith(ext):
                                    pick = n
                                    break
                            if pick:
                                break
                        if not pick and names:
                            # Fallback: pick largest file in the archive
                            pick = max(names, key=lambda n: (zf.getinfo(n).file_size or 0))
                        if pick:
                            fd, extracted_path = _tmp.mkstemp(prefix="pim_zip_", suffix="_" + os.path.basename(pick))
                            os.close(fd)
                            with open(extracted_path, "wb") as out:
                                out.write(zf.read(pick))
                            tmp_path = extracted_path
            except UserError:
                raise
            except Exception as zerr:
                _logger.warning("Archive/Excel detection failed: %s", zerr)

            # Build preview and append to log
            try:
                script_text = (options.get("script_default") or "")
                flags = self._parse_script_flags(script_text)
                preview_html = self.build_preview_html(tmp_path, has_header=has_header, delimiter=delimiter_opt, encoding=encoding_opt, flags=flags, delimiter_regex=delimiter_regex_opt)
                preview_html = self._strip_nul(preview_html)
                log.write({"log_html": (log.log_html or "") + preview_html})
            except Exception:
                flags = {}
                pass

            # Skeleton CSV pass for validations only (no writes yet)
            sel_enc, head = self._read_head(tmp_path, [encoding_opt] if encoding_opt else [])
            delimiter = delimiter_opt or self._detect_delimiter(head or "")
            delimiter_regex = delimiter_regex_opt
            if (not delimiter_regex) and delimiter and len(delimiter) > 5:
                raise UserError(_("Le délimiteur CSV peut contenir au maximum 5 caractères."))
            # Support literal "\t" entered by user as Tab
            if delimiter == "\\t":
                delimiter = "\t"

            total = 0
            ok_count = 0
            error_count = 0
            created_count = 0
            updated_count = 0
            product_created = 0
            product_found = 0
            supplier_created = 0
            supplierinfo_created = 0
            supplierinfo_updated = 0
            errs_top = []

            try:
                with open(tmp_path, "r", encoding=sel_enc, errors="replace", newline="") as f:
                    headers = []
                    rows_iter = None
                    if delimiter_regex:
                        pattern = re.compile(delimiter_regex)
                        def _split_re(line):
                            return pattern.split((line.rstrip("\r\n")))
                        if has_header:
                            try:
                                first = f.readline()
                                headers = _split_re(first) if first else []
                            except Exception:
                                headers = []
                        rows_iter = (_split_re(line) for line in f)
                    elif delimiter and len(delimiter) == 1:
                        reader = csv.reader(f, delimiter=delimiter)
                        if has_header:
                            try:
                                headers = next(reader) or []
                            except StopIteration:
                                headers = []
                        rows_iter = reader
                    else:
                        def _split(line):
                            return (line.rstrip("\r\n")).split(delimiter)
                        if has_header:
                            try:
                                first = f.readline()
                                headers = _split(first) if first else []
                            except Exception:
                                headers = []
                        rows_iter = (_split(line) for line in f)

                    # Pre-process rows per script flags: materialize, deduplicate identical rows, and clear barcodes used by multiple references
                    rows_all = list(rows_iter)
                    # ✅ FIX: Normaliser les accents dans hdr_index_local pour import_from_binary (pre-processing)
                    hdr_index_local = {self._normalize_string_for_comparison(h): i for i, h in enumerate(headers)}
                    # Determine candidate indices (case-insensitive)
                    bc_candidates = ["ean", "ean13", "barcode", "code barre", "code_barre", "codebarre", "ean/upc", "ean upc", "eanupc", "upc"]
                    # CORRECTION EET: Manufacturer Part No AVANT Item No pour priorité correcte
                    sku_candidates = ["default_code", "sku", "reference", "ref", "code", "code article", "manufacturer part no", "manufacturer part number", "mpn", "item no.", "item no"]
                    def _idx(name):
                        return hdr_index_local.get(self._normalize_string_for_comparison(name or ""))
                    def _get_cell(row, idx):
                        return (row[idx] or "").strip() if idx is not None and idx < len(row) else ""
                    # Pick indexes
                    bc_idx = None
                    for nm in bc_candidates:
                        ii = _idx(nm)
                        if ii is not None:
                            bc_idx = ii
                            break
                    ref_idx = None
                    for nm in sku_candidates:
                        ii = _idx(nm)
                        if ii is not None:
                            ref_idx = ii
                            break
                    # Build keep mask (dedup identical rows) if enabled
                    keep_mask = [True] * len(rows_all)
                    if globals().get("flags", {}) or locals().get("flags", {}):
                        _flags = flags or {}
                    else:
                        _flags = {}
                    enable_dedup = (_flags.get("ENABLE_DEDUP_IDENTICAL_ROWS", True) and do_write)
                    enable_clear = (_flags.get("ENABLE_CLEAR_DUP_BARCODES", True) and do_write)
                    if enable_dedup:
                        seen_keys = set()
                        for i, r in enumerate(rows_all):
                            raw_bc0 = _get_cell(r, bc_idx)
                            norm0 = self._normalize_ean(raw_bc0)
                            refv0 = _get_cell(r, ref_idx)
                            # consider duplicates by (reference, normalized barcode) regardless of other columns
                            key = (refv0 or "", norm0 or "")
                            if not (refv0 or norm0):
                                # No reliable key -> do not deduplicate this row
                                pass
                            elif key in seen_keys:
                                keep_mask[i] = False
                            else:
                                seen_keys.add(key)
                    # Clear barcodes shared by multiple distinct references if enabled
                    if enable_clear:
                        barcode_refs = {}
                        for i, r in enumerate(rows_all):
                            if not keep_mask[i]:
                                continue
                            raw_bc0 = _get_cell(r, bc_idx)
                            norm0 = self._normalize_ean(raw_bc0)
                            refv0 = _get_cell(r, ref_idx)
                            if norm0:
                                barcode_refs.setdefault(norm0, set()).add((refv0 or "").strip())
                        conflict_barcodes = {b for b, refs in barcode_refs.items() if len(refs) > 1}
                        if bc_idx is not None and conflict_barcodes:
                            for i, r in enumerate(rows_all):
                                if not keep_mask[i]:
                                    continue
                                raw_bc_i = _get_cell(r, bc_idx)
                                norm_i = self._normalize_ean(raw_bc_i)
                                if norm_i in conflict_barcodes:
                                    r[bc_idx] = ""
                    # Rebuild iterator with kept rows only
                    rows_iter = (r for i, r in enumerate(rows_all) if keep_mask[i])

                    # ✅ FIX: Normaliser les accents dans hdr_index pour import_from_binary (mapping)
                    hdr_index = {self._normalize_string_for_comparison(h): i for i, h in enumerate(headers)}
                    # try to find probable barcode column by common names (case-insensitive)
                    bc_candidates = ["ean", "ean13", "barcode", "code barre", "code_barre", "codebarre", "ean/upc", "ean upc", "eanupc", "upc"]
                    def _get(row, name):
                        idx = hdr_index.get(self._normalize_string_for_comparison(name or ""))
                        if idx is not None and idx < len(row):
                            return (row[idx] or "").strip()
                        return ""

                    def first(row, names):
                        for nm in names:
                            if nm in hdr_index:
                                val = _get(row, nm)
                                if val:
                                    return val
                        return ""

                    def to_float(s):
                        try:
                            if s is None:
                                return 0.0
                            s = str(s).replace(" ", "").replace("\xa0", "")
                            s = s.replace(",", ".")
                            return float(s)
                        except Exception:
                            return 0.0

                    name_candidates = ["name", "libelle", "designation", "product", "titre", "nom", "description", "description 2", "description 3"]
                    sku_candidates = ["default_code", "sku", "reference", "ref", "code", "code article", "item no.", "item no", "manufacturer part no", "manufacturer part number", "mpn"]
                    brand_candidates = ["brand", "marque", "brand_id", "code marque", "libellé marque", "libelle marque", "fabricant", "manufacturer"]
                    categ_candidates = ["category", "categorie", "categ_id", "categ"]
                    list_price_candidates = ["price", "list_price", "prix", "pv", "prix_vente", "prix public", "tarif"]
                    standard_price_candidates = ["cost", "standard_price", "prix_achat", "pa", "cost_price"]
                    qty_candidates = ["qty", "quantity", "stock", "qty_available", "qte", "qte_stock"]

                    # Get job_id for progress tracking
                    job_id = options.get("job_id")
                    total_rows_count = len(rows_all)

                    # -----------------------------------------------------------------
                    # Brand workflow (direct import + staging):
                    # - auto-create missing product.brand so imports never block
                    # - keep/append planete.pim.brand.pending (state=new_brand)
                    # - store examples at import time via pending_brand_agg
                    # -----------------------------------------------------------------
                    brand_cache = {}
                    new_brands_created = []
                    pending_brand_agg = {}
                    
                    # Initial progress update
                    if job_id:
                        self._update_job_progress(
                            job_id, 0, total_rows_count,
                            status=_("Démarrage du traitement de %d lignes...") % total_rows_count
                        )

                    for row in rows_iter:
                        total += 1
                        
                        # Update progress every 50 rows
                        if job_id and total % 50 == 0:
                            self._update_job_progress(
                                job_id, total, total_rows_count,
                                status=_("Ligne %d/%d - %d créés, %d trouvés, %d erreurs") % (
                                    total, total_rows_count,
                                    product_created if do_write else created_count,
                                    product_found if do_write else updated_count,
                                    error_count
                                ),
                                created=product_created if do_write else created_count,
                                updated=product_found if do_write else updated_count,
                                errors=error_count
                            )
                        
                        raw_bc = ""
                        if headers:
                            for nm in bc_candidates:
                                if nm in hdr_index:
                                    raw_bc = _get(row, nm)
                                    if raw_bc:
                                        break
                        else:
                            # no header: assume col 0
                            raw_bc = (row[0] or "").strip() if row else ""
                        norm_ean = self._normalize_ean(raw_bc)
                        digits_only = self._digits_only(raw_bc)
                        barcode_val = norm_ean or (digits_only if digits_only else None)
                        # Validation on barcode normalization or empty
                        if raw_bc and not norm_ean:
                            # Prévenir mais continuer l'import: importer la ligne avec le code brut (digits-only)
                            if len(errs_top) < 20:
                                errs_top.append(_("Ligne %d: code-barres non standard (%s) — normalisation partielle, utilisation du code brut") % (total, raw_bc))

                        # Compute main values and extra validations
                        ref_val0 = self._strip_nul(first(row, sku_candidates) or "")
                        lp = to_float(first(row, list_price_candidates))
                        sp = to_float(first(row, standard_price_candidates))
                        q = to_float(first(row, qty_candidates))

                        if lp < 0 or sp < 0 or q < 0:
                            error_count += 1
                            if len(errs_top) < 20:
                                errs_top.append(_("Ligne %d: valeurs négatives pour prix/stock") % (total))
                            continue
                        
                        # ⚠️ RÈGLE CRITIQUE: AUCUN PRODUIT sans EAN valide ne doit être créé
                        # Les produits sans EAN vont TOUJOURS en quarantaine (staging)
                        if not barcode_val:
                            error_count += 1
                            if len(errs_top) < 20:
                                errs_top.append(_("Ligne %d: aucun code-barres utilisable — ligne envoyée en quarantaine") % (total))
                            # Forcer vers staging/quarantaine même en mode do_write
                            if do_write:
                                # Créer un enregistrement de quarantaine au lieu de créer le produit
                                try:
                                    Staging = self.env["planete.pim.product.staging"]
                                    safe_name = self._strip_nul(first(row, name_candidates) or (ref_val0 or ""))
                                    safe_default_code = self._strip_nul(ref_val0)
                                    safe_file_name = self._strip_nul(filename or "")
                                    staging_vals = {
                                        "name": safe_name or (_("Produit ligne %d") % total),
                                        "ean13": None,
                                        "original_ean": raw_bc or None,
                                        "default_code": safe_default_code or None,
                                        "standard_price": sp,
                                        "list_price": lp,
                                        "qty_available": q,
                                        "provider_id": provider_id_opt,
                                        "supplier_id": supplier_id_opt,
                                        "file_name": safe_file_name,
                                        "row_number": total,
                                        "state": "pending",
                                        "quarantine_reason": "no_ean",
                                        "quarantine_details": _("EAN brut: '%s' - Aucun code-barres valide détecté") % (raw_bc or "vide"),
                                        # ⚠️ MULTI-SOCIÉTÉS: company_id=False pour partager entre sociétés
                                        "company_id": False,
                                        "currency_id": self.env.company.currency_id.id,
                                        "data_json": self._strip_nul_in({
                                            "headers": headers,
                                            "row": row,
                                            "raw_ean": raw_bc,
                                        }),
                                    }
                                    Staging.create(staging_vals)
                                except Exception as q_err:
                                    _logger.warning("Erreur création quarantaine ligne %d: %s", total, q_err)
                            continue

                        # Upsert vendor matrix line (EAN + Provider) with qty/cost for this provider
                        try:
                            _upsert_vendor_line(barcode_val, q, sp)
                        except Exception:
                            pass

                        ref_mod = self._normalize_reference(ref_val0)

                        # Validated line
                        ok_count += 1
                        if do_write:
                            try:
                                # Provider record + Digital flag (used for brand auto-create policy)
                                provider_rec = self.env["ftp.provider"].sudo().browse(provider_id_opt) if provider_id_opt else None
                                is_digital_provider = self._is_digital_provider(provider_rec) if provider_rec else False

                                # Nom prioritaire: colonne 'name', sinon référence normalisée, sinon EAN normalisé, sinon fallback
                                name_raw = first(row, name_candidates)
                                name_val = self._strip_nul(name_raw or ref_mod or norm_ean or barcode_val or ("Produit ligne %d" % total))
                                if not name_val or not name_val.strip():
                                    name_val = "Produit ligne %d" % total
                                
                                # ⚠️ SÉCURITÉ: À ce stade, barcode_val est GARANTI d'exister (validation ci-dessus)
                                # Si on arrive ici sans barcode_val, c'est un bug critique
                                if not barcode_val:
                                    raise ValueError(_("ERREUR CRITIQUE: produit sans EAN a passé la validation (ligne %d)") % total)
                                
                                # find or create product (prioritize product.template by barcode)
                                ProductTemplate = self.env["product.template"].sudo()
                                ProductProduct = self.env["product.product"].sudo()
                                variant = None
                                is_new_product = False
                                # 1) try to locate template by barcode (EAN)
                                if barcode_val:
                                    tmpl_hit = ProductTemplate.search([("barcode", "=", barcode_val)], limit=1)
                                    if tmpl_hit:
                                        variant = tmpl_hit.product_variant_id
                                        product_found += 1
                                # 2) fallback to variant search by barcode or reference
                                if not variant:
                                    product = None
                                    if barcode_val:
                                        product = ProductProduct.search([("barcode", "=", barcode_val)], limit=1)
                                    if not product and ref_val0:
                                        # essayer sur la référence originale puis la version normalisée
                                        product = ProductProduct.search([("default_code", "=", ref_val0)], limit=1)
                                        if not product and ref_mod:
                                            product = ProductProduct.search([("default_code", "=", ref_mod)], limit=1)
                                    if product:
                                        variant = product
                                        product_found += 1
                                # 3) create template/variant if still not found
                                if not variant:
                                    # ⚠️ CORRECTION CRITIQUE: Créer avec sale_ok=False pour éviter
                                    # la contrainte ivspro_profile sur la marque obligatoire
                                    create_vals = {
                                        "name": name_val,
                                        "sale_ok": False,      # Évite la contrainte marque obligatoire
                                        "purchase_ok": True,   # Permet les achats
                                    }
                                    # Odoo 18: use detailed_type='consu' (consumable) as safe default
                                    # 'product' for storable only works with detailed_type, not type
                                    try:
                                        if "detailed_type" in ProductTemplate._fields:
                                            create_vals["detailed_type"] = "consu"
                                    except Exception:
                                        pass
                                    if ref_mod:
                                        create_vals["default_code"] = ref_mod
                                    if barcode_val:
                                        create_vals["barcode"] = barcode_val
                                    tmpl = ProductTemplate.create(create_vals)
                                    variant = tmpl.product_variant_id
                                    # Assigner le code-barres au variant pour cohérence
                                    if variant and barcode_val:
                                        variant.write({"barcode": barcode_val})
                                    product_created += 1
                                    is_new_product = True
                                    
                                    # =====================================================================
                                    # NOUVEAU: Remplir x_created_by_supplier_id (ne change jamais après)
                                    # Ce champ permet de savoir quel fournisseur a créé ce produit
                                    # =====================================================================
                                    if supplier_id_opt and tmpl:
                                        try:
                                            if "x_created_by_supplier_id" in tmpl._fields:
                                                tmpl.write({"x_created_by_supplier_id": supplier_id_opt})
                                        except Exception as creator_err:
                                            _logger.warning("[IMPORT] Could not set x_created_by_supplier_id: %s", creator_err)
                                    
                                    # =====================================================================
                                    # NOUVEAU: Gérer les flags Digital si c'est GroupeDigital
                                    # source_dsonline: True si actuellement dans les fichiers Digital
                                    # origin_dsonline: True si déjà vu chez Digital (historique)
                                    # =====================================================================
                                    provider_rec = self.env["ftp.provider"].sudo().browse(provider_id_opt) if provider_id_opt else None
                                    is_digital = self._is_digital_provider(provider_rec) if provider_rec else False
                                    if is_digital and tmpl:
                                        self._update_digital_flags(tmpl, is_digital_source=True)
                                        _logger.debug("[IMPORT] Product %s created by Digital provider, flags set", tmpl.id)
                                # Map brand/category from CSV and update product template (direct write path)
                                # ✅ Marque: auto-create + pending(new_brand) + exemples
                                brand_rec = None
                                brand_id_val = False

                                brand_candidates_ext = [
                                    "libellé marque", "libelle marque", "brand", "marque", "brand_id",
                                    "code marque", "fabricant", "manufacturer", "brand name", "brand_name",
                                ]
                                raw_brand = ""
                                for nm in brand_candidates_ext:
                                    col_idx_brand = hdr_index.get(self._normalize_string_for_comparison(nm))
                                    if col_idx_brand is not None and col_idx_brand < len(row):
                                        raw_brand = (row[col_idx_brand] or "").strip()
                                        if raw_brand:
                                            break

                                raw_brand = self._strip_nul(raw_brand)
                                raw_brand = self._clean_brand_name(raw_brand)

                                if raw_brand:
                                    brand_id_val = self._find_or_create_brand(
                                        raw_brand,
                                        brand_cache,
                                        new_brands_created,
                                        provider_id=provider_id_opt,
                                        pending_brand_agg=pending_brand_agg,
                                        sample={"ean": barcode_val, "ref": ref_mod, "name": name_val},
                                    )
                                    if brand_id_val:
                                        brand_rec = self.env["product.brand"].sudo().browse(brand_id_val)
                                # Category mapping
                                categ_rec = None
                                try:
                                    create_categ_opt = bool(options.get("create_categories"))
                                except Exception:
                                    create_categ_opt = False
                                raw_categ = ""
                                for nm in categ_candidates:
                                    if nm in hdr_index:
                                        raw_categ = _get(row, nm)
                                        if raw_categ:
                                            break
                                raw_categ = self._strip_nul(raw_categ)
                                if raw_categ:
                                    Category = self.env["product.category"].sudo()
                                    cid = None
                                    try:
                                        cid = int(float(raw_categ))
                                    except Exception:
                                        cid = None
                                    if cid:
                                        cand_c = Category.browse(cid)
                                        if cand_c and cand_c.exists():
                                            categ_rec = cand_c
                                    if not categ_rec:
                                        categ_rec = Category.search([("name", "ilike", raw_categ)], limit=1)
                                    if not categ_rec and create_categ_opt:
                                        try:
                                            categ_rec = Category.create({"name": raw_categ})
                                        except Exception:
                                            categ_rec = None
                                # Write template fields (update or newly created)
                                tmpl_rec = variant.product_tmpl_id if variant else None
                                if tmpl_rec:
                                    tmpl_vals = {
                                        "name": name_val,
                                        "list_price": lp,
                                        "standard_price": sp,
                                    }
                                    # Mirror barcode on template as well (UI expectation on product.template)
                                    if barcode_val:
                                        tmpl_vals["barcode"] = barcode_val
                                    if brand_rec:
                                        tmpl_vals["product_brand_id"] = brand_rec.id
                                    if categ_rec:
                                        tmpl_vals["categ_id"] = categ_rec.id
                                    # Optionally set default_code on template if the field exists in this DB
                                    if ref_mod and "default_code" in tmpl_rec._fields:
                                        tmpl_vals["default_code"] = ref_mod
                                    with self.env.cr.savepoint():
                                        tmpl_rec.sudo().write(tmpl_vals)
                                    # Write variant-specific identifiers safely (barcode, default_code)
                                    with self.env.cr.savepoint():
                                        if barcode_val and variant:
                                            variant.sudo().write({"barcode": barcode_val})
                                    with self.env.cr.savepoint():
                                        if ref_mod and variant:
                                            variant.sudo().write({"default_code": ref_mod})
                                # =====================================================================
                                # MAPPING DYNAMIQUE: Appliquer le template de mapping complet
                                # =====================================================================
                                # Récupérer le mapping depuis les options (construit par le wizard)
                                dynamic_mapping = options.get("mapping")
                                mapping_lines = options.get("mapping_lines")
                                
                                # ✅ FIX BUG EXERTIS/INGRAM: Si le mapping est vide, RECHARGER depuis le provider
                                # Cela évite que tous les produits aillent en quarantaine si le wizard oublie de passer le mapping
                                if not dynamic_mapping and provider_id_opt:
                                    _logger.warning("[MAPPING] ⚠️ Mapping vide dans options, rechargement depuis provider...")
                                    try:
                                        provider_rec = self.env["ftp.provider"].sudo().browse(provider_id_opt)
                                        mapping_result = self._build_mapping_from_template(provider_rec)
                                        if mapping_result.get("has_template"):
                                            dynamic_mapping = mapping_result.get("mapping", {})
                                            mapping_lines = mapping_result.get("mapping_lines", [])
                                            # Mettre à jour les options pour cohérence
                                            options["mapping"] = dynamic_mapping
                                            options["mapping_lines"] = mapping_lines
                                            _logger.info("[MAPPING] ✅ Mapping rechargé depuis template: %d champs: %s", 
                                                        len(dynamic_mapping), list(dynamic_mapping.keys())[:10])
                                        else:
                                            _logger.error("[MAPPING] ❌ Provider %s n'a pas de template configuré!", provider_id_opt)
                                    except Exception as reload_err:
                                        _logger.error("[MAPPING] Erreur rechargement mapping: %s", reload_err)
                                
                                _logger.info("[MAPPING] Template mapping check: dynamic_mapping=%s, mapping_lines=%s, tmpl_rec=%s", 
                                            bool(dynamic_mapping), bool(mapping_lines), bool(tmpl_rec))
                                
                                # ⚠️ CORRECTION CRITIQUE: Forcer l'application du mapping si un template est défini
                                # même si le mapping semble vide - cela peut être dû à un problème de transmission
                                if tmpl_rec:
                                    # Toujours essayer d'appliquer le mapping si on a un produit
                                    if dynamic_mapping:
                                        _logger.info("[MAPPING] Applying template mapping with %d rules", len(dynamic_mapping))
                                        # Appliquer le mapping complet (champs IVS, etc.)
                                        self._apply_mapping_to_product(
                                            tmpl_rec, variant, row, headers, hdr_index,
                                            dynamic_mapping, mapping_lines, options
                                        )
                                        
                                        # Créer les ODR si mappées
                                        self._create_odr_from_mapping(
                                            tmpl_rec, row, headers, hdr_index,
                                            dynamic_mapping, mapping_lines
                                        )
                                    else:
                                        _logger.warning("[MAPPING] No mapping template found in options, mapping skipped for product %s", tmpl_rec.id)
                                        # DEBUG: Afficher le contenu des options pour diagnostic
                                        _logger.debug("[MAPPING] Options content: %s", {k: v for k, v in options.items() if k in ['mapping_template_id', 'mapping', 'mapping_lines']})
                                
                                # =====================================================================
                                # ACTIVATION sale_ok: Seulement si brand ET barcode sont présents
                                # =====================================================================
                                # Après le mapping complet, activer sale_ok si tout est OK
                                if tmpl_rec and (brand_rec or tmpl_rec.product_brand_id) and barcode_val:
                                    try:
                                        with self.env.cr.savepoint():
                                            tmpl_rec.sudo().write({"sale_ok": True})
                                    except Exception as sale_ok_err:
                                        _logger.warning("Impossible d'activer sale_ok pour %s: %s", tmpl_rec.id, sale_ok_err)
                                
                                # =====================================================================
                                # SUPPLIER HANDLING: Priorité au supplier du Provider
                                # =====================================================================
                                Partner = self.env["res.partner"].sudo()
                                supplier = None
                                
                                # PRIORITÉ 1: Utiliser le supplier du Provider (configuré dans le wizard)
                                if supplier_id_opt:
                                    supplier = Partner.browse(supplier_id_opt)
                                    if not supplier.exists():
                                        supplier = None
                                
                                # PRIORITÉ 2: Chercher dans le CSV si pas de supplier configuré
                                if not supplier:
                                    def _to_int(s):
                                        try:
                                            return int(float(str(s).strip()))
                                        except Exception:
                                            return None
                                    partner_id_raw = ""
                                    if "partner_id" in hdr_index:
                                        partner_id_raw = _get(row, "partner_id")
                                    if not partner_id_raw:
                                        for alt in ["supplier_id", "vendor_id"]:
                                            if alt in hdr_index:
                                                partner_id_raw = _get(row, alt)
                                                if partner_id_raw:
                                                    break
                                    pid = _to_int(partner_id_raw) if partner_id_raw else None
                                    if pid:
                                        partner = Partner.browse(pid)
                                        supplier = partner if partner and partner.exists() else None
                                    if not supplier:
                                        supplier_name = ""
                                        for nm in ["supplier_name", "fournisseur", "vendor", "fournisseur_nom"]:
                                            if nm in hdr_index:
                                                supplier_name = _get(row, nm)
                                                if supplier_name:
                                                    break
                                        if supplier_name:
                                            supplier_name = self._strip_nul(supplier_name)
                                            supplier = Partner.search([("name", "ilike", supplier_name), ("is_company", "=", True)], limit=1)
                                    if not supplier and supplier_name:
                                        partner_vals = {"name": supplier_name, "is_company": True}
                                        if "supplier_rank" in Partner._fields:
                                            partner_vals["supplier_rank"] = 1
                                        # Ensure autopost_bills is set if the field exists (required NOT NULL)
                                        if "autopost_bills" in Partner._fields:
                                            partner_vals["autopost_bills"] = False
                                        supplier = Partner.create(partner_vals)
                                        supplier_created += 1
                                
                                # =====================================================================
                                # SUPPLIERINFO: Utiliser le mapping dynamique ou les colonnes standard
                                # =====================================================================
                                if supplier and tmpl_rec:
                                    if dynamic_mapping:
                                        # Utiliser le mapping dynamique pour créer le SupplierInfo
                                        self._create_supplierinfo_from_mapping(
                                            tmpl_rec, supplier.id, row, headers, hdr_index,
                                            dynamic_mapping, mapping_lines
                                        )
                                        supplierinfo_created += 1
                                    else:
                                        # Fallback: utiliser les colonnes standard (product_cost)
                                        product_cost_raw = _get(row, "product_cost") if "product_cost" in hdr_index else ""
                                        if product_cost_raw != "":
                                            price_val = to_float(product_cost_raw)
                                            SupplierInfo = self.env["product.supplierinfo"].sudo()
                                            domain_si = [
                                                ("partner_id", "=", supplier.id),
                                                ("product_tmpl_id", "=", tmpl_rec.id),
                                                ("min_qty", "=", 1.0),
                                            ]
                                            si = SupplierInfo.search(domain_si, limit=1)
                                            if si:
                                                si.write({"price": price_val})
                                                supplierinfo_updated += 1
                                            else:
                                                SupplierInfo.create({
                                                    "partner_id": supplier.id,
                                                    "product_tmpl_id": tmpl_rec.id,
                                                    "min_qty": 1.0,
                                                    "price": price_val,
                                                })
                                                supplierinfo_created += 1
                            except Exception as we:
                                error_count += 1
                                if len(errs_top) < 20:
                                    errs_top.append(_("Ligne %d: erreur import direct [%s] (BARCODE=%s, REF=%s) - %s") % (total, we.__class__.__name__, (barcode_val or ""), (ref_mod or ""), str(we)))
                        else:
                            Staging = self.env["planete.pim.product.staging"]
                            safe_name = self._strip_nul(first(row, name_candidates) or (barcode_val or ""))
                            safe_default_code = self._strip_nul(ref_val0)
                            safe_file_name = self._strip_nul(filename or "")
                            safe_data_json = self._strip_nul_in({"headers": headers, "row": row, "reference_modified": ref_mod})
                            # Brand mapping (staging only)
                            brand_id_val = False
                            brand_candidates_ext = ["libellé marque", "libelle marque", "brand", "marque", "brand_id", "fabricant", "manufacturer", "brand name", "brand_name", "code marque"]
                            raw_brand = ""
                            for nm in brand_candidates_ext:
                                col_idx_brand = hdr_index.get(self._normalize_string_for_comparison(nm))
                                if col_idx_brand is not None and col_idx_brand < len(row):
                                    raw_brand = (row[col_idx_brand] or "").strip()
                                    if raw_brand:
                                        break
                            raw_brand = self._strip_nul(raw_brand)
                            raw_brand = self._clean_brand_name(raw_brand)
                            if raw_brand:
                                brand_id_val = self._find_or_create_brand(
                                    raw_brand,
                                    brand_cache,
                                    new_brands_created,
                                    provider_id=provider_id_opt,
                                    pending_brand_agg=pending_brand_agg,
                                    sample={"ean": barcode_val, "ref": ref_mod, "name": safe_name},
                                )
                            vals = {
                                "name": safe_name,
                                "ean13": barcode_val,
                                "default_code": safe_default_code,
                                "list_price": lp,
                                "standard_price": sp,
                                "qty_available": q,
                                "provider_id": provider_id_opt,
                                "supplier_id": supplier_id_opt,
                                "file_name": safe_file_name,
                                "row_number": total,
                                "log_id": log.id,
                                "state": "validated",
                                "data_json": safe_data_json,
                                # ⚠️ MULTI-SOCIÉTÉS: company_id=False pour partager entre sociétés
                                "company_id": False,
                                "currency_id": self.env.company.currency_id.id,
                            }
                            if brand_id_val:
                                vals["brand_id"] = brand_id_val
                            # Upsert by EAN (and provider if provided) to count as updates on re-import
                            domain = [("ean13", "=", barcode_val)]
                            if provider_id_opt:
                                domain.append(("provider_id", "=", provider_id_opt))
                            existing = Staging.search(domain, limit=1, order="id desc")
                            if existing:
                                existing.write(vals)
                                updated_count += 1
                            else:
                                Staging.create(vals)
                                created_count += 1

                    # Flush aggregated pending brands (examples stored at import time)
                    try:
                        self._flush_pending_brand_agg(pending_brand_agg)
                        pending_brand_agg = {}
                    except Exception:
                        pass
            except Exception as e:
                _logger.exception("PIM import validation failed: %s", e)
                raise UserError(_("Lecture du fichier échouée: %s") % str(e))

            # Final progress update avec synchronisation des stats du job
            if job_id:
                self._update_job_progress(
                    job_id, total, total_rows_count,
                    status=_("Terminé: %d lignes traitées - %d créés, %d trouvés, %d erreurs") % (
                        total,
                        product_created if do_write else created_count,
                        product_found if do_write else updated_count,
                        error_count
                    ),
                    created=product_created if do_write else created_count,
                    updated=product_found if do_write else updated_count,
                    errors=error_count
                )
                
                # ✅ CORRECTION: Synchroniser les statistiques finales dans le job
                # Cela corrige le problème des compteurs à 0 dans la liste des jobs
                try:
                    Job = self.env["planete.pim.import.job"].sudo()
                    job_rec = Job.browse(job_id)
                    if job_rec.exists():
                        job_rec.write({
                            "created_count": product_created if do_write else created_count,
                            "updated_count": product_found if do_write else updated_count,
                            "error_count": error_count,
                            "progress_total": total,
                        })
                except Exception as job_sync_err:
                    _logger.warning("Échec synchronisation stats job: %s", job_sync_err)

            # Append rules and errors to log
            try:
                rules_html = _("<h5>Règles</h5><ul>"
                               "<li>Codes-barres acceptés: EAN-13 (correction checksum), UPC-A converti en EAN-13, EAN-8 et GTIN-14/ITF-14</li>"
                               "<li>Si la normalisation échoue: utilisation de la version chiffres-seulement pour la recherche/mise à jour</li>"
                               "<li>Prévisualisation des 10 premières lignes</li>"
                               "</ul>")
                err_html = ""
                if errs_top:
                    err_html = "<h5>Top erreurs</h5><ul>%s</ul>" % "".join("<li>%s</li>" % html.escape(e) for e in errs_top)
                if do_write:
                    summary = _("<p>Total lignes: %d</p><p>OK: %d</p><p>Erreurs: %d</p>"
                                "<p>Produits créés: %d | Produits existants: %d</p>"
                                "<p>Fournisseurs créés: %d</p>"
                                "<p>Supplierinfo créés: %d | mis à jour: %d</p>") % (
                        total, ok_count, error_count,
                        product_created, product_found, supplier_created,
                        supplierinfo_created, supplierinfo_updated
                    )
                else:
                    summary = _("<p>Total lignes: %d</p><p>OK (validations): %d</p><p>Erreurs: %d</p><p>Créés (staging): %d</p><p>Mis à jour (staging): %d</p>") % (total, ok_count, error_count, created_count, updated_count)
                combined = self._strip_nul((rules_html or "") + (err_html or "") + (summary or ""))
                log.write({"log_html": (log.log_html or "") + combined})
            except Exception:
                pass

            # Create import history record
            try:
                self.env["planete.pim.import.history"].create({
                    "name": _("Import %s") % fields.Datetime.now(),
                    "log_id": log.id,
                    "provider_id": provider_id_opt,
                    "file_name": self._strip_nul(filename or ""),
                    "total_lines": total,
                    "success_count": ok_count,
                    "error_count": error_count,
                    "created_count": created_count,
                    "updated_count": updated_count,
                })
            except Exception as hist_e:
                _logger.warning("Could not create import history: %s", hist_e)

            done_msg = _("Import direct terminé.") if do_write else _("Validation terminée (écriture staging effectuée).")
            log.mark_done(total=total, success=ok_count, error=error_count, msg=done_msg)
        except Exception as e:
            log.mark_error(msg=str(e))
            raise
        finally:
            # Cleanup temp files (extracted and original)
            try:
                if tmp_path and os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except Exception:
                pass
            try:
                if 'orig_tmp_path' in locals() and orig_tmp_path and orig_tmp_path != tmp_path and os.path.exists(orig_tmp_path):
                    os.remove(orig_tmp_path)
            except Exception:
                pass
            try:
                if 'extracted_path' in locals() and extracted_path and extracted_path != tmp_path and os.path.exists(extracted_path):
                    os.remove(extracted_path)
            except Exception:
                pass

        return {
            "type": "ir.actions.act_window",
            "res_model": "ftp.tariff.import.log",
            "view_mode": "form",
            "views": [(False, "form")],
            "res_id": log.id,
        }

    # ---------------------------
    # Scheduling/automation entrypoints
    # ---------------------------
    @api.model
    def _get_supplier_for_provider(self, provider):
        """Helper: derive supplier_id from provider (linked partner)."""
        try:
            return provider.partner_id.id if provider and provider.partner_id else None
        except Exception:
            return None

    @api.model
    def _is_digital_provider(self, provider):
        """Vérifie si le provider est le fournisseur maître Digital (GroupeDigital).
        
        Le fournisseur Digital est identifié par le nom "GroupeDigital" sur le partner lié.
        Digital est le fournisseur maître qui a autorité sur les données produit.
        Les autres fournisseurs ne peuvent pas écraser les données Digital.
        
        Returns:
            bool: True si c'est le fournisseur Digital, False sinon
        """
        if not provider:
            return False
        try:
            supplier = provider.partner_id
            if supplier:
                name = (supplier.name or "").strip().lower()
                # Correspondance exacte ou partielle avec les variantes connues
                digital_names = ["groupedigital", "groupe digital", "digital", "ds online", "dsonline"]
                return any(d in name for d in digital_names)
            return False
        except Exception:
            return False

    @api.model
    def _update_digital_flags(self, tmpl_rec, is_digital_source=True):
        """Met à jour les flags Digital sur un produit.
        
        IMPORTANT: Les champs source_dsonline et origin_dsonline sont des champs
        COMPUTED sur product.template qui se synchronisent automatiquement avec
        product.product (variant). On doit écrire UNIQUEMENT sur le variant pour
        éviter que l'inverse du computed n'écrase nos valeurs.
        
        Flags:
        - source_dsonline: True si le produit est actuellement dans les fichiers Digital
        - origin_dsonline: True si le produit a déjà été vu chez Digital (historique)
        - x_last_digital_date: Date de dernière présence dans un fichier Digital
        
        Args:
            tmpl_rec: product.template record
            is_digital_source: True si le produit vient d'un fichier Digital
        """
        if not tmpl_rec:
            return
        
        try:
            # ✅ CORRECTION: Écrire UNIQUEMENT sur le VARIANT (champs stockés)
            # Le template se synchronisera automatiquement via _compute_source_dsonline
            if tmpl_rec.product_variant_ids:
                variant = tmpl_rec.product_variant_ids[0]
                if "source_dsonline" in variant._fields:
                    variant_vals = {}
                    
                    if is_digital_source:
                        variant_vals["source_dsonline"] = True
                        variant_vals["origin_dsonline"] = True
                    else:
                        # Décoche source mais garde origin (historique)
                        variant_vals["source_dsonline"] = False
                    
                    # Écriture SANS .sudo() pour respecter les permissions
                    variant.write(variant_vals)
                    _logger.info("[DIGITAL-FLAGS] ✅ Flags set on VARIANT %s: %s", 
                                variant.id, variant_vals)
            
            # ✅ x_last_digital_date sur le TEMPLATE (champ normal, pas computed)
            if is_digital_source and "x_last_digital_date" in tmpl_rec._fields:
                tmpl_rec.write({"x_last_digital_date": fields.Datetime.now()})
                
        except Exception as e:
            _logger.warning("[DIGITAL-FLAGS] Error updating flags: %s", e)

    @api.model
    def _unmark_absent_digital_products(self, eans_in_file, provider):
        """Décoche source_dsonline pour les produits Digital absents du fichier.
        
        RÈGLE: 
        - Si un produit a origin_dsonline=True (était chez Digital avant)
        - ET son EAN n'est PAS dans le fichier actuel
        - ALORS décocher source_dsonline=False
        - MAIS garder origin_dsonline=True (historique permanent)
        
        Cette méthode est appelée UNIQUEMENT pour le provider Digital (GroupeDigital)
        à la fin de chaque import FULL ou DELTA.
        
        Args:
            eans_in_file: Set des EAN présents dans le fichier Digital
            provider: ftp.provider record (pour vérifier que c'est Digital)
            
        Returns:
            int: Nombre de produits décochés
        """
        if not self._is_digital_provider(provider):
            _logger.debug("[DIGITAL-FLAGS] Provider %s is not Digital, skipping unmark", provider.id)
            return 0
        
        if not eans_in_file:
            _logger.warning("[DIGITAL-FLAGS] No EANs provided, skipping unmark")
            return 0
        
        _logger.info("[DIGITAL-FLAGS] Starting unmark for products absent from Digital file (%d EANs in file)", len(eans_in_file))
        
        try:
            ProductProduct = self.env["product.product"]  # ✅ SANS .sudo()
            
            # Vérifier si les champs existent sur le modèle
            if "source_dsonline" not in ProductProduct._fields:
                _logger.warning("[DIGITAL-FLAGS] Field 'source_dsonline' not found on product.product")
                return 0
            
            # Chercher tous les produits qui ont source_dsonline=True ET origin_dsonline=True
            # Ce sont les produits actuellement marqués comme "présents chez Digital"
            products_currently_digital = ProductProduct.search([
                ("source_dsonline", "=", True),
                ("origin_dsonline", "=", True),
            ])
            
            if not products_currently_digital:
                _logger.info("[DIGITAL-FLAGS] No products currently marked as Digital source")
                return 0
            
            _logger.info("[DIGITAL-FLAGS] Found %d products currently marked as Digital source", len(products_currently_digital))
            
            # Filtrer ceux dont l'EAN n'est PAS dans le fichier
            products_to_unmark = ProductProduct
            unmarked_count = 0
            
            for product in products_currently_digital:
                barcode = product.barcode
                if barcode and barcode not in eans_in_file:
                    products_to_unmark |= product
            
            if not products_to_unmark:
                _logger.info("[DIGITAL-FLAGS] All Digital products are still present in the file")
                return 0
            
            _logger.info("[DIGITAL-FLAGS] %d products to unmark (absent from file)", len(products_to_unmark))
            
            # Décocher source_dsonline par batch pour éviter les timeouts
            batch_size = 500
            total_to_unmark = len(products_to_unmark)
            
            for i in range(0, total_to_unmark, batch_size):
                batch = products_to_unmark[i:i + batch_size]
                try:
                    with self.env.cr.savepoint():
                        batch.write({"source_dsonline": False})
                        unmarked_count += len(batch)
                        _logger.info("[DIGITAL-FLAGS] Unmarked batch %d-%d (%d/%d)", 
                                    i, i + len(batch), unmarked_count, total_to_unmark)
                except Exception as batch_err:
                    _logger.warning("[DIGITAL-FLAGS] Error unmarking batch %d-%d: %s", i, i + batch_size, batch_err)
            
            _logger.info("[DIGITAL-FLAGS] ✅ Successfully unmarked %d products (source_dsonline=False)", unmarked_count)
            return unmarked_count
            
        except Exception as e:
            _logger.exception("[DIGITAL-FLAGS] Error unmarking absent Digital products: %s", e)
            return 0

    @api.model  
    def _can_update_content_fields(self, tmpl_rec, is_digital_provider):
        """Vérifie si on peut mettre à jour les champs de contenu d'un produit.
        
        RÈGLE CRITIQUE:
        - Si le fournisseur est Digital -> TOUJOURS autorisé (Digital est maître)
        - Si le fournisseur N'EST PAS Digital ET source_dsonline=True -> INTERDIT
          (le produit est géré par Digital, les autres ne peuvent pas écraser)
        - Si le fournisseur N'EST PAS Digital ET source_dsonline=False -> autorisé
          (le produit n'est plus géré activement par Digital)
        
        Les champs de contenu protégés sont: name, description, images, categories, attributes
        Les champs toujours modifiables sont: prix, stock, délais, conditions fournisseur
        
        Args:
            tmpl_rec: product.template record
            is_digital_provider: True si le provider actuel est Digital
            
        Returns:
            bool: True si les champs de contenu peuvent être modifiés
        """
        if is_digital_provider:
            # Digital est maître -> toujours autorisé
            return True
        
        if not tmpl_rec:
            return True
        
        try:
            # Vérifier le flag source_dsonline sur le variant
            source_digital = False
            if tmpl_rec.product_variant_ids:
                variant = tmpl_rec.product_variant_ids[0]
                if hasattr(variant, 'source_dsonline'):
                    source_digital = variant.source_dsonline
            
            # Si le produit est actuellement géré par Digital -> bloquer les autres
            if source_digital:
                return False
            
            return True
            
        except Exception:
            return True

    @api.model
    def process_provider(self, provider, mode=None, limit_files=None, auto_apply=None):
        """Process a provider's incoming files via FTP/SFTP and push rows to PIM staging.
        - mode: 'full' or 'rapid' (default falls back to provider.schedule_pim_level, then provider.schedule_level, then 'full')
        - limit_files: max files to process this run (overrides provider.max_files_per_run)
        - auto_apply: if True, apply validated staging rows to products after import (defaults to True in 'rapid' mode)
        Returns a list of per-file results.
        """
        if not provider:
            return []
        if isinstance(provider, int):
            provider = self.env["ftp.provider"].browse(provider)
        # Ensure single record and system permissions for cron context
        provider = provider.sudo().ensure_one()

        # Mark provider as running at the start of PIM processing
        now = fields.Datetime.now()
        try:
            provider.sudo().write({
                "last_connection_status": "running",
                "last_error": False,
                "last_run_at": now,
            })
        except Exception:
            pass

        level = (mode or getattr(provider, "schedule_pim_level", None) or getattr(provider, "schedule_level", None) or "full")
        if auto_apply is None:
            auto_apply = (level == "rapid")

        Backend = self.env["ftp.backend.service"]
        # Determine how many files to process
        max_files = None
        try:
            max_files = int(limit_files) if limit_files is not None else int(provider.max_files_per_run or 0) or None
        except Exception:
            max_files = None

        # List newest-first
        files = Backend.list_provider_files(provider, preview_limit=None)
        if max_files:
            files = files[:max_files]

        results = []
        for info in files:
            remote_path = info.get("path")
            tmp_path = None
            try:
                # Download remote file to a temp path
                tmp_path, _size = Backend.download_to_temp(provider, remote_path)
                # Read and import into staging
                with open(tmp_path, "rb") as bf:
                    b64 = base64.b64encode(bf.read())

                reader_params = provider.get_csv_reader_params()  # delimiter/encoding/header flags
                options = {
                    "has_header": reader_params.get("has_header"),
                    "encoding": reader_params.get("encoding"),
                    "delimiter": reader_params.get("delimiter"),
                    "delimiter_regex": reader_params.get("delimiter_regex"),
                    "provider_id": provider.id,
                    "supplier_id": self._get_supplier_for_provider(provider),
                }
                self.import_from_binary(b64, info.get("name"), options=options)

                # NE PAS déplacer les fichiers - les laisser sur le FTP
                # (ancienne logique désactivée)

                # Optional: auto-apply validated rows for this provider
                if auto_apply:
                    try:
                        staging = self.env["planete.pim.product.staging"].search([
                            ("provider_id", "=", provider.id),
                            ("state", "=", "validated"),
                        ], limit=5000)
                        if staging:
                            staging.action_apply_to_products()
                    except Exception as ap_e:
                        _logger.warning("PIM: auto-apply failed for provider %s: %s", provider.id, ap_e)

                results.append({"file": remote_path, "status": "ok"})
            except Exception as e:
                _logger.exception("PIM process_provider failed for file %s: %s", remote_path, e)
                # Move to error directory when possible
                try:
                    Backend.move_remote(provider, remote_path, provider.remote_dir_error or "/error")
                except Exception as mv2:
                    _logger.warning("PIM: could not move %s to error: %s", remote_path, mv2)
                results.append({"file": remote_path, "status": "error", "error": str(e)})
            finally:
                # Cleanup temp file
                try:
                    if tmp_path and os.path.exists(tmp_path):
                        os.remove(tmp_path)
                except Exception:
                    pass

        # Update provider status/time
        now = fields.Datetime.now()
        try:
            provider.sudo().write({
                "last_connection_status": "ok",
                "last_error": False,
                "last_run_at": now,
            })
        except Exception:
            pass
        return results

    @api.model
    def cron_process_scheduled_providers(self):
        """Fallback global cron (optional): process all providers with PIM schedule enabled.
        If you use per-provider crons (recommended), this can be left unused.
        """
        providers = self.env["ftp.provider"].sudo().search([
            ("schedule_pim_active", "=", True),
            ("auto_process", "=", True),
        ])
        for p in providers:
            try:
                self.process_provider(p, mode=getattr(p, "schedule_pim_level", None))
            except Exception as e:
                _logger.exception("PIM cron processing failed for provider %s: %s", p.id, e)
        return True

    # =========================================================================
    # FULL IMPORT (Création de nouveaux produits uniquement) - 1x/jour
    # =========================================================================
    @api.model
    def _cron_full_daily(self):
        """Cron entry point: exécute l'import FULL pour tous les providers configurés.
        
        AMÉLIORÉ:
        - Tolérance horaire: exécute dans la fenêtre de l'heure configurée (±30 min)
        - Reset automatique du guard si bloqué depuis plus de 2h
        - Logs détaillés pour le debugging
        """
        _logger.info("[FULL] Cron _cron_full_daily started at %s", fields.Datetime.now())
        
        # Kill switch global
        if self.env["ir.config_parameter"].sudo().get_param("planete_pim.disable_crons"):
            _logger.info("[FULL] Cron disabled via config parameter")
            return True
        
        from datetime import datetime, timedelta
        now = fields.Datetime.now()
        current_hour = now.hour
        today = now.date()
        
        # Trouver tous les providers avec FULL activé
        providers = self.env["ftp.provider"].sudo().search([
            ("schedule_pim_full_daily", "=", True),
        ])
        
        _logger.info("[FULL] Found %d providers with FULL enabled", len(providers))
        
        for provider in providers:
            try:
                # ============================================================
                # RESET AUTOMATIQUE: Si le guard est bloqué depuis trop longtemps
                # Timeout configurable via ir.config_parameter
                # ============================================================
                if provider.pim_full_cron_running:
                    if provider.pim_last_full_date:
                        last_run = fields.Datetime.from_string(provider.pim_last_full_date)
                        stuck_duration = now - last_run
                        # Récupérer le timeout depuis les paramètres système (défaut: 5 minutes)
                        try:
                            guard_timeout_minutes = int(
                                self.env["ir.config_parameter"].sudo().get_param(
                                    "planete_pim.job_guard_timeout_minutes", "5"
                                )
                            )
                        except Exception:
                            guard_timeout_minutes = 5
                        
                        if stuck_duration > timedelta(minutes=guard_timeout_minutes):
                            _logger.warning(
                                "[FULL] Provider %s: Guard stuck for %s, auto-resetting after %d min",
                                provider.id, stuck_duration, guard_timeout_minutes
                            )
                            provider.sudo().write({
                                "pim_full_cron_running": False,
                                "pim_progress_status": _("[FULL] Guard reset automatique après blocage"),
                            })
                            self.env.cr.commit()
                        else:
                            _logger.info("[FULL] Provider %s: import already running, skipping", provider.id)
                            continue
                    else:
                        # Pas de date de dernier run mais guard actif -> reset
                        _logger.warning("[FULL] Provider %s: Guard active but no last_date, resetting", provider.id)
                        provider.sudo().write({
                            "pim_full_cron_running": False,
                        })
                        self.env.cr.commit()
                
                # ============================================================
                # VÉRIFICATION HORAIRE avec tolérance
                # ============================================================
                target_hour = provider.pim_full_hour if provider.pim_full_hour is not None else 23
                
                # Tolérance: exécuter si on est dans la bonne heure
                # Le cron tourne toutes les heures, donc on vérifie juste l'heure
                if current_hour != target_hour:
                    _logger.debug(
                        "[FULL] Provider %s: current hour %d != target hour %d, skipping",
                        provider.id, current_hour, target_hour
                    )
                    continue
                
                # ============================================================
                # VÉRIFICATION DATE: déjà exécuté aujourd'hui?
                # ============================================================
                if provider.pim_last_full_date:
                    last_date = fields.Datetime.from_string(provider.pim_last_full_date).date()
                    if last_date == today:
                        _logger.info("[FULL] Provider %s: already ran today (%s), skipping", provider.id, last_date)
                        continue
                
                # ============================================================
                # LANCER L'IMPORT
                # ============================================================
                _logger.info("[FULL] Starting import for provider %s (%s) at hour %d", 
                             provider.id, provider.name, current_hour)
                
                # Créer un job asynchrone plutôt que d'exécuter directement
                # Cela permet de mieux gérer les timeouts et de suivre la progression
                Job = self.env["planete.pim.import.job"].sudo()
                job = Job.create({
                    "name": _("[FULL CRON] %s - %s") % (provider.name or "Provider", now.strftime("%Y-%m-%d %H:%M")),
                    "provider_id": provider.id,
                    "import_mode": "full",
                    "state": "pending",
                    "progress_status": _("Planifié par cron..."),
                })
                self.env.cr.commit()
                
                _logger.info("[FULL] Created job %s for provider %s", job.id, provider.id)
                
            except Exception as e:
                _logger.exception("[FULL] Cron failed for provider %s: %s", provider.id, e)
                try:
                    provider.sudo().write({
                        "pim_full_cron_running": False,
                        "pim_progress_status": _("[FULL] Erreur cron: %s") % str(e)[:200],
                    })
                    self.env.cr.commit()
                except Exception:
                    pass
        
        return True

    @api.model
    def _process_full_import_from_file(self, provider, file_data, filename, job_id=None, options=None):
        """[FULL] Import de création de produits depuis un fichier attaché (base64).
        
        Utilisé quand le fichier est déjà téléchargé et attaché au job (évite un 2ème téléchargement FTP).
        """
        import base64
        import tempfile
        
        if isinstance(provider, int):
            provider = self.env["ftp.provider"].browse(provider)
        if provider:
            provider = provider.sudo().ensure_one()

        # Keep ftp.provider status fields in sync with job-based PIM imports
        # (planning badge + "Dernière exécution" in planning views)
        try:
            now = fields.Datetime.now()
            provider.sudo().write({
                "last_connection_status": "running",
                "last_error": False,
                "last_run_at": now,
            })
            self.env.cr.commit()
        except Exception:
            pass
        
        options = options or {}
        
        # Récupérer le job si fourni
        job = None
        if job_id:
            job = self.env["planete.pim.import.job"].sudo().browse(job_id)
            if job.exists():
                job.write({"progress_status": _("[FULL] Initialisation...")})
        
        _logger.info("[FULL] _process_full_import_from_file (job_id=%s, provider=%s)", job_id, provider.name if provider else "N/A")
        
        # Marquer le début sur le provider si disponible
        if provider:
            provider.write({
                "pim_full_cron_running": True,
                "pim_progress": 0.0,
                "pim_progress_total": 0,
                "pim_progress_current": 0,
                "pim_progress_status": _("[FULL] Lecture du fichier attaché..."),
            })
            self.env.cr.commit()
        
        tmp_path = None
        try:
            # Écrire le fichier base64 dans un fichier temporaire
            tmp_fd, tmp_path = tempfile.mkstemp(prefix="pim_job_", suffix="_" + (filename or "import.csv"))
            os.close(tmp_fd)
            with open(tmp_path, "wb") as tf:
                tf.write(base64.b64decode(file_data or b""))
            
            if provider:
                provider.write({"pim_progress_status": _("[FULL] Traitement du fichier...")})
                self.env.cr.commit()
            
            # =====================================================================
            # CORRECTION BUG: Utiliser _build_mapping_from_template TOUJOURS
            # pour avoir le mapping complet avec tous les champs (durabilite, etc.)
            # =====================================================================
            mapping = options.get("mapping")
            mapping_lines = options.get("mapping_lines")
            
            if not mapping and provider:
                _logger.info("[FULL-FROM-FILE] ⚠️ Mapping not in options, loading from template...")
                mapping_result = self._build_mapping_from_template(provider)
                if mapping_result.get("has_template"):
                    mapping = mapping_result.get("mapping", {})
                    mapping_lines = mapping_result.get("mapping_lines", [])
                    # Mettre à jour les options avec le mapping complet
                    options["mapping"] = mapping
                    options["mapping_lines"] = mapping_lines
                    _logger.info("[FULL-FROM-FILE] ✅ Loaded mapping from template: %d fields: %s", 
                                len(mapping), list(mapping.keys()))
                else:
                    _logger.warning("[FULL-FROM-FILE] ⚠️ No template configured, falling back to JSON mapping")
                    mapping = self._parse_mapping_json(provider.pim_mapping_json)
            
            if not mapping:
                mapping = self._parse_mapping_json(None)  # Defaults
            
            # Lire les paramètres CSV depuis les options
            reader_params = {
                "has_header": options.get("has_header", True),
                "encoding": options.get("encoding"),
                "delimiter": options.get("delimiter"),
                "delimiter_regex": options.get("delimiter_regex"),
            }
            
            # Traiter le fichier
            result = self._process_full_file(
                provider,
                tmp_path,
                mapping,
                filename,
                has_header=reader_params.get("has_header"),
                encoding=reader_params.get("encoding"),
                delimiter=reader_params.get("delimiter"),
                delimiter_regex=reader_params.get("delimiter_regex"),
                job_id=job_id,
                options=options,  # Passer les options complètes incluant le mapping template
            )
            
            # Marquer comme terminé sur le provider
            if provider:
                provider.write({
                    "pim_full_cron_running": False,
                    "pim_last_full_date": fields.Datetime.now(),
                    "pim_progress": 100.0,
                    "pim_progress_status": _("[FULL] Terminé: %d créés, %d quarantaine, %d existants") % (
                        result.get("created", 0),
                        result.get("quarantined", 0),
                        result.get("skipped_existing", 0),
                    ),
                })
                self.env.cr.commit()
            
            _logger.info("[FULL] Completed from file: %s", result)

            # Mark provider as ok for job-based run
            try:
                now = fields.Datetime.now()
                provider.sudo().write({
                    "last_connection_status": "ok",
                    "last_error": False,
                    "last_run_at": now,
                })
                self.env.cr.commit()
            except Exception:
                pass
            return result
            
        except Exception as e:
            _logger.exception("[FULL] Failed from file: %s", e)
            # Mark provider failure for job-based run
            try:
                now = fields.Datetime.now()
                provider.sudo().write({
                    "last_connection_status": "failed",
                    "last_error": str(e)[:500],
                    "last_run_at": now,
                })
                self.env.cr.commit()
            except Exception:
                pass
            if provider:
                provider.write({
                    "pim_full_cron_running": False,
                    "pim_progress_status": _("[FULL] Erreur: %s") % str(e)[:200],
                })
                self.env.cr.commit()
            raise
        finally:
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass

    @api.model
    def _process_full_import(self, provider, job_id=None):
        """[FULL] Import de création de produits uniquement.
        
        NOUVEAU: Split automatique des gros fichiers (> 50k lignes) en jobs multiples.
        - Petit fichier (< 50k lignes): un seul job
        - Gros fichier (50k-150k): 2-3 jobs
        - Très gros fichier (150k+): 4-5 jobs
        
        AVANTAGES:
        - Pas de timeout sur les gros fichiers (154k+ lignes)
        - Checkpoint par batch pour reprise facile
        - Stats sauvegardées même en cas d'erreur/timeout
        - Meilleure scalabilité
        
        IMPORTANT: Utilise UNIQUEMENT le mapping template du provider.
        Si aucun template n'est configuré, l'import échoue avec une erreur explicite.
        """
        if isinstance(provider, int):
            provider = self.env["ftp.provider"].browse(provider)
        provider = provider.sudo().ensure_one()

        # Keep ftp.provider status fields in sync with job-based PIM imports
        try:
            now = fields.Datetime.now()
            provider.sudo().write({
                "last_connection_status": "running",
                "last_error": False,
                "last_run_at": now,
            })
            self.env.cr.commit()
        except Exception:
            pass
        
        # Récupérer le job si fourni
        job = None
        if job_id:
            job = self.env["planete.pim.import.job"].sudo().browse(job_id)
            if job.exists():
                job.write({"progress_status": _("[FULL] Initialisation...")})
        
        _logger.info("[FULL] _process_full_import for provider %s (%s)", provider.id, provider.name)
        
        # =====================================================================
        # NOUVEAU: Détection et split automatique des gros fichiers
        # =====================================================================
        tmp_path = None
        try:
            tmp_path, file_info = self._download_provider_file(provider)
            if not tmp_path:
                raise UserError(_("Aucun fichier trouvé sur le serveur"))
            
            # Compter les lignes du fichier
            reader_params = provider.get_csv_reader_params() or {}
            has_header = reader_params.get("has_header", True)
            encoding = reader_params.get("encoding")
            
            file_line_count = self._count_csv_lines(tmp_path, has_header=has_header, encoding=encoding)
            _logger.info("[FULL] File size: %d lines", file_line_count)
            
            # DÉCISION: Split ou non?
            LARGE_FILE_THRESHOLD = 12000   # Split si > 12k lignes (réduit pour éviter OOM)
            MAX_LINES_PER_JOB = 8000        # Max 8k lignes par job (réduit pour éviter OOM sur Odoo.sh)
            
            if file_line_count > LARGE_FILE_THRESHOLD:
                # Fichier gros -> Split en jobs multiples
                num_jobs_needed = ((file_line_count + MAX_LINES_PER_JOB - 1) // MAX_LINES_PER_JOB)
                _logger.warning(
                    "[FULL-SPLIT] 📦 Large file detected: %d lines > %d threshold. "
                    "Splitting into %d jobs of max %d lines each",
                    file_line_count, LARGE_FILE_THRESHOLD, num_jobs_needed, MAX_LINES_PER_JOB
                )
                
                result = self._process_full_import_split(
                    provider, tmp_path, file_info, job_id,
                    num_jobs_needed=num_jobs_needed, max_lines_per_job=MAX_LINES_PER_JOB
                )
                # Success status for provider
                try:
                    now = fields.Datetime.now()
                    provider.sudo().write({
                        "last_connection_status": "ok",
                        "last_error": False,
                        "last_run_at": now,
                    })
                    self.env.cr.commit()
                except Exception:
                    pass
                return result
            else:
                # Fichier petit -> Import normal en un seul job
                _logger.info("[FULL] Small file: %d lines < %d threshold. Processing as single job", 
                             file_line_count, LARGE_FILE_THRESHOLD)
                
                # =====================================================================
                # FIX BUG #2: Implémentation COMPLÈTE pour les petits fichiers
                # Avant ce fix, la méthode retournait None (import incomplet!)
                # =====================================================================
                try:
                    # 1. Charger le mapping template
                    mapping_result = self._build_mapping_from_template(provider)
                    if not mapping_result.get("has_template"):
                        error_msg = _(
                            "[FULL] ERREUR: Le provider '%s' n'a pas de template de mapping configuré."
                        ) % provider.name
                        _logger.error(error_msg)
                        if job_id:
                            self._mark_job_failed(job_id, error_msg)
                        raise UserError(error_msg)
                    
                    import_options = {
                        "mapping": mapping_result.get("mapping", {}),
                        "mapping_lines": mapping_result.get("mapping_lines", []),
                        "create_brands": False,
                        "create_categories": False,
                    }
                    _logger.info("[FULL] ✅ Mapping template loaded: %d target fields: %s",
                                len(import_options.get("mapping", {})),
                                list(import_options.get("mapping", {}).keys()))
                    
                    # 2. Traiter le fichier
                    result = self._process_full_file(
                        provider,
                        tmp_path,
                        import_options.get("mapping", {}),
                        file_info.get("name", "import.csv"),
                        has_header=has_header,
                        encoding=encoding,
                        delimiter=reader_params.get("delimiter"),
                        delimiter_regex=reader_params.get("delimiter_regex"),
                        job_id=job_id,
                        options=import_options,
                    )
                    
                    # 3. Marquer le provider comme terminé
                    try:
                        if not getattr(self.env.cr, 'closed', False):
                            provider.write({
                                "pim_full_cron_running": False,
                                "pim_last_full_date": fields.Datetime.now(),
                                "pim_progress": 100.0,
                                "pim_progress_status": "[FULL] Termine: %d crees, %d quarantaine, %d existants" % (
                                    result.get("created", 0),
                                    result.get("quarantined", 0),
                                    result.get("skipped_existing", 0),
                                ),
                            })
                            self.env.cr.commit()
                    except Exception:
                        _logger.warning("[FULL] Could not update provider status (shutdown?)")
                    
                    # 4. Synchroniser les stats du job
                    if job_id:
                        try:
                            Job = self.env["planete.pim.import.job"].sudo()
                            job_rec = Job.browse(job_id)
                            if job_rec.exists():
                                job_rec.write({
                                    "created_count": result.get("created", 0),
                                    "updated_count": result.get("updated", 0),
                                    "error_count": result.get("errors", 0),
                                    "progress_total": result.get("total", 0),
                                    "progress_status": "[FULL] Termine: %d crees, %d MAJ, %d quarantaine" % (
                                        result.get("created", 0),
                                        result.get("updated", 0),
                                        result.get("quarantined", 0),
                                    ),
                                })
                                self.env.cr.commit()
                                _logger.info("[FULL] ✅ Job %d stats synchronized", job_id)
                        except Exception as job_sync_err:
                            _logger.warning("[FULL] Could not sync job stats: %s", job_sync_err)
                    
                    _logger.info("[FULL] Completed for provider %s: %s", provider.id, result)

                    # Success status for provider
                    try:
                        now = fields.Datetime.now()
                        provider.sudo().write({
                            "last_connection_status": "ok",
                            "last_error": False,
                            "last_run_at": now,
                        })
                        self.env.cr.commit()
                    except Exception:
                        pass
                    return result
                    
                except Exception as e:
                    _logger.exception("[FULL] Failed for provider %s: %s", provider.id, e)
                    # Failure status for provider
                    try:
                        now = fields.Datetime.now()
                        provider.sudo().write({
                            "last_connection_status": "failed",
                            "last_error": str(e)[:500],
                            "last_run_at": now,
                        })
                        self.env.cr.commit()
                    except Exception:
                        pass
                    try:
                        if not getattr(self.env.cr, 'closed', False):
                            provider.write({
                                "pim_full_cron_running": False,
                                "pim_progress_status": "[FULL] Erreur: %s" % str(e)[:200],
                            })
                            self.env.cr.commit()
                    except Exception:
                        _logger.warning("[FULL] Could not update provider error status")
                    raise
                finally:
                    if tmp_path and os.path.exists(tmp_path):
                        try:
                            os.remove(tmp_path)
                        except Exception:
                            pass
        
        except Exception as e:
            _logger.exception("[FULL] Split detection failed: %s", e)
            # Failure status for provider
            try:
                now = fields.Datetime.now()
                provider.sudo().write({
                    "last_connection_status": "failed",
                    "last_error": str(e)[:500],
                    "last_run_at": now,
                })
                self.env.cr.commit()
            except Exception:
                pass
            # Fallback: continuer avec la logique existante
            raise

        finally:
            # Ensure we cleanup downloaded file in ALL cases (split path returned early previously)
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass
    @api.model
    def _process_full_import_split(self, provider, tmp_path, file_info, parent_job_id=None, num_jobs_needed=2, max_lines_per_job=35000):
        """[FULL-SPLIT] Traite un gros fichier en le divisant en plusieurs jobs.
        
        ALGORITHME:
        1. Chercher les splits existants (évite duplication après shutdown)
        2. Si trouvés → réutiliser, sinon → créer
        3. Exécuter chaque job en séquence
        4. Agréger les stats finales
        5. Supprimer les jobs enfants après fusion
        
        Args:
            provider: ftp.provider record
            tmp_path: Chemin du fichier à traiter
            file_info: Info du fichier (name, size, etc.)
            parent_job_id: ID du job parent (optionnel)
            num_jobs_needed: Nombre de jobs à créer
            max_lines_per_job: Max lignes par job
            
        Returns:
            dict avec stats aggrégées
        """
        _logger.info("[FULL-SPLIT] Starting split import: %d jobs of max %d lines each", 
                     num_jobs_needed, max_lines_per_job)
        
        Job = self.env["planete.pim.import.job"].sudo()
        
        reader_params = provider.get_csv_reader_params() or {}
        has_header = reader_params.get("has_header", True)
        
        # Compter les lignes totales
        total_lines = self._count_csv_lines(tmp_path, has_header=has_header, encoding=reader_params.get("encoding"))
        
        # =====================================================================
        # ✅ FIX: Chercher les splits existants AVANT de créer de nouveaux
        # Évite la duplication après shutdown ou erreurs
        # 
        # STRATÉGIE DE RECHERCHE:
        # 1. Chercher tous les jobs du même provider en mode "full"
        # 2. Filtrer ceux qui correspondent au pattern "[FULL-SPLIT X/Y]"
        # 3. Vérifier qu'on a EXACTEMENT le bon nombre (num_jobs_needed)
        # 4. Si oui → réutiliser, sinon → créer de nouveaux
        # =====================================================================
        from datetime import timedelta
        cutoff_date = fields.Datetime.now() - timedelta(hours=24)
        
        # ✅ CORRECTION: Recherche plus robuste avec pattern correct
        # Le nom des splits est: "[FULL-SPLIT 1/3] ProviderName"
        # On cherche tous les jobs qui contiennent "[FULL-SPLIT" pour ce provider
        existing_splits = Job.search([
            ("provider_id", "=", provider.id),
            ("import_mode", "=", "full"),
            ("name", "ilike", "[FULL-SPLIT%"),  # Pattern simplifié pour matcher tous les splits
            ("state", "in", ["pending", "running", "failed"]),
            ("create_date", ">=", cutoff_date)
        ], order="name ASC")
        
        _logger.info(
            "[FULL-SPLIT] Search for existing splits: provider=%d, found=%d, need=%d",
            provider.id, len(existing_splits), num_jobs_needed
        )
        
        # ✅ VALIDATION: Vérifier que les splits trouvés correspondent bien au bon découpage
        # Extraire le "X/Y" du nom pour vérifier la cohérence
        valid_splits = []
        if existing_splits:
            for split in existing_splits:
                # Pattern: "[FULL-SPLIT 1/3] ProviderName"
                # Extraire le "Y" (nombre total) pour vérifier la cohérence
                match = re.search(r'\[FULL-SPLIT \d+/(\d+)\]', split.name)
                if match:
                    split_total = int(match.group(1))
                    if split_total == num_jobs_needed:
                        valid_splits.append(split)
                        _logger.debug("[FULL-SPLIT] Valid split found: %s (id=%d)", split.name, split.id)
                    else:
                        _logger.debug("[FULL-SPLIT] Invalid split (wrong total %d != %d): %s", 
                                     split_total, num_jobs_needed, split.name)
        
        # Si on a exactement le bon nombre de splits valides, les réutiliser
        child_jobs = []
        if len(valid_splits) == num_jobs_needed:
            _logger.warning(
                "[FULL-SPLIT] ✅ REUSING %d existing split jobs (avoiding duplicates after shutdown/error)",
                len(valid_splits)
            )
            for i, split in enumerate(valid_splits):
                # Réinitialiser l'état à "pending" si nécessaire
                old_state = split.state
                if split.state in ["running", "failed"]:
                    split.write({
                        "state": "pending",
                        "progress_status": _("En attente (retry après %s)...") % old_state,
                        "progress": 0.0,
                        "progress_current": 0,
                    })
                    _logger.info("[FULL-SPLIT] Reset split job %d/%d: id=%d (was: %s → pending)", 
                                i+1, num_jobs_needed, split.id, old_state)
                else:
                    _logger.info("[FULL-SPLIT] Reusing split job %d/%d: id=%d (state: %s)", 
                                i+1, num_jobs_needed, split.id, split.state)
                child_jobs.append((i, split.id))
        else:
            # Pas de splits valides OU nombre incorrect → créer de nouveaux
            if existing_splits:
                _logger.warning(
                    "[FULL-SPLIT] Found %d existing splits (%d valid) but need %d, creating new ones",
                    len(existing_splits), len(valid_splits), num_jobs_needed
                )
                # ✅ CLEANUP: Supprimer les splits invalides/orphelins pour éviter la confusion
                for split in existing_splits:
                    try:
                        _logger.info("[FULL-SPLIT] Deleting orphan split job: %s (id=%d)", split.name, split.id)
                        split.unlink()
                    except Exception as del_err:
                        _logger.warning("[FULL-SPLIT] Could not delete orphan split %d: %s", split.id, del_err)
            else:
                _logger.info("[FULL-SPLIT] No existing splits found, creating %d new jobs", num_jobs_needed)
            
            for i in range(num_jobs_needed):
                child_job = Job.create({
                    "name": _("[FULL-SPLIT %d/%d] %s") % (i+1, num_jobs_needed, provider.name),
                    "provider_id": provider.id,
                    "import_mode": "full",
                    "state": "pending",
                    "progress_status": _("En attente..."),
                })
                child_jobs.append((i, child_job.id))
                _logger.info("[FULL-SPLIT] Created child job %d/%d: id=%d", i+1, num_jobs_needed, child_job.id)
        
        self.env.cr.commit()
        
        # Résultats agrégés
        final_result = {
            "total": total_lines,
            "created": 0,
            "updated": 0,
            "quarantined": 0,
            "skipped_existing": 0,
            "skipped_no_ean": 0,
            "errors": 0,
        }
        
        # Exécuter chaque job en séquence
        for job_index, child_job_id in child_jobs:
            try:
                _logger.info("[FULL-SPLIT] Executing child job %d/%d (id=%d)...", job_index+1, num_jobs_needed, child_job_id)
                
                # Calculer la plage de lignes pour ce job
                start_line = job_index * max_lines_per_job
                end_line = min((job_index + 1) * max_lines_per_job, total_lines)
                
                # Traiter cette plage
                child_result = self._process_full_file_range(
                    provider, tmp_path, file_info, child_job_id,
                    start_line, end_line, total_lines,
                    reader_params=reader_params
                )
                
                # Agréger les stats
                for key in ["created", "updated", "quarantined", "skipped_existing", "errors"]:
                    final_result[key] += child_result.get(key, 0)
                
                _logger.info("[FULL-SPLIT] Child job %d/%d completed: created=%d, updated=%d, quarantined=%d",
                             job_index+1, num_jobs_needed, child_result.get("created", 0),
                             child_result.get("updated", 0), child_result.get("quarantined", 0))
                
            except Exception as e:
                _logger.exception("[FULL-SPLIT] Child job %d/%d FAILED: %s", job_index+1, num_jobs_needed, e)
                final_result["errors"] += max_lines_per_job  # Approximation
        
        # Mettre à jour le job parent avec les stats agrégées
        if parent_job_id:
            try:
                parent_job = Job.browse(parent_job_id)
                if parent_job.exists():
                    parent_job.write({
                        "state": "done",
                        "progress": 100.0,
                        "progress_total": final_result["total"],
                        "created_count": final_result["created"],
                        "updated_count": final_result["updated"],
                        "quarantined_count": final_result["quarantined"],
                        "skipped_count": final_result["skipped_existing"],
                        "error_count": final_result["errors"],
                        "progress_status": _("[FULL-SPLIT] Terminé: %d créés, %d quarantaine, %d existants") % (
                            final_result["created"], final_result["quarantined"], final_result["skipped_existing"]
                        ),
                        "finished_at": fields.Datetime.now(),
                    })
                    self.env.cr.commit()
            except Exception as e:
                _logger.warning("[FULL-SPLIT] Could not update parent job: %s", e)
        
        # Nettoyer: Supprimer les jobs enfants
        for _idx, child_job_id in child_jobs:
            try:
                Job.browse(child_job_id).unlink()
            except Exception as e:
                _logger.warning("[FULL-SPLIT] Could not delete child job %d: %s", child_job_id, e)
        
        _logger.info("[FULL-SPLIT] ✅ Split import completed: %d total created/updated across %d jobs",
                     final_result["created"] + final_result["updated"], num_jobs_needed)
        
        return final_result

    def _process_full_file_range(self, provider, file_path, file_info, job_id, start_line, end_line, total_lines, reader_params=None):
        """Traite une plage de lignes d'un fichier pour le split FULL.
        
        ✅ CORRIGÉ: Passe maintenant start_line et end_line à _process_full_file
        pour que seule la plage assignée soit traitée (pas tout le fichier).
        
        Args:
            provider: ftp.provider record
            file_path: Chemin du fichier
            file_info: Info du fichier
            job_id: ID du job pour tracking
            start_line: Ligne de départ (0-based, après header)
            end_line: Ligne de fin (exclusive)
            total_lines: Total de lignes dans le fichier
            reader_params: Paramètres CSV
            
        Returns:
            dict avec stats pour cette plage
        """
        result = {
            "created": 0,
            "updated": 0,
            "quarantined": 0,
            "skipped_existing": 0,
            "errors": 0,
        }
        
        reader_params = reader_params or {}
        
        try:
            mapping_result = self._build_mapping_from_template(provider)
            if not mapping_result.get("has_template"):
                _logger.error("[FULL-RANGE] No mapping template for provider %s", provider.id)
                return result
            
            _logger.info("[FULL-RANGE] Processing lines %d-%d of %d for provider %s",
                         start_line, end_line, total_lines, provider.id)
            
            full_result = self._process_full_file(
                provider,
                file_path,
                mapping_result.get("mapping", {}),
                file_info.get("name", "import.csv"),
                has_header=reader_params.get("has_header"),
                encoding=reader_params.get("encoding"),
                delimiter=reader_params.get("delimiter"),
                delimiter_regex=reader_params.get("delimiter_regex"),
                job_id=job_id,
                options={
                    "mapping": mapping_result.get("mapping", {}),
                    "mapping_lines": mapping_result.get("mapping_lines", []),
                },
                start_line=start_line,
                end_line=end_line,
            )
            
            return full_result
            
        except Exception as e:
            _logger.exception("[FULL-RANGE] Error processing file range %d-%d: %s", start_line, end_line, e)
            return result

    def _process_full_file(self, provider, file_path, mapping, filename, has_header=True, encoding=None, delimiter=None, delimiter_regex=None, job_id=None, options=None, start_line=None, end_line=None):
        """Traitement du fichier pour l'import FULL (création uniquement).
        
        OPTIMISÉ pour éviter les problèmes de mémoire:
        - Lecture du fichier en 2 passes: 1) détection doublons, 2) création
        - Produits sans EAN -> quarantaine (pas de skip)
        - Doublons EAN -> EAN vidé + quarantaine pour tous les concernés
        - Doublons références -> quarantaine
        - Requêtes SQL directes pour vérifier l'existence des produits
        - Garbage collection périodique
        
        MAPPING:
        - Si options["mapping"] fourni (depuis template), utilise ce mapping complet
        - Sinon, utilise le mapping basique passé en paramètre
        
        SPLIT SUPPORT (start_line / end_line):
        - Si start_line et end_line sont fournis, seule cette plage est traitée en Passe 2
        - La Passe 1 (détection doublons) scanne TOUJOURS tout le fichier
        - start_line: 0-based (après header), end_line: exclusive
        """
        import gc
        
        # =====================================================================
        # 🔴 CRITICAL FIX: Stocker provider.id et provider.name IMMÉDIATEMENT
        # Avant tout risque que le curseur se ferme!
        # =====================================================================
        provider_id_for_log = provider.id if provider else None
        provider_name_for_log = provider.name if provider else "N/A"
        
        result = {
            "total": 0,
            "created": 0,
            "updated": 0,  # ✅ NOUVEAU : produits existants mis à jour
            "quarantined": 0,
            "skipped_existing": 0,
            "skipped_no_ean": 0,  # Gardé pour compatibilité mais sera toujours 0
            "errors": 0,
        }
        
        # Préparation: paramètres de lecture et timeout
        has_header = True if has_header is None else bool(has_header)
        sel_encoding = encoding
        sel_delimiter = delimiter
        sel_delimiter_regex = delimiter_regex
        if sel_delimiter == "\\t":
            sel_delimiter = "\t"
        timeout_seconds = self._get_import_timeout_seconds()
        # Time-slicing: budget soft pour rester sous la limite Odoo.sh (15 min)
        # (on pause proprement le job et on reprendra au cron suivant)
        try:
            time_budget_seconds = int(self.env["ir.config_parameter"].sudo().get_param(
                "planete_pim.full_time_budget_seconds"
            ) or 780)  # 13 min par défaut
        except Exception:
            time_budget_seconds = 780
        start_ts = time.time()
        
        # ====================================================================
        # AMÉLIORATION: Détection automatique du délimiteur
        # Si le délimiteur configuré ne donne pas de bons résultats (<=1 colonne),
        # on utilise la détection automatique.
        # ====================================================================
        enc_candidates = ["utf-8-sig", "utf-8", "cp1252", "latin-1"]
        if sel_encoding:
            enc_candidates = [sel_encoding] + [e for e in enc_candidates if e != sel_encoding]
        detected_enc, sample = self._read_head(file_path, enc_candidates)
        if not sel_encoding:
            sel_encoding = detected_enc
        
        # Tester le délimiteur configuré
        test_cols = 0
        if sel_delimiter_regex:
            try:
                pattern = re.compile(sel_delimiter_regex)
                first_line = sample.split('\n')[0] if sample else ""
                test_cols = len(pattern.split(first_line.rstrip("\r\n"))) if first_line else 0
            except Exception:
                test_cols = 0
        elif sel_delimiter and len(sel_delimiter) == 1:
            try:
                import io
                reader = csv.reader(io.StringIO(sample.split('\n')[0] if sample else ""), delimiter=sel_delimiter, quotechar='"')
                first_row = next(reader, [])
                test_cols = len(first_row)
            except Exception:
                test_cols = 0
        
        # Si le délimiteur configuré donne ≤1 colonne, utiliser la détection automatique
        if test_cols <= 1:
            detected_delim = self._detect_delimiter(sample or "")
            _logger.info("[FULL] Config delimiter gave %d cols, auto-detected: %r", test_cols, detected_delim)
            sel_delimiter = detected_delim
            sel_delimiter_regex = None  # Désactiver le regex
        
        # Compter les lignes sans tout charger en mémoire
        total_rows = self._count_csv_lines(file_path, has_header=has_header, encoding=sel_encoding)
        result["total"] = total_rows
        
        provider.write({
            "pim_progress_total": total_rows,
            "pim_progress_status": _("[FULL] Passe 1/2: Détection des doublons..."),
        })
        self.env.cr.commit()
        
        # Mise à jour job initiale si applicable
        if job_id:
            try:
                self._update_job_progress(
                    job_id, 0, total_rows,
                    status=_("[FULL] Passe 1/2: Détection des doublons (%d lignes à scanner)...") % total_rows,
                    progress_override=0.0
                )
            except Exception:
                pass
        
        # Lire les headers seulement
        headers = self._read_csv_headers(file_path, encoding=sel_encoding, delimiter=sel_delimiter, delimiter_regex=sel_delimiter_regex)
        
        # Index des colonnes selon le mapping
        col_idx = self._build_column_index(headers, mapping)
        
        # Construire un index des colonnes par nom (normalisé: sans accents, lowercase)
        # ✅ FIX: Utiliser _normalize_string_for_comparison pour matcher "Libellé marque" correctement
        hdr_index = {self._normalize_string_for_comparison(h): i for i, h in enumerate(headers)}
        
        def _get(row, name):
            idx = hdr_index.get(self._normalize_string_for_comparison(name or ""))
            if idx is not None and idx < len(row):
                return (row[idx] or "").strip()
            return ""
        
        ProductTemplate = self.env["product.template"].sudo()
        Brand = self.env["product.brand"].sudo()
        Staging = self.env["planete.pim.product.staging"].sudo()
        
        # Cache léger pour les marques (seulement celles rencontrées)
        brand_cache = {}
        
        # Tracker pour les nouvelles marques créées (pour notification)
        new_brands_created = []

        # Agrégation des upserts brand.pending (évite 1 write par ligne)
        pending_brand_agg = {}
        
        # =====================================================================
        # PASSE 1: Détection des doublons EAN et références dans le fichier
        #
        # IMPORTANT PERF / MÉMOIRE:
        # - Sur très gros fichiers (Jacob 1M), une Pass 1 stricte peut consommer
        #   tout le budget temps. On la désactive au-delà d'un seuil.
        # - ✅ FIX OOM: Si c'est un job SPLIT (start_line/end_line fournis),
        #   TOUJOURS désactiver la Passe 1 pour économiser 200-300 MB de RAM.
        #   Chaque job split scannait tout le fichier → accumulation RAM → OOM.
        # =====================================================================
        is_split_job = (start_line is not None or end_line is not None)
        
        try:
            pass1_strict_max_lines = int(self.env["ir.config_parameter"].sudo().get_param(
                "planete_pim.full_pass1_strict_max_lines"
            ) or 150000)
        except Exception:
            pass1_strict_max_lines = 150000

        # ✅ DÉSACTIVER la Passe 1 pour les jobs split (économie RAM massive)
        if is_split_job:
            do_pass1_strict = False
            _logger.warning(
                "[FULL] SPLIT JOB detected (lines %d-%d) - SKIPPING Pass 1 to save 200-300 MB RAM",
                start_line or 0, end_line or total_rows
            )
        else:
            do_pass1_strict = total_rows <= pass1_strict_max_lines
        duplicate_eans = {}
        duplicate_refs = {}
        all_eans_in_file = set()  # alimenté en Pass 1 (strict) ou Pass 2 (fast)
        all_refs_in_file = set()  # alimenté en Pass 1 (strict) pour bulk existence checks

        _logger.info(
            "[FULL] Pass 1 strict=%s (total_rows=%d, threshold=%d)",
            do_pass1_strict, total_rows, pass1_strict_max_lines,
        )

        if not do_pass1_strict:
            # Mode FAST: pas de pass 1 globale
            msg = _("[FULL] Fichier volumineux (%d lignes) : Pass 1 doublons désactivée (mode FAST)") % total_rows
            _logger.warning(msg)
            if job_id:
                try:
                    # Marquer directement à 25% (fin logique de Pass 1)
                    self._update_job_progress(job_id, int(total_rows * 0.25), total_rows, status=msg, progress_override=25.0)
                except Exception:
                    pass
        else:
            _logger.info("[FULL] Pass 1: Detecting duplicates in %d rows", total_rows)
        
            # ✅ OPTIMISATION PASS 1: Ne stocker QUE les comptages (pas les lignes!)
            # Cela réduit drastiquement la RAM pour les fichiers 150k+
            ean_count = {}      # ean -> list de (row_number, ref) (simple comptage)
            ref_count = {}      # ref -> list de (row_number, ean) (simple comptage)
        
        # ⚠️ OPTIMISATION: Plus de ean_to_rows/ean_duplicate_rows (on juste stocke les comptages)
        # Les références doublons ne causent pas de problème si les EAN sont différents
        if do_pass1_strict:
            # Variables pour tracking temps et doublons
            pass1_start = time.time()
            last_progress_update = pass1_start

            # Tracker les doublons par TYPE
            ean_only_duplicates = 0      # EAN doublon, ref différente
            ref_only_duplicates = 0      # Ref doublon, EAN différent
            ean_and_ref_duplicates = 0   # EAN ET ref doublons (vrais doublons)

            i = 0
            for row in self._iter_csv_rows(
                file_path,
                has_header=has_header,
                encoding=sel_encoding,
                delimiter=sel_delimiter,
                delimiter_regex=sel_delimiter_regex,
            ):
                i += 1

                # Pause planifiée si on dépasse le budget (évite kill à 15min)
                if job_id and (time.time() - start_ts) > time_budget_seconds:
                    pause_msg = _("[FULL] Pause planifiée (budget temps atteint) pendant Pass 1 à la ligne %d/%d") % (i, total_rows)
                    _logger.warning(pause_msg)
                    try:
                        self._save_job_checkpoint(job_id, 0, result)
                        self.env.cr.execute(
                            "UPDATE planete_pim_import_job SET next_retry_at = NOW() + interval '1 minute' WHERE id = %s",
                            [job_id],
                        )
                        self.env.cr.commit()
                    except Exception:
                        pass
                    return dict(result, paused=True, pause_reason=pause_msg)

                if (time.time() - start_ts) > timeout_seconds:
                    msg = _("[FULL] Délai dépassé pendant Passe 1 (%d sec). Arrêt à la ligne %d/%d.") % (timeout_seconds, i, total_rows)
                    _logger.error(msg)
                    if job_id:
                        self._mark_job_failed(job_id, msg)
                    raise UserError(msg)

                # Extraire l'EAN
                raw_ean = self._get_cell(row, col_idx.get("ean"))
                norm_ean = self._normalize_ean(raw_ean)
                if not norm_ean and raw_ean:
                    digits_only = self._digits_only(raw_ean)
                    if digits_only:
                        norm_ean = digits_only

                # Extraire la référence
                ref_val = self._strip_nul(self._get_cell(row, col_idx.get("ref")) or "")
                norm_ref = self._normalize_reference(ref_val) if ref_val else ""

                # Compter les EAN - SIMPLE et RAPIDE (O(n))
                if norm_ean:
                    ean_count.setdefault(norm_ean, []).append((i, norm_ref))
                    # Collecter TOUS les EAN valides pour la gestion des flags Digital
                    all_eans_in_file.add(norm_ean)

                # Compter les références - SIMPLE et RAPIDE (O(n))
                if norm_ref:
                    ref_count.setdefault(norm_ref, []).append((i, norm_ean))
                    all_refs_in_file.add(norm_ref)

                # Mise à jour progression toutes les 2 secondes OU 500 lignes
                current_time = time.time()
                if i % 500 == 0 or (current_time - last_progress_update) > 2.0:
                    last_progress_update = current_time
                    progress = (i / total_rows) * 25 if total_rows > 0 else 0  # Pass 1 = 0-25%
                    elapsed = current_time - pass1_start
                    rows_per_sec = i / elapsed if elapsed > 0 else 0
                    eta_sec = int((total_rows - i) / rows_per_sec) if rows_per_sec > 0 else 0

                    status_msg = _("[FULL] Passe 1/2: Analyse ligne %d/%d (%.0f lignes/sec, ETA: %d sec)...") % (
                        i, total_rows, rows_per_sec, eta_sec
                    )

                    try:
                        self._update_job_progress_direct(provider.id, progress, i, total_rows, status_msg)
                    except Exception:
                        pass

                    if job_id:
                        try:
                            self._update_job_progress(job_id, i, total_rows, status=status_msg, progress_override=progress)
                        except Exception:
                            pass

            # Stats Passe 1 - Analyse DÉTAILLÉE des doublons
            pass1_duration = time.time() - pass1_start
            _logger.info(
                "[FULL] Pass 1 complete in %.1f sec: %d lines, %.0f lines/sec",
                pass1_duration,
                i,
                i / pass1_duration if pass1_duration > 0 else 0,
            )

            # Identifier les doublons (avec analyse détaillée)
            for ean, rows_and_refs in ean_count.items():
                if len(rows_and_refs) > 1:
                    duplicate_eans[ean] = rows_and_refs
                    unique_refs = set(ref for _ln, ref in rows_and_refs)
                    if len(unique_refs) == 1 and rows_and_refs[0][1]:
                        ean_and_ref_duplicates += 1
                    else:
                        ean_only_duplicates += 1

            for ref, rows_and_eans in ref_count.items():
                if len(rows_and_eans) > 1:
                    duplicate_refs[ref] = rows_and_eans
                    unique_eans = set(ean for _ln, ean in rows_and_eans)
                    if len(unique_eans) > 1:
                        ref_only_duplicates += 1

            # Log détaillé des doublons
            _logger.info("=" * 70)
            _logger.info("[FULL] DIAGNOSTIC DÉTAILLÉ DES DOUBLONS")
            _logger.info("=" * 70)
            _logger.info("[FULL] EAN uniques détectés:           %d", len(ean_count))
            _logger.info("[FULL] EAN en doublon (même EAN):      %d", len(duplicate_eans))
            _logger.info("[FULL] Références uniques:             %d", len(ref_count))
            _logger.info("[FULL] Références en doublon:          %d", len(duplicate_refs))
            _logger.info("[FULL]")
            _logger.info("[FULL] ANALYSE DÉTAILLÉE:")
            _logger.info("[FULL] - Doublons EAN UNIQUEMENT (ref différentes): %d", ean_only_duplicates)
            _logger.info("[FULL] - Doublons REF UNIQUEMENT (EAN différents):  %d", ref_only_duplicates)
            _logger.info("[FULL] - VRAIS doublons (EAN ET ref identiques):   %d", ean_and_ref_duplicates)
            _logger.info("[FULL]")
            _logger.info("[FULL] RÉSUMÉ:")
            _logger.info("[FULL] - Total lignes:                 %d", total_rows)
            _logger.info("[FULL] - EAN valides:                  %d", len(all_eans_in_file))
            _logger.info(
                "[FULL] - Lignes affectées (doublons):  %d (%.1f%%)",
                len(duplicate_eans) + len(duplicate_refs),
                (len(duplicate_eans) + len(duplicate_refs)) / total_rows * 100 if total_rows > 0 else 0,
            )
            _logger.info(
                "[FULL] Pass 1 timing: %.1f sec (%.0f lignes/sec)",
                pass1_duration,
                i / pass1_duration if pass1_duration > 0 else 0,
            )
            _logger.info("=" * 70)

            # Libérer la mémoire des compteurs (on garde seulement les doublons)
            del ean_count
            del ref_count
            gc.collect()
        
        # =====================================================================
        # PASSE 2: Création des produits avec gestion quarantaine
        # DIAGNOSTIC AMÉLIORÉ: Compter les raisons de skip par catégorie
        # =====================================================================
        # =====================================================================
        # PRE-CHECK: Estimation du nombre de produits à créer (pour progress bar)
        # Vérifie un échantillon d'EANs en bulk pour estimer le % de nouveaux
        # =====================================================================
        estimated_new_count = total_rows  # Fallback: tous sont nouveaux
        try:
            sample_eans = list(all_eans_in_file)[:2000]  # Échantillonner 2000 EANs
            if sample_eans:
                with self.env.cr.savepoint():
                    self.env.cr.execute(
                        "SELECT COUNT(*) FROM product_product WHERE barcode = ANY(%s)",
                        [sample_eans]
                    )
                    existing_sample_count = self.env.cr.fetchone()[0]
                    existing_pct = existing_sample_count / len(sample_eans) if sample_eans else 0
                    estimated_new_count = max(1, int(total_rows * (1 - existing_pct)))
                    _logger.info("[FULL] Pre-check: %d/%d EANs exist (%.1f%%) -> estimated %d new products",
                                existing_sample_count, len(sample_eans), existing_pct * 100, estimated_new_count)
        except Exception as precheck_err:
            _logger.warning("[FULL] Pre-check failed: %s", precheck_err)
        
        provider.write({
            "pim_progress_status": _("[FULL] Passe 2/2: Création produits (~%d nouveaux estimés)...") % estimated_new_count,
        })
        self.env.cr.commit()
        
        if job_id:
            try:
                self._update_job_progress(job_id, int(total_rows * 0.25), total_rows, 
                    status=_("[FULL] Passe 2/2: ~%d nouveaux produits à créer...") % estimated_new_count)
            except Exception:
                pass
        
        _logger.info("[FULL] Pass 2: Creating products from %d rows (estimated %d new)", total_rows, estimated_new_count)
        
        # ✅ DIAGNOSTIC: Compteurs détaillés pour breakdown des skips
        skip_reasons = {
            "no_ean": 0,                    # Pas d'EAN valide
            "invalid_ean": 0,               # EAN brut invalide (pas normalisé)
            "duplicate_ean": 0,             # EAN présent plusieurs fois dans le fichier
            "duplicate_ref": 0,             # Référence présente plusieurs fois
            "product_exists_ean": 0,        # Produit existe déjà (EAN trouvé)
            "product_exists_ref": 0,        # Produit existe par référence
            "product_protected_digital": 0, # Produit protégé par Digital (non-Digital ne peut pas MAJ)
            "other": 0,                     # Autres raisons
        }
        
        # Set pour éviter les doublons dans le même fichier
        created_eans_this_file = set()
        created_refs_this_file = set()
        
        # =====================================================================
        # MAPPING UNIQUEMENT: Plus de candidats hardcodés pour la marque
        # La colonne de marque est définie UNIQUEMENT par le mapping template
        # =====================================================================
        
        # Batch size pour commits périodiques (OPTIMISÉ: réduit pour éviter timeouts workers)
        batch_size = 100  # Commit tous les 100 produits (réduit pour éviter timeouts)
        checkpoint_interval = 100  # Sauvegarder le checkpoint tous les 100 produits
        
        # Cache LEGERpour les marques (limiter à 500 max pour RAM)
        brand_cache_max_size = 500
        
        # Récupérer le checkpoint si c'est un retry
        start_row = 0
        if job_id:
            start_row = self._get_job_checkpoint(job_id)
            if start_row > 0:
                _logger.info("[FULL] Resuming from checkpoint at row %d", start_row)
        
        i = 0

        # =====================================================================
        # KEEPALIVE (SAFE): inline ping in the SAME thread (no background thread)
        # =====================================================================
        last_ping_ts = 0.0

        # =====================================================================
        # PERF: Bulk existence checks for EAN / REF (when Pass 1 strict ran)
        # - Avoid per-line SELECT 1 calls that make FULL imports take hours.
        # - Only enabled when we already have sets from Pass 1 strict.
        # =====================================================================
        existing_eans_in_db = None
        existing_refs_in_db = None
        if do_pass1_strict and all_eans_in_file:
            try:
                existing_eans_in_db = set()
                chunk_size = 5000
                all_eans_list = list(all_eans_in_file)
                for off in range(0, len(all_eans_list), chunk_size):
                    chunk = all_eans_list[off:off + chunk_size]
                    with self.env.cr.savepoint():
                        self.env.cr.execute(
                            "SELECT barcode FROM product_product WHERE barcode = ANY(%s)",
                            [chunk],
                        )
                        existing_eans_in_db.update(r[0] for r in self.env.cr.fetchall() if r and r[0])
                _logger.info("[FULL] Bulk existence check: %d/%d EAN already in DB", len(existing_eans_in_db), len(all_eans_in_file))
            except Exception as e:
                _logger.warning("[FULL] Bulk EAN existence check failed, falling back to per-row SQL: %s", e)
                existing_eans_in_db = None

        if do_pass1_strict and all_refs_in_file:
            try:
                existing_refs_in_db = set()
                chunk_size = 5000
                all_refs_list = list(all_refs_in_file)
                for off in range(0, len(all_refs_list), chunk_size):
                    chunk = all_refs_list[off:off + chunk_size]
                    with self.env.cr.savepoint():
                        self.env.cr.execute(
                            "SELECT default_code FROM product_product WHERE default_code = ANY(%s)",
                            [chunk],
                        )
                        existing_refs_in_db.update(r[0] for r in self.env.cr.fetchall() if r and r[0])
                _logger.info("[FULL] Bulk existence check: %d/%d REF already in DB", len(existing_refs_in_db), len(all_refs_in_file))
            except Exception as e:
                _logger.warning("[FULL] Bulk REF existence check failed, falling back to per-row SQL: %s", e)
                existing_refs_in_db = None

        def _maybe_checkpoint_commit(row_i):
            """Ensure checkpoint + commit advance even on skip/quarantine paths."""
            # Checkpoint first (cheap)
            if job_id and row_i and (row_i % checkpoint_interval == 0):
                try:
                    self._save_job_checkpoint(job_id, row_i, result)
                except Exception:
                    pass
            # Commit periodically to release locks / show progress
            if row_i and (row_i % batch_size == 0):
                try:
                    try:
                        self._flush_pending_brand_agg(pending_brand_agg)
                        pending_brand_agg.clear()
                    except Exception:
                        pass
                    self.env.cr.commit()
                    self.env.invalidate_all()
                    gc.collect()
                except Exception as commit_err:
                    if "closed" in str(commit_err).lower():
                        return
                    raise
        
        # Flag pour détecter si le serveur est en shutdown
        shutdown_detected = False
        
        # Obtenir le fournisseur une seule fois
        supplier_id = self._get_supplier_for_provider(provider)
        
        # ✅ FIX: Définir is_digital UNE SEULE FOIS avant la boucle
        # (utilisé pour auto_create brands et sale_ok)
        is_digital = self._is_digital_provider(provider)
        
        # =====================================================================
        # SPLIT SUPPORT: Si start_line/end_line sont fournis, ne traiter que cette plage
        # La Passe 1 (doublons) a déjà scanné TOUT le fichier
        # =====================================================================
        effective_start = start_line if start_line is not None else 0
        effective_end = end_line if end_line is not None else total_rows
        if start_line is not None or end_line is not None:
            _logger.info("[FULL] SPLIT MODE: Processing lines %d-%d only (total=%d)", effective_start, effective_end, total_rows)
            # Ajuster result["total"] pour refléter la plage
            result["total"] = effective_end - effective_start
        
        # Lire le fichier en streaming (2ème passe)
        for row in self._iter_csv_rows(file_path, has_header=has_header, encoding=sel_encoding, delimiter=sel_delimiter, delimiter_regex=sel_delimiter_regex):
            # Vérifier si le cursor est toujours ouvert (détection shutdown)
            if getattr(self.env.cr, 'closed', False):
                if not shutdown_detected:
                    _logger.warning("[FULL] Shutdown detected at row %d, saving checkpoint and exiting...", i)
                    shutdown_detected = True
                    # Sauvegarder le checkpoint pour permettre le retry
                    if job_id:
                        self._save_job_checkpoint(job_id, i, result)
                break  # Sortir de la boucle proprement
            
            try:
                i += 1

                # Safe keepalive ping every ~30s to avoid idle timeouts, without threading.
                last_ping_ts = _safe_inline_db_ping(self.env, last_ping_ts, interval_sec=30)

                # ✅ RETRY/RESUME SUPPORT: si un checkpoint existe, sauter les lignes déjà traitées
                # NOTE: start_row est 1-based (compteur i ci-dessus), car _save_job_checkpoint
                # stocke le compteur i de cette boucle.
                if start_row and i <= start_row:
                    continue
                
                # SPLIT: Sauter les lignes avant la plage assignée
                if i <= effective_start:
                    continue
                # SPLIT: Arrêter après la fin de la plage assignée
                if i > effective_end:
                    break
                
                # Mettre à jour la progression tous les 100 produits
                # ✅ FIX PROGRESS v2: Progression basée sur la POSITION dans le fichier (row/total)
                # C'est plus fiable car ça avance régulièrement, même quand la plupart des produits existent
                # Le statut affiche les stats détaillées (créés, MAJ, quarantaine)
                if i % 100 == 0:
                    # Progress basé sur la position dans le fichier (25-99%)
                    row_progress = (i / total_rows) * 74 if total_rows > 0 else 0
                    progress = min(25 + row_progress, 99)  # Cap 99% jusqu'à la fin
                    
                    elapsed_sec = int(time.time() - start_ts)
                    rows_per_sec = i / elapsed_sec if elapsed_sec > 0 else 0
                    eta_sec = int((total_rows - i) / rows_per_sec) if rows_per_sec > 0 else 0
                    
                    status_msg = _("[FULL] Ligne %d/%d - %d créés, %d MAJ, %d quarant., %d err (%.0f l/s, ETA %d min)") % (
                        i, total_rows, result["created"], result["updated"], result["quarantined"],
                        result["errors"], rows_per_sec, eta_sec // 60
                    )
                    try:
                        self._update_job_progress_direct(provider.id, progress, i, total_rows, status_msg)
                    except Exception:
                        pass
                    if job_id:
                        try:
                            self._update_job_progress(
                                job_id, i, total_rows,
                                status=status_msg,
                                created=result["created"],
                                updated=result["updated"],
                                errors=result["errors"],
                                progress_override=progress
                            )
                        except Exception:
                            pass
                
                # Timeout enforcement
                if (time.time() - start_ts) > timeout_seconds:
                    msg = _("[FULL] Délai dépassé (%d sec). Arrêt à la ligne %d/%d.") % (timeout_seconds, i, total_rows)
                    try:
                        self._update_job_progress_direct(provider.id, (i / total_rows) * 100 if total_rows else 0, i, total_rows, msg)
                    except Exception:
                        pass
                    if job_id:
                        try:
                            self._mark_job_failed(job_id, msg)
                            self._update_job_progress(job_id, i, total_rows, status=msg, created=result["created"], skipped=result["skipped_existing"], errors=result["errors"])
                        except Exception:
                            pass
                    self.env.cr.commit()
                    raise UserError(msg)
                
                # Extraire l'EAN
                raw_ean = self._get_cell(row, col_idx.get("ean"))
                norm_ean = self._normalize_ean(raw_ean)
                
                # IMPORTANT: Garder l'EAN brut (digits-only) pour déduplication même si invalide
                raw_ean_digits = self._digits_only(raw_ean) if raw_ean else ""
                original_ean = norm_ean or raw_ean_digits  # Garder l'original pour déduplication
                
                # Fallback: utiliser digits_only si la normalisation stricte échoue
                if not norm_ean and raw_ean:
                    digits_only = self._digits_only(raw_ean)
                    if digits_only:
                        norm_ean = digits_only
                
                # Extraire les autres valeurs
                name_val = self._strip_nul(self._get_cell(row, col_idx.get("name")) or "")
                ref_val = self._strip_nul(self._get_cell(row, col_idx.get("ref")) or "")
                norm_ref = self._normalize_reference(ref_val) if ref_val else ""
                price_val = self._to_float(self._get_cell(row, col_idx.get("price")))
                supplier_stock_val = self._to_float(self._get_cell(row, col_idx.get("supplier_stock")))
                
                # =====================================================================
                # MARQUE: Récupérer via le mapping template OU fallback candidats
                # 1) Le mapping template a product_brand_id → utiliser les colonnes mappées
                # 2) Sinon → fallback: chercher dans les colonnes courantes (libellé marque, brand, etc.)
                # =====================================================================
                brand_id = False
                raw_brand = ""
                
                # Vérifier si le mapping template définit un champ pour la marque
                dynamic_mapping = options.get("mapping") if options else None
                mapping_has_brand = dynamic_mapping and "product_brand_id" in dynamic_mapping
                if mapping_has_brand:
                    # Utiliser le mapping template pour trouver la colonne de marque
                    brand_source_cols = dynamic_mapping.get("product_brand_id", [])
                    if i <= 3:
                        _logger.info("[FULL-BRAND] Looking for brand in mapping cols: %s (hdr_index keys: %s)", 
                                     brand_source_cols, list(hdr_index.keys())[:15])
                    for src_col in brand_source_cols:
                        col_idx_brand = hdr_index.get(self._normalize_string_for_comparison(src_col))
                        if col_idx_brand is not None and col_idx_brand < len(row):
                            raw_brand = (row[col_idx_brand] or "").strip()
                            if raw_brand:
                                if i <= 5:
                                    _logger.info("[FULL-BRAND] ✅ Row %d: brand='%s' from mapped column '%s' (idx=%d)", i, raw_brand, src_col, col_idx_brand)
                                break
                    if not raw_brand and i <= 10:
                        _logger.info("[FULL-BRAND] ⚠️ Row %d: brand EMPTY in mapped columns %s (product will have no brand)", i, brand_source_cols)
                
                # FALLBACK: UNIQUEMENT si le mapping template ne définit PAS product_brand_id
                # ✅ FIX: Ne PAS utiliser le fallback quand le mapping template existe
                # pour éviter de lire la mauvaise colonne (ex: "Code fournisseur" au lieu de "Libellé marque")
                if not raw_brand and not mapping_has_brand:
                    brand_candidates_fallback = [
                        "libelle marque", "libellé marque", "brand", "marque", "brand_id",
                        "fabricant", "manufacturer", "brand name", "brand_name",
                    ]
                    for nm in brand_candidates_fallback:
                        nm_normalized = self._normalize_string_for_comparison(nm)
                        col_idx_brand = hdr_index.get(nm_normalized)
                        if col_idx_brand is not None and col_idx_brand < len(row):
                            raw_brand = (row[col_idx_brand] or "").strip()
                            if raw_brand:
                                _logger.info("[FULL-BRAND] FALLBACK (no mapping): Found brand '%s' in column '%s' (idx=%d)", raw_brand, nm, col_idx_brand)
                                break
                
                raw_brand = self._strip_nul(raw_brand)
                
                if raw_brand:
                    brand_id = self._find_or_create_brand(
                        raw_brand,
                        brand_cache,
                        new_brands_created,
                        provider_id=provider.id,
                        pending_brand_agg=pending_brand_agg,
                        sample={
                            "ean": norm_ean,
                            "ref": norm_ref,
                            "name": name_val,
                        },
                    )
                
                # Déterminer si ce produit doit aller en quarantaine
                quarantine_reason = None
                quarantine_details = None
                should_clear_ean = False
                
                # Cas 1: Pas d'EAN valide -> quarantaine
                if not norm_ean:
                    quarantine_reason = "no_ean"
                    quarantine_details = _("EAN brut: '%s' - Aucun code-barres valide détecté") % (raw_ean or "vide")
                
                # Cas 2: EAN en doublon dans le fichier -> quarantaine + vidage EAN
                elif norm_ean in duplicate_eans:
                    quarantine_reason = "duplicate_ean"
                    dup_rows = duplicate_eans.get(norm_ean, [])
                    quarantine_details = _("EAN '%s' présent %d fois aux lignes: %s") % (
                        norm_ean, len(dup_rows), ", ".join(str(r[0]) for r in dup_rows[:10])
                    )
                    should_clear_ean = True
                
                # Cas 3: Référence en doublon -> quarantaine
                elif norm_ref and norm_ref in duplicate_refs:
                    quarantine_reason = "duplicate_ref"
                    dup_rows = duplicate_refs.get(norm_ref, [])
                    quarantine_details = _("Référence '%s' présente %d fois aux lignes: %s") % (
                        norm_ref, len(dup_rows), ", ".join(str(r[0]) for r in dup_rows[:10])
                    )
                
                # Si produit doit aller en quarantaine
                if quarantine_reason:
                    try:
                        with self.env.cr.savepoint():
                            staging_vals = {
                                "name": name_val or norm_ean or norm_ref or (_("Produit ligne %d") % i),
                                "ean13": None if should_clear_ean else norm_ean,  # Vider si doublon
                                # TOUJOURS stocker l'EAN original (brut) pour déduplication future
                                "original_ean": original_ean or raw_ean_digits or None,
                                "default_code": norm_ref or None,
                                "standard_price": price_val,
                                "qty_available": supplier_stock_val,
                                "provider_id": provider.id,
                                "supplier_id": supplier_id,
                                "file_name": self._strip_nul(filename or ""),
                                "row_number": i,
                                "state": "pending",
                                "quarantine_reason": quarantine_reason,
                                "quarantine_details": quarantine_details,
                                # ⚠️ MULTI-SOCIÉTÉS: company_id=False pour partager entre sociétés
                                "company_id": False,
                                "currency_id": self.env.company.currency_id.id,
                                "data_json": self._strip_nul_in({
                                    "headers": headers,
                                    "row": row,
                                    "raw_ean": raw_ean,
                                    "norm_ean": norm_ean,
                                    "norm_ref": norm_ref,
                                }),
                            }
                            if brand_id:
                                staging_vals["brand_id"] = brand_id
                            
                            # UPSERT: Vérifier si un enregistrement similaire existe déjà en staging (quarantaine)
                            # pour éviter les doublons à chaque import
                            # Rechercher par: EAN original, OU référence
                            existing_staging = None
                            
                            # 1. Chercher par EAN original (si disponible) - domain Odoo corrigé
                            if original_ean:
                                existing_staging = Staging.search([
                                    ("provider_id", "=", provider.id),
                                    ("state", "=", "pending"),
                                    "|",
                                    ("ean13", "=", original_ean),
                                    ("original_ean", "=", original_ean),
                                ], limit=1)
                            
                            # 2. Chercher par référence (si pas trouvé par EAN)
                            if not existing_staging and norm_ref:
                                existing_staging = Staging.search([
                                    ("provider_id", "=", provider.id),
                                    ("state", "=", "pending"),
                                    ("default_code", "=", norm_ref),
                                ], limit=1)
                            
                            # 3. Chercher par EAN brut digits-only (si pas trouvé et qu'on a un EAN brut)
                            if not existing_staging and raw_ean:
                                digits_ean = self._digits_only(raw_ean)
                                if digits_ean:
                                    existing_staging = Staging.search([
                                        ("provider_id", "=", provider.id),
                                        ("state", "=", "pending"),
                                        "|",
                                        ("ean13", "=", digits_ean),
                                        ("original_ean", "=", digits_ean),
                                    ], limit=1)
                            
                            if existing_staging:
                                # Mettre à jour l'enregistrement existant (pas de création de doublon)
                                existing_staging.write(staging_vals)
                                _logger.debug("[FULL] Updated existing quarantine record for ref=%s, ean=%s", norm_ref, original_ean)
                                # Ne pas compter comme nouveau "quarantined"
                            else:
                                # Créer un nouvel enregistrement
                                Staging.create(staging_vals)
                                result["quarantined"] += 1
                            
                    except Exception as q_err:
                        _logger.warning("[FULL] Error creating quarantine record for row %d: %s", i, q_err)
                        result["errors"] += 1
                    _maybe_checkpoint_commit(i)
                    continue
                
                # Vérifier si déjà créé dans ce fichier (par EAN)
                if norm_ean in created_eans_this_file:
                    result["skipped_existing"] += 1
                    _maybe_checkpoint_commit(i)
                    continue
                
                # Vérifier si déjà créé dans ce fichier (par référence)
                if norm_ref and norm_ref in created_refs_this_file:
                    result["skipped_existing"] += 1
                    _maybe_checkpoint_commit(i)
                    continue
                
                # RÈGLE FULL: Vérifier si le produit existe déjà en base via SQL direct
                is_digital = self._is_digital_provider(provider)
                existing_product_id, existing_template_id = None, None
                
                # PERF: bulk existence checks (when Pass 1 strict ran)
                # If sets are built, prefer membership; else fallback to per-row SQL.
                if existing_eans_in_db is not None:
                    ean_exists = norm_ean in existing_eans_in_db
                else:
                    ean_exists = self._ean_exists_in_db(norm_ean)

                if ean_exists:
                    # =====================================================================
                    # RÈGLE DIGITAL: Si Digital, reprendre le contrôle du produit existant
                    # Les autres fournisseurs skipent les produits existants
                    # =====================================================================
                    if is_digital:
                        existing_product_id, existing_template_id = self._find_product_by_ean(norm_ean)
                        if existing_template_id:
                            _logger.info("[FULL-DIGITAL] Ligne %d: EAN %s existe -> Digital reprend le contrôle", i, norm_ean)
                            # Ne pas skip, on va traiter ce produit plus bas
                        else:
                            result["skipped_existing"] += 1
                            skip_reasons["product_exists_ean"] += 1
                            _logger.debug("[FULL] Ligne %d: EAN %s existe mais template non trouvé -> ignoré", i, norm_ean)
                            continue
                    else:
                        result["skipped_existing"] += 1
                        skip_reasons["product_exists_ean"] += 1
                        _logger.debug("[FULL] Ligne %d: EAN %s existe déjà en base -> ignoré", i, norm_ean)
                        _maybe_checkpoint_commit(i)
                        continue
                
                # Vérifier si référence existe déjà en base (sauf si Digital a déjà trouvé par EAN)
                if norm_ref and not existing_template_id:
                    if existing_refs_in_db is not None:
                        ref_exists = norm_ref in existing_refs_in_db
                    else:
                        ref_exists = self._ref_exists_in_db(norm_ref)
                else:
                    ref_exists = False

                if norm_ref and ref_exists and not existing_template_id:
                    if is_digital:
                        # Digital peut aussi reprendre par référence
                        found_pid, found_tid = self._find_product_by_ref(norm_ref)
                        if found_tid:
                            existing_product_id, existing_template_id = found_pid, found_tid
                            _logger.info("[FULL-DIGITAL] Ligne %d: Ref %s existe -> Digital reprend le contrôle", i, norm_ref)
                        else:
                            result["skipped_existing"] += 1
                            _logger.debug("[FULL] Ligne %d: référence %s existe mais template non trouvé -> ignoré", i, norm_ref)
                            continue
                    else:
                        result["skipped_existing"] += 1
                        _logger.debug("[FULL] Ligne %d: référence %s existe déjà en base -> ignoré", i, norm_ref)
                        _maybe_checkpoint_commit(i)
                        continue
                
                # =====================================================================
                # MISE À JOUR PRODUIT EXISTANT (TOUS FOURNISSEURS avec règles priorité)
                # Digital écrase tout, les autres ne peuvent écraser que si source_dsonline=False
                # Le name n'est JAMAIS écrasé après création
                # =====================================================================
                if existing_template_id:
                    try:
                        tmpl_rec = ProductTemplate.browse(existing_template_id)
                        if tmpl_rec.exists():
                            variant = tmpl_rec.product_variant_id
                            
                            # ✅ Vérifier les permissions de mise à jour via la méthode existante
                            can_update_content = self._can_update_content_fields(tmpl_rec, is_digital)
                            
                            if can_update_content:
                                # ==========================================
                                # AUTORISÉ : Mise à jour complète (sauf name)
                                # ==========================================
                                _logger.info("[FULL-UPDATE] Ligne %d: EAN %s existe -> mise à jour autorisée (is_digital=%s, source_dsonline=%s)", 
                                            i, norm_ean, is_digital, tmpl_rec.product_variant_id.source_dsonline if tmpl_rec.product_variant_id else False)
                                
                                # Activer les flags Digital si applicable
                                if is_digital:
                                    self._update_digital_flags(tmpl_rec, is_digital_source=True)
                                
                                # =====================================================================
                                # CORRECTION BUG: Récupérer le mapping depuis options OU depuis provider
                                # Les options peuvent être None ou vides dans certains cas
                                # =====================================================================
                                dynamic_mapping = None
                                mapping_lines = None
                                
                                # 1. Essayer depuis les options passées
                                if options and options.get("mapping"):
                                    dynamic_mapping = options.get("mapping")
                                    mapping_lines = options.get("mapping_lines", [])
                                    _logger.info("[FULL-UPDATE] Mapping from options: %d fields", len(dynamic_mapping))
                                
                                # 2. FALLBACK: Recharger depuis le provider si options vides
                                if not dynamic_mapping and provider:
                                    _logger.warning("[FULL-UPDATE] ⚠️ Options mapping empty, reloading from provider template...")
                                    mapping_result = self._build_mapping_from_template(provider)
                                    if mapping_result.get("has_template"):
                                        dynamic_mapping = mapping_result.get("mapping", {})
                                        mapping_lines = mapping_result.get("mapping_lines", [])
                                        _logger.info("[FULL-UPDATE] ✅ Reloaded mapping from provider: %d fields", len(dynamic_mapping))
                                    else:
                                        _logger.error("[FULL-UPDATE] ❌ No mapping template found for provider %s", provider.id)
                                
                                # 3. Appliquer le mapping si disponible
                                if dynamic_mapping:
                                    _logger.info("[FULL-UPDATE] 📝 Applying mapping to product %s with %d fields: %s", 
                                                tmpl_rec.id, len(dynamic_mapping), list(dynamic_mapping.keys())[:5])
                                    self._apply_mapping_to_product(
                                        tmpl_rec, variant, row, headers, hdr_index,
                                        dynamic_mapping, mapping_lines, options or {},
                                        exclude_name=True  # ✅ NE JAMAIS écraser le name
                                    )
                                    
                                    # Créer les ODR si mappées
                                    self._create_odr_from_mapping(
                                        tmpl_rec, row, headers, hdr_index,
                                        dynamic_mapping, mapping_lines
                                    )
                                    
                                    # Créer/MAJ le supplierinfo
                                    if supplier_id:
                                        self._create_supplierinfo_from_mapping(
                                            tmpl_rec, supplier_id, row, headers, hdr_index,
                                            dynamic_mapping, mapping_lines
                                        )
                                else:
                                    _logger.error("[FULL-UPDATE] ❌ NO MAPPING available for product %s - skipping field updates!", tmpl_rec.id)
                                
                                # =====================================================================
                                # AUTO-ALIAS: Si le produit existe avec une marque différente,
                                # créer automatiquement un alias pour le nom de marque du CSV
                                # =====================================================================
                                if raw_brand and tmpl_rec.product_brand_id and brand_id:
                                    # Le produit a déjà une marque ET on a trouvé une marque dans le CSV
                                    # Vérifier si elles sont différentes et créer un alias si nécessaire
                                    try:
                                        alias_created = self._create_brand_alias_if_needed(
                                            tmpl_rec.product_brand_id.id,
                                            raw_brand,
                                            norm_ean,
                                            provider.id
                                        )
                                        if alias_created:
                                            _logger.info("[FULL-UPDATE] 🏷️ Auto-alias créé: '%s' -> marque '%s' (EAN=%s)",
                                                        raw_brand, tmpl_rec.product_brand_id.name, norm_ean)
                                    except Exception as alias_err:
                                        _logger.warning("[FULL-UPDATE] Could not create auto-alias: %s", alias_err)
                                
                                # =====================================================================
                                # CORRECTION BUG: Digital doit TOUJOURS pouvoir mettre à jour la marque
                                # même si une marque (incorrecte) existe déjà
                                # Les autres fournisseurs ne peuvent mettre la marque que si vide
                                # =====================================================================
                                if brand_id:
                                    if is_digital or not tmpl_rec.product_brand_id:
                                        tmpl_rec.write({"product_brand_id": brand_id})
                                        _logger.info("[FULL-UPDATE] ✅ Marque mise à jour: %s -> %s (is_digital=%s)", 
                                                    tmpl_rec.product_brand_id.name if tmpl_rec.product_brand_id else "vide", 
                                                    brand_id, is_digital)
                                
                                # ✅ FIX: Activer sale_ok pour Digital (même en UPDATE)
                                # Digital est le fournisseur maître, ses produits doivent être vendables
                                if is_digital:
                                    try:
                                        if not tmpl_rec.sale_ok:
                                            tmpl_rec.write({"sale_ok": True})
                                            _logger.info("[FULL-UPDATE] ✅ sale_ok activé pour produit Digital %s", tmpl_rec.id)
                                    except Exception:
                                        pass
                                
                                result["updated"] += 1
                                _logger.info("[FULL-UPDATE] ✅ Produit %s mis à jour (mapping complet sauf name)", existing_template_id)
                                
                            else:
                                # ==========================================
                                # BLOQUÉ : Mise à jour UNIQUEMENT supplierinfo
                                # (produit géré par Digital, fournisseur non-Digital)
                                # ==========================================
                                _logger.info("[FULL-UPDATE] Ligne %d: EAN %s existe mais bloqué par Digital -> MAJ supplierinfo uniquement", i, norm_ean)
                                
                                # Mettre à jour UNIQUEMENT le supplierinfo de ce fournisseur
                                dynamic_mapping = options.get("mapping") if options else None
                                mapping_lines = options.get("mapping_lines") if options else None
                                
                                if supplier_id and dynamic_mapping:
                                    self._create_supplierinfo_from_mapping(
                                        tmpl_rec, supplier_id, row, headers, hdr_index,
                                        dynamic_mapping, mapping_lines
                                    )
                                
                                result["skipped_existing"] += 1  # Compter comme "skipped" car contenu non mis à jour
                                _logger.info("[FULL-UPDATE] ⏭️ Produit %s: contenu protégé par Digital, supplierinfo mis à jour", existing_template_id)
                            
                            # Marquer comme traité dans ce fichier
                            created_eans_this_file.add(norm_ean)
                            if norm_ref:
                                created_refs_this_file.add(norm_ref)
                            
                            continue  # Passer à la ligne suivante
                            
                    except Exception as update_err:
                        _logger.warning("[FULL-UPDATE] Erreur mise à jour produit %s: %s", existing_template_id, update_err)
                        result["errors"] += 1
                    
                    continue  # Passer à la ligne suivante
                
                # Créer le produit avec sale_ok=False pour éviter la contrainte ivspro_profile
                create_vals = {
                    "name": name_val or norm_ean,
                    "barcode": norm_ean,
                    "sale_ok": False,
                    "purchase_ok": True,
                }
                
                # Odoo 18: use detailed_type='consu' (consumable) as safe default
                try:
                    if "detailed_type" in ProductTemplate._fields:
                        create_vals["detailed_type"] = "consu"
                except Exception:
                    pass
                
                # Toujours définir default_code (utiliser EAN si pas de référence)
                if norm_ref:
                    create_vals["default_code"] = norm_ref
                else:
                    create_vals["default_code"] = norm_ean
                
                if price_val > 0:
                    create_vals["standard_price"] = price_val
                
                # Ajouter la marque si trouvée
                if brand_id:
                    create_vals["product_brand_id"] = brand_id
                
                try:
                    with self.env.cr.savepoint():
                        tmpl = ProductTemplate.create(create_vals)
                        variant = tmpl.product_variant_id
                        
                        # =====================================================================
                        # NOUVEAU: Remplir x_created_by_supplier_id (ne change jamais après)
                        # =====================================================================
                        if supplier_id and tmpl:
                            try:
                                if "x_created_by_supplier_id" in tmpl._fields:
                                    tmpl.write({"x_created_by_supplier_id": supplier_id})
                            except Exception as creator_err:
                                _logger.warning("[FULL] Could not set x_created_by_supplier_id: %s", creator_err)
                        
                        # =====================================================================
                        # NOUVEAU: Gérer les flags Digital si c'est GroupeDigital
                        # =====================================================================
                        is_digital = self._is_digital_provider(provider)
                        if is_digital:
                            self._update_digital_flags(tmpl, is_digital_source=True)
                            _logger.debug("[FULL] Product %s created by Digital provider, flags set", tmpl.id)
                        
                        # =====================================================================
                        # MAPPING TEMPLATE: Appliquer le mapping complet des options
                        # =====================================================================
                        options = options or {}
                        dynamic_mapping = options.get("mapping")
                        mapping_lines = options.get("mapping_lines")
                        
                        if dynamic_mapping and tmpl:
                            _logger.info("[FULL-MAPPING] Applying template mapping to product %s with %d rules", 
                                        tmpl.id, len(dynamic_mapping))
                            # Appliquer le mapping complet (champs IVS, etc.)
                            self._apply_mapping_to_product(
                                tmpl, variant, row, headers, hdr_index,
                                dynamic_mapping, mapping_lines, options
                            )
                            
                            # Créer les ODR si mappées
                            self._create_odr_from_mapping(
                                tmpl, row, headers, hdr_index,
                                dynamic_mapping, mapping_lines
                            )
                            
                            # Créer le supplierinfo depuis le mapping
                            if supplier_id:
                                self._create_supplierinfo_from_mapping(
                                    tmpl, supplier_id, row, headers, hdr_index,
                                    dynamic_mapping, mapping_lines
                                )
                        else:
                            # Fallback: Créer le supplierinfo avec les valeurs de base
                            if supplier_id and tmpl:
                                self._create_supplierinfo(tmpl.id, supplier_id, price_val, supplier_stock_val)
                        
                        # Après création, activer sale_ok:
                        # ✅ FIX DIGITAL: Toujours sale_ok=True pour Digital (même sans marque)
                        # Pour les autres: sale_ok=True si brand ET default_code sont présents
                        should_activate_sale = False
                        if is_digital:
                            should_activate_sale = True  # Digital -> toujours vendable
                        elif brand_id and create_vals.get("default_code"):
                            should_activate_sale = True  # Autres -> si marque + ref
                        
                        if should_activate_sale:
                            try:
                                tmpl.write({"sale_ok": True})
                            except Exception:
                                pass
                        
                        # Marquer comme créé pour éviter les doublons dans ce fichier
                        created_eans_this_file.add(norm_ean)
                        if norm_ref:
                            created_refs_this_file.add(norm_ref)
                        result["created"] += 1
                        
                except Exception as create_err:
                    _logger.warning("[FULL] Error creating product for row %d (EAN=%s): %s", i, norm_ean, create_err)
                    result["errors"] += 1
                
                # COMMIT PÉRIODIQUE + GARBAGE COLLECTION + CHECKPOINT
                if i % batch_size == 0:
                    # Vérifier si le cursor est fermé AVANT le commit
                    if getattr(self.env.cr, 'closed', False):
                        _logger.warning("[FULL] Shutdown detected before commit at row %d, exiting...", i)
                        shutdown_detected = True
                        break
                    
                    try:
                        # Flush des marques pending (batch) avant commit
                        try:
                            self._flush_pending_brand_agg(pending_brand_agg)
                            pending_brand_agg = {}
                        except Exception:
                            pass
                        self.env.cr.commit()
                        self.env.invalidate_all()
                        _logger.info("[FULL] Committed batch at row %d (%d created, %d quarantined)", 
                                     i, result["created"], result["quarantined"])
                    except Exception as commit_err:
                        # Shutdown pendant le commit
                        if "closed" in str(commit_err).lower():
                            _logger.warning("[FULL] Shutdown during commit at row %d, exiting...", i)
                            shutdown_detected = True
                            break
                        raise
                
                # Sauvegarder le checkpoint périodiquement (séparé du commit pour robustesse)
                if job_id and i % checkpoint_interval == 0:
                    self._save_job_checkpoint(job_id, i, result)
                    _logger.debug("[FULL] Saved checkpoint at row %d", i)

                # Ensure periodic maintenance even if creation/update paths didn't hit the commit block
                _maybe_checkpoint_commit(i)
                    
            except Exception as row_err:
                _logger.warning("[FULL] Error processing row %d: %s", i, row_err)
                result["errors"] += 1
                # Sauvegarder le checkpoint même en cas d'erreur
                if job_id:
                    self._save_job_checkpoint(job_id, i, result)
        
        # (no keepalive thread to stop)
        
        # Commit final (seulement si le cursor est encore ouvert)
        if not shutdown_detected and not getattr(self.env.cr, 'closed', False):
            try:
                # Flush final des marques pending avant commit final
                try:
                    self._flush_pending_brand_agg(pending_brand_agg)
                    pending_brand_agg = {}
                except Exception:
                    pass
                self.env.cr.commit()
                self.env.invalidate_all()
                gc.collect()
                
                # =====================================================================
                # NOUVEAU: Décocher source_dsonline pour les produits Digital absents
                # Seulement si c'est le provider Digital (GroupeDigital)
                # =====================================================================
                if self._is_digital_provider(provider) and all_eans_in_file:
                    _logger.info("[FULL] Provider is Digital - checking for products to unmark (%d EANs in file)", len(all_eans_in_file))
                    unmarked_count = self._unmark_absent_digital_products(all_eans_in_file, provider)
                    if unmarked_count > 0:
                        _logger.info("[FULL] ✅ Unmarked %d products (source_dsonline=False)", unmarked_count)
                        result["digital_unmarked"] = unmarked_count
                    self.env.cr.commit()
                
                # Créer l'historique avec notification des nouvelles marques
                self._create_import_history(provider, filename, result, mode="FULL", new_brands=new_brands_created)
                
                # Final job progress update
                if job_id:
                    try:
                        self._update_job_progress(
                            job_id, total_rows, total_rows,
                            status="[FULL] Termine: %d crees, %d MAJ, %d quarantaine, %d existants ignores" % (
                                result["created"], result["updated"], result["quarantined"], result["skipped_existing"]
                            ),
                            created=result["created"],
                            updated=result["updated"],
                            skipped=result["skipped_existing"],
                            errors=result["errors"]
                        )
                    except Exception:
                        pass
            except Exception as final_err:
                _logger.warning("[FULL] Final commit/cleanup failed (shutdown?): %s", final_err)
        else:
            _logger.info("[FULL] Skipping final commit/cleanup due to shutdown")
        
        # =====================================================================
        # CORRECTION: Stocker provider.id et provider.name AVANT les logs
        # pour éviter "cursor already closed" si le cursor est fermé
        # =====================================================================
        provider_id_for_log = provider.id if provider else None
        provider_name_for_log = provider.name if provider else "N/A"
        
        # Log récapitulatif détaillé au niveau INFO (toujours visible)
        _logger.info("=" * 70)
        _logger.info("[FULL] RÉSUMÉ IMPORT pour provider %s (%s)", provider_id_for_log, provider_name_for_log)
        _logger.info("=" * 70)
        _logger.info("[FULL] ✅ Produits créés:                    %d", result["created"])
        _logger.info("[FULL] 🔄 Produits mis à jour:               %d", result["updated"])
        _logger.info("[FULL] ⏭️  Produits ignorés (protégés):      %d", result["skipped_existing"])
        _logger.info("[FULL] ⚠️  Produits en quarantaine:          %d", result["quarantined"])
        _logger.info("[FULL] ❌ Erreurs:                          %d", result["errors"])
        _logger.info("[FULL] 📊 Total lignes traitées:            %d", result["total"])
        _logger.info("=" * 70)
        
        # =====================================================================
        # ✅ DIAGNOSTIC AMÉLIORÉ: Breakdown détaillé des raisons de skip
        # C'est LA INFO CRITIQUE que le user demandait!
        # =====================================================================
        if skip_reasons:
            total_skipped = sum(skip_reasons.values())
            _logger.info("[FULL] 📋 DIAGNOSTIC DÉTAILLÉ DES SKIPS (%d au total):", total_skipped)
            _logger.info("[FULL]    - Produits existants (EAN trouvé):    %d (%.1f%%)", 
                        skip_reasons.get("product_exists_ean", 0),
                        (skip_reasons.get("product_exists_ean", 0) / total_skipped * 100) if total_skipped > 0 else 0)
            _logger.info("[FULL]    - Produits existants (Référence):    %d (%.1f%%)", 
                        skip_reasons.get("product_exists_ref", 0),
                        (skip_reasons.get("product_exists_ref", 0) / total_skipped * 100) if total_skipped > 0 else 0)
            _logger.info("[FULL]    - EAN en doublon dans fichier:       %d (%.1f%%)", 
                        skip_reasons.get("duplicate_ean", 0),
                        (skip_reasons.get("duplicate_ean", 0) / total_skipped * 100) if total_skipped > 0 else 0)
            _logger.info("[FULL]    - Références en doublon:             %d (%.1f%%)", 
                        skip_reasons.get("duplicate_ref", 0),
                        (skip_reasons.get("duplicate_ref", 0) / total_skipped * 100) if total_skipped > 0 else 0)
            _logger.info("[FULL]    - EAN invalide (non normalisé):      %d (%.1f%%)", 
                        skip_reasons.get("invalid_ean", 0),
                        (skip_reasons.get("invalid_ean", 0) / total_skipped * 100) if total_skipped > 0 else 0)
            _logger.info("[FULL]    - Pas d'EAN -> Quarantaine:          %d (%.1f%%)", 
                        skip_reasons.get("no_ean", 0),
                        (skip_reasons.get("no_ean", 0) / total_skipped * 100) if total_skipped > 0 else 0)
            _logger.info("[FULL]    - Produit protégé par Digital:       %d (%.1f%%)", 
                        skip_reasons.get("product_protected_digital", 0),
                        (skip_reasons.get("product_protected_digital", 0) / total_skipped * 100) if total_skipped > 0 else 0)
            _logger.info("[FULL]    - Autres raisons:                    %d (%.1f%%)", 
                        skip_reasons.get("other", 0),
                        (skip_reasons.get("other", 0) / total_skipped * 100) if total_skipped > 0 else 0)
            _logger.info("[FULL]")
            _logger.info("[FULL] 💡 INTERPRÉTATION:")
            if skip_reasons.get("product_exists_ean", 0) + skip_reasons.get("product_exists_ref", 0) > total_skipped * 0.8:
                _logger.info("[FULL]    → Plus de 80%% des produits existaient DÉJÀ en base")
                _logger.info("[FULL]    → C'est NORMAL en mode FULL: seuls les NOUVEAUX produits sont créés")
                _logger.info("[FULL]    → Les produits existants doivent être mis à jour via DELTA ou UPDATE")
            if skip_reasons.get("no_ean", 0) > 0:
                _logger.info("[FULL]    → %d produits sans EAN ont été envoyés en QUARANTAINE pour validation", skip_reasons.get("no_ean", 0))
            if skip_reasons.get("duplicate_ean", 0) > 0:
                _logger.info("[FULL]    → %d doublons EAN détectés dans le fichier (bloqués pour éviter les conflits)", skip_reasons.get("duplicate_ean", 0))
        
        _logger.info("=" * 70)
        
        # Si beaucoup de produits ignorés, afficher un message explicatif
        if result["skipped_existing"] > 0:
            pct_skipped = (result["skipped_existing"] / result["total"] * 100) if result["total"] > 0 else 0
            _logger.info("[FULL] 💡 INFO: %.1f%% des produits existaient déjà en base (par EAN ou référence)", pct_skipped)
            _logger.info("[FULL] 💡 C'est normal en mode FULL: seuls les NOUVEAUX produits sont créés")
        
        return result

    # =========================================================================
    # DELTA IMPORT (Mise à jour prix/stock uniquement) - 3x/jour
    # =========================================================================
    @api.model
    def _cron_delta_daily(self):
        """Cron entry point: exécute l'import DELTA pour tous les providers configurés.
        Vérifie les heures configurées (ex: 9,13,17).
        
        AMÉLIORÉ:
        - Tolérance horaire: exécute dans la fenêtre de l'heure configurée
        - Reset automatique du guard si bloqué depuis plus de 2h
        - Logs détaillés pour le debugging
        """
        _logger.info("[DELTA] Cron _cron_delta_daily started at %s", fields.Datetime.now())
        
        # Kill switch global
        if self.env["ir.config_parameter"].sudo().get_param("planete_pim.disable_crons"):
            _logger.info("[DELTA] Cron disabled via config parameter")
            return True
        
        from datetime import datetime, timedelta
        now = fields.Datetime.now()
        current_hour = now.hour
        
        # Trouver tous les providers avec DELTA activé
        providers = self.env["ftp.provider"].sudo().search([
            ("schedule_pim_delta_daily", "=", True),
        ])
        
        _logger.info("[DELTA] Found %d providers with DELTA enabled", len(providers))
        
        for provider in providers:
            try:
                # ============================================================
                # RESET AUTOMATIQUE: Si le guard est bloqué depuis trop longtemps
                # Timeout configurable via ir.config_parameter
                # ============================================================
                if provider.pim_delta_cron_running:
                    if provider.pim_last_delta_date:
                        last_run = fields.Datetime.from_string(provider.pim_last_delta_date)
                        stuck_duration = now - last_run
                        # Récupérer le timeout depuis les paramètres système (défaut: 5 minutes)
                        try:
                            guard_timeout_minutes = int(
                                self.env["ir.config_parameter"].sudo().get_param(
                                    "planete_pim.job_guard_timeout_minutes", "5"
                                )
                            )
                        except Exception:
                            guard_timeout_minutes = 5
                        
                        if stuck_duration > timedelta(minutes=guard_timeout_minutes):
                            _logger.warning(
                                "[DELTA] Provider %s: Guard stuck for %s, auto-resetting after %d min",
                                provider.id, stuck_duration, guard_timeout_minutes
                            )
                            provider.sudo().write({
                                "pim_delta_cron_running": False,
                                "pim_progress_status": _("[DELTA] Guard reset automatique après blocage"),
                            })
                            self.env.cr.commit()
                        else:
                            _logger.info("[DELTA] Provider %s: import already running, skipping", provider.id)
                            continue
                    else:
                        # Pas de date de dernier run mais guard actif -> reset
                        _logger.warning("[DELTA] Provider %s: Guard active but no last_date, resetting", provider.id)
                        provider.sudo().write({
                            "pim_delta_cron_running": False,
                        })
                        self.env.cr.commit()
                
                # ============================================================
                # VÉRIFICATION HORAIRE
                # ============================================================
                delta_hours_str = provider.pim_delta_hours or "4,12,17"
                try:
                    delta_hours = [int(h.strip()) for h in delta_hours_str.split(",") if h.strip().isdigit()]
                except Exception:
                    delta_hours = [4, 12, 17]
                
                if current_hour not in delta_hours:
                    _logger.debug(
                        "[DELTA] Provider %s: current hour %d not in configured hours %s, skipping",
                        provider.id, current_hour, delta_hours
                    )
                    continue
                
                # ✅ FIX: Tolérance ADAPTÉE aux configurations d'heures
                # Le cron s'exécute TOUTES LES HEURES (interval=1h), donc on doit être STRICT pour éviter
                # les doublons à 30-45min d'intervalle. MAIS pas trop strict pour permettre les 3 execs/jour.
                # 
                # Cas typiques:
                # - 3 exécutions/jour (9,13,17) = 4h d'écart → tolérance 3h55min
                # - 2 exécutions/jour (9,17) = 8h d'écart → tolérance 7h55min
                # - 4 exécutions/jour (6,12,15,18) = 3h d'écart → tolérance 2h55min
                # 
                # Pour être sûr, on calcule l'écart MIN entre les heures configurées
                # + Protection "lock" (guard) pour empêcher les exécutions parallèles
                if provider.pim_last_delta_date:
                    last_run = fields.Datetime.from_string(provider.pim_last_delta_date)
                
                # Calculer l'écart minimum entre les heures configurées
                delta_hours_str = provider.pim_delta_hours or "9,13,17"
                try:
                    delta_hours = sorted([int(h.strip()) for h in delta_hours_str.split(",") if h.strip().isdigit()])
                except Exception:
                    delta_hours = [9, 13, 17]
                
                # Calculer l'écart minimum entre deux heures consécutives (cyclique)
                min_gap_hours = 24  # Pire cas: une seule heure configurée
                if len(delta_hours) > 1:
                    for i in range(len(delta_hours)):
                        next_h = delta_hours[(i + 1) % len(delta_hours)]
                        current_h = delta_hours[i]
                        if next_h > current_h:
                            gap = next_h - current_h
                        else:
                            gap = (24 - current_h) + next_h  # Traversée du jour
                        min_gap_hours = min(min_gap_hours, gap)
                
                # Tolérance = min_gap - 5 minutes (conservatif)
                tolerance_hours = max(0.5, min_gap_hours - (5.0 / 60))  # Min 30 min tolerance
                tolerance_delta = timedelta(hours=tolerance_hours)
                
                if (now - last_run) < tolerance_delta:
                    _logger.info("[DELTA] Provider %s: already ran %.0f min ago, skipping (need %.0f min minimum, gap=%.0f h, hours=%s)", 
                                provider.id, (now - last_run).total_seconds() / 60, 
                                tolerance_delta.total_seconds() / 60, min_gap_hours, delta_hours)
                    continue
                
                # ============================================================
                # LANCER L'IMPORT
                # ============================================================
                _logger.info("[DELTA] Starting import for provider %s (%s) at hour %d", 
                             provider.id, provider.name, current_hour)
                
                # Créer un job asynchrone plutôt que d'exécuter directement
                Job = self.env["planete.pim.import.job"].sudo()
                job = Job.create({
                    "name": _("[DELTA CRON] %s - %s") % (provider.name or "Provider", now.strftime("%Y-%m-%d %H:%M")),
                    "provider_id": provider.id,
                    "import_mode": "delta",
                    "state": "pending",
                    "progress_status": _("Planifié par cron..."),
                })
                self.env.cr.commit()
                
                _logger.info("[DELTA] Created job %s for provider %s", job.id, provider.id)
                
            except Exception as e:
                _logger.exception("[DELTA] Cron failed for provider %s: %s", provider.id, e)
                try:
                    provider.sudo().write({
                        "pim_delta_cron_running": False,
                        "pim_progress_status": _("[DELTA] Erreur cron: %s") % str(e)[:200],
                    })
                    self.env.cr.commit()
                except Exception:
                    pass
        
        return True

    @api.model
    def _process_delta_import(self, provider, job_id=None):
        """[DELTA] Import de mise à jour prix/stock uniquement.
        - Télécharge le fichier depuis FTP/SFTP
        - MET À JOUR UNIQUEMENT: prix fournisseur et stock fournisseur
        - NE CRÉE PAS de nouveaux produits
        - NE MODIFIE PAS: name, description, categories, images, EAN, attributes
        - Ignore les lignes dont l'EAN n'existe pas dans Odoo
        
        IMPORTANT: Utilise UNIQUEMENT le mapping template du provider.
        Si aucun template n'est configuré, l'import échoue avec une erreur explicite.
        """
        if isinstance(provider, int):
            provider = self.env["ftp.provider"].browse(provider)
        provider = provider.sudo().ensure_one()

        # Keep ftp.provider status fields in sync with job-based PIM imports
        try:
            now = fields.Datetime.now()
            provider.sudo().write({
                "last_connection_status": "running",
                "last_error": False,
                "last_run_at": now,
            })
            self.env.cr.commit()
        except Exception:
            pass
        
        # Récupérer le job si fourni
        job = None
        if job_id:
            job = self.env["planete.pim.import.job"].sudo().browse(job_id)
            if job.exists():
                job.write({"progress_status": _("[DELTA] Initialisation...")})
        
        _logger.info("[DELTA] _process_delta_import for provider %s (%s)", provider.id, provider.name)
        
        # Marquer le début
        provider.write({
            "pim_delta_cron_running": True,
            "pim_progress": 0.0,
            "pim_progress_total": 0,
            "pim_progress_current": 0,
            "pim_progress_status": _("[DELTA] Connexion au serveur..."),
        })
        self.env.cr.commit()
        
        tmp_path = None
        try:
            # =====================================================================
            # RÈGLE CRITIQUE: Le mapping template est OBLIGATOIRE
            # Pas de fallback vers des règles hardcodées
            # =====================================================================
            mapping_result = self._build_mapping_from_template(provider)
            
            if not mapping_result.get("has_template"):
                error_msg = _(
                    "[DELTA] ERREUR: Le provider '%s' n'a pas de template de mapping configuré.\n"
                    "Veuillez configurer un template de mapping dans: Provider → Mapping Template.\n"
                    "Le mapping template définit quelle colonne CSV correspond à quel champ Odoo."
                ) % provider.name
                _logger.error(error_msg)
                
                # Mettre à jour le job si présent
                if job_id:
                    try:
                        self._mark_job_failed(job_id, error_msg)
                    except Exception:
                        pass
                
                provider.write({
                    "pim_delta_cron_running": False,
                    "pim_progress_status": error_msg[:200],
                })
                self.env.cr.commit()
                raise UserError(error_msg)
            
            # Télécharger le fichier
            tmp_path, file_info = self._download_provider_file(provider)
            if not tmp_path:
                raise UserError(_("Aucun fichier trouvé sur le serveur"))
            
            provider.write({"pim_progress_status": _("[DELTA] Lecture du fichier...")})
            self.env.cr.commit()
            
            # Options avec mapping complet du template (UNIQUEMENT le template)
            import_options = {
                "mapping": mapping_result.get("mapping", {}),
                "mapping_lines": mapping_result.get("mapping_lines", []),
            }
            _logger.info("[DELTA] ✅ Mapping template '%s' loaded: %d target fields: %s", 
                        provider.mapping_template_id.name if provider.mapping_template_id else "N/A",
                        len(import_options.get("mapping", {})),
                        list(import_options.get("mapping", {}).keys()))
            
            # Lire et traiter le fichier
            reader_params = provider.get_csv_reader_params() or {}
            result = self._process_delta_file(
                provider,
                tmp_path,
                import_options.get("mapping", {}),  # Utiliser UNIQUEMENT le mapping template
                file_info.get("name", "import.csv"),
                has_header=reader_params.get("has_header"),
                encoding=reader_params.get("encoding"),
                delimiter=reader_params.get("delimiter"),
                delimiter_regex=reader_params.get("delimiter_regex"),
                job_id=job_id,
                options=import_options,  # NOUVEAU: Passer les options avec mapping complet
            )
            
            # Marquer comme terminé
            provider.write({
                "pim_delta_cron_running": False,
                "pim_last_delta_date": fields.Datetime.now(),
                "pim_progress": 100.0,
                "pim_progress_status": _("[DELTA] Terminé: %d prix MAJ, %d stocks MAJ, %d ignorés") % (
                    result.get("price_updated", 0),
                    result.get("stock_updated", 0),
                    result.get("skipped_not_found", 0),
                ),
            })
            self.env.cr.commit()

            # Success status for provider
            try:
                now = fields.Datetime.now()
                provider.sudo().write({
                    "last_connection_status": "ok",
                    "last_error": False,
                    "last_run_at": now,
                })
                self.env.cr.commit()
            except Exception:
                pass
            
            _logger.info("[DELTA] Completed for provider %s: %s", provider.id, result)
            return result
            
        except Exception as e:
            _logger.exception("[DELTA] Failed for provider %s: %s", provider.id, e)
            # Failure status for provider
            try:
                now = fields.Datetime.now()
                provider.sudo().write({
                    "last_connection_status": "failed",
                    "last_error": str(e)[:500],
                    "last_run_at": now,
                })
                self.env.cr.commit()
            except Exception:
                pass
            provider.write({
                "pim_delta_cron_running": False,
                "pim_progress_status": _("[DELTA] Erreur: %s") % str(e)[:200],
            })
            self.env.cr.commit()
            raise
        finally:
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass

    def _process_delta_file(self, provider, file_path, mapping, filename, has_header=True, encoding=None, delimiter=None, delimiter_regex=None, job_id=None, options=None):
        """Traitement du fichier pour l'import DELTA (mise à jour prix/stock uniquement).
        
        OPTIMISÉ pour éviter les problèmes de mémoire:
        - Pas de pré-chargement des produits
        - Lecture du fichier en streaming (ligne par ligne)
        - Requêtes SQL directes pour trouver les produits
        - Garbage collection périodique
        """
        import gc
        
        result = {
            "total": 0,
            "price_updated": 0,
            "stock_updated": 0,
            "skipped_not_found": 0,
            "skipped_no_ean": 0,
            "errors": 0,
            "warnings": [],  # Liste des warnings pour le debugging
        }
        
        # Préparation: paramètres de lecture et timeout
        has_header = True if has_header is None else bool(has_header)
        sel_encoding = encoding
        sel_delimiter = delimiter
        sel_delimiter_regex = delimiter_regex
        if sel_delimiter == "\\t":
            sel_delimiter = "\t"
        timeout_seconds = self._get_import_timeout_seconds()
        start_ts = time.time()
        
        # ====================================================================
        # AMÉLIORATION: Détection automatique du délimiteur pour DELTA aussi
        # ====================================================================
        enc_candidates = ["utf-8-sig", "utf-8", "cp1252", "latin-1"]
        if sel_encoding:
            enc_candidates = [sel_encoding] + [e for e in enc_candidates if e != sel_encoding]
        detected_enc, sample = self._read_head(file_path, enc_candidates)
        if not sel_encoding:
            sel_encoding = detected_enc
        
        # Tester le délimiteur configuré
        test_cols = 0
        if sel_delimiter_regex:
            try:
                pattern = re.compile(sel_delimiter_regex)
                first_line = sample.split('\n')[0] if sample else ""
                test_cols = len(pattern.split(first_line.rstrip("\r\n"))) if first_line else 0
            except Exception:
                test_cols = 0
        elif sel_delimiter and len(sel_delimiter) == 1:
            try:
                import io
                reader = csv.reader(io.StringIO(sample.split('\n')[0] if sample else ""), delimiter=sel_delimiter, quotechar='"')
                first_row = next(reader, [])
                test_cols = len(first_row)
            except Exception:
                test_cols = 0
        
        # Si le délimiteur configuré donne ≤1 colonne, utiliser la détection automatique
        if test_cols <= 1:
            detected_delim = self._detect_delimiter(sample or "")
            _logger.warning("[DELTA] Config delimiter gave %d cols, auto-detected: %r", test_cols, detected_delim)
            sel_delimiter = detected_delim
            sel_delimiter_regex = None  # Désactiver le regex
            result["warnings"].append(_("Délimiteur auto-détecté: '%s' (config donnait %d colonnes)") % (detected_delim, test_cols))
        
        # Compter les lignes sans tout charger en mémoire
        total_rows = self._count_csv_lines(file_path, has_header=has_header, encoding=sel_encoding)
        result["total"] = total_rows
        
        provider.write({
            "pim_progress_total": total_rows,
            "pim_progress_status": _("[DELTA] Traitement de %d lignes...") % total_rows,
        })
        self.env.cr.commit()
        
        # Mise à jour job initiale si applicable
        if job_id:
            try:
                self._update_job_progress(job_id, 0, total_rows, status=_("[DELTA] Traitement de %d lignes...") % total_rows)
            except Exception:
                pass
        
        # Set pour collecter TOUS les EAN valides du fichier (pour flags Digital)
        all_eans_in_file = set()
        
        # Lire les headers seulement
        headers = self._read_csv_headers(file_path, encoding=sel_encoding, delimiter=sel_delimiter, delimiter_regex=sel_delimiter_regex)
        
        # Index des colonnes selon le mapping
        col_idx = self._build_column_index(headers, mapping)
        
        # ⚠️ VALIDATION CRITIQUE: Vérifier si la colonne EAN a été trouvée
        if "ean" not in col_idx or col_idx.get("ean") is None:
            warning_msg = _("ATTENTION: Colonne EAN non trouvée dans le fichier! Headers détectés: %s. Mapping EAN cherché: %s") % (
                headers[:10] if headers else "aucun",
                mapping.get("ean", [])
            )
            _logger.error("[DELTA] %s", warning_msg)
            result["warnings"].append(warning_msg)
            
            # Mettre à jour le statut du job avec ce warning
            if job_id:
                try:
                    self._update_job_progress(
                        job_id, 0, total_rows,
                        status=_("[DELTA] ERREUR: Colonne EAN non trouvée - vérifier le mapping"),
                        errors=total_rows
                    )
                except Exception:
                    pass
            
            # Incrémenter skipped_no_ean pour TOUTES les lignes car la colonne n'existe pas
            result["skipped_no_ean"] = total_rows
            _logger.warning("[DELTA] Toutes les %d lignes seront ignorées car colonne EAN introuvable", total_rows)
            
            # Créer quand même l'historique avec le warning
            try:
                self._create_import_history(provider, filename, result, mode="DELTA")
            except Exception:
                pass
            
            return result
        
        SupplierInfo = self.env["product.supplierinfo"].sudo()
        
        # Récupérer le fournisseur lié au provider
        supplier_id = self._get_supplier_for_provider(provider)
        
        # Vérifier si supplier_stock existe dans le modèle (une seule fois)
        has_supplier_stock_field = "supplier_stock" in SupplierInfo._fields
        
        _logger.info("[DELTA] Starting streaming import for %d rows", total_rows)
        
        batch_size = 100
        i = 0
        
        # KEEPALIVE (SAFE): inline ping in the SAME thread (no background thread)
        last_ping_ts = 0.0
        
        # Lire le fichier en streaming
        for row in self._iter_csv_rows(file_path, has_header=has_header, encoding=sel_encoding, delimiter=sel_delimiter, delimiter_regex=sel_delimiter_regex):
            try:
                last_ping_ts = _safe_inline_db_ping(self.env, last_ping_ts, interval_sec=30)

                i += 1
                
                # Mettre à jour la progression tous les 100 produits
                if i % 100 == 0:
                    progress = (i / total_rows) * 100 if total_rows > 0 else 0
                    try:
                        self._update_job_progress_direct(provider.id, progress, i, total_rows,
                            _("[DELTA] Ligne %d/%d - %d MAJ...") % (i, total_rows, result["price_updated"]))
                    except Exception:
                        pass
                    if job_id:
                        try:
                            self._update_job_progress(
                                job_id, i, total_rows,
                                status=_("[DELTA] Ligne %d/%d - %d prix MAJ, %d stocks MAJ") % (i, total_rows, result["price_updated"], result["stock_updated"])
                            )
                        except Exception:
                            pass
                
                # Timeout enforcement
                if (time.time() - start_ts) > timeout_seconds:
                    msg = _("[DELTA] Délai dépassé (%d sec). Arrêt à la ligne %d/%d.") % (timeout_seconds, i, total_rows)
                    try:
                        self._update_job_progress_direct(provider.id, (i / total_rows) * 100 if total_rows else 0, i, total_rows, msg)
                    except Exception:
                        pass
                    if job_id:
                        try:
                            self._mark_job_failed(job_id, msg)
                            self._update_job_progress(job_id, i, total_rows, status=msg, updated=result["price_updated"] + result["stock_updated"], errors=result["errors"])
                        except Exception:
                            pass
                    self.env.cr.commit()
                    raise UserError(msg)
                # Extraire l'EAN
                raw_ean = self._get_cell(row, col_idx.get("ean"))
                norm_ean = self._normalize_ean(raw_ean)
                # Fallback: utiliser digits_only si la normalisation stricte échoue
                if not norm_ean and raw_ean:
                    digits_only = self._digits_only(raw_ean)
                    if digits_only:
                        norm_ean = digits_only
                
                # RÈGLE: Ignorer les lignes sans EAN valide (ni normalisé ni digits-only)
                if not norm_ean:
                    result["skipped_no_ean"] += 1
                    continue
                
                # Collecter TOUS les EAN valides pour la gestion des flags Digital
                all_eans_in_file.add(norm_ean)
                
                # RÈGLE DELTA: Trouver le produit via SQL direct (pas de pré-chargement)
                product_id, template_id = self._find_product_by_ean(norm_ean)
                if not template_id:
                    result["skipped_not_found"] += 1
                    continue
                
                # Extraire prix et stock
                price_val = self._to_float(self._get_cell(row, col_idx.get("price")))
                stock_val = self._to_float(self._get_cell(row, col_idx.get("stock")))
                supplier_stock_val = self._to_float(self._get_cell(row, col_idx.get("supplier_stock")))
                
                # NOUVEAU: Extraire list_price (PVGC) et standard_price (coût) pour le produit
                list_price_val = self._to_float(self._get_cell(row, col_idx.get("list_price")))
                standard_price_val = self._to_float(self._get_cell(row, col_idx.get("standard_price")))
                
                # =====================================================================
                # NOUVEAU: Mettre à jour list_price et standard_price sur le produit
                # Ces champs sont sur product.template et permettent de MAJ les prix
                # =====================================================================
                tmpl_vals = {}
                if list_price_val > 0:
                    tmpl_vals["list_price"] = list_price_val
                if standard_price_val > 0:
                    tmpl_vals["standard_price"] = standard_price_val
                
                if tmpl_vals and template_id:
                    try:
                        ProductTemplate = self.env["product.template"].sudo()
                        tmpl = ProductTemplate.browse(template_id)
                        if tmpl.exists():
                            tmpl.write(tmpl_vals)
                            _logger.debug("[DELTA] Updated product %s: list_price=%.2f, standard_price=%.2f",
                                         template_id, list_price_val, standard_price_val)
                    except Exception as tmpl_err:
                        _logger.warning("[DELTA] Error updating template %s prices: %s", template_id, tmpl_err)
                
                # =====================================================================
                # DELTA: Mettre à jour le supplierinfo via le mapping dynamique
                # NOUVEAU: Utilise _create_supplierinfo_from_mapping pour que le PVGC
                # et autres champs mappés soient correctement mis à jour
                # =====================================================================
                if supplier_id and template_id:
                    # Récupérer le template pour l'ORM
                    ProductTemplate = self.env["product.template"].sudo()
                    tmpl_rec = ProductTemplate.browse(template_id)
                    
                    # Construire l'index des colonnes
                    # ✅ FIX: Normaliser les accents dans hdr_index pour DELTA
                    hdr_index_local = {self._normalize_string_for_comparison(h): idx for idx, h in enumerate(headers)}
                    
                    # Récupérer le mapping dynamique depuis les options
                    dynamic_mapping = options.get("mapping") if options else None
                    mapping_lines = options.get("mapping_lines") if options else None
                    
                    if dynamic_mapping and tmpl_rec.exists():
                        # NOUVEAU: Utiliser le mapping complet pour le supplierinfo
                        self._create_supplierinfo_from_mapping(
                            tmpl_rec, supplier_id, row, headers, hdr_index_local,
                            dynamic_mapping, mapping_lines
                        )
                        result["price_updated"] += 1
                    else:
                        # Fallback: logique hardcodée si pas de mapping
                        si_vals = {}
                        if price_val >= 0:
                            si_vals["price"] = price_val
                        if has_supplier_stock_field and supplier_stock_val >= 0:
                            si_vals["supplier_stock"] = supplier_stock_val
                        
                        if si_vals:
                            # Utiliser SQL direct pour trouver le supplierinfo
                            self.env.cr.execute(
                                "SELECT id, price FROM product_supplierinfo WHERE partner_id = %s AND product_tmpl_id = %s LIMIT 1",
                                [supplier_id, template_id]
                            )
                            si_row = self.env.cr.fetchone()
                            
                            if si_row:
                                si_id = si_row[0]
                                # Utiliser ORM pour la mise à jour (triggers)
                                SupplierInfo.browse(si_id).write(si_vals)
                                result["price_updated"] += 1
                            else:
                                # Créer une nouvelle ligne supplierinfo
                                si_vals.update({
                                    "partner_id": supplier_id,
                                    "product_tmpl_id": template_id,
                                    "min_qty": 1.0,
                                })
                                SupplierInfo.create(si_vals)
                                result["price_updated"] += 1
                
                # Mettre à jour le stock fournisseur (staging vendor matrix)
                if stock_val >= 0:
                    try:
                        Vendor = self.env["planete.pim.staging.vendor"].sudo()
                        Vendor.upsert_from_import(
                            ean13=norm_ean,
                            provider_id=provider.id,
                            supplier_id=supplier_id,
                            quantity=stock_val,
                            price=price_val,
                            currency_id=self.env.company.currency_id.id,
                            log_id=None,
                        )
                        result["stock_updated"] += 1
                    except Exception:
                        pass
                
                # COMMIT PÉRIODIQUE + GARBAGE COLLECTION
                if i % batch_size == 0:
                    self.env.cr.commit()
                    self.env.invalidate_all()
                    gc.collect()
                    _logger.info("[DELTA] Committed batch at row %d (%d price updates, %d stock updates, memory freed)", 
                                 i, result["price_updated"], result["stock_updated"])
                    
            except Exception as e:
                _logger.warning("[DELTA] Error on row %d: %s", i, e)
                result["errors"] += 1
        
        # (no keepalive thread to stop)
        
        # Commit final
        self.env.cr.commit()
        self.env.invalidate_all()
        gc.collect()
        
        # =====================================================================
        # NOUVEAU: Décocher source_dsonline pour les produits Digital absents
        # Seulement si c'est le provider Digital (GroupeDigital)
        # =====================================================================
        if self._is_digital_provider(provider) and all_eans_in_file:
            _logger.info("[DELTA] Provider is Digital - checking for products to unmark (%d EANs in file)", len(all_eans_in_file))
            unmarked_count = self._unmark_absent_digital_products(all_eans_in_file, provider)
            if unmarked_count > 0:
                _logger.info("[DELTA] ✅ Unmarked %d products (source_dsonline=False)", unmarked_count)
                result["digital_unmarked"] = unmarked_count
            self.env.cr.commit()
        
        # Créer l'historique
        self._create_import_history(provider, filename, result, mode="DELTA")
        
        # Final job progress update
        if job_id:
            try:
                self._update_job_progress(
                    job_id, total_rows, total_rows,
                    status=_("[DELTA] Terminé: %d prix MAJ, %d stocks MAJ, %d ignorés") % (
                        result["price_updated"], result["stock_updated"], result["skipped_not_found"]
                    ),
                    updated=result["price_updated"] + result["stock_updated"],
                    errors=result["errors"]
                )
            except Exception:
                pass
        _logger.info("[DELTA] Completed: %d price updates, %d stock updates, %d skipped", 
                     result["price_updated"], result["stock_updated"], result["skipped_not_found"])
        
        return result

    # =========================================================================
    # Helpers pour FULL et DELTA
    # =========================================================================
    def _download_and_merge_multi_files(self, provider):
        """Télécharge et fusionne plusieurs fichiers pour les providers multi-fichiers.
        
        Utilisé pour Exertis, TD Synnex, etc. qui fournissent 3 fichiers à fusionner :
        - Fichier principal (Material) : EAN, nom, prix, marque, etc.
        - Fichier stock (Stock) : quantités disponibles
        - Fichier taxes (TaxesGouv) : écotaxes, DEEE
        
        Returns:
            tuple: (tmp_path, file_info) du fichier fusionné
        """
        import time as time_module
        
        try:
            from odoo.addons.planete_pim.models.multi_file_merger import merge_provider_files
        except ImportError:
            _logger.error("[MULTI-FILE] Cannot import merge_provider_files, multi-file mode unavailable")
            raise UserError(_("Le module de fusion multi-fichiers n'est pas disponible"))
        
        Backend = self.env["ftp.backend.service"]
        
        _logger.info("[MULTI-FILE] Starting multi-file download and merge for provider %s", provider.name)
        
        # Lister tous les fichiers
        list_start = time_module.time()
        files = Backend.list_provider_files(provider, preview_limit=None)
        _logger.info("[MULTI-FILE] Listed %d files in %.1f sec", len(files), time_module.time() - list_start)
        
        if not files:
            raise UserError(_("Aucun fichier trouvé sur le serveur FTP"))
        
        # Récupérer les patterns
        pattern_material = provider.file_pattern_material or "CataExpert*.csv"
        pattern_stock = provider.file_pattern_stock or "CataPrices*.csv"
        pattern_taxes = provider.file_pattern_taxes or "CataStock*.csv"
        
        _logger.info("[MULTI-FILE] Looking for files: material=%s, stock=%s, taxes=%s",
                    pattern_material, pattern_stock, pattern_taxes)
        
        # Convertir les patterns en regex (simple wildcard * → .*)
        def pattern_to_regex(pattern):
            return re.compile(pattern.replace("*", ".*"), re.IGNORECASE)
        
        material_regex = pattern_to_regex(pattern_material)
        stock_regex = pattern_to_regex(pattern_stock)
        taxes_regex = pattern_to_regex(pattern_taxes)
        
        # Identifier les fichiers
        material_file = None
        stock_file = None
        taxes_file = None
        
        for file_info in files:
            filename = os.path.basename(file_info.get("path", ""))
            if material_regex.match(filename):
                material_file = file_info
            elif stock_regex.match(filename):
                stock_file = file_info
            elif taxes_regex.match(filename):
                taxes_file = file_info
        
        if not material_file:
            raise UserError(_("Fichier principal non trouvé (pattern: %s)") % pattern_material)
        
        _logger.info("[MULTI-FILE] Files identified: material=%s, stock=%s, taxes=%s",
                    material_file.get("path") if material_file else "N/A",
                    stock_file.get("path") if stock_file else "N/A",
                    taxes_file.get("path") if taxes_file else "N/A")
        
        # Télécharger les fichiers
        material_path, _ = Backend.download_to_temp(provider, material_file.get("path"))
        stock_path = None
        taxes_path = None
        
        if stock_file:
            stock_path, _ = Backend.download_to_temp(provider, stock_file.get("path"))
        if taxes_file:
            taxes_path, _ = Backend.download_to_temp(provider, taxes_file.get("path"))
        
        try:
            # Lire les contenus
            with open(material_path, 'r', encoding='utf-8', errors='replace') as f:
                material_content = f.read()
            
            stock_content = None
            if stock_path:
                with open(stock_path, 'r', encoding='utf-8', errors='replace') as f:
                    stock_content = f.read()
            
            taxes_content = None
            if taxes_path:
                with open(taxes_path, 'r', encoding='utf-8', errors='replace') as f:
                    taxes_content = f.read()
            
            # Fusionner
            _logger.info("[MULTI-FILE] Merging files on key: %s", provider.multi_file_merge_key or "Article")
            merged_path, merged_headers = merge_provider_files(
                provider, material_content, stock_content, taxes_content
            )
            
            if not merged_path:
                raise UserError(_("La fusion des fichiers a échoué"))
            
            _logger.info("[MULTI-FILE] ✅ Files merged successfully: %s (headers: %s)",
                        merged_path, merged_headers[:10] if merged_headers else [])
            
            # Construire file_info pour le fichier fusionné
            file_info = {
                "name": "merged_" + os.path.basename(material_file.get("path", "import.csv")),
                "path": merged_path,
                "size": os.path.getsize(merged_path),
                "is_merged": True,
                "source_files": {
                    "material": material_file.get("path"),
                    "stock": stock_file.get("path") if stock_file else None,
                    "taxes": taxes_file.get("path") if taxes_file else None,
                }
            }
            
            return merged_path, file_info
            
        finally:
            # Cleanup fichiers téléchargés (le merged sera nettoyé plus tard)
            for path in [material_path, stock_path, taxes_path]:
                if path and os.path.exists(path):
                    try:
                        os.remove(path)
                    except Exception:
                        pass

    def _download_provider_file(self, provider):
        """Télécharge le fichier le plus récent depuis le FTP/SFTP du provider.
        
        AMÉLIORÉ: 
        - Détecte et extrait automatiquement les fichiers ZIP
        - Supporte le mode multi-fichiers (Exertis, TD Synnex)
        
        Si le provider a multi_file_mode=True, télécharge et fusionne les 3 fichiers.
        Sinon, télécharge un seul fichier comme avant.
        
        Retourne (tmp_path, file_info) ou (None, None) si aucun fichier.
        """
        # =====================================================================
        # NOUVEAU: Support mode multi-fichiers (Exertis, TD Synnex)
        # =====================================================================
        if getattr(provider, 'multi_file_mode', False):
            _logger.info("[DOWNLOAD] Provider %s has multi_file_mode=True, using merge logic", provider.name)
            return self._download_and_merge_multi_files(provider)
        
        # =====================================================================
        # Logique existante pour fichier unique
        # =====================================================================
        import time as time_module
        import zipfile
        import tempfile as _tmp
        
        Backend = self.env["ftp.backend.service"]
        
        # Lister les fichiers avec log
        _logger.info("[FTP] Listing files for provider %s (%s)...", provider.id, provider.name)
        list_start = time_module.time()
        
        try:
            files = Backend.list_provider_files(provider, preview_limit=None)
        except Exception as e:
            _logger.error("[FTP] Error listing files for provider %s: %s", provider.id, e)
            raise
        
        list_duration = time_module.time() - list_start
        _logger.info("[FTP] Listed %d files in %.1f sec for provider %s", len(files) if files else 0, list_duration, provider.id)
        
        if not files:
            _logger.warning("[FTP] No files found on server for provider %s", provider.id)
            return None, None
        
        # Prendre le plus récent (ou tous si pim_latest_only=False)
        if provider.pim_latest_only:
            files = files[:1]
        
        file_info = files[0]
        remote_path = file_info.get("path")
        file_size = file_info.get("size", "unknown")
        
        # Télécharger avec log
        _logger.info("[FTP] Downloading file '%s' (size: %s) for provider %s...", remote_path, file_size, provider.id)
        download_start = time_module.time()
        
        try:
            tmp_path, _size = Backend.download_to_temp(provider, remote_path)
        except Exception as e:
            _logger.error("[FTP] Error downloading file '%s' for provider %s: %s", remote_path, provider.id, e)
            raise
        
        download_duration = time_module.time() - download_start
        _logger.info("[FTP] Downloaded file '%s' to '%s' in %.1f sec", remote_path, tmp_path, download_duration)
        
        # =====================================================================
        # NOUVEAU: Extraction automatique des fichiers ZIP
        # Si le fichier est un ZIP, extraire le premier CSV/TXT trouvé
        # =====================================================================
        try:
            if zipfile.is_zipfile(tmp_path):
                _logger.info("[FTP] 📦 Detected ZIP archive, extracting...")
                
                with zipfile.ZipFile(tmp_path, 'r') as zf:
                    # Lister les fichiers (exclure les dossiers)
                    names = [n for n in zf.namelist() if not n.endswith("/")]
                    lower_names = [n.lower() for n in names]
                    
                    # Vérifier si c'est un Excel (.xlsx = ZIP avec xl/)
                    if any(n.startswith("xl/") or "/xl/" in n for n in lower_names):
                        _logger.error("[FTP] ❌ Le fichier est un Excel (.xlsx), pas un CSV!")
                        raise UserError(_(
                            "Le fichier '%s' est un Excel (.xlsx). "
                            "Veuillez l'exporter en CSV (séparateur ';' ou ',', encodage UTF-8/latin-1)."
                        ) % remote_path)
                    
                    # Chercher un CSV ou TXT
                    pick = None
                    for ext in (".csv", ".txt"):
                        for n in names:
                            if n.lower().endswith(ext):
                                pick = n
                                break
                        if pick:
                            break
                    
                    # Fallback: prendre le plus gros fichier
                    if not pick and names:
                        pick = max(names, key=lambda n: (zf.getinfo(n).file_size or 0))
                    
                    if pick:
                        # Extraire vers un fichier temporaire
                        fd, extracted_path = _tmp.mkstemp(prefix="pim_zip_", suffix="_" + os.path.basename(pick))
                        os.close(fd)
                        
                        with open(extracted_path, "wb") as out:
                            out.write(zf.read(pick))
                        
                        _logger.info("[FTP] ✅ Extracted '%s' from ZIP to '%s' (%.2f KB)", 
                                    pick, extracted_path, os.path.getsize(extracted_path) / 1024)
                        
                        # Supprimer le ZIP original
                        try:
                            os.remove(tmp_path)
                        except Exception:
                            pass
                        
                        # Mettre à jour file_info avec le nom du fichier extrait
                        file_info["name"] = pick
                        file_info["extracted_from_zip"] = True
                        
                        return extracted_path, file_info
                    else:
                        _logger.warning("[FTP] ⚠️ ZIP vide ou sans fichiers CSV/TXT!")
                        
        except zipfile.BadZipFile:
            # Pas un vrai ZIP, continuer avec le fichier original
            _logger.debug("[FTP] File is not a valid ZIP, processing as-is")
        except UserError:
            raise
        except Exception as zip_err:
            _logger.warning("[FTP] Error checking/extracting ZIP: %s", zip_err)
        
        return tmp_path, file_info

    def _parse_mapping_json(self, json_str):
        """Parse le mapping JSON du provider.
        Format attendu: {"ean": "EAN", "ref": "SKU", "name": "Description", "price": "PurchasePrice", "stock": "Stock", "supplier_stock": "VendorStock"}
        """
        default_mapping = {
            "ean": ["ean", "ean13", "barcode", "code barre", "code_barre", "ean/upc", "gencode"],
            "ref": ["default_code", "sku", "reference", "ref", "code", "item no."],
            "name": ["name", "libelle", "designation", "description", "product"],
            "price": ["cost", "product_cost", "standard_price", "prix_achat", "pa", "purchaseprice", "price", "unit sales price", "unit sales price(from customer", "sales price", "prix vente", "selling price"],
            "stock": ["qty", "quantity", "stock", "qty_available", "qte"],
            "supplier_stock": ["supplier_stock", "stock_fournisseur", "vendor_stock", "qty_supplier", "qte_fournisseur"],
            # NOUVEAU: Champs pour DELTA - mise à jour list_price et standard_price
            "list_price": ["pvgc", "pvgc ttc", "list_price", "prix public", "prix_public", "prix vente ttc", "prix_vente_ttc", "pv ttc", "pvttc"],
            "standard_price": ["prix net", "prix_net", "cout", "coût", "cost", "standard_price", "prix achat", "prix_achat", "pa ht", "pa"],
        }
        
        if not json_str:
            return default_mapping
        
        try:
            parsed = json.loads(json_str)
            # Le mapping peut être directement les colonnes ou sous une clé "fields"
            if isinstance(parsed, dict):
                if "fields" in parsed:
                    parsed = parsed["fields"]
                # Convertir en listes si nécessaire
                result = {}
                for key, val in parsed.items():
                    if isinstance(val, str):
                        result[key.lower()] = [val.lower()]
                    elif isinstance(val, list):
                        result[key.lower()] = [v.lower() for v in val]
                    else:
                        result[key.lower()] = default_mapping.get(key.lower(), [])
                # Compléter avec les valeurs par défaut
                for key, val in default_mapping.items():
                    if key not in result:
                        result[key] = val
                return result
        except Exception as e:
            _logger.warning("Failed to parse mapping JSON: %s", e)
        
        return default_mapping

    @api.model
    def _build_mapping_from_template(self, provider):
        """Construit le mapping complet depuis le mapping_template_id du provider.
        
        IMPORTANT: Cette méthode charge le template de mapping configuré sur le provider
        et construit les dictionnaires mapping et mapping_lines utilisés par
        _apply_mapping_to_product pour écraser les anciennes valeurs avec les nouvelles.
        
        Args:
            provider: ftp.provider record
            
        Returns:
            dict avec:
            - 'mapping': Dict {target_field: [source_columns]}
            - 'mapping_lines': Liste des lignes avec transformations complètes
            - 'has_template': True si un template a été trouvé
        """
        result = {
            "mapping": {},
            "mapping_lines": [],
            "has_template": False,
        }
        
        if not provider:
            _logger.warning("[MAPPING-TEMPLATE] No provider provided")
            return result
        
        # Vérifier si le provider a un mapping_template_id
        template = None
        try:
            template = provider.mapping_template_id
        except Exception as e:
            _logger.warning("[MAPPING-TEMPLATE] Could not access mapping_template_id: %s", e)
        
        if not template:
            _logger.info("[MAPPING-TEMPLATE] Provider %s has no mapping_template_id configured", provider.id)
            return result
        
        _logger.info("[MAPPING-TEMPLATE] Loading template '%s' (id=%s) for provider %s", 
                     template.name, template.id, provider.id)
        
        # Construire le mapping depuis les lignes du template
        mapping = {}
        mapping_lines = []
        
        try:
            lines = template.line_ids.filtered(lambda l: l.active)
            _logger.info("[MAPPING-TEMPLATE] Found %d active mapping lines", len(lines))
            
            for line in lines:
                source_column = line.source_column

                if not source_column:
                    continue

                # IMPORTANT: normaliser la colonne source pour qu'elle matche les headers
                # même si accents / espaces Unicode (NBSP, zero-width, etc.)
                source_col_key = self._normalize_string_for_comparison(source_column)
                
                # =====================================================================
                # NOUVEAU: Gérer les champs multiples (target_field_ids) 
                # Si target_field_ids est rempli, créer une entrée pour chaque champ
                # Sinon, utiliser target_field (compatibilité)
                # =====================================================================
                target_fields_to_process = []
                
                # Vérifier si des champs multiples sont sélectionnés
                if hasattr(line, 'target_field_ids') and line.target_field_ids:
                    # Utiliser les champs multiples
                    for field_reg in line.target_field_ids:
                        if field_reg.name:
                            target_fields_to_process.append(field_reg.name)
                    _logger.debug("[MAPPING-TEMPLATE] Multi-fields for %s: %s", source_column, target_fields_to_process)
                
                # Ajouter aussi target_field (compat) OU fallback via target_field_id
                # (certains anciens templates ont target_field vide alors que target_field_id est rempli)
                main_target = None
                try:
                    main_target = (line.target_field or (line.target_field_id.name if getattr(line, "target_field_id", False) else None))
                except Exception:
                    main_target = line.target_field

                if main_target and main_target not in target_fields_to_process:
                    target_fields_to_process.append(main_target)
                
                # Si aucun champ cible, ignorer cette ligne
                if not target_fields_to_process:
                    _logger.warning("[MAPPING-TEMPLATE] Line '%s' has no target field, skipping", source_column)
                    continue
                
                # Créer une entrée pour chaque champ cible
                for target_field in target_fields_to_process:
                    # Ajouter au mapping {target_field: [source_columns]}
                    if target_field not in mapping:
                        mapping[target_field] = []
                    if source_col_key not in mapping[target_field]:
                        mapping[target_field].append(source_col_key)
                    
                    # Construire la ligne détaillée avec transformations
                    line_info = {
                        "target_field": target_field,
                        "source_column": source_column,
                        "transform_type": line.transform_type or "none",
                        "transform_value": line.transform_value or "",
                        "transform_value2": line.transform_value2 or "",
                        "concat_column": line.concat_column or "",
                        "concat_separator": line.concat_separator if line.concat_separator is not None else " ",
                        "skip_if_empty": line.skip_if_empty if hasattr(line, 'skip_if_empty') else True,
                        "required_field": line.required_field if hasattr(line, 'required_field') else False,
                    }
                    mapping_lines.append(line_info)
                    
                    _logger.debug("[MAPPING-TEMPLATE] Line: %s -> %s (transform=%s, skip_if_empty=%s)",
                                 source_column, target_field, line_info["transform_type"], line_info["skip_if_empty"])
            
            result["mapping"] = mapping
            result["mapping_lines"] = mapping_lines
            result["has_template"] = True
            
            _logger.info("[MAPPING-TEMPLATE] ✅ Built mapping with %d target fields: %s", 
                        len(mapping), list(mapping.keys()))
            
        except Exception as e:
            _logger.error("[MAPPING-TEMPLATE] Error building mapping from template: %s", e, exc_info=True)
        
        return result

    def _build_column_index(self, headers, mapping):
        """Construit un dictionnaire {field_name: column_index} selon le mapping.
        
        IMPORTANT: Ajoute des alias pour que les noms Odoo et les noms logiques fonctionnent.
        Par exemple, si le mapping template définit "barcode", on crée aussi l'alias "ean".
        
        AMÉLIORÉ: 
        - Normalise les accents dans les noms de colonnes
        - Ajoute du logging détaillé pour déboguer les problèmes de détection de colonnes
        
        ✅ FIX BUG: "Libellé marque" vs "libelle marque" maintenant matched correctement
        """
        # ✅ CORRECTION: Normaliser les accents dans les headers
        # Cette méthode convertit "Libellé marque" et "Libelle marque" en "libelle marque"
        hdr_lower = {}
        for h in headers:
            h_normalized = self._normalize_string_for_comparison(h)
            hdr_lower[h_normalized] = len(hdr_lower)  # Utiliser l'index pour éviter les doublons
        
        col_idx = {}
        
        _logger.info("[COLUMN-DETECTION] Headers détectés (normalisés): %s", list(hdr_lower.keys())[:20])
        _logger.info("[COLUMN-DETECTION] Mapping demandé pour champs: %s", list(mapping.keys()))
        
        # Mapping des alias: nom_odoo -> nom_logique utilisé par DELTA/FULL
        FIELD_ALIASES = {
            "barcode": "ean",           # barcode (Odoo) = ean (logique DELTA)
            "default_code": "ref",      # default_code (Odoo) = ref (logique)
            "standard_price": "price",  # standard_price (Odoo) = price (logique pour coût)
        }
        
        for field, candidates in mapping.items():
            candidates_lower = [self._normalize_string_for_comparison(c) if c else "" for c in candidates]
            _logger.debug("[COLUMN-DETECTION] Cherchant colonne pour '%s' parmi: %s", field, candidates_lower)
            
            for candidate in candidates_lower:
                if candidate in hdr_lower:
                    col_idx[field] = hdr_lower[candidate]
                    _logger.info("[COLUMN-DETECTION] ✅ Trouvé '%s' -> colonne %d (header=%r)", 
                                field, hdr_lower[candidate], candidate)
                    # Créer aussi l'alias logique si nécessaire
                    alias = FIELD_ALIASES.get(field)
                    if alias and alias not in col_idx:
                        col_idx[alias] = hdr_lower[candidate]
                        _logger.debug("[COLUMN-DETECTION] Alias '%s' créé pour '%s'", alias, field)
                    break
            else:
                # Champ non trouvé
                _logger.warning("[COLUMN-DETECTION] ❌ Champ '%s' NOT FOUND - cherché dans: %s", field, candidates_lower)
        
        # Log final - montrer le mapping trouvé
        _logger.info("[COLUMN-DETECTION] Résultat final: EAN/barcode=%s, REF/default_code=%s, NAME=%s, PRICE=%s", 
                    col_idx.get("ean", col_idx.get("barcode", "MANQUANT")),
                    col_idx.get("ref", col_idx.get("default_code", "MANQUANT")),
                    col_idx.get("name", "MANQUANT"),
                    col_idx.get("price", col_idx.get("standard_price", "MANQUANT")))
        
        return col_idx

    def _read_csv_file(self, file_path):
        """Lit un fichier CSV et retourne (rows, headers)."""
        enc_candidates = ["utf-8-sig", "utf-8", "cp1252", "latin-1"]
        sel_enc, head = self._read_head(file_path, enc_candidates)
        delimiter = self._detect_delimiter(head or "")
        
        rows = []
        headers = []
        
        with open(file_path, "r", encoding=sel_enc, errors="replace", newline="") as f:
            if delimiter and len(delimiter) == 1:
                reader = csv.reader(f, delimiter=delimiter)
                try:
                    headers = next(reader) or []
                except StopIteration:
                    headers = []
                rows = list(reader)
            else:
                def _split(line):
                    return (line.rstrip("\r\n")).split(delimiter)
                try:
                    first = f.readline()
                    headers = _split(first) if first else []
                except Exception:
                    headers = []
                rows = [_split(line) for line in f]
        
        return rows, headers

    def _get_cell(self, row, idx):
        """Récupère la valeur d'une cellule de manière sécurisée."""
        if idx is None or idx >= len(row):
            return ""
        return (row[idx] or "").strip()

    def _to_float(self, s):
        """Convertit une chaîne en float de manière robuste."""
        try:
            if s is None:
                return 0.0
            s = str(s).replace(" ", "").replace("\xa0", "")
            s = s.replace(",", ".")
            return float(s)
        except Exception:
            return 0.0

    def _create_import_history(self, provider, filename, result, mode="FULL", new_brands=None):
        """Crée un enregistrement d'historique d'import et notifie si de nouvelles marques ont été créées."""
        try:
            History = self.env["planete.pim.import.history"]
            
            if mode == "FULL":
                name = _("[FULL] Import création %s") % fields.Datetime.now()
                success_count = result.get("created", 0)
                error_count = result.get("errors", 0)
            else:
                name = _("[DELTA] Import prix/stock %s") % fields.Datetime.now()
                success_count = result.get("price_updated", 0) + result.get("stock_updated", 0)
                error_count = result.get("errors", 0)
            
            # Préparer les nouvelles marques créées
            new_brands_text = ""
            new_brands_count = 0
            if new_brands:
                new_brands_count = len(new_brands)
                new_brands_text = "\n".join(new_brands)
            
            history = History.create({
                "name": name,
                "provider_id": provider.id,
                "file_name": self._strip_nul(filename or ""),
                "total_lines": result.get("total", 0),
                "success_count": success_count,
                "error_count": error_count,
                "created_count": result.get("created", 0) if mode == "FULL" else 0,
                "updated_count": result.get("price_updated", 0) + result.get("stock_updated", 0) if mode == "DELTA" else 0,
                # Colonnes détaillées pour traçabilité complète
                "skipped_existing_count": result.get("skipped_existing", 0),
                "skipped_not_found_count": result.get("skipped_not_found", 0),
                "quarantined_count": result.get("quarantined", 0),
                "new_brands_created": new_brands_text,
                "new_brands_count": new_brands_count,
            })
            
            # Créer une notification/activité si des nouvelles marques ont été créées
            if new_brands_count > 0:
                self._notify_new_brands_created(provider, new_brands, history)
                
        except Exception as e:
            _logger.warning("[%s] Could not create import history: %s", mode, e)

    def _notify_new_brands_created(self, provider, new_brands, history):
        """Crée une notification pour informer des nouvelles marques créées.
        Envoie un message sur le provider et crée une activité pour l'admin.
        """
        try:
            brand_list = ", ".join(new_brands[:20])
            if len(new_brands) > 20:
                brand_list += _(" ... et %d autres") % (len(new_brands) - 20)
            
            message = _(
                "<p><strong>⚠️ %d nouvelle(s) marque(s) créée(s) pendant l'import</strong></p>"
                "<p>Les marques suivantes n'existaient pas et ont été créées automatiquement. "
                "Veuillez vérifier si des alias doivent être ajoutés :</p>"
                "<p><code>%s</code></p>"
                "<p><a href='/web#id=%d&model=planete.pim.import.history&view_type=form'>Voir l'historique d'import</a></p>"
            ) % (len(new_brands), brand_list, history.id)
            
            # Poster un message sur le provider (si mail.thread disponible)
            if hasattr(provider, 'message_post'):
                provider.message_post(
                    body=message,
                    subject=_("Nouvelles marques créées: %d") % len(new_brands),
                    message_type='notification',
                    subtype_xmlid='mail.mt_note',
                )
            
            # Logger pour traçabilité
            _logger.warning(
                "[PIM] %d new brand(s) created during import for provider %s: %s",
                len(new_brands), provider.name, ", ".join(new_brands[:10])
            )
            
        except Exception as e:
            _logger.warning("Could not send new brands notification: %s", e)

    # =========================================================================
    # Timeout and job helpers
    # =========================================================================
    def _get_import_timeout_seconds(self):
        """Return hard timeout in seconds for FULL/DELTA imports (default 7200)."""
        try:
            val = int(self.env["ir.config_parameter"].sudo().get_param("planete_pim.import_timeout_seconds") or 7200)
            return max(60, val)
        except Exception:
            return 7200

    def _mark_job_failed(self, job_id, message):
        """Mark planete_pim_import_job as failed using direct SQL + savepoint."""
        if not job_id:
            return
        try:
            with self.env.cr.savepoint():
                self.env.cr.execute(
                    "UPDATE planete_pim_import_job SET state=%s, progress_status=%s WHERE id=%s",
                    ["failed", message or "", job_id],
                )
        except Exception as e:
            _logger.warning("Failed to mark job %s as failed (isolated): %s", job_id, e)

    def _save_job_checkpoint(self, job_id, row_number, result_stats=None):
        """Save checkpoint to job for resume capability.
        
        Uses direct SQL with savepoint to ensure checkpoint is saved even if
        the main transaction fails.
        
        Args:
            job_id: ID of the planete.pim.import.job
            row_number: Current row number being processed
            result_stats: Optional dict with stats (created, updated, errors, etc.)
        """
        if not job_id:
            return
        try:
            with self.env.cr.savepoint():
                # Build update query
                sql = """
                    UPDATE planete_pim_import_job
                    SET checkpoint_row = %s,
                        progress_current = %s,
                        write_date = NOW(),
                        write_uid = %s
                """
                params = [row_number, row_number, self.env.uid]
                
                if result_stats:
                    if "created" in result_stats:
                        sql += ", created_count = %s"
                        params.append(result_stats.get("created", 0))
                    if "updated" in result_stats or "price_updated" in result_stats:
                        sql += ", updated_count = %s"
                        params.append(result_stats.get("updated", 0) + result_stats.get("price_updated", 0) + result_stats.get("stock_updated", 0))
                    if "quarantined" in result_stats:
                        sql += ", quarantined_count = %s"
                        params.append(result_stats.get("quarantined", 0))
                    if "skipped_existing" in result_stats or "skipped_not_found" in result_stats:
                        sql += ", skipped_count = %s"
                        params.append(result_stats.get("skipped_existing", 0) + result_stats.get("skipped_not_found", 0))
                    if "errors" in result_stats:
                        sql += ", error_count = %s"
                        params.append(result_stats.get("errors", 0))
                
                sql += " WHERE id = %s"
                params.append(job_id)
                
                self.env.cr.execute(sql, params)
        except Exception as e:
            _logger.warning("Failed to save checkpoint for job %s at row %d: %s", job_id, row_number, e)

    def _get_job_checkpoint(self, job_id):
        """Get the last checkpoint row for a job.
        
        Returns:
            int: The last processed row number, or 0 if no checkpoint exists
        """
        if not job_id:
            return 0
        try:
            self.env.cr.execute(
                "SELECT checkpoint_row FROM planete_pim_import_job WHERE id = %s",
                [job_id]
            )
            row = self.env.cr.fetchone()
            return row[0] if row and row[0] else 0
        except Exception as e:
            _logger.warning("Failed to get checkpoint for job %s: %s", job_id, e)
            return 0

    # =========================================================================
    # Helpers optimisés pour le streaming et les requêtes SQL directes
    # =========================================================================
    
    def _count_csv_lines(self, file_path, has_header=True, encoding=None):
        """Compte le nombre de lignes dans un fichier CSV sans tout charger en mémoire.
        Si has_header=True, la première ligne est ignorée dans le comptage.
        """
        count = 0
        if encoding:
            sel_enc = encoding
        else:
            enc_candidates = ["utf-8-sig", "utf-8", "cp1252", "latin-1"]
            sel_enc, _head = self._read_head(file_path, enc_candidates)
        try:
            with open(file_path, "r", encoding=sel_enc, errors="replace", newline="") as f:
                if has_header:
                    f.readline()
                for _line in f:
                    count += 1
        except Exception as e:
            _logger.warning("Error counting CSV lines: %s", e)
            return 0
        return count

    def _read_csv_headers(self, file_path, encoding=None, delimiter=None, delimiter_regex=None):
        """Lit uniquement la ligne d'en-tête d'un fichier CSV/TXT.
        Supporte les délimiteurs regex (ex: r"\\s{2,}") et les délimiteurs sur 1+ caractères.
        """
        if encoding:
            sel_enc = encoding
            head = ""
            try:
                with open(file_path, "r", encoding=sel_enc, errors="replace", newline="") as tf:
                    head = tf.read(4096)
            except Exception:
                head = ""
        else:
            enc_candidates = ["utf-8-sig", "utf-8", "cp1252", "latin-1"]
            sel_enc, head = self._read_head(file_path, enc_candidates)
        if not delimiter and not delimiter_regex:
            delimiter = self._detect_delimiter(head or "")
        headers = []
        try:
            with open(file_path, "r", encoding=sel_enc, errors="replace", newline="") as f:
                if delimiter_regex:
                    pattern = re.compile(delimiter_regex)
                    first = f.readline()
                    headers = pattern.split(first.rstrip("\r\n")) if first else []
                elif delimiter and len(delimiter) == 1:
                    reader = csv.reader(f, delimiter=delimiter)
                    headers = next(reader, [])
                else:
                    first = f.readline()
                    headers = (first.rstrip("\r\n")).split(delimiter or "") if first else []
        except Exception as e:
            _logger.warning("Error reading CSV headers: %s", e)
        return headers

    def _iter_csv_rows(self, file_path, has_header=True, encoding=None, delimiter=None, delimiter_regex=None):
        """Générateur qui lit un fichier en streaming.
        - has_header=True: saute la première ligne.
        - delimiter_regex prioritaire si fourni (ex: r"\\s{2,}").
        - sinon delimiter (1 char => csv.reader, multi-char => split()).
        """
        if encoding:
            sel_enc = encoding
            head = ""
            try:
                with open(file_path, "r", encoding=sel_enc, errors="replace", newline="") as tf:
                    head = tf.read(4096)
            except Exception:
                head = ""
        else:
            enc_candidates = ["utf-8-sig", "utf-8", "cp1252", "latin-1"]
            sel_enc, head = self._read_head(file_path, enc_candidates)
        if not delimiter and not delimiter_regex:
            delimiter = self._detect_delimiter(head or "")
        try:
            with open(file_path, "r", encoding=sel_enc, errors="replace", newline="") as f:
                if delimiter_regex:
                    pattern = re.compile(delimiter_regex)
                    if has_header:
                        f.readline()
                    for line in f:
                        yield pattern.split(line.rstrip("\r\n"))
                elif delimiter and len(delimiter) == 1:
                    reader = csv.reader(f, delimiter=delimiter)
                    if has_header:
                        next(reader, None)
                    for row in reader:
                        yield row
                else:
                    if has_header:
                        f.readline()
                    for line in f:
                        yield (line.rstrip("\r\n")).split(delimiter or "")
        except Exception as e:
            _logger.warning("Error iterating CSV rows: %s", e)

    def _ean_exists_in_db(self, ean):
        """Vérifie si un EAN existe déjà dans la base de données via SQL direct.
        Beaucoup plus rapide que de pré-charger tous les produits en mémoire.
        Utilise un savepoint pour isoler les erreurs de transaction.
        
        NOTE: Le champ 'barcode' est sur product_product uniquement (pas product_template).
        """
        if not ean:
            return False
        
        try:
            # Utiliser un savepoint pour isoler les erreurs
            with self.env.cr.savepoint():
                # Vérifier dans product_product (le champ barcode est sur product_product, pas product_template)
                self.env.cr.execute(
                    "SELECT 1 FROM product_product WHERE barcode = %s LIMIT 1",
                    [ean]
                )
                if self.env.cr.fetchone():
                    return True
            
            return False
        except Exception as e:
            _logger.warning("Error checking EAN existence (isolated): %s", e)
            return False

    def _ref_exists_in_db(self, ref):
        """Vérifie si une référence (default_code) existe déjà dans la base de données via SQL direct.
        Beaucoup plus rapide que de pré-charger tous les produits en mémoire.
        Utilise un savepoint pour isoler les erreurs de transaction.
        
        NOTE: Le champ 'default_code' est sur product_product.
        """
        if not ref:
            return False
        
        try:
            # Utiliser un savepoint pour isoler les erreurs
            with self.env.cr.savepoint():
                # Vérifier dans product_product (default_code)
                self.env.cr.execute(
                    "SELECT 1 FROM product_product WHERE default_code = %s LIMIT 1",
                    [ref]
                )
                if self.env.cr.fetchone():
                    return True
            
            return False
        except Exception as e:
            _logger.warning("Error checking reference existence (isolated): %s", e)
            return False

    @api.model
    def _clean_brand_name(self, raw_name):
        """Nettoie agressivement un nom de marque pour la comparaison.
        
        Supprime:
        - Espaces classiques ET Unicode (zero-width space, non-breaking space, etc.)
        - Caractères de contrôle invisibles
        - Guillemets et apostrophes autour du nom
        - Espaces multiples internes
        
        ✅ FIX BUG VOGELS: Le fichier CSV peut contenir des caractères Unicode invisibles
        (ex: zero-width space U+200B, non-breaking space U+00A0) qui empêchent la correspondance
        exacte avec la marque existante en base.
        
        Exemples:
        - "VOGELS\\u200b" → "VOGELS"
        - " VOGELS " → "VOGELS"
        - "\\xa0VOGELS\\xa0" → "VOGELS"
        - '"VOGELS"' → "VOGELS"
        """
        if not raw_name:
            return ""
        
        s = str(raw_name)
        
        # 1. Supprimer les caractères Unicode invisibles courants
        # U+200B Zero Width Space, U+200C Zero Width Non-Joiner, U+200D Zero Width Joiner
        # U+FEFF BOM, U+00A0 Non-breaking space, U+2007 Figure space, U+202F Narrow NBSP
        invisible_chars = '\u200b\u200c\u200d\ufeff\u00a0\u2007\u202f\u2060\u180e'
        for ch in invisible_chars:
            s = s.replace(ch, '')
        
        # 2. Strip espaces classiques
        s = s.strip()
        
        # 3. Supprimer les guillemets/apostrophes autour du nom
        if len(s) >= 2 and s[0] == '"' and s[-1] == '"':
            s = s[1:-1].strip()
        if len(s) >= 2 and s[0] == "'" and s[-1] == "'":
            s = s[1:-1].strip()
        
        # 4. Remplacer les espaces multiples par un seul espace
        s = re.sub(r'\s+', ' ', s).strip()
        
        return s

    # =========================================================================
    # Brand helpers (auto-create + tracking in brand.pending)
    # =========================================================================

    def _find_brand_id_by_name_or_alias(self, clean_name):
        """Return product.brand id if found by exact name (case-insensitive) or alias.

        NOTE: Uses SQL direct for robustness/perf in long imports.
        """
        if not clean_name:
            return False

        try:
            with self.env.cr.savepoint():
                # 1) exact name match (trim + lower)
                self.env.cr.execute(
                    "SELECT id FROM product_brand WHERE LOWER(TRIM(name)) = LOWER(%s) LIMIT 1",
                    [clean_name],
                )
                row = self.env.cr.fetchone()
                if row:
                    return row[0]
        except Exception as e:
            _logger.warning("[BRAND] Error searching brand by name: %s", e)

        # 2) alias match (scan only brands that have aliases)
        try:
            with self.env.cr.savepoint():
                self.env.cr.execute(
                    "SELECT id, aliases FROM product_brand WHERE aliases IS NOT NULL AND aliases != ''"
                )
                key = self._normalize_string_for_comparison(clean_name)
                for bid, aliases_str in self.env.cr.fetchall():
                    if not aliases_str:
                        continue
                    alias_list = [
                        self._normalize_string_for_comparison(self._clean_brand_name(a))
                        for a in aliases_str.split(",")
                        if a.strip()
                    ]
                    if key in alias_list:
                        return bid
        except Exception as e:
            _logger.warning("[BRAND] Error searching brand by aliases: %s", e)

        return False

    def _create_brand_safe(self, clean_name):
        """Create product.brand if missing. Returns brand id."""
        if not clean_name:
            return False

        Brand = self.env["product.brand"].sudo()
        try:
            rec = Brand.create({"name": clean_name})
            return rec.id
        except Exception as e:
            # Possible race/constraint => re-search
            _logger.warning("[BRAND] Create failed for '%s', retrying search: %s", clean_name, e)
            found = Brand.search([("name", "=ilike", clean_name)], limit=1)
            return found.id if found else False

    def _flush_pending_brand_agg(self, pending_brand_agg):
        """Flush aggregated pending brands created during import.

        pending_brand_agg format:
            {
              key: {
                'brand_name': str,
                'provider_id': int,
                'brand_id': int,
                'count': int,
                'samples': [ {'ean':..., 'ref':..., 'name':...}, ...]
              }
            }
        """
        if not pending_brand_agg:
            return

        BrandPending = self.env["planete.pim.brand.pending"].sudo()
        for info in pending_brand_agg.values():
            try:
                BrandPending.upsert_pending_brand(
                    info.get("brand_name"),
                    info.get("provider_id"),
                    product_count=int(info.get("count") or 0) or 1,
                    created_brand_id=info.get("brand_id"),
                    sample_products=info.get("samples") or [],
                    state="new_brand",
                )
            except Exception as e:
                _logger.warning("[BRAND] Could not upsert pending brand '%s': %s", info.get("brand_name"), e)

    def _create_brand_alias_if_needed(self, existing_brand_id, csv_brand_name, source_ean, provider_id):
        """Crée un alias de marque si le nom CSV diffère de la marque existante.
        
        RÈGLE AUTO-ALIAS:
        - Si un produit existe avec un EAN identique
        - MAIS que le nom de marque dans le CSV est différent
        - ALORS créer automatiquement un alias sur la marque existante
        - ET enregistrer dans l'historique des alias
        
        Args:
            existing_brand_id: ID de la marque existante sur le produit
            csv_brand_name: Nom de marque trouvé dans le CSV
            source_ean: EAN du produit source (pour traçabilité)
            provider_id: ID du provider (pour traçabilité)
            
        Returns:
            bool: True si un alias a été créé, False sinon
        """
        if not existing_brand_id or not csv_brand_name:
            return False
        
        try:
            Brand = self.env["product.brand"].sudo()
            existing_brand = Brand.browse(existing_brand_id)
            
            if not existing_brand.exists():
                return False
            
            # Nettoyer le nom CSV
            clean_csv_name = self._clean_brand_name(csv_brand_name)
            existing_brand_name = self._clean_brand_name(existing_brand.name)
            
            # Normaliser pour comparaison (insensible à la casse/accents)
            csv_normalized = self._normalize_string_for_comparison(clean_csv_name)
            existing_normalized = self._normalize_string_for_comparison(existing_brand_name)
            
            # Si identiques, pas besoin d'alias
            if csv_normalized == existing_normalized:
                return False
            
            # Vérifier si l'alias existe déjà
            existing_aliases = existing_brand.aliases or ""
            alias_list = [self._normalize_string_for_comparison(a.strip()) 
                         for a in existing_aliases.split(",") if a.strip()]
            
            if csv_normalized in alias_list:
                _logger.debug("[AUTO-ALIAS] Alias '%s' already exists for brand '%s'", 
                             clean_csv_name, existing_brand.name)
                return False
            
            # Créer le nouvel alias
            if existing_aliases:
                new_aliases = existing_aliases + ", " + clean_csv_name
            else:
                new_aliases = clean_csv_name
            
            existing_brand.write({"aliases": new_aliases})
            
            # Créer l'historique
            try:
                History = self.env["planete.pim.brand.alias.history"].sudo()
                History.create({
                    "brand_id": existing_brand_id,
                    "alias_name": clean_csv_name,
                    "provider_id": provider_id,
                    "auto_created": True,
                    "source_ean": source_ean,
                    "creation_date": fields.Datetime.now(),
                })
            except Exception as hist_err:
                _logger.warning("[AUTO-ALIAS] Could not create history record: %s", hist_err)
            
            _logger.info("[AUTO-ALIAS] ✅ Created alias '%s' for brand '%s' (EAN=%s, provider=%s)",
                        clean_csv_name, existing_brand.name, source_ean, provider_id)
            
            return True
            
        except Exception as e:
            _logger.warning("[AUTO-ALIAS] Error creating alias for brand %s: %s", 
                           existing_brand_id, e)
            return False

    def _find_or_create_brand(
        self,
        brand_name,
        cache,
        new_brands_tracker=None,
        provider_id=None,
        pending_brand_agg=None,
        sample=None,
    ):
        """Trouve une marque (nom/alias) et la crée automatiquement si absente.

        Si la marque est créée automatiquement, on garde une trace dans
        `planete.pim.brand.pending` (state=new_brand) avec :
        - compteur de produits concernés
        - quelques exemples (EAN | ref | nom)

        IMPORTANT PERF: l'upsert pending peut être agrégé via pending_brand_agg
        pour éviter 1 write par ligne.

        Args:
            brand_name: nom marque issu du fichier
            cache: dict {brand_key_normalized: brand_id}
            new_brands_tracker: list optionnelle (import history)
            provider_id: ftp.provider id
            pending_brand_agg: dict d'agrégation (voir _flush_pending_brand_agg)
            sample: dict {ean, ref, name} optionnel
        """
        if not brand_name:
            return False

        # ✅ FIX: Nettoyage agressif pour supprimer les caractères invisibles Unicode
        clean_name = self._clean_brand_name(brand_name)
        if not clean_name:
            return False
        
        # Utiliser une clé normalisée (accents + espaces) pour maximiser les correspondances
        brand_key = self._normalize_string_for_comparison(clean_name)

        # Vérifier le cache d'abord
        if brand_key in cache:
            brand_id = cache[brand_key]
            # Si cette marque fait partie d'une agrégation "new_brand", incrémenter le compteur
            if pending_brand_agg is not None and brand_key in pending_brand_agg:
                info = pending_brand_agg[brand_key]
                info["count"] = int(info.get("count") or 0) + 1
                if sample and len(info.get("samples") or []) < 10:
                    info.setdefault("samples", []).append(sample)
            return brand_id

        # 1) Rechercher une marque existante (nom exact ou alias)
        brand_id = self._find_brand_id_by_name_or_alias(clean_name)
        if brand_id:
            cache[brand_key] = brand_id
            return brand_id

        # 2) Créer la marque automatiquement
        brand_id = self._create_brand_safe(clean_name)
        cache[brand_key] = brand_id

        if new_brands_tracker is not None:
            new_brands_tracker.append(clean_name)

        # 3) Track dans brand.pending (state=new_brand)
        if provider_id:
            if pending_brand_agg is not None:
                info = pending_brand_agg.setdefault(
                    brand_key,
                    {
                        "brand_name": clean_name,
                        "provider_id": provider_id,
                        "brand_id": brand_id,
                        "count": 0,
                        "samples": [],
                    },
                )
                info["count"] = int(info.get("count") or 0) + 1
                if sample and len(info.get("samples") or []) < 10:
                    info.setdefault("samples", []).append(sample)
            else:
                try:
                    BrandPending = self.env["planete.pim.brand.pending"].sudo()
                    BrandPending.upsert_pending_brand(
                        clean_name,
                        provider_id,
                        product_count=1,
                        created_brand_id=brand_id,
                        sample_products=[sample] if sample else [],
                        state="new_brand",
                    )
                except Exception as pending_err:
                    _logger.warning("[BRAND] Could not create pending brand '%s': %s", clean_name, pending_err)

        return brand_id

    def _create_supplierinfo(self, template_id, supplier_id, price, supplier_stock):
        """Crée ou met à jour un supplierinfo via SQL direct + ORM avec savepoint pour isoler les erreurs.
        
        OPTIMISÉ: Utilise SQL direct pour chercher le supplierinfo existant (pas de .search() dans la boucle).
        """
        if not template_id or not supplier_id:
            return
        
        try:
            # Utiliser un savepoint pour isoler les erreurs
            with self.env.cr.savepoint():
                SupplierInfo = self.env["product.supplierinfo"].sudo()
                
                # Chercher un supplierinfo existant via SQL direct (pas de .search() dans la boucle!)
                self.env.cr.execute(
                    "SELECT id FROM product_supplierinfo WHERE partner_id = %s AND product_tmpl_id = %s LIMIT 1",
                    [supplier_id, template_id]
                )
                si_row = self.env.cr.fetchone()
                
                si_vals = {"price": price}
                # Ajouter supplier_stock si le champ existe dans le modèle
                if "supplier_stock" in SupplierInfo._fields:
                    si_vals["supplier_stock"] = supplier_stock
                
                if si_row:
                    si_id = si_row[0]
                    SupplierInfo.browse(si_id).write(si_vals)
                else:
                    si_vals.update({
                        "partner_id": supplier_id,
                        "product_tmpl_id": template_id,
                        "min_qty": 1.0,
                    })
                    SupplierInfo.create(si_vals)
                
        except Exception as e:
            _logger.warning("Error creating supplierinfo (isolated): %s", e)

    # =========================================================================
    # Cron: Expiration du stock fournisseur
    # =========================================================================
    @api.model
    def _cron_expire_stale_supplier_stock(self):
        """Cron: Met le supplier_stock à 0 pour les lignes non mises à jour depuis 26h.
        
        RÈGLE:
        - Si write_date < (maintenant - 26 heures) ET supplier_stock > 0
        - Alors mettre supplier_stock à 0
        
        Ce cron s'exécute toutes les 6 heures.
        """
        from datetime import timedelta
        
        cutoff = fields.Datetime.now() - timedelta(hours=26)
        
        SupplierInfo = self.env["product.supplierinfo"].sudo()
        
        # Vérifier si le champ supplier_stock existe
        if "supplier_stock" not in SupplierInfo._fields:
            return True
        
        # Rechercher les supplierinfo obsolètes
        stale_supplierinfo = SupplierInfo.search([
            ("write_date", "<", cutoff),
            ("supplier_stock", ">", 0),
        ])
        
        if stale_supplierinfo:
            stale_supplierinfo.write({"supplier_stock": 0})
        
        return True

    # =========================================================================
    # REFRESH CONTENT IMPORT (Mise à jour contenu des produits existants)
    # =========================================================================
    @api.model
    def _process_refresh_content_import(self, provider, job_id=None):
        """[REFRESH] Rafraîchit le contenu des produits existants selon le mapping template.
        
        Cas d'usage: Le mapping template a été modifié et on veut ré-appliquer les nouvelles
        règles de mapping sur tous les produits existants.
        
        RÈGLES:
        - Télécharge le fichier FTP le plus récent
        - Pour chaque EAN trouvé dans le fichier ET existant en base:
          - Applique le mapping template complet (name, description, marque, etc.)
        - NE CRÉE PAS de nouveaux produits
        - NE MODIFIE PAS les prix/stocks (c'est DELTA qui fait ça)
        
        Args:
            provider: ftp.provider record
            job_id: ID du job pour tracking progression
            
        Returns:
            dict avec statistiques (updated, skipped_not_found, skipped_no_ean, errors, total)
        """
        if isinstance(provider, int):
            provider = self.env["ftp.provider"].browse(provider)
        provider = provider.sudo().ensure_one()

        # Keep ftp.provider status fields in sync with job-based PIM imports
        try:
            now = fields.Datetime.now()
            provider.sudo().write({
                "last_connection_status": "running",
                "last_error": False,
                "last_run_at": now,
            })
            self.env.cr.commit()
        except Exception:
            pass
        
        _logger.info("[REFRESH] _process_refresh_content_import for provider %s (%s)", provider.id, provider.name)
        
        # Vérifier que le mapping template est configuré
        mapping_result = self._build_mapping_from_template(provider)
        if not mapping_result.get("has_template"):
            error_msg = _("[REFRESH] ERREUR: Pas de template de mapping configuré pour '%s'") % provider.name
            _logger.error(error_msg)
            if job_id:
                self._mark_job_failed(job_id, error_msg)
            from odoo.exceptions import UserError
            raise UserError(error_msg)
        
        # Marquer le début
        provider.write({
            "pim_progress": 0.0,
            "pim_progress_total": 0,
            "pim_progress_current": 0,
            "pim_progress_status": _("[REFRESH] Connexion au serveur..."),
        })
        self.env.cr.commit()
        
        tmp_path = None
        try:
            # Télécharger le fichier FTP
            tmp_path, file_info = self._download_provider_file(provider)
            if not tmp_path:
                raise UserError(_("Aucun fichier trouvé sur le serveur"))
            
            provider.write({"pim_progress_status": _("[REFRESH] Traitement du fichier...")})
            self.env.cr.commit()
            
            # Préparer les options avec le mapping template
            import_options = {
                "mapping": mapping_result.get("mapping", {}),
                "mapping_lines": mapping_result.get("mapping_lines", []),
            }
            
            # Lire les paramètres CSV
            reader_params = provider.get_csv_reader_params() or {}
            
            # Traiter le fichier
            result = self._process_refresh_content_file(
                provider,
                tmp_path,
                import_options.get("mapping", {}),
                file_info.get("name", "import.csv"),
                has_header=reader_params.get("has_header"),
                encoding=reader_params.get("encoding"),
                delimiter=reader_params.get("delimiter"),
                delimiter_regex=reader_params.get("delimiter_regex"),
                job_id=job_id,
                options=import_options,
            )
            
            # Marquer comme terminé
            provider.write({
                "pim_progress": 100.0,
                "pim_progress_status": _("[REFRESH] Terminé: %d produits mis à jour, %d ignorés") % (
                    result.get("updated", 0),
                    result.get("skipped_not_found", 0),
                ),
            })
            self.env.cr.commit()

            # Success status for provider
            try:
                now = fields.Datetime.now()
                provider.sudo().write({
                    "last_connection_status": "ok",
                    "last_error": False,
                    "last_run_at": now,
                })
                self.env.cr.commit()
            except Exception:
                pass
            
            _logger.info("[REFRESH] Completed for provider %s: %s", provider.id, result)
            return result
            
        except Exception as e:
            _logger.exception("[REFRESH] Failed for provider %s: %s", provider.id, e)
            # Failure status for provider
            try:
                now = fields.Datetime.now()
                provider.sudo().write({
                    "last_connection_status": "failed",
                    "last_error": str(e)[:500],
                    "last_run_at": now,
                })
                self.env.cr.commit()
            except Exception:
                pass
            provider.write({
                "pim_progress_status": _("[REFRESH] Erreur: %s") % str(e)[:200],
            })
            self.env.cr.commit()
            raise
        finally:
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass

    def _process_refresh_content_file(self, provider, file_path, mapping, filename, has_header=True, encoding=None, delimiter=None, delimiter_regex=None, job_id=None, options=None):
        """Traitement du fichier pour REFRESH CONTENT (mise à jour contenu uniquement).
        
        Pour chaque ligne du fichier:
        1. Extraire l'EAN
        2. Chercher le produit en base
        3. Si trouvé: appliquer le mapping template complet
        4. Si non trouvé: ignorer (pas de création)
        """
        import gc
        
        result = {
            "total": 0,
            "updated": 0,
            "skipped_not_found": 0,
            "skipped_no_ean": 0,
            "errors": 0,
        }
        
        # Paramètres de lecture
        has_header = True if has_header is None else bool(has_header)
        sel_encoding = encoding
        sel_delimiter = delimiter
        sel_delimiter_regex = delimiter_regex
        if sel_delimiter == "\\t":
            sel_delimiter = "\t"
        timeout_seconds = self._get_import_timeout_seconds()
        start_ts = time.time()
        
        # Détection encodage et délimiteur
        enc_candidates = ["utf-8-sig", "utf-8", "cp1252", "latin-1"]
        if sel_encoding:
            enc_candidates = [sel_encoding] + [e for e in enc_candidates if e != sel_encoding]
        detected_enc, sample = self._read_head(file_path, enc_candidates)
        if not sel_encoding:
            sel_encoding = detected_enc
        
        # Détection automatique du délimiteur si nécessaire
        if not sel_delimiter and not sel_delimiter_regex:
            sel_delimiter = self._detect_delimiter(sample or "")
        
        # Compter les lignes
        total_rows = self._count_csv_lines(file_path, has_header=has_header, encoding=sel_encoding)
        result["total"] = total_rows
        
        provider.write({
            "pim_progress_total": total_rows,
            "pim_progress_status": _("[REFRESH] Traitement de %d lignes...") % total_rows,
        })
        self.env.cr.commit()
        
        if job_id:
            self._update_job_progress(job_id, 0, total_rows, status=_("[REFRESH] Traitement de %d lignes...") % total_rows)
        
        # Lire les headers
        headers = self._read_csv_headers(file_path, encoding=sel_encoding, delimiter=sel_delimiter, delimiter_regex=sel_delimiter_regex)
        
        # Index des colonnes
        col_idx = self._build_column_index(headers, mapping)
        # ✅ FIX: Normaliser les accents dans hdr_index pour REFRESH
        hdr_index = {self._normalize_string_for_comparison(h): i for i, h in enumerate(headers)}
        
        # Récupérer le mapping dynamique
        dynamic_mapping = options.get("mapping") if options else None
        mapping_lines = options.get("mapping_lines") if options else None
        
        ProductTemplate = self.env["product.template"].sudo()
        
        batch_size = 100
        i = 0
        
        _logger.info("[REFRESH] Starting content refresh for %d rows", total_rows)
        
        # Lire le fichier en streaming
        for row in self._iter_csv_rows(file_path, has_header=has_header, encoding=sel_encoding, delimiter=sel_delimiter, delimiter_regex=sel_delimiter_regex):
            try:
                i += 1
                
                # Mise à jour progression tous les 100 produits
                if i % 100 == 0:
                    progress = (i / total_rows) * 100 if total_rows > 0 else 0
                    self._update_job_progress_direct(provider.id, progress, i, total_rows,
                        _("[REFRESH] Ligne %d/%d - %d mis à jour...") % (i, total_rows, result["updated"]))
                    if job_id:
                        self._update_job_progress(job_id, i, total_rows,
                            status=_("[REFRESH] Ligne %d/%d - %d mis à jour...") % (i, total_rows, result["updated"]))
                
                # Timeout check
                if (time.time() - start_ts) > timeout_seconds:
                    msg = _("[REFRESH] Délai dépassé (%d sec). Arrêt à la ligne %d/%d.") % (timeout_seconds, i, total_rows)
                    if job_id:
                        self._mark_job_failed(job_id, msg)
                    raise UserError(msg)
                
                # Extraire l'EAN
                raw_ean = self._get_cell(row, col_idx.get("ean"))
                norm_ean = self._normalize_ean(raw_ean)
                if not norm_ean and raw_ean:
                    digits_only = self._digits_only(raw_ean)
                    if digits_only:
                        norm_ean = digits_only
                
                # Ignorer les lignes sans EAN
                if not norm_ean:
                    result["skipped_no_ean"] += 1
                    continue
                
                # Chercher le produit en base
                product_id, template_id = self._find_product_by_ean(norm_ean)
                if not template_id:
                    result["skipped_not_found"] += 1
                    continue
                
                # Récupérer le template
                tmpl_rec = ProductTemplate.browse(template_id)
                if not tmpl_rec.exists():
                    result["skipped_not_found"] += 1
                    continue
                
                variant = tmpl_rec.product_variant_id
                
                # Appliquer le mapping template complet
                if dynamic_mapping:
                    try:
                        self._apply_mapping_to_product(
                            tmpl_rec, variant, row, headers, hdr_index,
                            dynamic_mapping, mapping_lines, options
                        )
                        
                        # Aussi créer/mettre à jour les ODR si mappées
                        self._create_odr_from_mapping(
                            tmpl_rec, row, headers, hdr_index,
                            dynamic_mapping, mapping_lines
                        )
                        
                        result["updated"] += 1
                        
                    except Exception as map_err:
                        _logger.warning("[REFRESH] Error applying mapping to product %s: %s", template_id, map_err)
                        result["errors"] += 1
                else:
                    _logger.warning("[REFRESH] No dynamic mapping available, skipping product %s", template_id)
                    result["skipped_not_found"] += 1
                
                # Commit périodique
                if i % batch_size == 0:
                    self.env.cr.commit()
                    self.env.invalidate_all()
                    gc.collect()
                    _logger.info("[REFRESH] Committed batch at row %d (%d updated)", i, result["updated"])
                    
            except UserError:
                raise
            except Exception as e:
                _logger.warning("[REFRESH] Error on row %d: %s", i, e)
                result["errors"] += 1
        
        # Commit final
        self.env.cr.commit()
        self.env.invalidate_all()
        gc.collect()
        
        # Créer l'historique
        self._create_import_history(provider, filename, result, mode="REFRESH")
        
        # Final job progress update
        if job_id:
            self._update_job_progress(
                job_id, total_rows, total_rows,
                status=_("[REFRESH] Terminé: %d mis à jour, %d non trouvés, %d sans EAN") % (
                    result["updated"], result["skipped_not_found"], result["skipped_no_ean"]
                ),
                updated=result["updated"],
                errors=result["errors"]
            )
        
        _logger.info("[REFRESH] Completed: %d updated, %d not found, %d no EAN, %d errors",
                     result["updated"], result["skipped_not_found"], result["skipped_no_ean"], result["errors"])
        
        return result

    def _find_product_by_ref(self, ref):
        """Trouve un produit par référence (default_code) via SQL direct.
        Retourne (product_id, template_id) ou (None, None) si non trouvé.
        Utilise un savepoint pour isoler les erreurs de transaction.
        """
        if not ref:
            return None, None
        
        try:
            with self.env.cr.savepoint():
                # Chercher dans product_product.default_code
                self.env.cr.execute(
                    "SELECT id, product_tmpl_id FROM product_product WHERE default_code = %s LIMIT 1",
                    [ref]
                )
                result = self.env.cr.fetchone()
                if result:
                    _logger.debug("[REF-MATCH] Found product by default_code: REF=%s -> product_id=%s, tmpl_id=%s", ref, result[0], result[1])
                    return result[0], result[1]
            
            return None, None
        except Exception as e:
            _logger.warning("Error finding product by ref (isolated): %s", e)
            return None, None

    def _find_product_by_ean(self, ean):
        """Trouve un produit par EAN via SQL direct.
        Retourne (product_id, template_id) ou (None, None) si non trouvé.
        Utilise un savepoint pour isoler les erreurs de transaction.
        
        RECHERCHE DANS L'ORDRE:
        1. product_product.barcode - le code-barres standard du produit
        2. product_supplierinfo.product_code - la référence article fournisseur (souvent l'EAN)
        3. product_template.barcode - le barcode sur le template (si le champ existe)
        
        Cela permet de matcher les EAN même s'ils sont stockés uniquement
        dans les infos fournisseur (ce qui est courant après import).
        """
        if not ean:
            return None, None
        
        try:
            # Utiliser un savepoint pour isoler les erreurs
            with self.env.cr.savepoint():
                # 1. Chercher dans product_product.barcode (le plus courant)
                self.env.cr.execute(
                    "SELECT id, product_tmpl_id FROM product_product WHERE barcode = %s LIMIT 1",
                    [ean]
                )
                result = self.env.cr.fetchone()
                if result:
                    _logger.debug("[EAN-MATCH] Found product by barcode: EAN=%s -> product_id=%s, tmpl_id=%s", ean, result[0], result[1])
                    return result[0], result[1]
                
                # 2. Chercher dans product_supplierinfo.product_code (référence fournisseur = EAN)
                # C'est très courant car les imports fournisseur stockent l'EAN dans product_code
                self.env.cr.execute("""
                    SELECT pp.id, pp.product_tmpl_id 
                    FROM product_supplierinfo psi
                    JOIN product_template pt ON psi.product_tmpl_id = pt.id
                    JOIN product_product pp ON pp.product_tmpl_id = pt.id
                    WHERE psi.product_code = %s
                    LIMIT 1
                """, [ean])
                result = self.env.cr.fetchone()
                if result:
                    _logger.debug("[EAN-MATCH] Found product by supplierinfo.product_code: EAN=%s -> product_id=%s, tmpl_id=%s", ean, result[0], result[1])
                    return result[0], result[1]
                
                # 3. Chercher dans product_template.barcode (certaines installations ont ce champ)
                # Vérifier d'abord si la colonne existe
                self.env.cr.execute("""
                    SELECT column_name FROM information_schema.columns 
                    WHERE table_name = 'product_template' AND column_name = 'barcode'
                """)
                if self.env.cr.fetchone():
                    self.env.cr.execute("""
                        SELECT pp.id, pt.id 
                        FROM product_template pt
                        JOIN product_product pp ON pp.product_tmpl_id = pt.id
                        WHERE pt.barcode = %s
                        LIMIT 1
                    """, [ean])
                    result = self.env.cr.fetchone()
                    if result:
                        _logger.debug("[EAN-MATCH] Found product by template.barcode: EAN=%s -> product_id=%s, tmpl_id=%s", ean, result[0], result[1])
                        return result[0], result[1]
            
            _logger.debug("[EAN-MATCH] Product NOT FOUND for EAN=%s", ean)
            return None, None
        except Exception as e:
            _logger.warning("Error finding product by EAN (isolated): %s", e)
            return None, None
