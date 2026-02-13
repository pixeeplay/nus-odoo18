# -*- coding: utf-8 -*-
from odoo import api, fields, models, _


class FtpProvider(models.Model):
    _inherit = "ftp.provider"

    # =====================
    # Compteurs / Stats
    # =====================
    pim_run_count = fields.Integer(
        string="Exécutions PIM",
        compute="_compute_pim_run_count",
        store=False,
    )

    # =====================
    # FULL (Création produits) - 1x/jour
    # =====================
    schedule_pim_full_daily = fields.Boolean(
        string="[FULL] Import création 1x/jour",
        default=False,
        help="Si coché, un cron lancera un import PIM FULL (création produits uniquement) une fois par jour.",
    )
    pim_full_hour = fields.Integer(
        string="Heure import FULL (0-23)",
        default=4,
        help="Heure locale à laquelle lancer l'import FULL quotidien (ex: 4 pour 04:00)."
    )
    pim_last_full_date = fields.Datetime(
        string="Dernier import FULL",
        readonly=True
    )
    pim_full_cron_running = fields.Boolean(
        string="FULL en cours",
        default=False,
        help="Guard: empêche les exécutions concurrentes du cron FULL."
    )

    # =====================
    # DELTA (MAJ prix/stock) - 3x/jour
    # =====================
    schedule_pim_delta_daily = fields.Boolean(
        string="[DELTA] Import prix/stock 3x/jour",
        default=False,
        help="Si coché, un cron lancera un import PIM DELTA (mise à jour prix et stock uniquement) 3x par jour.",
    )
    pim_delta_hours = fields.Char(
        string="Heures import DELTA",
        default="9,13,17",
        help="Heures séparées par virgule pour l'import DELTA (ex: 9,13,17 pour 09:00, 13:00, 17:00)."
    )
    pim_last_delta_date = fields.Datetime(
        string="Dernier import DELTA",
        readonly=True
    )
    pim_delta_cron_running = fields.Boolean(
        string="DELTA en cours",
        default=False,
        help="Guard: empêche les exécutions concurrentes du cron DELTA."
    )

    # =====================
    # Barre de progression
    # =====================
    pim_progress = fields.Float(
        string="Progression (%)",
        default=0.0,
        help="Pourcentage de progression de l'import en cours."
    )
    pim_progress_total = fields.Integer(
        string="Lignes totales",
        default=0,
        help="Nombre total de lignes à traiter."
    )
    pim_progress_current = fields.Integer(
        string="Lignes traitées",
        default=0,
        help="Nombre de lignes déjà traitées."
    )
    pim_progress_status = fields.Char(
        string="Statut progression",
        default="",
        help="Message de statut de l'import en cours."
    )
    pim_estimated_remaining = fields.Char(
        string="Temps restant estimé",
        compute="_compute_pim_estimated_remaining",
        store=False,
    )

    # =====================
    # Statut Planification
    # =====================
    planning_status = fields.Selection(
        [
            ("ok", "✅ OK - Import < 24h"),
            ("warning", "🟠 Orange - Pas de mapping ou import > 24h"),
            ("error", "🔴 Erreur - Connexion échouée"),
        ],
        string="Statut",
        compute="_compute_planning_status",
        store=False,
        help="Statut du provider basé sur la connexion et l'historique d'import",
    )

    # =====================
    # Options communes
    # =====================
    pim_latest_only = fields.Boolean(
        string="Ne traiter que le dernier fichier",
        default=True,
        help="Si coché, l'import ne traitera que le fichier le plus récent listé sur le FTP."
    )
    pim_mapping_json = fields.Text(
        string="Mapping JSON PIM",
        help="Mapping des colonnes CSV vers les champs Odoo. Exemple:\n"
             "{\n"
             '  "ean": "EAN",\n'
             '  "ref": "SKU",\n'
             '  "name": "Description",\n'
             '  "price": "PurchasePrice",\n'
             '  "stock": "Stock"\n'
             "}"
    )
    pim_delimiter_regex = fields.Char(
        string="Regex délimiteur PIM",
        help="Expression régulière pour découper les colonnes lors des imports PIM. Exemple: \\s{2,} pour des colonnes séparées par plusieurs espaces. Laissez vide pour utiliser le délimiteur simple."
    )

    def _compute_pim_run_count(self):
        Run = self.env["planete.pim.plan.run"].sudo()
        counts = {prov_id: 0 for prov_id in self.ids}
        if self.ids:
            data = Run.read_group(
                domain=[("provider_id", "in", self.ids)],
                fields=["provider_id"],
                groupby=["provider_id"],
            )
            for row in data:
                prov_id = row["provider_id"][0]
                # Odoo 18 read_group returns count as <field>_count; older
                # code sometimes used '__count'. Be robust across versions.
                count = row.get("__count", row.get("provider_id_count", 0)) or 0
                counts[prov_id] = count
        for rec in self:
            rec.pim_run_count = counts.get(rec.id, 0)

    @api.depends("pim_progress_total", "pim_progress_current", "pim_last_full_date", "pim_last_delta_date")
    def _compute_pim_estimated_remaining(self):
        """Calcule le temps restant estimé basé sur la moyenne de ~800 secondes pour un import complet."""
        for rec in self:
            if rec.pim_progress_total > 0 and rec.pim_progress_current > 0:
                # Estimation basée sur le ratio de progression
                # En moyenne ~800 secondes pour un import complet
                avg_total_seconds = 800
                progress_ratio = rec.pim_progress_current / rec.pim_progress_total
                if progress_ratio > 0:
                    estimated_total = avg_total_seconds  # On garde la moyenne comme référence
                    remaining_seconds = int(estimated_total * (1 - progress_ratio))
                    if remaining_seconds > 60:
                        minutes = remaining_seconds // 60
                        seconds = remaining_seconds % 60
                        rec.pim_estimated_remaining = _("%d min %d sec") % (minutes, seconds)
                    else:
                        rec.pim_estimated_remaining = _("%d sec") % remaining_seconds
                else:
                    rec.pim_estimated_remaining = _("Calcul en cours...")
            elif rec.pim_full_cron_running or rec.pim_delta_cron_running:
                rec.pim_estimated_remaining = _("Démarrage...")
            else:
                rec.pim_estimated_remaining = ""

    @api.depends(
        "last_connection_status",
        "mapping_template_id",
        "last_run_at",
        "pim_last_full_date",
        "pim_last_delta_date",
    )
    def _compute_planning_status(self):
        """Calcule le statut de Planification du provider.
        
        🟢 Vert: Connexion OK + Import réussi depuis < 24h
        🟠 Orange: Connexion OK + (pas de mapping OU import > 24h)
        🔴 Rouge: Connexion échouée
        """
        from datetime import timedelta
        now = fields.Datetime.now()
        
        for rec in self:
            # Vérifier l'état de la connexion d'abord
            if rec.last_connection_status == "failed":
                # Connexion échouée
                rec.planning_status = "error"
            elif not rec.mapping_template_id:
                # Pas de mapping configuré
                rec.planning_status = "warning"
            else:
                # Utiliser la dernière exécution la plus récente (FTP ou PIM)
                last_dt = rec.last_run_at
                if rec.pim_last_full_date and (not last_dt or rec.pim_last_full_date > last_dt):
                    last_dt = rec.pim_last_full_date
                if rec.pim_last_delta_date and (not last_dt or rec.pim_last_delta_date > last_dt):
                    last_dt = rec.pim_last_delta_date

                if last_dt and (now - last_dt) <= timedelta(hours=24):
                    rec.planning_status = "ok"
                else:
                    rec.planning_status = "warning"

    # =====================
    # Actions manuelles
    # =====================
    def action_pim_execute_now(self):
        """Créer une exécution PIM liée à ce provider et la lancer immédiatement (legacy)."""
        self.ensure_one()
        Run = self.env["planete.pim.plan.run"].sudo()
        run = Run.create({
            "name": _("Exécution PIM - %s") % (self.name or self.id),
            "provider_id": self.id,
            "started_at": fields.Datetime.now(),
            "status": "pending",
        })
        run.action_execute_now()
        return False

    def action_pim_execute_full_now(self):
        """[FULL] Lancer manuellement un import de création de produits via job asynchrone."""
        self.ensure_one()
        
        # Vérifier qu'un job n'est pas déjà en cours pour ce provider
        Job = self.env["planete.pim.import.job"].sudo()
        running_jobs = Job.search([
            ("provider_id", "=", self.id),
            ("state", "in", ["pending", "running"]),
        ], limit=1)
        if running_jobs:
            from odoo.exceptions import UserError
            raise UserError(_("Un job d'import est déjà en cours ou en attente pour ce fournisseur. Veuillez attendre qu'il se termine."))
        
        # Créer un job asynchrone
        job = Job.create({
            "name": _("[FULL] %s - %s") % (self.name or "Provider", fields.Datetime.now().strftime("%Y-%m-%d %H:%M")),
            "provider_id": self.id,
            "import_mode": "full",
            "state": "pending",
            "progress_status": _("En attente de traitement..."),
        })
        
        # Commit pour que le job soit visible immédiatement
        try:
            self.env.cr.commit()
        except Exception:
            pass
        
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Job FULL créé"),
                "message": _("Un job d'import FULL a été créé (ID: %s). Il sera traité automatiquement par le cron. Consultez les 'Jobs d'import' pour suivre la progression.") % job.id,
                "sticky": False,
                "type": "success",
                "next": {
                    "type": "ir.actions.act_window",
                    "res_model": "planete.pim.import.job",
                    "res_id": job.id,
                    "view_mode": "form",
                    "views": [(False, "form")],
                    "target": "current",
                },
            },
        }

    def action_pim_execute_delta_now(self):
        """[DELTA] Lancer manuellement une mise à jour des prix et stocks via job asynchrone."""
        self.ensure_one()
        
        # Vérifier qu'un job n'est pas déjà en cours pour ce provider
        Job = self.env["planete.pim.import.job"].sudo()
        running_jobs = Job.search([
            ("provider_id", "=", self.id),
            ("state", "in", ["pending", "running"]),
        ], limit=1)
        if running_jobs:
            from odoo.exceptions import UserError
            raise UserError(_("Un job d'import est déjà en cours ou en attente pour ce fournisseur. Veuillez attendre qu'il se termine."))
        
        # Créer un job asynchrone
        job = Job.create({
            "name": _("[DELTA] %s - %s") % (self.name or "Provider", fields.Datetime.now().strftime("%Y-%m-%d %H:%M")),
            "provider_id": self.id,
            "import_mode": "delta",
            "state": "pending",
            "progress_status": _("En attente de traitement..."),
        })
        
        # Commit pour que le job soit visible immédiatement
        try:
            self.env.cr.commit()
        except Exception:
            pass
        
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Job DELTA créé"),
                "message": _("Un job d'import DELTA a été créé (ID: %s). Il sera traité automatiquement par le cron. Consultez les 'Jobs d'import' pour suivre la progression.") % job.id,
                "sticky": False,
                "type": "success",
                "next": {
                    "type": "ir.actions.act_window",
                    "res_model": "planete.pim.import.job",
                    "res_id": job.id,
                    "view_mode": "form",
                    "views": [(False, "form")],
                    "target": "current",
                },
            },
        }

    def action_pim_refresh_content(self):
        """[REFRESH] Lancer manuellement une mise à jour du contenu des produits existants via job asynchrone.
        
        Ce bouton permet de ré-appliquer le mapping template sur tous les produits existants.
        Utile quand le mapping template a été modifié et qu'on veut mettre à jour les champs
        (name, description, marque, etc.) des produits déjà importés.
        
        Ne crée PAS de nouveaux produits.
        Ne modifie PAS les prix/stocks (c'est DELTA qui fait ça).
        """
        self.ensure_one()
        
        # Vérifier qu'un job n'est pas déjà en cours pour ce provider
        Job = self.env["planete.pim.import.job"].sudo()
        running_jobs = Job.search([
            ("provider_id", "=", self.id),
            ("state", "in", ["pending", "running"]),
        ], limit=1)
        if running_jobs:
            from odoo.exceptions import UserError
            raise UserError(_("Un job d'import est déjà en cours ou en attente pour ce fournisseur. Veuillez attendre qu'il se termine."))
        
        # Vérifier qu'un mapping template est configuré
        if not self.mapping_template_id:
            from odoo.exceptions import UserError
            raise UserError(_("Aucun template de mapping n'est configuré pour ce fournisseur. Veuillez configurer un mapping template avant d'utiliser cette fonction."))
        
        # Créer un job asynchrone
        job = Job.create({
            "name": _("[REFRESH] %s - %s") % (self.name or "Provider", fields.Datetime.now().strftime("%Y-%m-%d %H:%M")),
            "provider_id": self.id,
            "import_mode": "refresh_content",
            "state": "pending",
            "progress_status": _("En attente de traitement..."),
        })
        
        # Commit pour que le job soit visible immédiatement
        try:
            self.env.cr.commit()
        except Exception:
            pass
        
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Job REFRESH créé"),
                "message": _("Un job de rafraîchissement du contenu a été créé (ID: %s). Il appliquera le mapping template '%s' à tous les produits existants trouvés dans le fichier FTP.") % (job.id, self.mapping_template_id.name),
                "sticky": False,
                "type": "success",
                "next": {
                    "type": "ir.actions.act_window",
                    "res_model": "planete.pim.import.job",
                    "res_id": job.id,
                    "view_mode": "form",
                    "views": [(False, "form")],
                    "target": "current",
                },
            },
        }

    def action_pim_stop_import(self):
        """Arrêter l'import en cours (reset des flags)."""
        self.ensure_one()
        self.sudo().write({
            "pim_full_cron_running": False,
            "pim_delta_cron_running": False,
            "pim_progress_status": _("Import arrêté manuellement"),
        })
        return True

    def action_reset_all_guards(self):
        """Reset tous les guards PIM bloqués sur TOUS les providers."""
        providers = self.sudo().search([
            "|",
            ("pim_full_cron_running", "=", True),
            ("pim_delta_cron_running", "=", True),
        ])
        
        count = len(providers)
        if count > 0:
            providers.write({
                "pim_full_cron_running": False,
                "pim_delta_cron_running": False,
                "pim_progress_status": _("Guards reset manuellement le %s") % fields.Datetime.now(),
            })
            self.env.cr.commit()
        
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Guards PIM Reset"),
                "message": _("%d provider(s) avaient des guards bloqués et ont été reset.") % count,
                "sticky": False,
                "type": "warning" if count > 0 else "success",
            },
        }

    def action_force_full_cron_now(self):
        """Force l'exécution du cron FULL immédiatement (ignore les checks d'heure/date)."""
        self.ensure_one()
        
        # Reset le guard d'abord
        self.sudo().write({
            "pim_full_cron_running": False,
            "pim_last_full_date": False,  # Reset la date pour permettre l'exécution
        })
        self.env.cr.commit()
        
        # Créer un job FULL directement
        Job = self.env["planete.pim.import.job"].sudo()
        job = Job.create({
            "name": _("[FULL FORCÉ] %s - %s") % (self.name or "Provider", fields.Datetime.now().strftime("%Y-%m-%d %H:%M")),
            "provider_id": self.id,
            "import_mode": "full",
            "state": "pending",
            "progress_status": _("Créé manuellement - en attente du cron job runner..."),
        })
        self.env.cr.commit()
        
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Job FULL Forcé"),
                "message": _("Job FULL créé (ID: %s). Il sera exécuté par le cron '[PIM] Import Job Runner' dans la minute.") % job.id,
                "sticky": True,
                "type": "success",
                "next": {
                    "type": "ir.actions.act_window",
                    "res_model": "planete.pim.import.job",
                    "res_id": job.id,
                    "view_mode": "form",
                    "views": [(False, "form")],
                    "target": "current",
                },
            },
        }

    def action_open_pim_runs(self):
        """Ouvrir les exécutions PIM filtrées sur ce provider."""
        self.ensure_one()
        action = self.env.ref("planete_pim.action_planete_pim_plan_run").read()[0]
        domain = action.get("domain") or []
        domain = [("provider_id", "=", self.id)] + domain
        action["domain"] = domain
        ctx = dict(self.env.context or {})
        ctx.update({"default_provider_id": self.id})
        action["context"] = ctx
        return action
