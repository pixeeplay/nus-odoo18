# -*- coding: utf-8 -*-
"""
Multi-File Merger for TD Synnex and similar providers.

Fusionne plusieurs fichiers (Material, Stock, Taxes) sur une clé commune (Matnr).
"""

import csv
import io
import re
import logging
import tempfile
import os

_logger = logging.getLogger(__name__)


class MultiFileMerger:
    """Fusionne plusieurs fichiers sur une clé commune.
    
    Cas d'usage principal: TD Synnex avec 3 fichiers:
    - MaterialFile.txt (base, header, tab/espaces)
    - StockFile.txt (header, tab/espaces)  
    - TaxesGouv.txt (PAS de header, Matnr en regex, taxe = dernier float)
    """
    
    def __init__(self, merge_key="Matnr"):
        """
        Args:
            merge_key: Nom de la colonne clé pour la fusion (default: Matnr)
        """
        self.merge_key = merge_key
        self.data = {}  # {matnr: {col1: val1, col2: val2, ...}}
        self.all_columns = []  # Liste ordonnée de toutes les colonnes
        self._seen_columns = set()
    
    def parse_sap_file(self, content, delimiter="sap", has_header=True, prefix=""):
        """Parse un fichier format SAP (tab ou espaces multiples).
        
        Args:
            content: Contenu du fichier (str)
            delimiter: "sap" (auto), "\t" (tab), ";", ","
            has_header: True si le fichier a une ligne d'en-tête
            prefix: Préfixe à ajouter aux colonnes (pour éviter les conflits)
            
        Returns:
            dict: {matnr: {col1: val1, ...}}
        """
        if not content:
            return {}
        
        lines = content.split('\n')
        if not lines:
            return {}
        
        result = {}
        headers = []
        
        # Déterminer le délimiteur
        if delimiter == "sap":
            # SAP: Tab prioritaire, sinon 2+ espaces
            delimiter_pattern = re.compile(r'\t|\s{2,}')
        elif delimiter == "\t":
            delimiter_pattern = re.compile(r'\t')
        else:
            delimiter_pattern = None
        
        for i, line in enumerate(lines):
            line = line.rstrip('\r\n')
            if not line.strip():
                continue
            
            # Split selon le délimiteur
            if delimiter_pattern:
                cells = delimiter_pattern.split(line)
            else:
                cells = line.split(delimiter)
            
            cells = [c.strip() for c in cells]
            
            if i == 0 and has_header:
                # Ligne d'en-tête
                headers = cells
                # Ajouter le préfixe si défini
                if prefix:
                    headers = [f"{prefix}_{h}" if h != self.merge_key else h for h in headers]
                
                # Enregistrer les colonnes
                for h in headers:
                    if h not in self._seen_columns and h != self.merge_key:
                        self._seen_columns.add(h)
                        self.all_columns.append(h)
                continue
            
            if not headers:
                # Pas de header défini (ou has_header=False)
                continue
            
            # Trouver la valeur de la clé
            key_idx = None
            for idx, h in enumerate(headers):
                if h.lower() == self.merge_key.lower() or h == self.merge_key:
                    key_idx = idx
                    break
            
            if key_idx is None or key_idx >= len(cells):
                continue
            
            matnr = cells[key_idx].strip()
            if not matnr:
                continue
            
            # Construire le dict pour cette ligne
            row_data = {}
            for idx, h in enumerate(headers):
                if idx < len(cells) and h != self.merge_key:
                    row_data[h] = cells[idx]
            
            result[matnr] = row_data
        
        _logger.info("[MERGER] Parsed SAP file: %d lines with key '%s'", len(result), self.merge_key)
        return result
    
    def parse_taxes_file(self, content, column_name="deee_tax"):
        """Parse le fichier TaxesGouv (format spécial sans header).
        
        Format attendu:
        - Pas d'en-tête
        - 1ère colonne = Matnr (7-8 chiffres au début de ligne)
        - Taxe = dernier nombre flottant de la ligne
        
        Exemple: "10310134 920-010104?KIT DEEE 0.32"
        => matnr=10310134, taxe=0.32
        
        Args:
            content: Contenu du fichier
            column_name: Nom de la colonne à créer pour la taxe
            
        Returns:
            dict: {matnr: {column_name: tax_value}}
        """
        if not content:
            return {}
        
        result = {}
        lines = content.split('\n')
        
        # Pattern pour extraire Matnr (7-8 chiffres au début)
        matnr_pattern = re.compile(r'^(\d{7,8})')
        # Pattern pour extraire les nombres flottants
        float_pattern = re.compile(r'(\d+[.,]\d+)')
        
        for line in lines:
            line = line.strip()
            if not line:
                continue
            
            # Extraire Matnr
            matnr_match = matnr_pattern.match(line)
            if not matnr_match:
                continue
            
            matnr = matnr_match.group(1)
            
            # Extraire le dernier float de la ligne (= taxe)
            floats = float_pattern.findall(line)
            if floats:
                tax_str = floats[-1].replace(',', '.')
                try:
                    tax_value = float(tax_str)
                except ValueError:
                    tax_value = 0.0
            else:
                tax_value = 0.0
            
            result[matnr] = {column_name: str(tax_value)}
        
        # Enregistrer la colonne
        if column_name not in self._seen_columns:
            self._seen_columns.add(column_name)
            self.all_columns.append(column_name)
        
        _logger.info("[MERGER] Parsed taxes file: %d lines, column '%s'", len(result), column_name)
        return result
    
    def merge(self, base_data, *additional_data_dicts):
        """Fusionne plusieurs dictionnaires sur la clé Matnr.
        
        LEFT JOIN: seuls les Matnr présents dans base_data sont conservés.
        
        Args:
            base_data: Dictionnaire de base (MaterialFile)
            *additional_data_dicts: Dictionnaires additionnels (StockFile, TaxesGouv)
            
        Returns:
            dict: Données fusionnées {matnr: {col1: val1, ...}}
        """
        merged = {}
        
        for matnr, base_row in base_data.items():
            merged[matnr] = dict(base_row)
            
            # Ajouter les données des fichiers additionnels
            for add_data in additional_data_dicts:
                if matnr in add_data:
                    merged[matnr].update(add_data[matnr])
        
        _logger.info("[MERGER] Merged data: %d rows", len(merged))
        return merged
    
    def to_csv(self, merged_data, include_key=True):
        """Convertit les données fusionnées en CSV.
        
        Args:
            merged_data: Données fusionnées {matnr: {col1: val1, ...}}
            include_key: Inclure la clé de fusion comme première colonne
            
        Returns:
            str: Contenu CSV
        """
        if not merged_data:
            return ""
        
        # Construire les headers
        headers = []
        if include_key:
            headers.append(self.merge_key)
        headers.extend(self.all_columns)
        
        output = io.StringIO()
        writer = csv.writer(output, delimiter=';', quoting=csv.QUOTE_MINIMAL)
        
        # Écrire l'en-tête
        writer.writerow(headers)
        
        # Écrire les lignes
        for matnr, row_data in merged_data.items():
            row = []
            if include_key:
                row.append(matnr)
            for col in self.all_columns:
                row.append(row_data.get(col, ''))
            writer.writerow(row)
        
        return output.getvalue()
    
    def to_temp_file(self, merged_data, include_key=True):
        """Écrit les données fusionnées dans un fichier temporaire.
        
        Args:
            merged_data: Données fusionnées
            include_key: Inclure la clé de fusion
            
        Returns:
            str: Chemin du fichier temporaire
        """
        csv_content = self.to_csv(merged_data, include_key)
        
        fd, tmp_path = tempfile.mkstemp(prefix="merged_", suffix=".csv")
        os.close(fd)
        
        with open(tmp_path, 'w', encoding='utf-8', newline='') as f:
            f.write(csv_content)
        
        _logger.info("[MERGER] Written merged CSV to: %s", tmp_path)
        return tmp_path
    
    def get_all_headers(self, include_key=True):
        """Retourne la liste de tous les headers du fichier fusionné.
        
        Args:
            include_key: Inclure la clé de fusion
            
        Returns:
            list: Liste des noms de colonnes
        """
        headers = []
        if include_key:
            headers.append(self.merge_key)
        headers.extend(self.all_columns)
        return headers


