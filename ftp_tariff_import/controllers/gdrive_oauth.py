# -*- coding: utf-8 -*-
import json
import logging
import requests
from datetime import datetime, timedelta

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)

GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"


class GoogleDriveOAuthController(http.Controller):

    @http.route("/gdrive/oauth/callback", type="http", auth="user", website=True)
    def gdrive_oauth_callback(self, code=None, state=None, error=None, **kwargs):
        """Handle OAuth callback from Google."""
        if error:
            _logger.error("Google Drive OAuth error: %s", error)
            return request.redirect("/web#action=display_notification&title=Erreur&message=" + str(error))

        if not code or not state:
            _logger.error("Google Drive OAuth: missing code or state")
            return request.redirect("/web#action=display_notification&title=Erreur&message=Paramètres manquants")

        # Parse state: provider_id_random_state
        try:
            parts = state.split("_", 1)
            provider_id = int(parts[0])
            auth_state = parts[1] if len(parts) > 1 else ""
        except (ValueError, IndexError) as e:
            _logger.error("Google Drive OAuth: invalid state format: %s", e)
            return request.redirect("/web?#error=invalid_state")

        # Find the provider
        Provider = request.env["ftp.provider"].sudo()
        provider = Provider.browse(provider_id)
        if not provider.exists():
            _logger.error("Google Drive OAuth: provider %s not found", provider_id)
            return request.redirect("/web?#error=provider_not_found")

        # Verify state matches
        if provider.gdrive_auth_state != auth_state:
            _logger.error("Google Drive OAuth: state mismatch for provider %s", provider_id)
            return request.redirect("/web?#error=state_mismatch")

        # Exchange code for tokens
        base_url = request.env["ir.config_parameter"].sudo().get_param("web.base.url", "")
        redirect_uri = f"{base_url}/gdrive/oauth/callback"

        try:
            token_response = requests.post(
                GOOGLE_TOKEN_URL,
                data={
                    "code": code,
                    "client_id": provider.gdrive_client_id,
                    "client_secret": provider.gdrive_client_secret,
                    "redirect_uri": redirect_uri,
                    "grant_type": "authorization_code",
                },
                timeout=30,
            )
            token_data = token_response.json()

            if "error" in token_data:
                _logger.error("Google Drive OAuth token error: %s", token_data)
                return request.redirect("/web?#error=" + token_data.get("error", "unknown"))

            # Store tokens
            access_token = token_data.get("access_token")
            refresh_token = token_data.get("refresh_token")
            expires_in = token_data.get("expires_in", 3600)
            token_expiry = datetime.now() + timedelta(seconds=expires_in - 60)

            provider.write({
                "gdrive_access_token": access_token,
                "gdrive_refresh_token": refresh_token,
                "gdrive_token_expiry": token_expiry,
                "gdrive_auth_state": False,  # Clear state after use
            })

            _logger.info("Google Drive OAuth: successfully authorized provider %s", provider_id)

            # Redirect back to provider form
            return request.redirect(f"/web#id={provider_id}&model=ftp.provider&view_type=form")

        except requests.RequestException as e:
            _logger.error("Google Drive OAuth: request error: %s", e)
            return request.redirect("/web?#error=request_failed")
        except Exception as e:
            _logger.exception("Google Drive OAuth: unexpected error: %s", e)
            return request.redirect("/web?#error=unexpected_error")
