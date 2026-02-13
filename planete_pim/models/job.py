# -*- coding: utf-8 -*-
from odoo import models, fields, api
from datetime import timedelta
import json
import logging

_logger = logging.getLogger(__name__)


class PlanetePimImportJob(models.Model):
    _name = "planete.pim.import.job"
    _description = "Planète PIM - Import Job"
    _order = "id desc"

    name = fields.Char(required=True)
    state = fields.Selection(
        [
            ("pending", "En attente"),
            ("running", "En cours"),
            ("done", "Terminé"),
            ("failed", "Échoué"),
            ("retry_pending", "En attente de retry"),
            ("paused", "En pause (reprise auto)"),
        ],
        default="pending",
        required=True,
        index=True,
        string="État",
    )
    import_mode = fields.Selection(
        [
            ("standard", "Standard (fichier)"),
            ("full", "FULL - Création produits"),
            ("delta", "DELTA - Prix/Stock"),
            ("refresh_content", "REFRESH - Contenu produits"),
        ],
        default="standard",
        required=True,
        string="Mode d'import",
        help="standard: Import depuis fichier uploadé\n"
             "full: Import FULL (création nouveaux produits)\n"
             "delta: Import DELTA (mise à jour prix/stock uniquement)\n"
             "refresh_content: REFRESH (mise à jour contenu des produits existants)",
    )
    user_id = fields.Many2one(
        "res.users",
        string="Lancé par",
        required=True,
        default=lambda self: self.env.user,
        index=True,
    )
    company_id = fields.Many2one(
        "res.company",
        string="Société",
        required=True,
        default=lambda self: self.env.company,
        index=True,
    )
    provider_id = fields.Many2one("ftp.provider", string="Provider", index=True)

    file_data = fields.Binary(string="Fichier", attachment=True)
    file_data_name = fields.Char(string="Nom du fichier")
    options_json = fields.Text(string="Options (JSON)")

    log_id = fields.Many2one("ftp.tariff.import.log", string="Journal d'import", readonly=True)
    error = fields.Text(string="Erreur", readonly=True)
    started_at = fields.Datetime(string="Démarré le", readonly=True)
    finished_at = fields.Datetime(string="Terminé le", readonly=True)

    # Progress tracking fields
    progress = fields.Float(string="Progression (%)", default=0.0, readonly=True)
    progress_total = fields.Integer(string="Total lignes", default=0, readonly=True)
    progress_current = fields.Integer(string="Ligne courante", default=0, readonly=True)
    progress_status = fields.Char(string="Statut détaillé", readonly=True)
    
    # Stats
    created_count = fields.Integer(string="Créés", default=0, readonly=True)
    updated_count = fields.Integer(string="Mis à jour", default=0, readonly=True)
    skipped_count = fields.Integer(string="Ignorés", default=0, readonly=True)
    error_count = fields.Integer(string="Erreurs", default=0, readonly=True)
    quarantined_count = fields.Integer(string="En quarantaine", default=0, readonly=True)
    
    # Checkpoint pour reprise après interruption
    checkpoint_row = fields.Integer(
        string="Checkpoint (ligne)",
        default=0,
        help="Dernière ligne traitée avec succès. Permet de reprendre l'import après une interruption.",
    )
    checkpoint_data = fields.Text(
        string="Checkpoint data (JSON)",
        help="Données de checkpoint pour la reprise (caches, etc.)",
    )
    
    # Retry mechanism
    retry_count = fields.Integer(
        string="Tentatives",
        default=0,
        help="Nombre de tentatives effectuées après échec.",
    )
    max_retries = fields.Integer(
        string="Max retries",
        default=3,
        help="Nombre maximum de tentatives après échec. Défaut: 3.",
    )
    next_retry_at = fields.Datetime(
        string="Prochain retry",
        help="Date/heure du prochain retry automatique.",
    )
    last_error = fields.Text(
        string="Dernière erreur",
        help="Détail de la dernière erreur survenue.",
    )
    
    # Duration
    duration_seconds = fields.Float(
        string="Durée (s)",
        compute="_compute_duration",
        store=False,
    )
    
    # Diagnostic logs
    log_details = fields.Text(
        string="Logs détaillés",
        help="Logs détaillés de l'exécution du job pour diagnostic.",
    )
    
    # =====================================================================
    # NOUVEAU: Champ de rapport de diagnostic détaillé (raisons skips)
    # Affichable dans l'interface Odoo pour comprendre les skips
    # =====================================================================
    diagnosis_report = fields.Text(
        string="Diagnostic détaillé (Skips)",
        help="Rapport détaillé montrant pourquoi les produits ont été ignorés/skippés.\n"
             "Inclut le breakdown par raison (EAN manquant, doublon, etc.) avec pourcentages.",
    )
    
    # =====================================================================
    # NOUVEAU: Champ commentaire pour notes et documentation
    # =====================================================================
    comment = fields.Text(
        string="Commentaire",
        help="Notes et commentaires concernant ce job d'import.\n"
             "Utile pour documenter les problèmes, les actions prises, ou les observations.",
    )
    
    # =====================================================================
    # NOUVEAU: Champs de diagnostic calculés pour expliquer ralentissements
    # =====================================================================
    diagnostic_summary = fields.Text(
        string="Résumé diagnostic",
        compute="_compute_diagnostic_info",
        help="Résumé détaillé du diagnostic du job (blocage, timeouts, etc.)",
    )
    elapsed_time_display = fields.Char(
        string="Temps écoulé",
        compute="_compute_diagnostic_info",
        help="Temps écoulé depuis le démarrage du job",
    )
    is_stuck = fields.Boolean(
        string="Bloqué",
        compute="_compute_diagnostic_info",
        help="True si le job est bloqué (stuck) depuis trop longtemps",
    )
    stuck_duration_minutes = fields.Integer(
        string="Durée du blocage (min)",
        compute="_compute_diagnostic_info",
        help="Nombre de minutes que le job est bloqué",
    )
    processing_speed = fields.Char(
        string="Vitesse de traitement",
        compute="_compute_diagnostic_info",
        help="Nombre de lignes traitées par seconde",
    )
    estimated_remaining_time = fields.Char(
        string="Temps restant estimé",
        compute="_compute_diagnostic_info",
        help="Estimation du temps nécessaire pour terminer",
    )
    retry_reason = fields.Char(
        string="Raison du retry",
        compute="_compute_diagnostic_info",
        help="Raison pour laquelle le job est en retry",
    )

    @api.depends("started_at", "finished_at")
    def _compute_duration(self):
        for rec in self:
            if rec.started_at and rec.finished_at:
                delta = rec.finished_at - rec.started_at
                rec.duration_seconds = delta.total_seconds()
            elif rec.started_at:
                delta = fields.Datetime.now() - rec.started_at
                rec.duration_seconds = delta.total_seconds()
            else:
                rec.duration_seconds = 0.0

    @api.depends("state", "started_at", "finished_at", "progress_current", "progress_total", 
                 "progress", "progress_status", "retry_count", "last_error", "error")
    def _compute_diagnostic_info(self):
        """Calcule les informations de diagnostic pour expliquer les ralentissements."""
        from datetime import datetime, timedelta
        
        for rec in self:
            now = fields.Datetime.now()
            
            # =====================================================================
            # 1. TEMPS ÉCOULÉ ET BLOCAGE
            # =====================================================================
            rec.elapsed_time_display = ""
            rec.is_stuck = False
            rec.stuck_duration_minutes = 0
            
            if rec.started_at:
                if rec.finished_at:
                    elapsed = rec.finished_at - rec.started_at
                else:
                    elapsed = now - rec.started_at
                
                total_seconds = elapsed.total_seconds()
                hours = int(total_seconds // 3600)
                minutes = int((total_seconds % 3600) // 60)
                seconds = int(total_seconds % 60)
                
                rec.elapsed_time_display = f"{hours}h {minutes}m {seconds}s"
                
                # Déterminer si le job est bloqué
                if rec.state == "running" and total_seconds > 600:  # > 10 minutes
                    rec.is_stuck = True
                    rec.stuck_duration_minutes = int(total_seconds / 60)
            
            # =====================================================================
            # 2. VITESSE DE TRAITEMENT ET TEMPS RESTANT
            # =====================================================================
            rec.processing_speed = "N/A"
            rec.estimated_remaining_time = "N/A"
            
            if rec.started_at and rec.progress_total > 0 and rec.progress_current > 0:
                elapsed_seconds = (now - rec.started_at).total_seconds()
                if elapsed_seconds > 0:
                    speed = rec.progress_current / elapsed_seconds  # lignes par seconde
                    rec.processing_speed = f"{speed:.2f} lignes/sec"
                    
                    # Calculer le temps restant
                    remaining_lines = rec.progress_total - rec.progress_current
                    if speed > 0:
                        remaining_seconds = remaining_lines / speed
                        est_hours = int(remaining_seconds // 3600)
                        est_minutes = int((remaining_seconds % 3600) // 60)
                        est_seconds = int(remaining_seconds % 60)
                        rec.estimated_remaining_time = f"{est_hours}h {est_minutes}m {est_seconds}s"
            
            # =====================================================================
            # 3. RAISON DU RETRY
            # =====================================================================
            rec.retry_reason = ""
            if rec.state == "retry_pending":
                if rec.last_error:
                    rec.retry_reason = f"Erreur: {rec.last_error[:100]}"
                else:
                    rec.retry_reason = "Raison inconnue"
            
            # =====================================================================
            # 4. RÉSUMÉ DIAGNOSTIC COMPLET
            # =====================================================================
            diagnostic_lines = []
            diagnostic_lines.append("=" * 70)
            diagnostic_lines.append("DIAGNOSTIC COMPLET DU JOB")
            diagnostic_lines.append("=" * 70)
            
            # État général
            diagnostic_lines.append("")
            diagnostic_lines.append("📊 ÉTAT GÉNÉRAL")
            diagnostic_lines.append("-" * 70)
            diagnostic_lines.append(f"État du job:         {dict(rec._fields['state'].selection).get(rec.state, rec.state)}")
            diagnostic_lines.append(f"Mode d'import:       {dict(rec._fields['import_mode'].selection).get(rec.import_mode, rec.import_mode)}")
            diagnostic_lines.append(f"Progression:         {rec.progress:.1f}% ({rec.progress_current}/{rec.progress_total} lignes)")
            diagnostic_lines.append(f"Temps écoulé:        {rec.elapsed_time_display}")
            
            # Statut de blocage
            if rec.is_stuck:
                diagnostic_lines.append("")
                diagnostic_lines.append("🚨 ATTENTION: JOB BLOQUÉ")
                diagnostic_lines.append("-" * 70)
                diagnostic_lines.append(f"Durée du blocage:    {rec.stuck_duration_minutes} minutes")
                diagnostic_lines.append(f"Statut:              {rec.progress_status or 'Non disponible'}")
                if rec.progress == 100.0:
                    diagnostic_lines.append("Problème détecté:    Job à 100% mais pas marqué comme terminé (STUCK BUG)")
                    diagnostic_lines.append("Cause probable:      Une exception s'est produite après avoir mis la progression à 100%")
                    diagnostic_lines.append("Action recommandée:  Le cron devrait forcer la completion d'ici 3 minutes")
                elif rec.progress < 100.0:
                    diagnostic_lines.append("Problème détecté:    Job bloqué en cours de traitement")
                    if rec.processing_speed != "N/A":
                        diagnostic_lines.append(f"Vitesse:             {rec.processing_speed}")
                        diagnostic_lines.append(f"Temps restant estimé: {rec.estimated_remaining_time}")
            
            # Informations sur les retries
            if rec.retry_count > 0 or rec.state == "retry_pending":
                diagnostic_lines.append("")
                diagnostic_lines.append("🔄 MÉCANISME DE RETRY")
                diagnostic_lines.append("-" * 70)
                diagnostic_lines.append(f"Tentatives effectuées: {rec.retry_count}/{rec.max_retries}")
                if rec.next_retry_at:
                    time_until_retry = (rec.next_retry_at - now).total_seconds()
                    if time_until_retry > 0:
                        diagnostic_lines.append(f"Prochain retry:        Dans {int(time_until_retry / 60)}m {int(time_until_retry % 60)}s")
                    else:
                        diagnostic_lines.append(f"Prochain retry:        IMMINENT (en retard de {int(abs(time_until_retry) / 60)}m)")
                if rec.last_error:
                    diagnostic_lines.append(f"Dernière erreur:       {rec.last_error[:200]}")
            
            # Performances et vitesse
            if rec.processing_speed != "N/A":
                diagnostic_lines.append("")
                diagnostic_lines.append("⚡ PERFORMANCES")
                diagnostic_lines.append("-" * 70)
                diagnostic_lines.append(f"Vitesse traitement:    {rec.processing_speed}")
                diagnostic_lines.append(f"Temps restant estimé:  {rec.estimated_remaining_time}")
            
            # Statistiques
            diagnostic_lines.append("")
            diagnostic_lines.append("📈 STATISTIQUES")
            diagnostic_lines.append("-" * 70)
            diagnostic_lines.append(f"Créés:                 {rec.created_count}")
            diagnostic_lines.append(f"Mis à jour:            {rec.updated_count}")
            diagnostic_lines.append(f"Ignorés:               {rec.skipped_count}")
            diagnostic_lines.append(f"En quarantaine:        {rec.quarantined_count}")
            diagnostic_lines.append(f"Erreurs:               {rec.error_count}")
            
            # Checkpoint
            if rec.checkpoint_row > 0:
                diagnostic_lines.append("")
                diagnostic_lines.append("🔖 CHECKPOINT (Reprise après interruption)")
                diagnostic_lines.append("-" * 70)
                diagnostic_lines.append(f"Dernière ligne traitée: {rec.checkpoint_row}")
                diagnostic_lines.append(f"Prochain démarrage à:    Ligne {rec.checkpoint_row + 1}")
                diagnostic_lines.append(f"Lignes traitées:         {rec.checkpoint_row} / {rec.progress_total}")
            
            # Recommandations
            diagnostic_lines.append("")
            diagnostic_lines.append("💡 RECOMMANDATIONS")
            diagnostic_lines.append("-" * 70)
            
            if rec.state == "running" and rec.is_stuck:
                diagnostic_lines.append("• Le job semble bloqué. Vérifiez les logs serveur pour les erreurs SQL ou timeout")
                diagnostic_lines.append("• Si le job est à 100%, attendez ~3 minutes pour que le cron le marque automatiquement comme terminé")
                diagnostic_lines.append("• Sinon, vous pouvez forcer un retry manuel avec le bouton 'Forcer le Retry'")
            elif rec.state == "retry_pending":
                diagnostic_lines.append("• Le job est en attente de retry après une erreur")
                if rec.last_error:
                    diagnostic_lines.append(f"• Erreur: {rec.last_error[:150]}")
                diagnostic_lines.append("• Le retry s'exécutera automatiquement à la date indiquée")
                diagnostic_lines.append("• Vous pouvez aussi cliquer sur 'Exécuter maintenant' pour relancer immédiatement")
            elif rec.state == "failed":
                diagnostic_lines.append("• Le job a échoué définitivement après tous les retries")
                if rec.error:
                    diagnostic_lines.append(f"• Erreur finale: {rec.error[:150]}")
                diagnostic_lines.append("• Vérifiez les logs détaillés pour comprendre la cause")
                diagnostic_lines.append("• Corrigez le problème et relancez le job avec 'Forcer le Retry'")
            elif rec.state == "done":
                diagnostic_lines.append("✅ Le job s'est terminé avec succès")
                diagnostic_lines.append(f"• Durée totale: {rec.elapsed_time_display}")
                diagnostic_lines.append(f"• Résultats: {rec.created_count} créés, {rec.updated_count} MAJ, {rec.error_count} erreurs")
            
            diagnostic_lines.append("")
            diagnostic_lines.append("=" * 70)
            
            rec.diagnostic_summary = "\n".join(diagnostic_lines)


    def _is_job_stuck(self):
        """Check if this job is stuck (running for too long)."""
        if self.state != "running":
            return False
        if not self.started_at:
            return False
        from datetime import timedelta
        now = fields.Datetime.now()
        duration = now - self.started_at
        # Job is considered stuck if running for more than 2 hours
        max_duration = timedelta(hours=2)
        return duration > max_duration

    def _parse_options(self):
        self.ensure_one()
        try:
            return dict(json.loads(self.options_json or "{}") or {})
        except Exception:
            _logger.exception("Invalid options_json on job %s", self.id)
            return {}

    def run_job(self):
        """Execute this job synchronously (used by cron).
        
        Supports retry mechanism:
        - If state is 'retry_pending', it will attempt to resume from checkpoint
        - If import fails and retry_count < max_retries, schedules a retry
        
        IMPORTANT FIX: Stats are saved to database BEFORE final write,
        ensuring they are never lost even if an exception occurs.
        """
        for job in self:
            if job.state not in ("pending", "retry_pending", "paused"):
                continue
            
            # Check if this is a retry attempt
            is_retry = job.state in ("retry_pending", "paused")
            checkpoint_row = job.checkpoint_row if is_retry else 0
            
            try:
                # Prepare initial values - use simple strings to avoid DB queries on shutdown
                if not is_retry:
                    progress_status = "Demarrage..."
                else:
                    if job.state == "paused":
                        progress_status = "Reprise depuis ligne %d (pause planifiée)..." % checkpoint_row
                    else:
                        progress_status = "Reprise depuis ligne %d (tentative %d/%d)..." % (
                            checkpoint_row, job.retry_count + 1, job.max_retries
                        )
                
                start_vals = {
                    "state": "running",
                    "error": False,
                    "progress_status": progress_status,
                }
                
                # Only reset started_at and progress if this is the first attempt
                if not is_retry:
                    start_vals.update({
                        "started_at": fields.Datetime.now(),
                        "progress": 0.0,
                        "progress_current": 0,
                    })
                
                job.write(start_vals)
                # Commit to show "running" state immediately
                try:
                    self.env.cr.commit()
                except Exception:
                    pass

                importer = job.with_company(job.company_id).env["planete.pim.importer"].sudo()
                
                # Initialize result storage
                result = None
                vals = {}
                
                # Execute import based on mode
                # =====================================================================
                # CUMUL DES STATS: Si c'est un retry, lire les stats précédentes
                # pour les cumuler avec les nouvelles stats du retry
                # =====================================================================
                prev_created = 0
                prev_updated = 0
                prev_skipped = 0
                prev_errors = 0
                prev_quarantined = 0
                
                if is_retry:
                    try:
                        self.env.cr.execute(
                            "SELECT created_count, updated_count, skipped_count, error_count, quarantined_count "
                            "FROM planete_pim_import_job WHERE id = %s",
                            [job.id]
                        )
                        prev_row = self.env.cr.fetchone()
                        if prev_row:
                            prev_created = prev_row[0] or 0
                            prev_updated = prev_row[1] or 0
                            prev_skipped = prev_row[2] or 0
                            prev_errors = prev_row[3] or 0
                            prev_quarantined = prev_row[4] or 0
                            _logger.info(
                                "Retry: Previous stats for job %s: created=%d, updated=%d, skipped=%d, errors=%d, quarantined=%d",
                                job.id, prev_created, prev_updated, prev_skipped, prev_errors, prev_quarantined
                            )
                    except Exception as prev_err:
                        _logger.warning("Could not read previous stats for retry: %s", prev_err)
                
                if job.import_mode == "full":
                    # FULL mode: création nouveaux produits
                    # Peut utiliser soit le fichier attaché (file_data), soit télécharger depuis FTP
                    if job.file_data:
                        # Fichier déjà attaché au job (créé depuis le wizard mapping)
                        _logger.info("Running FULL import for job %s from attached file", job.id)
                        result = importer._process_full_import_from_file(
                            job.provider_id, 
                            job.file_data, 
                            job.file_data_name or "import.csv",
                            job_id=job.id,
                            options=job._parse_options(),
                        )
                    elif job.provider_id:
                        # Télécharger depuis FTP
                        _logger.info("Running FULL import for job %s, provider %s", job.id, job.provider_id.name)
                        result = importer._process_full_import(job.provider_id, job_id=job.id)
                    else:
                        raise ValueError("Aucun fichier attache ni provider configure pour import FULL")

                    # ✅ NOUVEAU: Pause planifiée (time-slicing) sans consommer les retries
                    # L'importer renvoie {'paused': True} lorsqu'il approche du budget temps.
                    if isinstance(result, dict) and result.get("paused"):
                        vals = {
                            "state": "paused",
                            "progress_status": result.get("pause_reason")
                                              or ("Pause planifiée - reprise auto depuis ligne %d" % (job.checkpoint_row or 0)),
                        }
                        # Ne pas toucher à finished_at/progress=100%
                        job.write(vals)
                        try:
                            self.env.cr.commit()
                        except Exception:
                            pass
                        continue
                    
                    # ✅ CUMUL: Additionner les stats précédentes (avant retry) avec les nouvelles
                    created = (result.get("created", 0) if result else 0) + prev_created
                    updated = (result.get("updated", 0) if result else 0) + prev_updated
                    quarantined = (result.get("quarantined", 0) if result else 0) + prev_quarantined
                    skipped = (result.get("skipped_existing", 0) if result else 0) + prev_skipped
                    errors = (result.get("errors", 0) if result else 0) + prev_errors
                    
                    vals = {
                        "state": "done",
                        "finished_at": fields.Datetime.now(),
                        "progress": 100.0,
                        "progress_status": "Import FULL termine: %d crees, %d quarantaine, %d MAJ, %d ignores" % (created, quarantined, updated, skipped),
                        "created_count": created,
                        "updated_count": updated,
                        "quarantined_count": quarantined,
                        "skipped_count": skipped,
                        "error_count": errors,
                    }
                elif job.import_mode == "delta" and job.provider_id:
                    # DELTA mode: mise à jour prix/stock via FTP
                    _logger.info("Running DELTA import for job %s, provider %s", job.id, job.provider_id.name)
                    result = importer._process_delta_import(job.provider_id, job_id=job.id)
                    price_updated = result.get("price_updated", 0) if result else 0
                    stock_updated = result.get("stock_updated", 0) if result else 0
                    total_updated = price_updated + stock_updated
                    # ✅ CORRECTION: Inclure skipped_no_ean dans les stats ignorés
                    skipped_not_found = result.get("skipped_not_found", 0) if result else 0
                    skipped_no_ean = result.get("skipped_no_ean", 0) if result else 0
                    total_skipped = skipped_not_found + skipped_no_ean
                    error_count = result.get("errors", 0) if result else 0
                    
                    # Message détaillé pour le debugging
                    status_msg = "Import DELTA termine: %d MAJ" % total_updated
                    if skipped_no_ean > 0:
                        status_msg += " | %d lignes sans EAN" % skipped_no_ean
                    if skipped_not_found > 0:
                        status_msg += " | %d produits non trouves" % skipped_not_found
                    
                    vals = {
                        "state": "done",
                        "finished_at": fields.Datetime.now(),
                        "progress": 100.0,
                        "progress_status": status_msg,
                        "updated_count": total_updated,
                        "skipped_count": total_skipped,
                        "error_count": error_count,
                    }
                elif job.import_mode == "refresh_content" and job.provider_id:
                    # REFRESH CONTENT mode: mise à jour du contenu des produits existants
                    _logger.info("Running REFRESH CONTENT import for job %s, provider %s", job.id, job.provider_id.name)
                    result = importer._process_refresh_content_import(job.provider_id, job_id=job.id)
                    updated = result.get("updated", 0) if result else 0
                    skipped_not_found = result.get("skipped_not_found", 0) if result else 0
                    skipped_no_ean = result.get("skipped_no_ean", 0) if result else 0
                    total_skipped = skipped_not_found + skipped_no_ean
                    error_count = result.get("errors", 0) if result else 0
                    
                    # Message détaillé
                    status_msg = "REFRESH termine: %d produits mis a jour" % updated
                    if skipped_no_ean > 0:
                        status_msg += " | %d lignes sans EAN" % skipped_no_ean
                    if skipped_not_found > 0:
                        status_msg += " | %d produits non trouves" % skipped_not_found
                    
                    vals = {
                        "state": "done",
                        "finished_at": fields.Datetime.now(),
                        "progress": 100.0,
                        "progress_status": status_msg,
                        "updated_count": updated,
                        "skipped_count": total_skipped,
                        "error_count": error_count,
                    }
                else:
                    # Standard mode: import depuis fichier uploadé
                    opts = job._parse_options()
                    opts["do_write"] = True
                    opts.setdefault("allow_create_products", True)
                    if job.provider_id and not opts.get("provider_id"):
                        opts["provider_id"] = job.provider_id.id
                    opts["job_id"] = job.id

                    action = importer.import_from_binary(
                        job.file_data, job.file_data_name or "upload.csv", options=opts
                    )

                    res_id = None
                    try:
                        if isinstance(action, dict) and action.get("res_id"):
                            res_id = int(action.get("res_id"))
                    except Exception:
                        res_id = None

                    vals = {
                        "state": "done",
                        "finished_at": fields.Datetime.now(),
                        "progress": 100.0,
                        "progress_status": "Import termine",
                    }
                    if res_id:
                        vals["log_id"] = res_id

                # ✅ CRITICAL FIX: Save stats BEFORE final write
                # This ensures stats are never lost even if write() fails
                if vals:
                    try:
                        job.write(vals)
                    except Exception as e:
                        _logger.warning("Could not update job status, trying to save stats directly to DB: %s", e)
                        # Fallback: save stats via SQL
                        try:
                            stat_update = {
                                "created_count": vals.get("created_count", 0),
                                "updated_count": vals.get("updated_count", 0),
                                "skipped_count": vals.get("skipped_count", 0),
                                "error_count": vals.get("error_count", 0),
                                "quarantined_count": vals.get("quarantined_count", 0),
                            }
                            # Only include non-zero values
                            stat_updates = ", ".join([f"{k} = {v}" for k, v in stat_update.items() if v > 0])
                            if stat_updates:
                                sql = f"UPDATE planete_pim_import_job SET {stat_updates} WHERE id = %s"
                                self.env.cr.execute(sql, [job.id])
                            self.env.cr.commit()
                        except Exception as sql_err:
                            _logger.error("Failed to save stats via SQL: %s", sql_err)
                        raise

                # Commit per job to shorten lock windows
                try:
                    self.env.cr.commit()
                except Exception:
                    pass
            except Exception as e:
                _logger.exception("PIM import job failed (id=%s)", job.id)
                error_msg = str(e)[:500] if e else "Unknown error"
                
                # Try to rollback first if cursor is still valid
                try:
                    self.env.cr.rollback()
                except Exception:
                    pass
                
                # ✅ FIX: Read all needed data BEFORE trying retry logic
                try:
                    self.env.cr.execute(
                        "SELECT retry_count, max_retries, progress_current, created_count, updated_count, skipped_count, error_count, quarantined_count FROM planete_pim_import_job WHERE id = %s",
                        [job.id]
                    )
                    row = self.env.cr.fetchone()
                    current_retry_count = row[0] if row else 0
                    max_retries = row[1] if row else 3
                    current_checkpoint = row[2] if row else 0
                    current_created = row[3] if row else 0
                    current_updated = row[4] if row else 0
                    current_skipped = row[5] if row else 0
                    current_errors = row[6] if row else 0
                    current_quarantined = row[7] if row else 0
                except Exception:
                    current_retry_count = 0
                    max_retries = 3
                    current_checkpoint = 0
                    current_created = 0
                    current_updated = 0
                    current_skipped = 0
                    current_errors = 0
                    current_quarantined = 0
                
                # Check if we can retry
                can_retry = current_retry_count < max_retries
                
                if can_retry:
                    # Schedule a retry in 2 minutes (reduced from 5 for faster recovery)
                    next_retry = fields.Datetime.now() + timedelta(minutes=2)
                    new_retry_count = current_retry_count + 1
                    # Use simple string to avoid DB queries when cursor may be closed
                    progress_msg = "Erreur (tentative %d/%d): %s - Retry prevu a %s" % (
                        new_retry_count, max_retries, error_msg[:100], 
                        next_retry.strftime("%H:%M:%S")
                    )
                    
                    _logger.warning(
                        "Job %s failed (attempt %d/%d), scheduling retry at %s. Checkpoint: line %d. Stats: %d/%d/%d/%d",
                        job.id, new_retry_count, max_retries, next_retry, current_checkpoint,
                        current_created, current_updated, current_skipped, current_errors
                    )
                    
                    try:
                        sql = """
                            UPDATE planete_pim_import_job
                            SET state = %s,
                                error = %s,
                                last_error = %s,
                                retry_count = %s,
                                next_retry_at = %s,
                                checkpoint_row = %s,
                                progress_status = %s,
                                created_count = %s,
                                updated_count = %s,
                                skipped_count = %s,
                                error_count = %s,
                                quarantined_count = %s
                            WHERE id = %s
                        """
                        self.env.cr.execute(sql, (
                            'retry_pending', 
                            error_msg, 
                            error_msg,
                            new_retry_count, 
                            next_retry, 
                            current_checkpoint,
                            progress_msg,
                            current_created,
                            current_updated,
                            current_skipped,
                            current_errors,
                            current_quarantined,
                            job.id
                        ))
                        self.env.cr.commit()
                    except Exception as write_err:
                        _logger.error("Failed to schedule retry for job %s: %s", job.id, write_err)
                else:
                    # Max retries reached - mark as failed definitively
                    # Use simple string to avoid DB queries when cursor may be closed
                    progress_msg = "Echec definitif apres %d tentatives: %s" % (max_retries, error_msg[:150])
                    
                    _logger.error("Job %s failed definitively after %d attempts. Final stats: %d/%d/%d/%d", 
                                 job.id, max_retries, current_created, current_updated, current_skipped, current_errors)
                    
                    try:
                        sql = """
                            UPDATE planete_pim_import_job
                            SET state = %s,
                                error = %s,
                                last_error = %s,
                                finished_at = %s,
                                progress_status = %s,
                                created_count = %s,
                                updated_count = %s,
                                skipped_count = %s,
                                error_count = %s,
                                quarantined_count = %s
                            WHERE id = %s
                        """
                        self.env.cr.execute(sql, (
                            'failed', 
                            error_msg, 
                            error_msg,
                            fields.Datetime.now(), 
                            progress_msg,
                            current_created,
                            current_updated,
                            current_skipped,
                            current_errors,
                            current_quarantined,
                            job.id
                        ))
                        self.env.cr.commit()
                    except Exception as write_err:
                        _logger.error("Failed to record job error state: %s", write_err)
        return True

    @api.model
    def _cron_process_import_jobs(self):
        """Cron runner: process pending jobs and retry-pending jobs.
        
        Processes:
        1. Jobs in 'pending' state (new jobs)
        2. Jobs in 'retry_pending' state where next_retry_at has passed
        
        Runs jobs one by one to avoid long transactions and memory issues.
        """
        now = fields.Datetime.now()
        
        # 1. Process new pending jobs
        pending_jobs = self.search([("state", "=", "pending")], limit=2, order="id asc")
        
        # 2. Process retry-pending jobs that are due
        retry_jobs = self.search([
            ("state", "=", "retry_pending"),
            "|",
            ("next_retry_at", "=", False),
            ("next_retry_at", "<=", now),
        ], limit=2, order="id asc")

        # 2b. Process paused jobs that are due (pause planifiée)
        paused_jobs = self.search([
            ("state", "=", "paused"),
            "|",
            ("next_retry_at", "=", False),
            ("next_retry_at", "<=", now),
        ], limit=2, order="id asc")
        
        # Combine and process (pending first, then retries)
        all_jobs = pending_jobs + retry_jobs + paused_jobs
        
        for job in all_jobs:
            try:
                # ✅ FIX RACE CONDITION: Check if job still exists before accessing attributes
                # Jobs can be deleted by split cleanup while cron is running
                if not job.exists():
                    _logger.warning("Cron: Job %s no longer exists (deleted by split cleanup), skipping", job.id)
                    continue
                
                _logger.info(
                    "Cron processing job %s (state=%s, retry_count=%d)",
                    job.id, job.state, job.retry_count
                )
                job.run_job()
            except Exception:
                _logger.exception("Failed to run job %s from cron", job.id)
        
        # 3. Clean up stale 'running' jobs that stopped making progress
        # ✅ FIX: Use write_date instead of started_at to detect truly stale jobs.
        # Progress tracking updates write_date every 50-100 rows, so if write_date
        # hasn't changed in the configured timeout, the job is truly stuck (not just slow).
        # Previously used started_at with 5min timeout which incorrectly flagged
        # active long-running imports (e.g., Digital 11k+ products at 97%).
        # 
        # ✅ CONFIGURABLE: Timeout is now configurable via system parameter
        # Default: 5 minutes (middle of 3-5 minute range)
        try:
            stale_timeout_minutes = int(
                self.env['ir.config_parameter'].sudo().get_param(
                    'planete_pim.job_stale_timeout_minutes', 
                    '5'
                )
            )
            # Clamp to reasonable range (1-60 minutes)
            stale_timeout_minutes = max(1, min(60, stale_timeout_minutes))
        except (ValueError, TypeError):
            stale_timeout_minutes = 5
        
        stale_cutoff = now - timedelta(minutes=stale_timeout_minutes)
        stale_jobs = self.search([
            ("state", "=", "running"),
            ("write_date", "<", stale_cutoff),
        ], limit=5)
        
        for stale_job in stale_jobs:
            try:
                last_update_ago = int((now - stale_job.write_date).total_seconds() / 60)
                _logger.warning(
                    "Recovering stale job %s (last update %d min ago, progress=%.1f%%, %d/%d lines)",
                    stale_job.id, last_update_ago, stale_job.progress,
                    stale_job.progress_current, stale_job.progress_total
                )
                # Mark as retry_pending to trigger a retry - use simple strings to avoid DB queries
                stale_job.write({
                    "state": "retry_pending",
                    "error": "Job bloque apres %dmin sans progres - retry automatique" % last_update_ago,
                    "last_error": "Timeout detecte - aucune progression depuis %d minutes" % last_update_ago,
                    "next_retry_at": now + timedelta(minutes=1),
                    "progress_status": "Recuperation automatique - retry en cours...",
                })
            except Exception:
                _logger.exception("Failed to recover stale job %s", stale_job.id)
        
        # 4. ⚠️ FIX 3: Force completion of jobs at 99%+ stuck in 'running' state (MORE AGGRESSIVE)
        # Jobs at 99%+ should be marked as done if they've been running for > 3 minutes
        # This is MUCH more aggressive to fix the Ingram stuck job issue
        # NOTE: We use 99.0 because 154000/154006 = 99.996% which is < 100.0
        completed_stuck_jobs = self.search([
            ("state", "=", "running"),
            ("progress", ">=", 99.0),  # ⚠️ Changed from 100.0 to 99.0 to catch near-complete jobs
            ("started_at", "<", now - timedelta(minutes=3)),
        ], limit=20)
        
        for stuck_job in completed_stuck_jobs:
            try:
                elapsed_minutes = int((now - stuck_job.started_at).total_seconds() / 60)
                _logger.warning(
                    "🔧 [AUTO-FIX] Completing stuck job %s at %.1f%% (stuck for %d minutes, started at %s)",
                    stuck_job.id, stuck_job.progress, elapsed_minutes, stuck_job.started_at
                )
                stuck_job.write({
                    "state": "done",
                    "finished_at": now,
                    "progress_status": f"Termine automatiquement (bloque a {stuck_job.progress:.1f}%% pendant {elapsed_minutes}m)",
                })
            except Exception:
                _logger.exception("Failed to complete stuck job %s", stuck_job.id)
        
        # 5. ⚠️ FIX 4: Force cancellation of jobs stuck in 'running' state with no progress for > 30 min
        # ✅ FIX: Use write_date instead of started_at to avoid killing active long-running imports
        dead_stuck_jobs = self.search([
            ("state", "=", "running"),
            ("write_date", "<", now - timedelta(minutes=30)),
        ], limit=20)
        
        for dead_job in dead_stuck_jobs:
            try:
                elapsed_minutes = int((now - dead_job.started_at).total_seconds() / 60)
                _logger.error(
                    "🔧 [FORCE-CANCEL] Job %s stuck for %d minutes (>30min timeout) - CANCELLING",
                    dead_job.id, elapsed_minutes
                )
                dead_job.write({
                    "state": "failed",
                    "finished_at": now,
                    "error": f"Job timeout: stuck in running state for {elapsed_minutes} minutes",
                    "progress_status": f"ANNULE - Job bloque {elapsed_minutes}m (timeout 30min)",
                })
            except Exception:
                _logger.exception("Failed to cancel dead stuck job %s", dead_job.id)
        
        return True

    def action_force_retry(self):
        """Action manuelle pour forcer un retry immédiat sur un job échoué."""
        for job in self:
            if job.state in ("failed", "retry_pending"):
                job.write({
                    "state": "retry_pending",
                    "retry_count": 0,  # Reset retry count
                    "next_retry_at": fields.Datetime.now(),
                    "error": False,
                    "progress_status": "Retry force par utilisateur",
                })
        return True

    def action_cancel_job(self):
        """Annuler un job en attente ou en retry."""
        for job in self:
            if job.state in ("pending", "retry_pending", "running"):
                job.write({
                    "state": "failed",
                    "error": "Annule manuellement",
                    "progress_status": "Annule par utilisateur",
                    "finished_at": fields.Datetime.now(),
                })
        return True