def merge_provider_files(provider, material_content, stock_content=None, taxes_content=None):
    """Fonction utilitaire pour fusionner les fichiers d'un provider multi-fichiers.
    
    Args:
        provider: ftp.provider record
        material_content: Contenu du fichier Material (str)
        stock_content: Contenu du fichier Stock (str, optionnel)
        taxes_content: Contenu du fichier Taxes (str, optionnel)
        
    Returns:
        tuple: (tmp_path, headers) - Chemin du fichier fusionné et liste des headers
    """
    merge_key = provider.multi_file_merge_key or "Matnr"
    
    merger = MultiFileMerger(merge_key=merge_key)
    
    # Parser le fichier Material (base)
    material_delim = provider.multi_file_material_delimiter or "sap"
    material_data = merger.parse_sap_file(
        material_content, 
        delimiter=material_delim,
        has_header=True,
        prefix=""  # Pas de préfixe pour le fichier principal
    )
    
    if not material_data:
        _logger.warning("[MERGER] Material file is empty or could not be parsed")
        return None, []
    
    additional_data = []
    
    # Parser le fichier Stock (optionnel)
    if stock_content:
        stock_delim = provider.multi_file_stock_delimiter or "sap"
        stock_data = merger.parse_sap_file(
            stock_content,
            delimiter=stock_delim,
            has_header=True,
            prefix="stock"  # Préfixe pour éviter les conflits
        )
        additional_data.append(stock_data)
    
    # Parser le fichier Taxes (optionnel, format spécial)
    if taxes_content:
        taxes_data = merger.parse_taxes_file(
            taxes_content,
            column_name="deee_tax"
        )
        additional_data.append(taxes_data)
    
    # Fusionner tout
    merged_data = merger.merge(material_data, *additional_data)
    
    # Écrire dans un fichier temporaire
    tmp_path = merger.to_temp_file(merged_data, include_key=True)
    headers = merger.get_all_headers(include_key=True)
    
    return tmp_path, headers
