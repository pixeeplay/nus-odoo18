# -*- coding: utf-8 -*-
import fnmatch
import io
import os
import posixpath
import socket
import tempfile
import time
import re
from datetime import datetime
import hmac
import hashlib

from odoo import api, models, _
from odoo.exceptions import UserError

import logging

_logger = logging.getLogger(__name__)


def sanitize_null_bytes(value):
    """Remove null bytes (0x00) from string values to prevent PostgreSQL errors.
    
    PostgreSQL does not accept null bytes in string literals, so this function
    sanitizes data coming from external sources (FTP files, CSV data, etc.)
    """
    if value is None:
        return value
    if isinstance(value, str):
        return value.replace('\x00', '')
    if isinstance(value, bytes):
        return value.replace(b'\x00', b'')
    return value


def sanitize_dict(d, keys=None):
    """Sanitize string values in a dictionary by removing null bytes.
    
    Args:
        d: Dictionary to sanitize
        keys: Optional list of keys to sanitize. If None, sanitize all string values.
    """
    if not d or not isinstance(d, dict):
        return d
    result = dict(d)
    for k, v in result.items():
        if keys is not None and k not in keys:
            continue
        if isinstance(v, str):
            result[k] = sanitize_null_bytes(v)
    return result

try:
    import ftplib  # stdlib FTP/FTPS
except Exception:  # pragma: no cover
    ftplib = None

try:
    import paramiko  # SFTP
except Exception:  # pragma: no cover
    paramiko = None

try:
    import imaplib  # stdlib IMAP
    import email
    from email import policy
    from email.utils import parsedate_to_datetime
except Exception:  # pragma: no cover
    imaplib = None
    email = None
    policy = None
    parsedate_to_datetime = None

try:
    import requests  # for Google Drive API
except Exception:  # pragma: no cover
    requests = None

# Google Drive API constants
GOOGLE_DRIVE_API_BASE = "https://www.googleapis.com/drive/v3"
GOOGLE_UPLOAD_API_BASE = "https://www.googleapis.com/upload/drive/v3"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"


class _BaseBackend(object):
    """Abstract backend API for FTP/SFTP/IMAP providers."""

    def __init__(self, provider, env):
        self.provider = provider
        self.env = env

    # Context manager helpers
    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            self.close()
        except Exception as e:
            _logger.warning("Error closing backend: %s", e)

    # API to implement
    def connect(self):
        raise NotImplementedError

    def close(self):
        raise NotImplementedError

    def list_files(self, remote_dir, pattern=None, exclude=None, limit=None):
        """Return list of dicts:
        [{'path': '/incoming/file.csv', 'name': 'file.csv', 'size': 123, 'mtime': 1700000000.0}]
        """
        raise NotImplementedError

    def download(self, remote_path, local_path):
        raise NotImplementedError

    def ensure_dir(self, remote_dir):
        raise NotImplementedError

    def move(self, remote_path, dst_dir):
        """Move remote_path to dst_dir keeping basename; return new path."""
        raise NotImplementedError

    def mark_seen(self, remote_path):
        """Optional: mark a message/file as Seen on remote (IMAP only). Default: no-op."""
        return False

    # Utilities
    def _match_patterns(self, name, pattern=None, exclude=None):
        ok = True
        if pattern:
            ok = fnmatch.fnmatch(name, pattern)
        if ok and exclude:
            ok = not fnmatch.fnmatch(name, exclude)
        return ok

    @staticmethod
    def _now_ts():
        return time.time()


class _FTPBackend(_BaseBackend):
    """Basic FTP backend using ftplib."""

    def __init__(self, provider, env):
        super().__init__(provider, env)
        self.ftp = None

    def connect(self):
        if ftplib is None:
            raise UserError(_("ftplib not available in this environment."))
        timeout = self.provider.timeout or 60
        host = re.sub(r'^\w+://', '', (self.provider.host or '').strip())
        port = self.provider.port or 21
        use_tls = bool(getattr(self.provider, 'ftp_use_tls', False))
        
        # =====================================================================
        # STRATÉGIE DE CONNEXION (comme FileZilla):
        # 1. Si ftp_use_tls=True  → Explicit FTPS (AUTH TLS) obligatoire
        # 2. Si ftp_use_tls=False → FTP classique non sécurisé
        # 3. Si ftp_use_tls=False ET la connexion échoue → tenter TLS automatiquement
        # =====================================================================
        
        if use_tls:
            self._connect_ftps(host, port, timeout)
        else:
            # Essayer FTP classique d'abord
            try:
                self._connect_plain_ftp(host, port, timeout)
            except Exception as plain_err:
                # Si FTP classique échoue, tenter automatiquement FTPS
                # (comme FileZilla "Use explicit FTP over TLS if available")
                _logger.warning(
                    "FTP plain failed for %s:%d (%s), trying FTPS auto-detect...",
                    host, port, plain_err
                )
                try:
                    self._connect_ftps(host, port, timeout)
                    _logger.info(
                        "FTP auto-detect: FTPS worked for %s:%d! "
                        "💡 Astuce: activez 'FTP TLS/SSL' dans la config du provider pour éviter ce fallback.",
                        host, port
                    )
                except Exception as tls_err:
                    # Les deux échouent -> remonter l'erreur originale avec diagnostic
                    _logger.error(
                        "FTP connect FAILED for %s:%d - Plain FTP: %s | FTPS: %s",
                        host, port, plain_err, tls_err
                    )
                    raise UserError(_(
                        "Connexion FTP impossible à %s:%d.\n\n"
                        "• FTP classique: %s\n"
                        "• FTPS (TLS): %s\n\n"
                        "💡 Vérifiez:\n"
                        "  - Le hostname et le port sont corrects\n"
                        "  - Le serveur accepte les connexions FTP\n"
                        "  - Essayez d'activer/désactiver 'FTP TLS/SSL (FTPS)'\n"
                        "  - Vérifiez le mode passif (activé par défaut)"
                    ) % (host, port, plain_err, tls_err))
        
        # Passive mode for firewalls if set
        try:
            self.ftp.set_pasv(bool(self.provider.ftp_passive))
        except Exception:
            pass
        try:
            self.ftp.sock.settimeout(timeout)
        except Exception:
            pass

    def _connect_plain_ftp(self, host, port, timeout):
        """Connexion FTP classique (non sécurisé)."""
        _logger.info("FTP plain: connecting to %s:%d...", host, port)
        self.ftp = ftplib.FTP()
        self.ftp.connect(host, port, timeout=timeout)
        if self.provider.username:
            self.ftp.login(self.provider.username, self.provider.password or "")
        else:
            self.ftp.login()
        _logger.info("FTP plain: connected to %s:%d ✅", host, port)

    def _connect_ftps(self, host, port, timeout):
        """Connexion FTPS (FTP over TLS/SSL) - Explicit TLS (AUTH TLS)."""
        _logger.info("FTP TLS: connecting to %s:%d with explicit TLS...", host, port)
        import ssl
        # Contexte SSL permissif (certains serveurs ont des certificats auto-signés)
        ssl_context = ssl.create_default_context()
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE
        self.ftp = ftplib.FTP_TLS(context=ssl_context)
        self.ftp.connect(host, port, timeout=timeout)
        if self.provider.username:
            self.ftp.login(self.provider.username, self.provider.password or "")
        else:
            self.ftp.login()
        # Sécuriser le canal de données (obligatoire pour FTPS)
        self.ftp.prot_p()
        _logger.info("FTP TLS: connected and data channel secured (PROT P) ✅")

    def close(self):
        if self.ftp:
            try:
                self.ftp.quit()
            except Exception:
                try:
                    self.ftp.close()
                except Exception:
                    pass
            self.ftp = None

    def _cwd(self, path):
        # Change working directory safely, creating if needed is caller's job
        parts = [p for p in path.split("/") if p and p != "."]
        if path.startswith("/"):
            # go to root
            try:
                self.ftp.cwd("/")
            except Exception:
                pass
        for p in parts:
            self.ftp.cwd(p)

    def ensure_dir(self, remote_dir):
        # Create remote directories recursively if they do not exist
        parts = [p for p in remote_dir.split("/") if p and p != "."]
        # start from root if absolute
        if remote_dir.startswith("/"):
            try:
                self.ftp.cwd("/")
            except Exception:
                pass
        for p in parts:
            try:
                self.ftp.mkd(p)
            except Exception:
                # likely exists
                pass
            self.ftp.cwd(p)

    def list_files(self, remote_dir, pattern=None, exclude=None, limit=None):
        if not remote_dir:
            remote_dir = "/"
        
        # =====================================================================
        # STRATÉGIE DE RÉPERTOIRE (améliorée pour Exertis et serveurs chroot):
        # Certains serveurs FTP utilisent un chroot vers le home directory.
        # Si remote_dir="/" et qu'on ne trouve rien, on essaie:
        # 1. "." (home directory courant)
        # 2. "" (empty string, certains serveurs FTP)
        # 3. PWD actuel sans CWD
        # =====================================================================
        dirs_to_try = [remote_dir]
        if remote_dir == "/":
            dirs_to_try.extend([".", ""])  # fallback: home directory
        
        _logger.info(
            "[FTP-LIST] 🔍 Début listing pour provider (pattern='%s', exclude='%s', limit=%s)",
            pattern, exclude, limit
        )
        
        for idx, try_dir in enumerate(dirs_to_try, 1):
            _logger.info("[FTP-LIST] Tentative %d/%d: essai répertoire '%s'", idx, len(dirs_to_try), try_dir)
            files = self._list_files_in_dir(try_dir, pattern, exclude)
            
            if files:
                _logger.info(
                    "[FTP-LIST] ✅ SUCCÈS dans '%s': %d fichiers trouvés (pattern='%s')",
                    try_dir, len(files), pattern
                )
                files.sort(key=lambda x: x.get("mtime") or 0, reverse=True)
                if limit:
                    files = files[:int(limit)]
                return files
            
            _logger.info("[FTP-LIST] ❌ 0 fichier dans '%s', tentative suivante...", try_dir)
        
        # Aucun fichier trouvé dans aucun répertoire
        _logger.error(
            "[FTP-LIST] ❌ ÉCHEC: 0 fichiers trouvés après %d tentatives\n"
            "  - Répertoires essayés: %s\n"
            "  - Pattern: %s\n"
            "  - Exclude: %s\n"
            "  💡 DIAGNOSTIC:\n"
            "    1. Vérifiez que 'Répertoire entrant' est correct dans la config du provider\n"
            "    2. Si FileZilla voit les fichiers, notez le chemin EXACT affiché\n"
            "    3. Essayez de laisser 'Répertoire entrant' VIDE ou mettre '.'",
            len(dirs_to_try), dirs_to_try, pattern, exclude
        )
        return []

    def _list_files_in_dir(self, remote_dir, pattern=None, exclude=None):
        """Liste les fichiers dans un répertoire FTP en essayant MLSD, NLST puis LIST."""
        files = []
        try:
            self._cwd(remote_dir)
        except Exception as cwd_err:
            _logger.info("[FTP-LIST] ❌ CWD échoué pour '%s': %s", remote_dir, cwd_err)
            return []

        # =====================================================================
        # STRATÉGIE DE LISTING (ordre de tentative):
        # 1. MLSD - le plus fiable (Python 3)
        # 2. NLST + SIZE/MDTM - compatible serveurs simples
        # 3. LIST (parsing) - dernier recours
        #
        # ✅ FIX: Si MLSD retourne 0 fichiers (mais pas d'exception),
        # on tente quand même NLST/LIST en fallback.
        # =====================================================================
        
        # Tentative 1: MLSD via ftplib.mlsd() (Python 3+)
        mlsd_tried = False
        try:
            mlsd_entries = list(self.ftp.mlsd())
            mlsd_tried = True
            _logger.info(
                "[FTP-LIST] MLSD: %d entrées brutes retournées dans '%s'",
                len(mlsd_entries), remote_dir
            )
            for name, facts in mlsd_entries:
                ftype = (facts.get("type") or "").lower()
                # Accepter "file" et aussi les entrées sans type (certains serveurs)
                if ftype and ftype not in ("file",):
                    continue
                if not name or name in (".", ".."):
                    continue
                if not self._match_patterns(name, pattern, exclude):
                    continue
                size = int(facts.get("size", "0") or "0")
                mtime = facts.get("modify")
                ts = None
                if mtime and len(mtime) >= 14:
                    try:
                        t = time.strptime(mtime[:14], "%Y%m%d%H%M%S")
                        ts = time.mktime(t)
                    except Exception:
                        pass
                files.append({
                    "path": posixpath.join(remote_dir, name),
                    "name": name,
                    "size": size,
                    "mtime": ts or self._now_ts(),
                })
            if files:
                _logger.debug("FTP list: MLSD found %d files", len(files))
                return files
            _logger.debug("FTP list: MLSD returned entries but 0 matching files, trying NLST...")
        except Exception as mlsd_err:
            _logger.debug("FTP list: MLSD not supported (%s), falling back to NLST", mlsd_err)
        
        # Tentative 2: NLST + SIZE/MDTM (compatible serveurs simples/anonymes)
        try:
            # Re-cwd au cas où MLSD aurait changé le répertoire
            if mlsd_tried:
                try:
                    self._cwd(remote_dir)
                except Exception:
                    pass
            names = []
            self.ftp.retrlines("NLST", names.append)
            _logger.debug("FTP list: NLST returned %d entries in '%s'", len(names), remote_dir)
            for name in names:
                name = name.strip()
                if not name or name in (".", ".."):
                    continue
                if not self._match_patterns(name, pattern, exclude):
                    continue
                size = 0
                mtime_ts = self._now_ts()
                try:
                    size = self.ftp.size(name) or 0
                except Exception:
                    pass
                try:
                    resp = self.ftp.sendcmd("MDTM " + name)
                    parts = resp.split()
                    if len(parts) == 2 and parts[0] == "213":
                        t = time.strptime(parts[1][:14], "%Y%m%d%H%M%S")
                        mtime_ts = time.mktime(t)
                except Exception:
                    pass
                files.append({
                    "path": posixpath.join(remote_dir, name),
                    "name": name,
                    "size": int(size),
                    "mtime": mtime_ts,
                })
            if files:
                _logger.debug("FTP list: NLST found %d files", len(files))
                return files
            _logger.debug("FTP list: NLST returned entries but 0 matching files")
        except Exception as nlst_err:
            _logger.warning("FTP list: NLST failed (%s), trying LIST", nlst_err)
        
        # Tentative 3: LIST (parsing du format long)
        try:
            # Re-cwd
            try:
                self._cwd(remote_dir)
            except Exception:
                pass
            list_lines = []
            self.ftp.retrlines("LIST", list_lines.append)
            _logger.debug("FTP list: LIST returned %d lines in '%s'", len(list_lines), remote_dir)
            for line in list_lines:
                line = line.strip()
                if not line:
                    continue
                # Ignorer les dossiers (commencent par 'd')
                if line.startswith("d"):
                    continue
                # Extraire le nom de fichier (dernier champ après les espaces)
                parts = line.split()
                if len(parts) >= 1:
                    name = parts[-1]
                    if name in (".", ".."):
                        continue
                    if not self._match_patterns(name, pattern, exclude):
                        continue
                    # Taille = 5ème champ typiquement (format Unix ls -l)
                    size = 0
                    try:
                        if len(parts) >= 5:
                            size = int(parts[4])
                    except Exception:
                        pass
                    files.append({
                        "path": posixpath.join(remote_dir, name),
                        "name": name,
                        "size": size,
                        "mtime": self._now_ts(),
                    })
            _logger.debug("FTP list: LIST parsing found %d files", len(files))
        except Exception as list_err:
            _logger.error("FTP list: All methods failed (MLSD, NLST, LIST) in '%s': %s", remote_dir, list_err)
        
        return files

    def download(self, remote_path, local_path):
        # Change to directory and RETR basename
        directory = posixpath.dirname(remote_path) or "/"
        basename = posixpath.basename(remote_path)
        self._cwd(directory)
        with open(local_path, "wb") as f:
            self.ftp.retrbinary("RETR " + basename, f.write)

    def move(self, remote_path, dst_dir):
        # Robust move with normalized destination, collision handling and copy+remove fallback
        basename = posixpath.basename(remote_path)
        src_dir = posixpath.dirname(remote_path) or "/"
        # Resolve destination base dir
        if dst_dir and not str(dst_dir).startswith("/"):
            target_base = posixpath.join(src_dir, dst_dir)
        else:
            target_base = dst_dir or src_dir
        # Ensure destination directory exists
        try:
            self.ensure_dir(target_base)
        except Exception:
            pass
        # Build final target path
        new_remote = posixpath.join(target_base, basename)
        # If target exists, suffix with epoch to avoid collision (best-effort via NLST)
        try:
            self._cwd(target_base)
            names = []
            self.ftp.retrlines("NLST", names.append)
            if basename in names:
                _logger.debug("FTP move: target exists %s; will suffix", posixpath.join(target_base, basename))
                new_remote = posixpath.join(target_base, f"{basename}.{int(time.time())}")
        except Exception:
            pass
        # Try fast path: rename
        try:
            self.ftp.rename(remote_path, new_remote)
            _logger.debug("FTP move: rename OK %s -> %s", remote_path, new_remote)
            return new_remote
        except Exception as e:
            _logger.debug("FTP move: rename failed (%s), using copy+remove", e)
            # Fallback: download locally then upload and delete source
            tmp = None
            tmp_path = None
            try:
                tmp = tempfile.NamedTemporaryFile(prefix="ftp_move_", suffix=".dat", delete=False)
                tmp_path = tmp.name
                tmp.close()
                # Download source
                self.download(remote_path, tmp_path)
                # Upload to destination
                try:
                    self._cwd(target_base)
                except Exception:
                    pass
                with open(tmp_path, "rb") as fp:
                    self.ftp.storbinary("STOR " + posixpath.basename(new_remote), fp)
                # Verify size if possible
                try:
                    size_local = os.path.getsize(tmp_path)
                except Exception:
                    size_local = None
                size_remote = None
                try:
                    resp = self.ftp.sendcmd("SIZE " + posixpath.basename(new_remote))
                    # Typical response: "213 <size>"
                    parts = resp.split()
                    for token in parts:
                        try:
                            size_remote = int(token)
                            break
                        except Exception:
                            continue
                except Exception:
                    pass
                if size_local is not None and size_remote is not None and size_remote != size_local:
                    try:
                        self.ftp.delete(posixpath.basename(new_remote))
                    except Exception:
                        pass
                    raise UserError(_("FTP move failed %s -> %s: copy size mismatch") % (remote_path, new_remote))
                # Delete source
                try:
                    try:
                        self._cwd(src_dir)
                    except Exception:
                        pass
                    self.ftp.delete(posixpath.basename(remote_path))
                except Exception as rm_e:
                    _logger.warning("FTP move: uploaded but could not delete source %s: %s", remote_path, rm_e)
                    raise UserError(_("FTP move completed copy but could not delete source %s: %s") % (remote_path, rm_e))
                _logger.debug("FTP move: copy+remove OK %s -> %s", remote_path, new_remote)
                return new_remote
            finally:
                if tmp_path and os.path.exists(tmp_path):
                    try:
                        os.remove(tmp_path)
                    except Exception:
                        pass


class _SFTPBackend(_BaseBackend):
    """SFTP backend using paramiko."""

    def __init__(self, provider, env):
        super().__init__(provider, env)
        self.client = None
        self.sftp = None

    def _build_pkey(self):
        if not self.provider.sftp_pkey_content:
            return None
        pem = self.provider.sftp_pkey_content
        passphrase = self.provider.sftp_pkey_passphrase or None
        key = None
        # Try common key types
        for key_cls in (getattr(paramiko, "RSAKey", None),
                        getattr(paramiko, "Ed25519Key", None),
                        getattr(paramiko, "ECDSAKey", None),
                        getattr(paramiko, "DSAKey", None)):
            if not key_cls:
                continue
            try:
                key = key_cls.from_private_key(io.StringIO(pem), password=passphrase)
                if key:
                    return key
            except Exception:
                continue
        raise UserError(_("Unable to load provided private key."))

    def connect(self):
        if paramiko is None:
            raise UserError(_("paramiko not available in this environment."))
        timeout = self.provider.timeout or 60

        self.client = paramiko.SSHClient()

        # Host key policy
        fingerprint = (self.provider.sftp_hostkey_fingerprint or "").strip()
        if fingerprint:
            # strict checking using provided fingerprint
            class _FingerprintPolicy(paramiko.MissingHostKeyPolicy):
                def missing_host_key(self_inner, client, hostname, key):
                    fp = key.get_fingerprint().hex()
                    if fp.replace(":", "").lower() != fingerprint.replace(":", "").lower():
                        raise UserError(_("SFTP host key fingerprint mismatch. Expected %s, got %s")
                                        % (fingerprint, fp))
                    client._host_keys.add(hostname, key.get_name(), key)
            self.client.set_missing_host_key_policy(_FingerprintPolicy())
        else:
            # Auto add - less strict, but practical if fingerprint unknown
            self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        pkey = self._build_pkey()
        try:
            host = re.sub(r'^\w+://', '', (self.provider.host or '').strip())
            self.client.connect(
                hostname=host,
                port=self.provider.port or 22,
                username=self.provider.username or None,
                password=self.provider.password or None,
                pkey=pkey,
                timeout=timeout,
                banner_timeout=timeout,
                auth_timeout=timeout,
                look_for_keys=False,
                allow_agent=False,
            )
            self.sftp = self.client.open_sftp()
        except Exception as e:
            raise UserError(_("SFTP connect failed: %s") % (e,))

        # Keepalive
        try:
            ka = int(self.provider.keepalive or 0)
            if ka > 0:
                self.client.get_transport().set_keepalive(ka)
        except Exception:
            pass

    def close(self):
        try:
            if self.sftp:
                self.sftp.close()
        except Exception:
            pass
        try:
            if self.client:
                self.client.close()
        except Exception:
            pass
        self.client = None
        self.sftp = None

    def ensure_dir(self, remote_dir):
        # Recursively create directories if missing
        if not remote_dir:
            return
        path = "/"
        for part in [p for p in remote_dir.split("/") if p and p != "."]:
            path = posixpath.join(path, part)
            try:
                self.sftp.listdir(path)
            except IOError:
                try:
                    self.sftp.mkdir(path)
                except Exception:
                    # Another process might have created it
                    pass

    def list_files(self, remote_dir, pattern=None, exclude=None, limit=None):
        req_dir = (remote_dir or "").strip()
        # =====================================================================
        # STRATÉGIE DE RÉPERTOIRE SFTP:
        # Certains serveurs SFTP (comme Exertis) placent l'utilisateur dans
        # son home directory. Si remote_dir="/" contient des dossiers mais
        # pas de fichiers, on essaie aussi le home directory et ".".
        # =====================================================================
        candidates = []
        # Discover home directory as seen by server
        try:
            home = self.sftp.normalize(".")
        except Exception:
            home = ""
        _logger.debug("SFTP list: home=%s, req_dir=%s", home, req_dir)
        # If requested dir is absolute, try it first
        if req_dir:
            if req_dir.startswith("/"):
                candidates.append(req_dir)
            else:
                # Relative to home
                candidates.append(posixpath.join(home, req_dir) if home else req_dir)
        # Generic fallbacks
        for cand in (home, ".", "/"):
            if cand and cand not in candidates:
                candidates.append(cand)

        # ✅ FIX: Essayer CHAQUE candidat jusqu'à trouver des FICHIERS
        # (pas juste des entrées/dossiers comme avant)
        for cand in candidates:
            try:
                entries = self.sftp.listdir_attr(cand)
            except IOError as e:
                _logger.debug("SFTP list: listdir_attr failed for '%s': %s", cand, e)
                continue
            
            files = []
            for attr in entries:
                # Only files (S_ISREG 0o100000)
                try:
                    is_file = (attr.st_mode & 0o170000) == 0o100000
                except Exception:
                    is_file = True
                name = getattr(attr, "filename", None) or ""
                if not name or not is_file:
                    continue
                if not self._match_patterns(name, pattern, exclude):
                    continue
                files.append({
                    "path": posixpath.join(cand, name),
                    "name": name,
                    "size": int(getattr(attr, "st_size", 0) or 0),
                    "mtime": float(getattr(attr, "st_mtime", self._now_ts())),
                })
            
            if files:
                _logger.info("SFTP list: found %d files in '%s'", len(files), cand)
                files.sort(key=lambda x: x.get("mtime") or 0, reverse=True)
                if limit:
                    files = files[:int(limit)]
                return files
            _logger.debug("SFTP list: %d entries but 0 matching files in '%s', trying next...",
                         len(entries), cand)

        _logger.warning("SFTP list: 0 files found (tried: %s, pattern: %s)", candidates, pattern)
        return []

    def download(self, remote_path, local_path):
        self.sftp.get(remote_path, local_path)

    def move(self, remote_path, dst_dir):
        # Robust move with normalized destination, collision handling and copy+remove fallback
        basename = posixpath.basename(remote_path)
        src_dir = posixpath.dirname(remote_path) or "/"
        # Resolve destination base dir
        if dst_dir and not str(dst_dir).startswith("/"):
            target_base = posixpath.join(src_dir, dst_dir)
        else:
            target_base = dst_dir or src_dir
        # Normalize on server if possible
        try:
            target_base = self.sftp.normalize(target_base)
        except Exception:
            pass
        _logger.debug("SFTP move: src=%s, dst_dir=%s -> target_base=%s", remote_path, dst_dir, target_base)
        # Ensure destination directory exists
        try:
            self.ensure_dir(target_base)
        except Exception:
            pass
        # Build final target path
        new_remote = posixpath.join(target_base, basename)
        # If target exists, suffix with epoch to avoid collision
        try:
            self.sftp.stat(new_remote)
            _logger.debug("SFTP move: target exists %s; will suffix", new_remote)
            new_remote = posixpath.join(target_base, f"{basename}.{int(time.time())}")
        except IOError:
            # not existing -> ok
            pass
        # Try fast path: rename
        try:
            _logger.debug("SFTP move: attempt rename %s -> %s", remote_path, new_remote)
            self.sftp.rename(remote_path, new_remote)
            _logger.debug("SFTP move: rename OK %s -> %s", remote_path, new_remote)
            return new_remote
        except Exception as e:
            _logger.debug("SFTP move: rename failed (%s), using copy+remove", e)
            # Fallback: copy then remove source
            try:
                with self.sftp.open(remote_path, "rb") as fin, self.sftp.open(new_remote, "wb") as fout:
                    bufsize = 131072
                    while True:
                        chunk = fin.read(bufsize)
                        if not chunk:
                            break
                        fout.write(chunk)
                    fout.flush()
                # Verify sizes if possible
                try:
                    src_size = int(getattr(self.sftp.stat(remote_path), "st_size", 0) or 0)
                except Exception:
                    src_size = None
                try:
                    dst_size = int(getattr(self.sftp.stat(new_remote), "st_size", 0) or 0)
                except Exception:
                    dst_size = None
                if (src_size is not None and dst_size is not None) and (dst_size != src_size):
                    try:
                        self.sftp.remove(new_remote)
                    except Exception:
                        pass
                    raise UserError(_("SFTP move failed %s -> %s: copy size mismatch") % (remote_path, new_remote))
                # Remove source
                try:
                    self.sftp.remove(remote_path)
                except Exception as rm_e:
                    # As a last resort, rename source to .old
                    try:
                        self.sftp.rename(remote_path, remote_path + ".old")
                    except Exception:
                        pass
                    raise UserError(_("SFTP move completed copy but could not delete source %s: %s") % (remote_path, rm_e))
                _logger.debug("SFTP move: copy+remove OK %s -> %s", remote_path, new_remote)
                return new_remote
            except Exception as e2:
                raise UserError(_("SFTP move failed %s -> %s: %s; fallback copy failed: %s") % (remote_path, new_remote, e, e2))


class _IMAPBackend(_BaseBackend):
    """IMAP backend using Python stdlib (imaplib + email). Treats attachments as 'files'."""

    def __init__(self, provider, env):
        super().__init__(provider, env)
        self.imap = None
        self.selected_mailbox = None

    def connect(self):
        if imaplib is None:
            raise UserError(_("imaplib not available in this environment."))
        timeout = self.provider.timeout or 60
        host = re.sub(r'^\w+://', '', (self.provider.host or '').strip())
        use_ssl = getattr(self.provider, "imap_use_ssl", True)
        port = self.provider.port or (993 if use_ssl else 143)

        # Establish per-connection timeout (do not set global socket timeout)
        try:
            try:
                self.imap = imaplib.IMAP4_SSL(host, port, timeout=timeout) if use_ssl else imaplib.IMAP4(host, port, timeout=timeout)
            except TypeError:
                # Older Python versions may not support the timeout kwarg
                self.imap = imaplib.IMAP4_SSL(host, port) if use_ssl else imaplib.IMAP4(host, port)
        except Exception as e:
            raise UserError(_("IMAP connect failed: %s") % (e,))

        # Discover capabilities
        caps = set()
        try:
            typ, data = self.imap.capability()
            if typ == "OK" and data:
                caps = set((data[0].decode("utf-8", errors="ignore").upper().split()))
        except Exception:
            caps = set()

        # Upgrade to STARTTLS on non-SSL if supported
        if (not use_ssl) and ("STARTTLS" in caps):
            try:
                self.imap.starttls()
                typ, data = self.imap.capability()
                if typ == "OK" and data:
                    caps = set((data[0].decode("utf-8", errors="ignore").upper().split()))
            except Exception as e:
                _logger.debug("IMAP STARTTLS failed (continuing): %s", e)

        # Authenticate: try LOGIN first, then AUTHENTICATE PLAIN if required/available
        username = (self.provider.username or "").strip()
        password = self.provider.password or ""
        if not username:
            raise UserError(_("IMAP requires username/password."))

        def _caps_have(prefix):
            p = str(prefix or "").upper()
            return any(t.startswith(p) for t in caps)

        def _auth_plain():
            auth_bytes = ("\0%s\0%s" % (username, password)).encode("utf-8")
            self.imap.authenticate("PLAIN", lambda _: auth_bytes)

        def _auth_cram_md5():
            # RFC 2195: response is "username SP HMAC_MD5(challenge, password) hex"
            def cb(chal):
                chal_bytes = chal if isinstance(chal, (bytes, bytearray)) else str(chal).encode("utf-8", "ignore")
                digest = hmac.new((password or "").encode("utf-8"), chal_bytes, hashlib.md5).hexdigest()
                return ("%s %s" % (username, digest)).encode("ascii")
            self.imap.authenticate("CRAM-MD5", cb)

        # Try in order: LOGIN (if allowed), AUTH PLAIN, AUTH CRAM-MD5
        authed = False
        errors = []

        if "LOGINDISABLED" not in caps:
            try:
                self.imap.login(username, password)
                authed = True
            except Exception as e_login:
                errors.append("LOGIN: %s" % e_login)

        if not authed and _caps_have("AUTH=PLAIN"):
            try:
                _auth_plain()
                authed = True
            except Exception as e_plain:
                errors.append("AUTH PLAIN: %s" % e_plain)

        if not authed and _caps_have("AUTH=CRAM-MD5"):
            try:
                _auth_cram_md5()
                authed = True
            except Exception as e_cram:
                errors.append("AUTH CRAM-MD5: %s" % e_cram)

        if not authed:
            raise UserError(_("IMAP authentication failed. Tried: %s; capabilities: %s")
                            % (", ".join(errors) or "none", " ".join(sorted(caps))))

    def close(self):
        try:
            if self.imap:
                try:
                    self.imap.logout()
                except Exception:
                    try:
                        self.imap.shutdown()
                    except Exception:
                        pass
        except Exception:
            pass
        self.imap = None

    def _select(self, mailbox, readonly=True):
        # Normalize mailbox name: treat empty or "/" as INBOX and strip leading slashes
        mbox_raw = (mailbox or "INBOX")
        mbox = str(mbox_raw or "").strip()
        if not mbox or mbox in ("/", "."):
            mbox = "INBOX"
        while mbox.startswith("/"):
            mbox = mbox[1:] or "INBOX"

        typ, _ = self.imap.select(mbox, readonly=readonly)
        if typ != "OK":
            # Try to create and re-select
            try:
                self.imap.create(mbox)
            except Exception:
                pass
            typ, _ = self.imap.select(mbox, readonly=readonly)
            if typ != "OK":
                raise UserError(_("IMAP select failed for mailbox: %s") % mbox)
        self.selected_mailbox = mbox
        return mbox

    def ensure_dir(self, remote_dir):
        if not remote_dir:
            return
        try:
            self.imap.create(remote_dir)
        except Exception:
            # Already exists or cannot create
            pass

    def _iter_attachments(self, msg):
        for part in msg.walk():
            try:
                fname = part.get_filename()
            except Exception:
                fname = None
            if not fname:
                continue
            payload = None
            try:
                payload = part.get_payload(decode=True)
            except Exception:
                payload = None
            if payload is None:
                continue
            yield fname, payload

    def _extract_attachment_names(self, bodystruct):
        """Best-effort extraction of attachment filenames from BODYSTRUCTURE bytes."""
        try:
            s = bodystruct.decode("utf-8", errors="ignore") if isinstance(bodystruct, (bytes, bytearray)) else str(bodystruct or "")
        except Exception:
            s = str(bodystruct or "")
        # Heuristics covering common BODYSTRUCTURE variants:
        names = []
        # 1) NAME / FILENAME "value"
        names += re.findall(r'(?i)\b(?:NAME|FILENAME)\*?"\s*([^"]+)"', s)
        # 2) ("FILENAME" "value") or ("NAME" "value")
        names += re.findall(r'(?i)\("FILENAME"\s+"([^"]+)"\)', s)
        names += re.findall(r'(?i)\("NAME"\s+"([^"]+)"\)', s)
        # 3) name=value without quotes
        names += re.findall(r'(?i)\b(?:NAME|FILENAME)\*?=([^"\s\)]+)', s)
        # 4) RFC2231 simple: filename*=utf-8''value
        names += re.findall(r"(?i)\b(?:NAME|FILENAME)\*=[^'\"\s]*''([^\"\s\)]+)", s)
        # Deduplicate preserving order
        seen = set()
        out = []
        for n in names:
            n = (n or "").strip()
            if not n or n in seen:
                continue
            seen.add(n)
            out.append(n)
        return out

    def list_files(self, remote_dir, pattern=None, exclude=None, limit=None):
        mailbox = (remote_dir or "INBOX")
        self._select(mailbox, readonly=True)
        criteria = (getattr(self.provider, "imap_search_criteria", None) or "ALL").strip()
        try:
            typ, data = self.imap.uid("search", None, criteria)
        except Exception:
            try:
                typ, data = self.imap.uid("search", None, "(" + criteria + ")")
            except Exception as e:
                raise UserError(_("IMAP search failed for %s: %s") % (mailbox, e))
        if typ != "OK":
            raise UserError(_("IMAP search failed for %s") % (mailbox,))
        uids = []
        try:
            if data and data[0]:
                uids = [u for u in data[0].decode("utf-8", errors="ignore").split() if u]
        except Exception:
            pass
        files = []
        # New strategy: do not fetch full messages. Use BODYSTRUCTURE + INTERNALDATE to enumerate attachments.
        # We scan newest first and stop as soon as 'limit' attachments have been collected.
        max_count = int(limit) if limit else None
        # Cap how many UIDs we scan to keep listing responsive
        try:
            cap_param = self.env["ir.config_parameter"].sudo().get_param("ftp_tariff_import.imap_max_uid_scan", "200")
            max_uid_scan = int(cap_param) if str(cap_param).strip() else 200
        except Exception:
            max_uid_scan = 200
        uids_to_scan = list(reversed(uids))[:max_uid_scan]  # newest first, hard cap
        for uid in uids_to_scan:
            if max_count is not None and len(files) >= max_count:
                break
            try:
                typ, resp = self.imap.uid("fetch", uid, "(BODYSTRUCTURE INTERNALDATE)")
                if typ != "OK" or not resp:
                    continue
                bs_bytes = b""
                # resp is a list like [(b'UID (BODYSTRUCTURE ... INTERNALDATE "dd-Mon-YYYY hh:mm:ss +0000")', b'')]
                for item in resp:
                    if isinstance(item, tuple) and item and item[0]:
                        chunk = item[0]
                        try:
                            chunk_bytes = chunk if isinstance(chunk, (bytes, bytearray)) else str(chunk).encode("utf-8", "ignore")
                        except Exception:
                            chunk_bytes = b""
                        bs_bytes += chunk_bytes
                # INTERNALDATE -> timestamp (best-effort)
                try:
                    m = re.search(br'INTERNALDATE\s+"([^"]+)"', bs_bytes, flags=re.IGNORECASE)
                    if m:
                        ds = m.group(1).decode("utf-8", errors="ignore")
                        try:
                            dt = datetime.strptime(ds[:20], "%d-%b-%Y %H:%M:%S")
                            ts = time.mktime(dt.timetuple())
                        except Exception:
                            ts = self._now_ts()
                    else:
                        ts = self._now_ts()
                except Exception:
                    ts = self._now_ts()
                # Attachment names from BODYSTRUCTURE
                names = self._extract_attachment_names(bs_bytes)
                if not names:
                    continue
                for name in names:
                    if not self._match_patterns(name, pattern, exclude):
                        continue
                    files.append({
                        "path": f"imap://{mailbox}|{uid}|{name}",
                        "name": name,
                        "size": 0,  # unknown without downloading; kept lightweight for preview
                        "mtime": float(ts),
                    })
                    if max_count is not None and len(files) >= max_count:
                        break
            except Exception:
                continue
        # Fallback: if BODYSTRUCTURE yielded nothing, try lightweight full fetch on a few newest messages
        if not files and uids_to_scan:
            fallback_n = min(3, len(uids_to_scan), (max_count or 3))
            for uid in uids_to_scan[:fallback_n]:
                try:
                    typ, resp = self.imap.uid("fetch", uid, "(BODY.PEEK[])")
                    if typ != "OK" or not resp:
                        continue
                    raw = None
                    for item in resp:
                        if isinstance(item, tuple) and item[1]:
                            raw = item[1]
                            break
                    if not raw:
                        continue
                    msg = email.message_from_bytes(raw, policy=policy.default if policy else None)
                    ts = self._now_ts()
                    try:
                        idate = msg["Date"]
                        if idate:
                            dtd = parsedate_to_datetime(idate)
                            if dtd:
                                ts = dtd.timestamp()
                    except Exception:
                        pass
                    for name, payload in self._iter_attachments(msg):
                        if not self._match_patterns(name, pattern, exclude):
                            continue
                        files.append({
                            "path": f"imap://{mailbox}|{uid}|{name}",
                            "name": name,
                            "size": 0,
                            "mtime": float(ts),
                        })
                        if max_count is not None and len(files) >= max_count:
                            break
                    if max_count is not None and len(files) >= max_count:
                        break
                except Exception:
                    continue
        try:
            self._last_listing_meta = {
                "search_count": len(uids),
                "scanned_candidates": len(uids_to_scan),
                "found_count": len(files),
                "limit": max_count,
                "mailbox": mailbox,
                "criteria": criteria,
            }
        except Exception:
            pass
        _logger.debug("IMAP list: mailbox=%s criteria=%s uids=%s scanned=%s found=%s limit=%s", mailbox, criteria, len(uids), len(uids_to_scan), len(files), max_count)
        return files

    def _parse_imap_path(self, remote_path):
        s = str(remote_path or "")
        if s.startswith("imap://"):
            s = s[len("imap://"):]
        parts = s.split("|", 2)
        if len(parts) != 3:
            raise UserError(_("Invalid IMAP path: %s") % (remote_path,))
        return parts[0], parts[1], parts[2]

    def download(self, remote_path, local_path):
        mailbox, uid, fname = self._parse_imap_path(remote_path)
        self._select(mailbox, readonly=True)
        typ, resp = self.imap.uid("fetch", uid, "(BODY.PEEK[])")
        if typ != "OK" or not resp:
            raise UserError(_("IMAP fetch failed for %s") % remote_path)
        raw = None
        for item in resp:
            if isinstance(item, tuple) and item[1]:
                raw = item[1]
                break
        if raw is None:
            raise UserError(_("IMAP fetch returned empty payload for %s") % remote_path)
        msg = email.message_from_bytes(raw, policy=policy.default if policy else None)
        for name, payload in self._iter_attachments(msg):
            if name == fname:
                with open(local_path, "wb") as f:
                    f.write(payload)
                return
        raise UserError(_("Attachment not found in message: %s") % fname)

    def mark_seen(self, remote_path):
        mailbox, uid, _fname = self._parse_imap_path(remote_path)
        self._select(mailbox, readonly=False)
        try:
            typ, _ = self.imap.uid("store", uid, "+FLAGS", r"(\Seen)")
            return typ == "OK"
        except Exception:
            return False

    def move(self, remote_path, dst_dir):
        mailbox, uid, _fname = self._parse_imap_path(remote_path)
        target = dst_dir or getattr(self.provider, "remote_dir_processed", None) or "Processed"
        try:
            self.imap.create(target)
        except Exception:
            pass
        typ, _ = self.imap.uid("copy", uid, target)
        if typ != "OK":
            raise UserError(_("IMAP copy failed to %s") % target)
        # mark deleted in source mailbox and expunge
        self._select(mailbox, readonly=False)
        try:
            self.imap.uid("store", uid, "+FLAGS", r"(\Deleted)")
            self.imap.expunge()
        except Exception as e:
            _logger.warning("IMAP move: copied but could not delete src uid=%s in %s: %s", uid, mailbox, e)
        return f"imap://{target}|{uid}|"

class _LocalBackend(_BaseBackend):
    """Local filesystem backend - reads files from server's local filesystem."""

    def __init__(self, provider, env):
        super().__init__(provider, env)

    def connect(self):
        """Verify the local directory exists."""
        local_path = (self.provider.local_path or "").strip()
        if not local_path:
            raise UserError(_("Le chemin local n'est pas configuré. Veuillez spécifier un chemin dans 'Chemin local'."))
        if not os.path.exists(local_path):
            raise UserError(_("Le chemin local n'existe pas: %s") % local_path)
        if not os.path.isdir(local_path):
            raise UserError(_("Le chemin local n'est pas un dossier: %s") % local_path)
        _logger.info("Local backend connected for provider %s, path: %s", self.provider.id, local_path)

    def close(self):
        """Nothing to close for local filesystem."""
        pass

    def ensure_dir(self, remote_dir):
        """Create local directory if it doesn't exist."""
        if not remote_dir:
            return
        try:
            os.makedirs(remote_dir, exist_ok=True)
        except Exception as e:
            _logger.warning("Could not create local directory %s: %s", remote_dir, e)

    def list_files(self, remote_dir, pattern=None, exclude=None, limit=None, include_folders=False):
        """List files in a local directory.
        
        Args:
            remote_dir: Directory path to list. If empty, uses provider's local_path.
            pattern: Filename pattern to match (glob style)
            exclude: Filename pattern to exclude
            limit: Maximum number of files to return
            include_folders: If True, include folders in the result
        """
        base_path = (self.provider.local_path or "").strip()
        
        # Resolve the actual path to list
        if remote_dir and remote_dir != "/":
            # If remote_dir is relative, join with base_path
            if not os.path.isabs(remote_dir):
                list_path = os.path.join(base_path, remote_dir)
            else:
                list_path = remote_dir
        else:
            list_path = base_path
        
        if not os.path.exists(list_path):
            raise UserError(_("Le chemin local n'existe pas: %s") % list_path)
        if not os.path.isdir(list_path):
            raise UserError(_("Le chemin local n'est pas un dossier: %s") % list_path)
        
        files = []
        try:
            for entry in os.scandir(list_path):
                try:
                    is_folder = entry.is_dir()
                    
                    # Skip folders unless include_folders is True
                    if is_folder and not include_folders:
                        continue
                    
                    name = entry.name
                    
                    # Don't filter folders by pattern
                    if not is_folder and not self._match_patterns(name, pattern, exclude):
                        continue
                    
                    stat_info = entry.stat()
                    
                    files.append({
                        "path": os.path.join(list_path, name),
                        "name": name,
                        "size": int(stat_info.st_size) if not is_folder else 0,
                        "mtime": float(stat_info.st_mtime),
                        "is_folder": is_folder,
                        "folder_id": os.path.join(list_path, name) if is_folder else None,
                    })
                except Exception as e:
                    _logger.debug("Could not stat file %s: %s", entry.name, e)
                    continue
        except Exception as e:
            raise UserError(_("Erreur lors de la lecture du dossier %s: %s") % (list_path, e))
        
        # Sort by modification time (newest first) for files, folders first
        files.sort(key=lambda x: (not x.get("is_folder", False), -(x.get("mtime") or 0)))
        
        if limit:
            files = files[:int(limit)]
        
        return files

    def list_folders(self, folder_path=None):
        """List only folders in a local directory.
        
        Args:
            folder_path: Directory path to list. If None, uses provider's local_path.
            
        Returns:
            List of folder dicts with keys: id, name, path
        """
        base_path = (self.provider.local_path or "").strip()
        list_path = folder_path or base_path
        
        if not os.path.exists(list_path):
            return []
        if not os.path.isdir(list_path):
            return []
        
        folders = []
        try:
            for entry in os.scandir(list_path):
                if entry.is_dir():
                    folders.append({
                        "id": entry.path,
                        "name": entry.name,
                        "path": entry.path,
                    })
        except Exception as e:
            _logger.warning("Could not list folders in %s: %s", list_path, e)
        
        folders.sort(key=lambda x: x.get("name", "").lower())
        return folders

    def get_folder_path(self, folder_path):
        """Get the full path (breadcrumb) of a folder.
        
        Args:
            folder_path: Folder path to get breadcrumb for
            
        Returns:
            List of dicts [{id, name}, ...] from root to the folder
        """
        base_path = (self.provider.local_path or "").strip()
        
        if not folder_path:
            return [{"id": base_path, "name": os.path.basename(base_path) or "Racine"}]
        
        # Normalize paths
        folder_path = os.path.normpath(folder_path)
        base_path = os.path.normpath(base_path)
        
        path = []
        current = folder_path
        
        # Build path from current to base_path
        while current and current != base_path:
            name = os.path.basename(current)
            if name:
                path.insert(0, {"id": current, "name": name})
            parent = os.path.dirname(current)
            if parent == current:
                break
            current = parent
        
        # Add base path at the beginning
        path.insert(0, {"id": base_path, "name": os.path.basename(base_path) or "Racine"})
        
        return path

    def download(self, remote_path, local_path):
        """Copy a local file to another local path."""
        import shutil
        
        if not os.path.exists(remote_path):
            raise UserError(_("Le fichier source n'existe pas: %s") % remote_path)
        
        try:
            shutil.copy2(remote_path, local_path)
        except Exception as e:
            raise UserError(_("Erreur lors de la copie du fichier %s: %s") % (remote_path, e))

    def move(self, remote_path, dst_dir):
        """Move a local file to another directory."""
        import shutil
        
        if not os.path.exists(remote_path):
            raise UserError(_("Le fichier source n'existe pas: %s") % remote_path)
        
        basename = os.path.basename(remote_path)
        
        # Ensure destination directory exists
        self.ensure_dir(dst_dir)
        
        dst_path = os.path.join(dst_dir, basename)
        
        # Handle collision
        if os.path.exists(dst_path):
            dst_path = os.path.join(dst_dir, f"{basename}.{int(time.time())}")
        
        try:
            shutil.move(remote_path, dst_path)
            return dst_path
        except Exception as e:
            raise UserError(_("Erreur lors du déplacement du fichier %s vers %s: %s") % (remote_path, dst_path, e))


class _URLBackend(_BaseBackend):
    """URL backend for downloading files via HTTP/HTTPS."""

    def __init__(self, provider, env):
        super().__init__(provider, env)

    def connect(self):
        """Verify URL is configured."""
        url = (self.provider.url or "").strip()
        if not url:
            raise UserError(_("L'URL n'est pas configurée. Veuillez spécifier une URL dans le champ 'URL du fichier'."))
        if not url.startswith(("http://", "https://")):
            raise UserError(_("L'URL doit commencer par http:// ou https://"))
        _logger.info("URL backend connected for provider %s, URL: %s", self.provider.id, url)

    def close(self):
        """Nothing to close for URL backend."""
        pass

    def ensure_dir(self, remote_dir):
        """No-op for URL backend."""
        pass

    def list_files(self, remote_dir, pattern=None, exclude=None, limit=None):
        """URL backend returns a single file entry (the configured URL).
        
        The URL is treated as a single file to download.
        """
        url = (self.provider.url or "").strip()
        if not url:
            return []
        
        # Extract filename from URL
        filename = url.split("/")[-1].split("?")[0] or "download.csv"
        
        # Check pattern matching
        if pattern and not self._match_patterns(filename, pattern, exclude):
            return []
        
        return [{
            "path": url,
            "name": filename,
            "size": 0,  # Size unknown until download
            "mtime": self._now_ts(),
        }]

    def download(self, remote_path, local_path):
        """Download file from URL to local path."""
        if requests is None:
            raise UserError(_("URL backend requires the 'requests' library."))
        
        url = remote_path if remote_path.startswith(("http://", "https://")) else self.provider.url
        username = (self.provider.url_username or "").strip()
        password = self.provider.url_password or ""
        
        timeout = self.provider.timeout or 60
        
        try:
            # Prepare authentication if provided
            auth = None
            if username:
                auth = (username, password)
            
            # Download with streaming to handle large files
            response = requests.get(
                url,
                auth=auth,
                timeout=timeout,
                stream=True,
                allow_redirects=True,
            )
            response.raise_for_status()
            
            # Write to local file
            with open(local_path, "wb") as f:
                for chunk in response.iter_content(chunk_size=131072):
                    if chunk:
                        f.write(chunk)
                        
        except requests.RequestException as e:
            raise UserError(_("URL download failed for %s: %s") % (url, e))

    def move(self, remote_path, dst_dir):
        """No-op for URL backend - files cannot be moved on remote HTTP server."""
        return remote_path


class _GoogleDriveBackend(_BaseBackend):
    """Google Drive backend using REST API with OAuth 2.0."""

    def __init__(self, provider, env):
        super().__init__(provider, env)
        self._access_token = None

    def _refresh_token_if_needed(self):
        """Refresh access token if expired or missing."""
        from datetime import datetime, timedelta
        
        provider = self.provider
        now = datetime.now()
        
        # Check if token is still valid
        if provider.gdrive_access_token and provider.gdrive_token_expiry:
            expiry = provider.gdrive_token_expiry
            if isinstance(expiry, str):
                expiry = datetime.fromisoformat(expiry.replace('Z', '+00:00'))
            if expiry.replace(tzinfo=None) > now:
                self._access_token = provider.gdrive_access_token
                return
        
        # Need to refresh
        if not provider.gdrive_refresh_token:
            raise UserError(_("Google Drive not authorized. Please authorize first."))
        
        if requests is None:
            raise UserError(_("requests library not available."))
        
        try:
            response = requests.post(
                GOOGLE_TOKEN_URL,
                data={
                    "client_id": provider.gdrive_client_id,
                    "client_secret": provider.gdrive_client_secret,
                    "refresh_token": provider.gdrive_refresh_token,
                    "grant_type": "refresh_token",
                },
                timeout=30,
            )
            token_data = response.json()
            
            if "error" in token_data:
                raise UserError(_("Google Drive token refresh failed: %s") % token_data.get("error_description", token_data.get("error")))
            
            access_token = token_data.get("access_token")
            expires_in = token_data.get("expires_in", 3600)
            token_expiry = now + timedelta(seconds=expires_in - 60)
            
            # Update provider (using sudo to bypass ACL)
            provider.sudo().write({
                "gdrive_access_token": access_token,
                "gdrive_token_expiry": token_expiry,
            })
            
            self._access_token = access_token
            
        except requests.RequestException as e:
            raise UserError(_("Google Drive token refresh failed: %s") % e)

    def _headers(self):
        return {"Authorization": f"Bearer {self._access_token}"}

    def connect(self):
        if requests is None:
            raise UserError(_("Google Drive backend requires the 'requests' library."))
        if not self.provider.gdrive_client_id or not self.provider.gdrive_client_secret:
            raise UserError(_("Google Drive Client ID and Secret are required."))
        self._refresh_token_if_needed()
        _logger.info("Google Drive backend connected for provider %s", self.provider.id)

    def close(self):
        self._access_token = None

    def ensure_dir(self, remote_dir):
        # Google Drive uses folder IDs, not paths
        # This is a no-op for now; folders should be pre-created
        pass

    def list_files(self, remote_dir, pattern=None, exclude=None, limit=None, include_folders=False):
        """List files in a Google Drive folder.
        
        Args:
            remote_dir: Folder ID to list. If starts with 'gdrive://', extracts the ID.
            pattern: Filename pattern to match
            exclude: Filename pattern to exclude
            limit: Maximum number of items to return
            include_folders: If True, include folders in the result
        """
        self._refresh_token_if_needed()
        
        # Support navigating via gdrive:// paths
        folder_id = remote_dir or self.provider.gdrive_folder_id or "root"
        if folder_id.startswith("gdrive://"):
            folder_id = folder_id[len("gdrive://"):]
        
        # Build query
        query_parts = [f"'{folder_id}' in parents", "trashed = false"]
        query = " and ".join(query_parts)
        
        params = {
            "q": query,
            "fields": "files(id, name, size, modifiedTime, mimeType)",
            "pageSize": min(limit or 500, 1000),
            "orderBy": "folder,name",  # Folders first, then by name
        }
        
        try:
            response = requests.get(
                f"{GOOGLE_DRIVE_API_BASE}/files",
                headers=self._headers(),
                params=params,
                timeout=60,
            )
            response.raise_for_status()
            data = response.json()
        except requests.RequestException as e:
            raise UserError(_("Google Drive list failed: %s") % e)
        
        files = []
        for item in data.get("files", []):
            is_folder = item.get("mimeType") == "application/vnd.google-apps.folder"
            
            # Skip folders unless include_folders is True
            if is_folder and not include_folders:
                continue
            
            name = item.get("name", "")
            
            # Don't filter folders by pattern
            if not is_folder and not self._match_patterns(name, pattern, exclude):
                continue
            
            # Parse modifiedTime
            mtime = self._now_ts()
            try:
                mod_str = item.get("modifiedTime", "")
                if mod_str:
                    # Format: 2024-12-24T10:30:00.000Z
                    dt = datetime.fromisoformat(mod_str.replace("Z", "+00:00"))
                    mtime = dt.timestamp()
            except Exception:
                pass
            
            files.append({
                "path": f"gdrive://{item.get('id')}",
                "name": name,
                "size": int(item.get("size", 0) or 0),
                "mtime": mtime,
                "is_folder": is_folder,
                "folder_id": item.get("id") if is_folder else None,
            })
        
        if limit:
            files = files[:int(limit)]
        
        return files

    def list_folders(self, folder_id=None):
        """List only folders in a Google Drive folder.
        
        Args:
            folder_id: Parent folder ID. If None, uses provider's gdrive_folder_id or root.
            
        Returns:
            List of folder dicts with keys: id, name, path
        """
        self._refresh_token_if_needed()
        
        parent_id = folder_id or self.provider.gdrive_folder_id or "root"
        if parent_id.startswith("gdrive://"):
            parent_id = parent_id[len("gdrive://"):]
        
        # Build query for folders only
        query = f"'{parent_id}' in parents and trashed = false and mimeType = 'application/vnd.google-apps.folder'"
        
        params = {
            "q": query,
            "fields": "files(id, name)",
            "pageSize": 100,
            "orderBy": "name",
        }
        
        try:
            response = requests.get(
                f"{GOOGLE_DRIVE_API_BASE}/files",
                headers=self._headers(),
                params=params,
                timeout=60,
            )
            response.raise_for_status()
            data = response.json()
        except requests.RequestException as e:
            raise UserError(_("Google Drive list folders failed: %s") % e)
        
        folders = []
        for item in data.get("files", []):
            folders.append({
                "id": item.get("id"),
                "name": item.get("name", ""),
                "path": f"gdrive://{item.get('id')}",
            })
        
        return folders

    def get_folder_path(self, folder_id):
        """Get the full path (breadcrumb) of a folder.
        
        Args:
            folder_id: Folder ID to get path for
            
        Returns:
            List of dicts [{id, name}, ...] from root to the folder
        """
        self._refresh_token_if_needed()
        
        if not folder_id or folder_id == "root":
            return [{"id": "root", "name": "Mon Drive"}]
        
        path = []
        current_id = folder_id
        max_depth = 20  # Prevent infinite loops
        
        while current_id and current_id != "root" and max_depth > 0:
            max_depth -= 1
            try:
                response = requests.get(
                    f"{GOOGLE_DRIVE_API_BASE}/files/{current_id}",
                    headers=self._headers(),
                    params={"fields": "id, name, parents"},
                    timeout=30,
                )
                response.raise_for_status()
                data = response.json()
                
                path.insert(0, {"id": data.get("id"), "name": data.get("name", "")})
                
                parents = data.get("parents", [])
                current_id = parents[0] if parents else None
            except Exception:
                break
        
        # Add root at the beginning
        path.insert(0, {"id": "root", "name": "Mon Drive"})
        
        return path

    def _parse_gdrive_path(self, remote_path):
        """Extract file ID from gdrive:// path."""
        s = str(remote_path or "")
        if s.startswith("gdrive://"):
            return s[len("gdrive://"):]
        return s

    def download(self, remote_path, local_path):
        """Download a file from Google Drive."""
        self._refresh_token_if_needed()
        
        file_id = self._parse_gdrive_path(remote_path)
        
        try:
            response = requests.get(
                f"{GOOGLE_DRIVE_API_BASE}/files/{file_id}",
                headers=self._headers(),
                params={"alt": "media"},
                timeout=300,
                stream=True,
            )
            response.raise_for_status()
            
            with open(local_path, "wb") as f:
                for chunk in response.iter_content(chunk_size=131072):
                    if chunk:
                        f.write(chunk)
                        
        except requests.RequestException as e:
            raise UserError(_("Google Drive download failed: %s") % e)

    def upload(self, local_path, remote_name, folder_id=None):
        """Upload a file to Google Drive."""
        self._refresh_token_if_needed()
        
        folder_id = folder_id or self.provider.gdrive_export_folder_id or "root"
        
        # Prepare metadata
        metadata = {
            "name": remote_name,
            "parents": [folder_id],
        }
        
        # Read file content
        with open(local_path, "rb") as f:
            file_content = f.read()
        
        # Multipart upload
        import mimetypes
        mime_type = mimetypes.guess_type(remote_name)[0] or "application/octet-stream"
        
        try:
            # Simple upload for files < 5MB
            if len(file_content) < 5 * 1024 * 1024:
                import json
                boundary = "---boundary---"
                body = (
                    f"--{boundary}\r\n"
                    f"Content-Type: application/json; charset=UTF-8\r\n\r\n"
                    f"{json.dumps(metadata)}\r\n"
                    f"--{boundary}\r\n"
                    f"Content-Type: {mime_type}\r\n\r\n"
                ).encode("utf-8") + file_content + f"\r\n--{boundary}--".encode("utf-8")
                
                headers = self._headers()
                headers["Content-Type"] = f"multipart/related; boundary={boundary}"
                
                response = requests.post(
                    f"{GOOGLE_UPLOAD_API_BASE}/files?uploadType=multipart",
                    headers=headers,
                    data=body,
                    timeout=300,
                )
                response.raise_for_status()
                return response.json().get("id")
            else:
                # Resumable upload for larger files
                raise UserError(_("Files larger than 5MB require resumable upload (not implemented yet)."))
                
        except requests.RequestException as e:
            raise UserError(_("Google Drive upload failed: %s") % e)

    def move(self, remote_path, dst_dir):
        """Move a file to a different folder in Google Drive."""
        self._refresh_token_if_needed()
        
        file_id = self._parse_gdrive_path(remote_path)
        dst_folder_id = dst_dir or self.provider.remote_dir_processed or "root"
        
        try:
            # Get current parents
            response = requests.get(
                f"{GOOGLE_DRIVE_API_BASE}/files/{file_id}",
                headers=self._headers(),
                params={"fields": "parents"},
                timeout=30,
            )
            response.raise_for_status()
            current_parents = response.json().get("parents", [])
            
            # Move to new folder
            params = {
                "addParents": dst_folder_id,
                "removeParents": ",".join(current_parents),
            }
            response = requests.patch(
                f"{GOOGLE_DRIVE_API_BASE}/files/{file_id}",
                headers=self._headers(),
                params=params,
                timeout=30,
            )
            response.raise_for_status()
            
            return f"gdrive://{file_id}"
            
        except requests.RequestException as e:
            raise UserError(_("Google Drive move failed: %s") % e)


def get_backend(provider, env):
    """Factory returning a connected backend context manager."""
    proto = (provider.protocol or "sftp").lower()
    if proto == "ftp":
        return _FTPBackend(provider, env)
    elif proto == "gdrive":
        return _GoogleDriveBackend(provider, env)
    elif proto == "local":
        return _LocalBackend(provider, env)
    elif proto == "url":
        return _URLBackend(provider, env)
    elif proto == "sftp":
        # Optional SFTP: controlled by system parameter and availability of paramiko
        try:
            enable_sftp = env["ir.config_parameter"].sudo().get_param("ftp_tariff_import.enable_sftp", "1")
        except Exception:
            enable_sftp = "1"
        enable_sftp_bool = str(enable_sftp).strip().lower() not in ("0", "false", "no", "off")
        if not enable_sftp_bool:
            raise UserError(_("SFTP disabled by system parameter 'ftp_tariff_import.enable_sftp'. Enable it or switch this provider to FTP."))
        if paramiko is None:
            raise UserError(_("SFTP backend requires the 'paramiko' library. Install it in the Odoo environment or disable SFTP."))
        return _SFTPBackend(provider, env)
    elif proto == "imap":
        return _IMAPBackend(provider, env)
    else:
        raise UserError(_("Unsupported protocol: %s") % (provider.protocol,))


class FtpBackendService(models.AbstractModel):
    """Convenience façade used by wizards/services to interact with providers."""
    _name = "ftp.backend.service"
    _description = "FTP/SFTP/IMAP Backend Service"

    @api.model
    def list_provider_files(self, provider, preview_limit=None, backend=None):
        if backend:
            files = backend.list_files(
                provider.remote_dir_in or "/",
                pattern=provider.file_pattern or None,
                exclude=provider.exclude_pattern or None,
                limit=preview_limit or provider.max_preview or 500,
            )
        else:
            with get_backend(provider, self.env) as bk:
                files = bk.list_files(
                    provider.remote_dir_in or "/",
                    pattern=provider.file_pattern or None,
                    exclude=provider.exclude_pattern or None,
                    limit=preview_limit or provider.max_preview or 500,
                )
        # Sanitize file names and paths to remove null bytes that PostgreSQL rejects
        return [sanitize_dict(f, keys=["path", "name"]) for f in files]

    @api.model
    def list_provider_files_with_meta(self, provider, preview_limit=None, backend=None):
        """Return (files, meta) where meta may include IMAP listing diagnostics."""
        if backend:
            files = backend.list_files(
                provider.remote_dir_in or "/",
                pattern=provider.file_pattern or None,
                exclude=provider.exclude_pattern or None,
                limit=preview_limit or provider.max_preview or 500,
            )
            meta = getattr(backend, "_last_listing_meta", {}) or {}
        else:
            with get_backend(provider, self.env) as bk:
                files = bk.list_files(
                    provider.remote_dir_in or "/",
                    pattern=provider.file_pattern or None,
                    exclude=provider.exclude_pattern or None,
                    limit=preview_limit or provider.max_preview or 500,
                )
                meta = getattr(bk, "_last_listing_meta", {}) or {}
        # Sanitize file names and paths to remove null bytes that PostgreSQL rejects
        return [sanitize_dict(f, keys=["path", "name"]) for f in files], meta

    @api.model
    def download_to_temp(self, provider, remote_path, backend=None):
        """Download a remote file to a named temporary file on the Odoo server.
        Returns: (local_path, size_bytes)
        """
        tmp = tempfile.NamedTemporaryFile(prefix="ftp_imp_", suffix=".dat", delete=False)
        tmp_path = tmp.name
        tmp.close()
        if backend:
            backend.download(remote_path, tmp_path)
        else:
            with get_backend(provider, self.env) as bk:
                bk.download(remote_path, tmp_path)
        size = 0
        try:
            size = os.path.getsize(tmp_path)
        except Exception:
            pass
        return tmp_path, size

    @api.model
    def move_remote(self, provider, remote_path, dst_dir, backend=None):
        if backend:
            return backend.move(remote_path, dst_dir)
        with get_backend(provider, self.env) as bk:
            return bk.move(remote_path, dst_dir)

    @api.model
    def ensure_remote_dir(self, provider, remote_dir, backend=None):
        if backend:
            return backend.ensure_dir(remote_dir)
        with get_backend(provider, self.env) as bk:
            return bk.ensure_dir(remote_dir)

    @api.model
    def mark_seen(self, provider, remote_path, backend=None):
        if backend:
            return backend.mark_seen(remote_path)
        with get_backend(provider, self.env) as bk:
            return bk.mark_seen(remote_path)
